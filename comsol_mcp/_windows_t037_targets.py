"""Finite static Windows intent and current readonly comparisons, never Resume.

The loader's process-local source association is deliberately absent from the
JSON schema. Neither a parsed value nor any observation is issuer authority.
Filesystem SD hashes below bind SDK OWNER/GROUP/DACL bytes; WFP hashes bind the
existing canonical OWNER/GROUP/DACL value including ordered ACEs, not SDDL.
The original source identity instead binds the private helper's composite
pinned-chain list (legacy IDs, raw OWNER/DACL hashes and owner roles).
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes as C
from contextlib import contextmanager
import hashlib
import json
import ntpath
import re
import struct
import threading
import weakref

from ._execution_contract import ExecutionContractError
from ._windows_t037_native import canonical_security_descriptor, canonical_security_descriptor_sha256
from ._windows_t037_wfp import guid_key

SCHEMA = 'comsol-mcp.windows-t037-finite-targets/1'
FS_SECURITY = 'SDK_OWNER_GROUP_DACL_SELF_RELATIVE_RAW_SHA256'
WFP_SECURITY = 'OWNER_GROUP_DACL_CANONICAL_ACE_ORDER_SHA256'
MAX_TARGETS = 64
MAX_CONTENT_BYTES = 512 * 1024 * 1024
# fwpmu.h, FWPM_ACTRL_READ. Enumeration visibility needs READ, while the
# descriptor getter needs READ_CONTROL. Neither permits ADD/ADD_LINK/write.
FWPM_ACTRL_READ = 0x00000080
WFP_OBJECT_READ = 0x00020000 | FWPM_ACTRL_READ
_SHA = re.compile(r'[0-9a-f]{64}')
_TCB = frozenset(('S-1-5-18', 'S-1-5-32-544'))
_INSTALLER = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
_ROLES = frozenset(('installation_rx', 'code_rx', 'config_rx', 'runtime_rx',
                    'project_root', 'data_leaf', 'temp_leaf',
                    'launcher_image', 'server_image', 'worker_image'))
_DIRECTORIES = _ROLES - {'launcher_image', 'server_image', 'worker_image'}
_WRITABLE = frozenset(('project_root', 'data_leaf', 'temp_leaf'))
_LEAVES = frozenset(('data_leaf', 'temp_leaf'))
_ORIGINS = {}  # Weak identity associations made only after a fresh loader SDK read.
_READS = {}  # Strong roots for original readonly APIs, retained on UNKNOWN.
_LOADER_APIS = {}  # Actual startup reader APIs retained before their first SDK open.
_HELD = {}  # Original same-thread A scopes, retained on unresolved cleanup; never a Resume capability.
_HELD_SEAL = object()


def _fail(reason, message, **details):
    raise ExecutionContractError('T037_POLICY_UNVERIFIED', message, safe_retry=False,
        stage='pre_dispatch', details={'phase': 'STATIC_HOST_BROKER_POLICY', 'reasons': [reason], **details})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _path(value):
    if (type(value) is not str or not re.fullmatch(r'[A-Za-z]:\\[^\x00]*', value)
            or len(value) >= 240 or '/' in value or ntpath.normpath(value) != value
            or any(part in ('', '.', '..') or part.endswith((' ', '.')) or ':' in part
                   for part in value[3:].split('\\'))):
        _fail('FINITE_TARGET_PATH_INVALID', 'finite target requires a canonical ordinary local drive path')
    return ntpath.normcase(value)


def _within(path, root):
    return path == root or path.startswith(root + '\\')


def _sid(raw):
    if len(raw) < 8 or raw[0] != 1 or raw[1] > 15 or len(raw) != 8 + raw[1] * 4:
        _fail('FINITE_DESCRIPTOR_INVALID', 'canonical SID bytes required')
    return 'S-1-' + str(int.from_bytes(raw[2:8], 'big')) + ''.join(
        '-' + str(int.from_bytes(raw[i:i+4], 'little')) for i in range(8, len(raw), 4))


def _descriptor(raw, *, writers=_TCB, ancestor=False, wfp=False):
    """Closed basic ACE contract. Denies never cancel a broad allow grant."""
    try:
        value = canonical_security_descriptor(raw)
    except (TypeError, ValueError) as error:
        _fail('FINITE_DESCRIPTOR_INVALID', 'unsupported bounded OWNER/GROUP/DACL bytes', cause=str(error))
    dacl = value['dacl']
    if (value['sbz1'] or not value['owner_sid_bytes'] or not value['group_sid_bytes']
            or not dacl['present'] or dacl['null'] or dacl.get('sbz1') or dacl.get('sbz2')
            or any(bytes.fromhex(dacl['tail_hex']))):
        _fail('FINITE_DESCRIPTOR_INVALID', 'explicit owner/group and non-null bounded DACL required')
    owner = _sid(bytes.fromhex(value['owner_sid_bytes']))
    if owner not in writers and not (ancestor and owner == _INSTALLER):
        _fail('FINITE_OWNER_MISMATCH', 'target owner is outside the declared finite TCB/writable-leaf subjects')
    for text in dacl['aces']:
        ace = bytes.fromhex(text)
        if len(ace) < 16 or ace[0] not in (0, 1) or ace[1] & ~0x1F:
            _fail('FINITE_DESCRIPTOR_INVALID', 'only bounded basic allow/deny ACEs are supported')
        subject = _sid(ace[8:])
        mask = int.from_bytes(ace[4:8], 'little')
        if ace[0] != 0 or subject in writers:
            continue
        if ancestor and ace[1] & 8:
            continue  # Existing pinned-ancestor rule: each opened child is rechecked.
        if wfp:
            # A configured object must be visible to the observer as well as
            # allowing its SD to be read. All management rights stay in TCB.
            allowed = WFP_OBJECT_READ
            mapped = mask
        else:
            allowed = 0x1200AF if ancestor else 0x1200A9
            mapped = mask & ~(0x80000000 | 0x20000000)
            if mask & 0x80000000: mapped |= 0x120089
            if mask & 0x20000000: mapped |= 0x1200A0
        if mapped & ~allowed:
            _fail('FINITE_DACL_UNTRUSTED_WRITE', 'an allow ACE grants mutation/unknown rights outside the finite subjects')
    return value, owner


def _hex_sd(value):
    if type(value) is not str or len(value) % 2 or not 40 <= len(value) <= 131072 or not re.fullmatch('[0-9a-f]+', value):
        _fail('FINITE_DESCRIPTOR_INVALID', 'bounded lowercase self-relative descriptor hex required')
    return bytes.fromhex(value)


@dataclass(frozen=True)
class FileTarget:
    path: str
    kind: str
    roles: tuple[str, ...]
    descriptor_hex: str
    mandatory_label: tuple[int, int, int]  # explicit RID, mask, flags, never a default ACE
    content_sha256: str | None

    def as_dict(self):
        return dict(path=self.path, kind=self.kind, roles=list(self.roles), descriptor_hex=self.descriptor_hex,
            mandatory_label=dict(zip(('rid', 'mask', 'flags'), self.mandatory_label)), content_sha256=self.content_sha256)


@dataclass(frozen=True)
class PolicyTarget:
    kind: str
    key: str
    descriptor_hex: str

    def as_dict(self):
        return dict(kind=self.kind, key=self.key, descriptor_hex=self.descriptor_hex)


@dataclass(frozen=True)
class FiniteTargets:
    files: tuple[FileTarget, ...]
    policy: tuple[PolicyTarget, ...]

    def as_dict(self):
        return dict(schema=SCHEMA, filesystem_security=FS_SECURITY, policy_security=WFP_SECURITY,
                    files=[t.as_dict() for t in self.files], policy=[t.as_dict() for t in self.policy])

    @property
    def sha256(self):
        return hashlib.sha256(_canonical(self.as_dict())).hexdigest()


def parse_contract(document, host):
    if (type(document) is not dict or set(document) != {'schema', 'filesystem_security', 'policy_security', 'files', 'policy'}
            or document['schema'] != SCHEMA or document['filesystem_security'] != FS_SECURITY
            or document['policy_security'] != WFP_SECURITY):
        _fail('FINITE_TARGET_CONTRACT_INVALID', 'exact finite target schema/security semantics required')
    files, policies = document['files'], document['policy']
    if type(files) is not list or not 1 <= len(files) <= MAX_TARGETS or type(policies) is not list or not 1 <= len(policies) <= 1024:
        _fail('FINITE_TARGET_CONTRACT_INVALID', 'finite bounded file and own-policy lists required')
    result, paths, roles = [], set(), set()
    writers = _TCB | {host.windows_isolation.service_logon_sid,
                      host.windows_isolation.service_sid, host.windows_isolation.appcontainer_sid}
    for row in files:
        if type(row) is not dict or set(row) != {'path', 'kind', 'roles', 'descriptor_hex', 'mandatory_label', 'content_sha256'}:
            _fail('FINITE_TARGET_CONTRACT_INVALID', 'target uses unknown or missing fields')
        path = _path(row['path']); rr = row['roles']; label = row['mandatory_label']
        if (path in paths or type(rr) is not list or not rr
                or any(type(r) is not str or r not in _ROLES for r in rr)
                or len(rr) != len(set(rr)) or roles.intersection(rr)
                or row['kind'] not in ('file', 'directory')
                or any((r in _DIRECTORIES) != (row['kind'] == 'directory') for r in rr)
                or bool(set(rr) & _WRITABLE) and bool(set(rr) - _WRITABLE)):
            _fail('FINITE_TARGET_CONTRACT_INVALID', 'target paths/roles/kinds must be exact, unique and consistent')
        if (type(label) is not dict or set(label) != {'rid', 'mask', 'flags'}
                or any(type(label[k]) is not int for k in label)
                or label['rid'] not in (0x1000, 0x2000, 0x3000, 0x4000)
                or not 1 <= label['mask'] <= 7 or not label['mask'] & 1
                or not 0 <= label['flags'] <= 0x1F or label['flags'] & 8):
            _fail('FINITE_LABEL_INTENT_INVALID', 'explicit bounded mandatory label with NO_WRITE_UP required')
        raw = _hex_sd(row['descriptor_hex'])
        _descriptor(raw, writers=writers if set(rr) & _LEAVES else _TCB)
        digest = row['content_sha256']
        if (row['kind'] == 'file' and (type(digest) is not str or not _SHA.fullmatch(digest))
                or row['kind'] == 'directory' and digest is not None):
            _fail('FINITE_TARGET_CONTRACT_INVALID', 'files require an explicit digest; finite RX directories have no invented tree hash')
        result.append(FileTarget(row['path'], row['kind'], tuple(rr), row['descriptor_hex'],
                                 tuple(label[k] for k in ('rid', 'mask', 'flags')), digest))
        paths.add(path); roles.update(rr)
    if roles != _ROLES:
        _fail('FINITE_TARGET_CONTRACT_INCOMPLETE', 'installation/code/config/runtime RX, original root, leaves and three images required')
    by_role = {r: t for t in result for r in t.roles}
    if _path(by_role['project_root'].path) != _path(host.root.path):
        _fail('FINITE_PROJECT_ROOT_MISMATCH', 'finite project root differs from host profile')
    for role in ('data_leaf', 'temp_leaf'):
        leaf = _path(by_role[role].path); root = _path(host.root.path)
        if not _within(leaf, root) or leaf == root:
            _fail('FINITE_TARGET_CONTRACT_INVALID', 'writable leaves must be explicit descendants of this project root')
        if any(_within(leaf, _path(t.path)) or _within(_path(t.path), leaf)
               for t in result if not set(t.roles) & _WRITABLE):
            _fail('FINITE_TARGET_CONTRACT_INVALID', 'writable leaf overlaps protected installation/code/config/runtime')
    leaves = {_path(by_role[r].path) for r in ('data_leaf', 'temp_leaf')}
    if any(_within(a, b) for a in leaves for b in leaves if a != b):
        _fail('FINITE_TARGET_CONTRACT_INVALID', 'data/temp leaves cannot contain each other')
    if any('WRITE' in grant.rights and _path(grant.path) not in leaves for grant in host.file_grants):
        _fail('FINITE_TARGET_CONTRACT_INVALID', 'host writable grants must name the exact explicit data/temp leaves')
    for image in host.launch_images:
        target = by_role.get(image.role + '_image')
        if target is None or _path(target.path) != _path(image.path) or target.content_sha256 != image.image_sha256:
            _fail('FINITE_IMAGE_INTENT_MISMATCH', 'finite image target must bind the exact existing launch image/hash')
    expected = {('Provider', guid_key(host.windows_isolation.wfp_provider_key)),
                ('SubLayer', guid_key(host.windows_isolation.wfp_sublayer_key)),
                *(('Filter', guid_key(k)) for k in host.windows_isolation.wfp_filter_keys)}
    protected = []
    for row in policies:
        if (type(row) is not dict or set(row) != {'kind', 'key', 'descriptor_hex'}
                or type(row['kind']) is not str or row['kind'] not in {'Provider', 'SubLayer', 'Filter'}):
            _fail('FINITE_POLICY_INTENT_INVALID', 'only the exact own provider/sublayer/filter protection set is supported')
        key = guid_key(row['key']); _descriptor(_hex_sd(row['descriptor_hex']), wfp=True)
        protected.append(PolicyTarget(row['kind'], key, row['descriptor_hex']))
    if len(protected) != len(expected) or {(t.kind, t.key) for t in protected} != expected:
        _fail('FINITE_POLICY_INTENT_INVALID', 'own policy protection set must exactly cover current configured keys')
    return FiniteTargets(tuple(result), tuple(protected))


def require_contract(profile):
    from ._windows_t037 import WindowsT037ProfileV2, PROFILE_SCHEMA_V4
    if type(profile) is not WindowsT037ProfileV2 or profile.wrapper_schema != PROFILE_SCHEMA_V4 or type(profile.finite_targets) is not FiniteTargets:
        _fail('FINITE_FILESYSTEM_MIC_TARGET_PRODUCER_UNAVAILABLE', 'real v4 finite target contract required; old intent is incomplete')
    parsed = parse_contract(profile.finite_targets.as_dict(), profile.host_resource_profile)
    if parsed != profile.finite_targets:
        _fail('FINITE_TARGET_CONTRACT_INVALID', 'finite target intent changed')
    return parsed


def _once_close(function):
    """Capture a new original HANDLE closer; never re-enter an unknown call."""
    entered, confirmed = False, False
    def close(handle):
        nonlocal entered, confirmed
        if confirmed:
            return True
        if entered:
            _fail('FINITE_NATIVE_RELEASE_UNKNOWN', 'possibly effective original CloseHandle cannot be repeated')
        entered = True
        result = function(handle)
        raw = getattr(result, 'value', result)
        if type(raw) is not int or raw != 1:
            _fail('FINITE_NATIVE_RELEASE_UNKNOWN', 'original HANDLE release was not confirmed')
        confirmed = True
        return True
    return close


from ._windows_parameter_publish import _API as _PathApi


_RELATIVE_CALL_SEAL=object()
_RELATIVE_CALLS=weakref.WeakValueDictionary()


class _OriginalRelativeOpen:
    """Actual original noncreating NT call/storage, not an absence DTO."""
    def __init__(self,api,function,kept,leaf,*,seal):
        if seal is not _RELATIVE_CALL_SEAL:
            _fail('FINITE_PATH_OPEN_INVALID','original noncreating call producer required')
        self.api,self.function,self.kept,self.leaf=api,function,kept,leaf
        self.output,self.io,self.attributes=kept[:3]
        self.entered=False;self.returned=False;self.raw_status=None;self.error=None;self._seal=seal
        _RELATIVE_CALLS[id(self)]=self

    def known_absent(self,api,leaf):
        parent=self.kept[-1]
        from ._windows_owned_resources import registry_for
        registry=registry_for(api)
        pin=registry.find(api,parent)
        return (self._seal is _RELATIVE_CALL_SEAL and _RELATIVE_CALLS.get(id(self)) is self
            and getattr(api,'_last_relative_open',None) is self and self.api is api and self.leaf==leaf
            and self.function is api.nt.NtCreateFile and self.entered and self.returned
            and type(self.raw_status) is int and self.raw_status==-1073741772  # STATUS_OBJECT_NAME_NOT_FOUND
            and not self.output.value and self.attributes.RootDirectory==getattr(parent, 'value', parent)
            and pin is not None and pin.registry is registry and pin.state=='ACQUIRED'
            and not any(e.state=='UNKNOWN' for e in registry.unresolved())
            and getattr(api,'pending_relative',None) is None)


class _TargetPathApi(_PathApi):
    """Existing relative path pinning, with fixed single-effect root closers."""
    def _resource_closer(self):
        return _once_close(self.k.CloseHandle)

    def rewind(self, handle):
        # Existing original readonly HANDLE; never reopen a pathname.
        self.check(self.k.SetFilePointerEx(handle, 0, None, 0))

    def open(self, path, *, directory=False, **options):
        # This inherited path walker calls open only for its ordinary drive
        # root. The raw return slot/API are retained before the SDK call.
        from ._windows_owned_resources import registry_for
        if not directory or options != {'metadata': True, 'read_control': True}:
            _fail('FINITE_PATH_OPEN_INVALID', 'only readonly root opens are supported')
        storage = C.c_void_p(); function = self.k.CreateFileW
        closer = self._resource_closer(); registry = registry_for(self)
        self.pending_root = (storage, function, closer, path)
        returned = False
        try:
            result = function(path, 0x20000, 3, None, 3, 0x2200000, None)
            storage.value = getattr(result, 'value', result)
            returned = True
        finally:
            if not returned or storage.value not in (None, 0, C.c_void_p(-1).value):
                def value(slot):
                    if not returned:
                        _fail('FINITE_NATIVE_ACQUISITION_UNKNOWN', 'original CreateFile return was lost; no handle reconstruction')
                    return slot.value
                registry.acquire(self, storage, closer, mode='BOOL', label='finite_root',
                    keepalive=(storage, function, closer, path, self), value_reader=value)
            self.pending_root = None
        if storage.value in (None, 0, C.c_void_p(-1).value):
            _fail('FINITE_PATH_OPEN_FAILED', 'original drive root CreateFile failed')
        return storage.value

    def _open_relative(self, parent_handle, leaf, **options):
        # Same established relative SDK operation/layout, narrowed to reads.
        # Retain its output slot before entering the SDK, including a lost
        # return or registration error; the outer original scope owns cleanup.
        from ._windows_parameter_publish import _UnicodeString, _ObjectAttributes, _IoStatusBlock
        from ._windows_owned_resources import registry_for
        if (set(options)-{'directory', 'read_data', 'read_control'} or type(leaf) is not str
                or not leaf or leaf in ('.', '..') or any(c in leaf for c in '\\/:\x00')):
            _fail('FINITE_PATH_OPEN_INVALID', 'finite target path walker cannot mutate a path')
        directory = options.get('directory', False); data = options.get('read_data', False)
        buffer = C.create_unicode_buffer(leaf); encoded = leaf.encode('utf-16-le')
        name = _UnicodeString(len(encoded), len(encoded)+2, C.cast(buffer, C.POINTER(C.c_wchar)))
        attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent_handle, C.pointer(name), 0x1040, None, None)
        output = C.c_void_p(); io = _IoStatusBlock()
        access = 0x1000A0 if directory else 0x100081 if data else 0x100080
        if options.get('read_control', False): access |= 0x20000
        function = self.nt.NtCreateFile; closer = self._resource_closer(); registry = registry_for(self)
        kept = (output, io, attributes, name, buffer, function, closer, parent_handle)
        self.pending_relative = kept
        call=_OriginalRelativeOpen(self,function,kept,leaf,seal=_RELATIVE_CALL_SEAL)
        self._last_relative_open=call  # Root exact output/call/buffers before SDK entry.
        entry = None
        try:
            try:
                call.entered=True
                status = function(C.byref(output), access, C.byref(attributes), C.byref(io), None,
                    0x80, 3 if directory else 1, 1, (1 if directory else 0x40)|0x20|0x200000, None, 0)
                call.raw_status=status;call.returned=True
            finally:
                if output.value:
                    entry = registry.acquire(self, output, closer, mode='BOOL', label='finite_relative',
                        keepalive=kept, value_reader=lambda slot:slot.value)
            if type(status) is not int or not -2**31 <= status < 2**31:
                _fail('FINITE_NATIVE_ACQUISITION_UNKNOWN', 'relative open NTSTATUS was not a native signed 32-bit value')
            if status < 0:
                if entry is None:
                    self.pending_relative = None  # documented failed open, no returned resource
                _fail('FINITE_PATH_OPEN_FAILED', 'original readonly relative open failed', ntstatus=status)
            if status != 0:
                _fail('FINITE_NATIVE_ACQUISITION_UNKNOWN', 'nonterminal/informational relative open status cannot supply a file fact')
            if entry is None or io.Information != 1:
                _fail('FINITE_NATIVE_ACQUISITION_UNKNOWN', 'relative open did not return FILE_OPENED and an original HANDLE')
            self.pending_relative = None
            return output
        except BaseException as error:
            call.error=error
            if entry is not None:
                entry.state, entry.error = 'UNKNOWN', error
                self.pending_relative = None  # exact output and fixed SDK are retained by that entry
            raise

    def open_security_path(self, path, *, directory, read_data=False):
        # Reuse the same original relative SDK operation/structures. Cleanup
        # belongs to the enclosing original registry scope, so a partial/close
        # UNKNOWN never causes a second unwinder to release its pinned parents.
        raw = str(path); normalized = ntpath.normpath(raw)
        if len(raw) >= 240 or normalized != raw or '/' in raw or '\x00' in raw:
            _fail('FINITE_TARGET_PATH_INVALID', 'ordinary canonical Windows path required')
        drive, tail = ntpath.splitdrive(raw)
        if not re.fullmatch('[A-Za-z]:', drive) or not tail.startswith('\\'):
            _fail('FINITE_TARGET_PATH_INVALID', 'ordinary local drive path required')
        parts = tail[1:].split('\\') if tail != '\\' else []
        if any(not p or p in ('.', '..') or p.endswith((' ', '.')) or ':' in p for p in parts):
            _fail('FINITE_TARGET_PATH_INVALID', 'ordinary bounded path components required')
        handles = [self.open(drive + '\\', directory=True, metadata=True, read_control=True)]
        self.info(handles[0], directory=True)
        for index, leaf in enumerate(parts):
            is_directory = index < len(parts) - 1 or directory
            handle = self._open_relative(handles[-1], leaf, directory=is_directory,
                read_control=True, read_data=read_data and not is_directory)
            handles.append(handle)
            self.info(handle, directory=is_directory)
        return handles


def _loader_api():
    api = _TargetPathApi()
    _LOADER_APIS[id(api)] = api
    return api


def _finish_loader_api(api):
    from ._windows_owned_resources import registry_for
    registry = registry_for(api)
    if _LOADER_APIS.get(id(api)) is not api:
        _fail('FINITE_PROFILE_SOURCE_ORIGIN_REQUIRED', 'original loader API is not retained')
    if getattr(api, 'pending_root', None) or getattr(api, 'pending_relative', None):
        _fail('FINITE_NATIVE_ACQUISITION_UNKNOWN', 'original loader API/output/parent storage retained; no retry')
    _release_scope(registry, None)
    if not registry.unresolved():
        _LOADER_APIS.pop(id(api))


def _release_scope(registry, scope):
    entries = [e for e in registry.unresolved() if scope is None or e.seq > scope.seq]
    if any(e.state == 'UNKNOWN' for e in entries):
        _fail('FINITE_NATIVE_RESOURCE_UNKNOWN', 'original readonly resources retained; no automatic release retry')
    # Stop at the first ambiguous release, preserving its original parent pins.
    for entry in reversed(entries):
        entry.release_owned(registry.owner)
    if any(scope is None or e.seq > scope.seq for e in registry.unresolved()):
        _fail('FINITE_NATIVE_RESOURCE_UNKNOWN', 'native release bookkeeping did not retire its original entry')


class _SdReturn:
    """One real SDK output allocation, not an owner/admission capability."""
    def __init__(self, function, args, query, closer, family):
        self.storage = C.c_void_p()
        self.function, self.args, self.query = function, args, query
        self.closer, self.family = closer, family
        self.value = None; self.readable = False; self.entered = False; self.confirmed = False
        self.status = None; self.getter_succeeded = False; self.error = None

    def capture(self, storage):
        if storage is not self.storage:
            _fail('FINITE_NATIVE_ACQUISITION_UNKNOWN', 'only original descriptor output storage may be captured')
        self.value = storage.value
        return self.value

    def release(self, pointer):
        if self.confirmed:
            return None
        if not self.getter_succeeded:
            if self.error is not None:
                raise self.error
            _fail('FINITE_NATIVE_RELEASE_UNKNOWN', 'original getter did not confirm allocation ownership')
        if self.entered or not self.readable or pointer != self.value or self.storage.value != pointer:
            _fail('FINITE_NATIVE_RELEASE_UNKNOWN', 'unvalidated/possibly freed descriptor is retained without retry')
        self.entered = True
        if self.family == 'wfp':
            local = C.c_void_p(pointer)
            result = self.closer(C.byref(local))
            if result is not None:
                _fail('FINITE_NATIVE_RELEASE_UNKNOWN', 'FwpmFreeMemory0 return is not VOID')
        else:
            result = self.closer(pointer)
            if result is not None and (type(result) is not int or result != 0):
                _fail('FINITE_NATIVE_RELEASE_UNKNOWN', 'LocalFree release is not confirmed')
        self.confirmed = True


def _sd_bytes(api, pointer):
    """Bound every referenced component before the SDK length helper reads it."""
    from ._windows_t037_mic import NativeFileMicApi
    readable = lambda address, size: NativeFileMicApi._readable(api, address, size)
    readable(pointer, 20)
    header = bytes(C.string_at(pointer, 20))
    revision, reserved, control, owner, group, sacl, dacl = struct.unpack('<BBHIIII', header)
    if revision != 1 or reserved or not control & 0x8000 or sacl:
        _fail('FINITE_DESCRIPTOR_INVALID', 'only self-relative OWNER/GROUP/DACL SD is requested')
    extent = 20
    for offset in (owner, group):
        if not offset:
            continue
        if offset < 20 or offset % 4 or offset > 65536 - 68:
            _fail('FINITE_DESCRIPTOR_INVALID', 'SID offset exceeds bounded descriptor')
        readable(pointer + offset, 8)
        prefix = bytes(C.string_at(pointer + offset, 8))
        if prefix[0] != 1 or prefix[1] > 15:
            _fail('FINITE_DESCRIPTOR_INVALID', 'invalid SID extent')
        extent = max(extent, offset + 8 + prefix[1] * 4)
    if dacl:
        if dacl < 20 or dacl % 4 or dacl > 65536 - 8:
            _fail('FINITE_DESCRIPTOR_INVALID', 'DACL offset exceeds bounded descriptor')
        readable(pointer + dacl, 8)
        size = struct.unpack_from('<H', C.string_at(pointer + dacl, 8), 2)[0]
        if size < 8 or dacl + size > 65536:
            _fail('FINITE_DESCRIPTOR_INVALID', 'DACL extent exceeds bounded descriptor')
        extent = max(extent, dacl + size)
    readable(pointer, extent)
    raw = bytes(C.string_at(pointer, extent))
    canonical_security_descriptor(raw)
    if api.a.GetSecurityDescriptorLength(pointer) != extent or bytes(C.string_at(pointer, extent)) != raw:
        _fail('FINITE_DESCRIPTOR_CHANGED', 'SDK length or original descriptor bytes changed')
    return raw


def _read_sd(adapter, query, function, args, *, closer, family):
    from ._windows_owned_resources import registry_for
    registry = registry_for(adapter)
    if getattr(adapter, '_finite_pending_sd', None) is not None:
        _fail('FINITE_NATIVE_RESOURCE_UNKNOWN', 'original descriptor output is pending; no replacement query')
    for original in registry.unresolved():
        if original.adapter is adapter and original.domain == 'finite_sd':
            if original.error is not None:
                raise original.error
            _fail('FINITE_NATIVE_RESOURCE_UNKNOWN', 'original descriptor is unresolved; no replacement query')
    holder = _SdReturn(function, args, query, closer, family)
    adapter._finite_pending_sd = holder  # original output retained before SDK writes it
    entry = None
    try:
        try:
            holder.status = function(*args, C.byref(holder.storage))
            # Bound DWORD exports return an exact int. A readable output,
            # bool, wrapper or lost return cannot confirm ownership transfer.
            holder.getter_succeeded = type(holder.status) is int and holder.status == 0
        except BaseException as error:
            holder.error = error  # preserve a lost return even if output capture also fails
            raise
        finally:
            if holder.storage.value:
                entry = registry.acquire(adapter, holder.storage, holder.release, mode='VOID',
                    label='finite_' + family + '_descriptor', domain='finite_sd',
                    keepalive=(holder, holder.storage, function, args, closer, adapter, query), value_reader=holder.capture)
                adapter._finite_pending_sd = None
        if not holder.getter_succeeded or entry is None:
            _fail('FINITE_SECURITY_GETTER_FAILED', 'readonly SDK descriptor getter failed or returned no SD',
                  native_status=holder.status if type(holder.status) is int else None)
        # Validate the base even for a later malformed descriptor. Invalid
        # pointers retain the original allocation rather than inventing a free.
        from ._windows_t037_mic import NativeFileMicApi
        NativeFileMicApi._readable(query, entry.value, 20)
        holder.readable = True
        raw = _sd_bytes(query, entry.value)
        if holder.storage.value != entry.value:
            _fail('FINITE_DESCRIPTOR_CHANGED', 'original SDK output pointer changed')
        return raw
    except BaseException as error:
        if holder.error is None:
            holder.error = error
        raise
    finally:
        if entry is not None:
            if not holder.getter_succeeded or not holder.readable:
                # No query/free/retry can establish a transfer the original
                # call did not confirm. Keep its exact storage and bindings.
                entry.state, entry.error = 'UNKNOWN', holder.error
            else:
                entry.release_owned(registry.owner)
        elif not holder.storage.value:
            adapter._finite_pending_sd = None


class NativeTargetReader:
    """One scoped readonly reader attached to the existing registry owner."""
    def __init__(self, owner=None):
        from ._windows_owned_resources import registry_for, attach_registry
        from ._windows_t037_mic import NativeFileMicApi
        self.owner = self if owner is None else owner
        self.registry = registry_for(self.owner); self.scope = self.registry.scope()
        self.api = _TargetPathApi(); attach_registry(self.api, self.registry)
        self.mic = NativeFileMicApi(self.owner)
        self.pins = []
        self.contents = []
        self.source_chains = []
        self.retired = False
        _READS[id(self)] = self
        if owner is not None:
            owner._prebirth_observers.append(self)

    def _security(self, handle, information=7):
        return _read_sd(self.mic, self.mic, self.mic._get_security,
            (handle, 1, information, None, None, None, None), closer=self.mic._local_free, family='local')

    def _source_chain(self, chain, subject_sid):
        """Match the ORIGINAL private-path helper's composite, not a leaf SD."""
        result = []
        for index, handle in enumerate(chain):
            identity, _ = self.api.info(handle, directory=index < len(chain)-1)
            raw = self._security(handle, 5)
            parsed = canonical_security_descriptor(raw)
            if not parsed['owner_sid_bytes']:
                _fail('FINITE_PROFILE_SOURCE_CHANGED', 'actual path-chain owner is missing')
            actual_owner = _sid(bytes.fromhex(parsed['owner_sid_bytes']))
            result.append(dict(identity=dict(device=identity[0], inode=identity[1]),
                descriptor_sha256=hashlib.sha256(raw).hexdigest(),
                owner='daemon' if actual_owner == subject_sid else 'trusted-host-admin'))
        return result

    def read(self, path, *, directory, content=False, legacy_source=False, source_subject_sid=None):
        from ._windows_t037_mic import BorrowedFileHandle, observe_file_mandatory_label
        if (self.retired or _READS.get(id(self)) is not self
                or threading.get_ident() != self.scope.thread_id):
            _fail('FINITE_READER_LIFETIME_MISMATCH', 'only the original active reader/thread can acquire a target')
        _path(path)
        chain = self.api.open_security_path(path, directory=directory, read_data=content)
        leaf = chain[-1]
        for handle in chain[:-1]:
            _descriptor(self._security(handle), ancestor=True)
        before = self.api.full_fingerprint(leaf, directory=directory)
        entry = self.registry.find(self.api, leaf)
        if entry is None:
            _fail('FINITE_HANDLE_OWNER_MISSING', 'original pinned file entry required')
        mic = observe_file_mandatory_label(BorrowedFileHandle(self.owner, self.api, entry), api=self.mic)
        descriptor = self._security(leaf)
        legacy_sd = self._security(leaf, 5) if legacy_source else None
        source_chain = self._source_chain(chain, source_subject_sid) if legacy_source else None
        digest = None
        if content:
            limit = 65536 if legacy_source else MAX_CONTENT_BYTES
            if before[1] < 1 or before[1] > limit or before[-1] != 1:
                _fail('FINITE_CONTENT_BOUNDS', 'ordinary single-link content exceeds the finite byte bound')
            digest = hashlib.sha256(); remaining = before[1]
            while remaining:
                block = self.api.read_chunk(leaf, min(65536, remaining))
                if type(block) is not bytes or not block or len(block) > remaining:
                    _fail('FINITE_CONTENT_CHANGED', 'bounded pinned file read was truncated or oversized')
                digest.update(block); remaining -= len(block)
            if self.api.read_chunk(leaf, 1):
                _fail('FINITE_CONTENT_CHANGED', 'pinned file grew during the bounded read')
            digest = digest.hexdigest()
        after_sd = self._security(leaf)
        after = self.api.full_fingerprint(leaf, directory=directory)
        if (before != after or descriptor != after_sd or before[-1] != 1
                or legacy_source and (self._security(leaf, 5) != legacy_sd
                    or self._source_chain(chain, source_subject_sid) != source_chain)):
            _fail('FINITE_TARGET_CHANGED', 'pinned file identity/links/extent/times/security changed')
        self.pins.append((leaf, directory, before, descriptor, mic.before_label, legacy_sd))
        if content:
            self.contents.append((leaf, before[1], digest))
        if source_chain is not None:
            self.source_chains.append((chain, source_subject_sid, source_chain))
        return dict(fingerprint=before, descriptor=descriptor, label=mic.before_label,
                    legacy_identity=(mic.before_file.volume_serial, mic.before_file.file_id), content_sha256=digest,
                    legacy_security_sha256=hashlib.sha256(legacy_sd).hexdigest() if legacy_sd is not None else None,
                    source_chain_sha256=hashlib.sha256(_canonical(source_chain)).hexdigest() if source_chain is not None else None)

    def check_current(self):
        from ._windows_t037_mic import BorrowedFileHandle, observe_file_mandatory_label
        if self.retired:
            _fail('FINITE_READER_LIFETIME_MISMATCH', 'retired original pins cannot supply a new observation')
        if _READS.get(id(self)) is not self or threading.get_ident() != self.scope.thread_id:
            _fail('FINITE_READER_LIFETIME_MISMATCH', 'only original reader/thread can check its pinned targets')
        if (getattr(self.api, 'pending_root', None) or getattr(self.api, 'pending_relative', None)
                or getattr(self.mic, '_finite_pending_sd', None) is not None or self.mic.pending is not None):
            _fail('FINITE_NATIVE_ACQUISITION_UNKNOWN', 'original SDK output storage retained; no new read or release')
        if any(e.state == 'UNKNOWN' for e in self.registry.unresolved()):
            _fail('FINITE_NATIVE_RESOURCE_UNKNOWN', 'original readonly resources remain unknown; no cleanup retry')
        for handle, directory, before, descriptor, label, legacy_sd in self.pins:
            entry = self.registry.find(self.api, handle)
            if entry is None or entry.state != 'ACQUIRED':
                _fail('FINITE_HANDLE_OWNER_MISSING', 'original acquired target entry is no longer held')
            fresh = observe_file_mandatory_label(BorrowedFileHandle(self.owner, self.api, entry), api=self.mic)
            if (self.api.full_fingerprint(handle, directory=directory) != before
                    or self._security(handle) != descriptor or fresh.before_label != label
                    or legacy_sd is not None and self._security(handle, 5) != legacy_sd):
                _fail('FINITE_TARGET_CHANGED', 'original retained target identity/security/MIC changed')
            expected = next((row for row in self.contents if row[0] is handle), None)
            if expected is not None:
                _, size, digest = expected
                self.api.rewind(handle)
                hasher = hashlib.sha256(); remaining = size
                while remaining:
                    block = self.api.read_chunk(handle, min(65536, remaining))
                    if type(block) is not bytes or not block or len(block) > remaining:
                        _fail('FINITE_CONTENT_CHANGED', 'original pinned content was truncated or oversized')
                    hasher.update(block); remaining -= len(block)
                if (self.api.read_chunk(handle, 1) or hasher.hexdigest() != digest
                        or self.api.full_fingerprint(handle, directory=directory) != before
                        or self._security(handle) != descriptor):
                    _fail('FINITE_CONTENT_CHANGED', 'original pinned content/security changed without reopening')
        for chain, subject, expected in self.source_chains:
            if self._source_chain(chain, subject) != expected:
                _fail('FINITE_PROFILE_SOURCE_CHANGED', 'original pinned source path-chain identity/security changed')

    def finish(self):
        if (threading.get_ident() != self.scope.thread_id
                or not self.retired and _READS.get(id(self)) is not self):
            _fail('FINITE_READER_LIFETIME_MISMATCH', 'only original reader/thread can finish its pinned targets')
        if self.retired:
            return  # Cached known release, never observe former/reused HANDLEs.
        if getattr(self, '_finish_error', None) is not None:
            raise self._finish_error  # UNKNOWN release is not automatically entered again.
        try:
            try:
                self.check_current()
            finally:
                pending = (getattr(self.api, 'pending_root', None) or getattr(self.api, 'pending_relative', None)
                           or getattr(self.mic, '_finite_pending_sd', None) is not None or self.mic.pending is not None)
                if not pending and not any(e.state == 'UNKNOWN' for e in self.registry.unresolved()):
                    _release_scope(self.registry, self.scope)
                if not pending and not any(e.seq > self.scope.seq for e in self.registry.unresolved()):
                    _READS.pop(id(self), None)
                    self.retired = True
        except BaseException as error:
            self._finish_error = error
            raise


def _label_matches(label, expected):
    ace = label.ace
    return ace is not None and (ace.rid, ace.mask, ace.flags) == expected


def _has_only_original_pins(owner, registry):
    """Permit nesting a readonly SCM query inside this original pin scope."""
    readers = [r for r in _READS.values() if r.owner is owner and r.registry is registry]
    return (len(readers) == 1 and type(readers[0]) is NativeTargetReader
            and bool(readers[0].pins) and all(e.state == 'ACQUIRED' and e.adapter is readers[0].api
                and e.seq > readers[0].scope.seq and e.thread_id == readers[0].scope.thread_id
                for e in registry.unresolved()))


def _source_fact(reader, profile, path):
    fact = reader.read(path, directory=False, content=True, legacy_source=True,
                       source_subject_sid=profile.service_account_sid)
    _descriptor(fact['descriptor'])  # source config must be protected by the declared installer TCB
    ace = fact['label'].ace
    if ace is None or ace.rid not in (0x2000, 0x3000, 0x4000) or not ace.mask & 1 or ace.flags & 8:
        _fail('FINITE_PROFILE_SOURCE_LABEL_MISMATCH', 'protected source requires a real explicit Medium-or-higher NO_WRITE_UP label')
    if (fact['content_sha256'] != profile.source_sha256
            or fact['legacy_identity'] != (profile.source_identity.get('device'), profile.source_identity.get('inode'))
            or fact['source_chain_sha256'] != profile.source_identity.get('security_sha256')):
        _fail('FINITE_PROFILE_SOURCE_CHANGED', 'actual source identity/bytes differ from the trusted loader read')
    return fact


def _retain_loaded_origin(profile, path):
    """Only load_trusted_profile calls this after its original ACL/host check.

    Do not accept a caller-supplied origin/observation. Perform the new native
    read here and retain only this exact profile object's actual source facts.
    """
    require_contract(profile); path = ntpath.normpath(path); _path(path)
    reader = NativeTargetReader()
    try:
        fact = _source_fact(reader, profile, path)
    finally:
        reader.finish()
    identity = id(profile)
    ref = weakref.ref(profile, lambda original: _ORIGINS.pop(identity, None)
                      if _ORIGINS.get(identity, (None,))[0] is original else None)
    _ORIGINS[identity] = (ref, path, profile.sha256, fact)


def _project_binding(profile, factory, daemon):
    from ._project_authority import ProjectAuthority
    binding = getattr(factory, '_scm_binding', None)
    authority = daemon.project_authority
    if (type(authority) is not ProjectAuthority or type(binding) is not tuple or len(binding) != 4
            or binding[1] is not daemon or binding[3] is not factory
            or binding[2] != (profile.host_resource_profile.project_id, factory.session_id)
            or daemon._session_t037_process_factories.get(binding[2]) is not factory
            or binding[0][4] is not authority):
        _fail('FINITE_PROJECT_BINDING_MISMATCH', 'original registered project/session/daemon/authority must match the profile')
    record = authority.get_project(binding[2][0])
    if (record.get('project_id') != binding[2][0] or _path(record.get('workspace')) != _path(profile.workspace_root)):
        _fail('FINITE_PROJECT_ROOT_MISMATCH', 'actual registered project workspace differs from the startup profile root')
    return record


class _HeldFiniteA:
    """Original retained file acquisitions; neither diagnostic data nor B/Resume authority."""
    def __init__(self, profile, factory, daemon, reader, record, origin, *, seal):
        if seal is not _HELD_SEAL:
            _fail('FINITE_A_ORIGINAL_REQUIRED', 'original same-thread target acquisition required')
        self._seal=seal;self.profile=profile;self.factory=factory;self.daemon=daemon
        self.reader=reader;self.record=record;self.origin=origin;self.digest=profile.sha256
        self.binding=factory._scm_binding;self.state='ACQUIRED';self.observations=[]
        self.primary_error=None;self.cleanup_error=None
        self._deferred_binding=None;self._deferred_finish_entered=False

    def _retain_failed_child(self, error):
        """Retain the actual failed dependency and original pin entries, not a DTO."""
        from ._windows_t037 import NativeSuspendedChildLaunchError, _RetainedSuspendedChild
        if type(error) is not NativeSuspendedChildLaunchError:
            return
        child=error._process;factory=self.factory;reader=self.reader
        if (type(child) is not _RetainedSuspendedChild or child._original_factory is not factory
                or child._api is not factory._api or child._original_profile is not self.profile
                or child._original_job is not factory._job or factory._job is None):
            return
        entries=tuple((e,e.adapter,e.handle,e.value,e.closer,e.mode,e.label,e.seq,e.domain,
                       e.thread_id,e.keepalive) for e in reader.registry.unresolved())
        self._deferred_binding=(error,child,factory._api,factory._job,reader,reader.scope,
            reader.scope.seq,reader.scope.thread_id,reader.api,reader.mic,entries)

    def _finish_after_original_child_exit(self, factory):
        """First finish only, after this original never-resumed dependency retired.

        All admission below is pure retained-object validation. The only SDK
        entry is the original reader.finish after every condition passes. An
        interrupted finish is sticky; it never authorizes a second close.
        """
        from ._windows_t037 import (T037SessionProcessFactory, NativeSuspendedChildLaunchError,
            _RetainedSuspendedChild, _NATIVE_SUSPENDED_CHILD_SEAL)
        from ._java_worker import PersistentJavaWorker
        from ._windows_owned_resources import registry_for
        from ._windows_job_limits import _original_job_close_call
        from ._windows_t037_primary import require_no_preparation
        binding=self._deferred_binding;reader=self.reader
        if self._deferred_finish_entered:
            if self.cleanup_error is not None:
                raise self.cleanup_error  # Same first failure, never enter finish or a closer again.
            _fail('FINITE_A_FINISH_UNKNOWN', 'original deferred finish was already entered')
        if (type(binding) is not tuple or len(binding)!=11
                or self._seal is not _HELD_SEAL or type(factory) is not T037SessionProcessFactory
                or factory is not self.factory or _HELD.get(id(factory)) is not self
                or getattr(factory,'_held_static_a',None) is not self or self.state!='UNKNOWN'
                or self._deferred_finish_entered or self.cleanup_error is not None
                or type(reader) is not NativeTargetReader or reader.retired
                or _READS.get(id(reader)) is not reader or reader.owner is not factory
                or reader._held_a_result is not self or getattr(reader,'_finish_error',None) is not None
                or factory._scm_binding is not self.binding or factory.profile is not self.profile
                or self.profile.sha256!=self.digest or _ORIGINS.get(id(self.profile)) is not self.origin
                or self.origin[0]() is not self.profile
                or self.daemon._windows_t037_profile is not self.profile
                or _path(self.daemon._windows_t037_profile_path)!=_path(self.origin[1])
                or self.binding[1] is not self.daemon or self.binding[3] is not factory
                or self.daemon._session_t037_process_factories.get(self.binding[2]) is not factory):
            _fail('FINITE_A_DEFERRED_RETIREMENT_UNVERIFIED', 'original failed A/reader/source lifetime is not eligible')
        error,child,api,job,original_reader,scope,seq,thread,path_api,mic,entries=binding
        if (self.primary_error is not error or type(error) is not NativeSuspendedChildLaunchError
                or error._seal is not _NATIVE_SUSPENDED_CHILD_SEAL or error._process is not child
                or type(child) is not _RetainedSuspendedChild or child._api is not api
                or api is not factory._api or factory._job is not job or factory._closed
                or child._original_factory is not factory or child._original_profile is not self.profile
                or child._original_job is not job or original_reader is not reader
                or reader.scope is not scope or scope.seq!=seq or scope.thread_id!=thread
                or thread!=threading.get_ident() or reader.registry is not registry_for(factory)
                or scope.registry is not reader.registry or reader.api is not path_api or reader.mic is not mic
                or registry_for(path_api) is not reader.registry or registry_for(mic) is not reader.registry
                or not child._closed or not child._creation_confirmed or type(child.returncode) is not int
                or child._resume_attempted or child._resume_confirmed or child._returned
                or child._retirement_error is not None or child._release_request is not None
                or child._release_attempts!={'process','thread'}
                or child._process_entry is None or child._process_entry.state!='RELEASED'
                or child._thread_entry is None or child._thread_entry.state!='RELEASED'):
            _fail('FINITE_A_DEPENDENCY_NOT_TERMINAL', 'original never-resumed child/storage/entry retirement is not confirmed')
        owner=child._owner
        if (type(owner) is not PersistentJavaWorker or owner._original_process_factory is not factory
                or owner._process_factory is not factory or owner._process is not None
                or owner._fixed_process is not None or owner._fixed_launch_owner is not None
                or owner._startup_unknown is not None or owner._retirement_unknown is not None):
            _fail('FINITE_A_DEPENDENCY_OWNER_CHANGED', 'original Worker has not completed its own child retirement')
        attempt=getattr(child,'_resume_attempt',None)
        if attempt is not None and getattr(attempt,'state',None)!='NO_EFFECT':
            _fail('FINITE_A_DEPENDENCY_NOT_TERMINAL','active/UNKNOWN Resume cannot retire original reader pins')
        if attempt is None and child._resume_epoch!=0:
            _fail('FINITE_A_DEPENDENCY_NOT_TERMINAL','original no-attempt branch requires its unchanged epoch0')
        child._check_original()  # Storage/closer/entry checks only; never query released HANDLEs.
        require_no_preparation(factory);require_no_preparation(api)
        if any(getattr(api,name,None) for name in ('_retained_children','_retained_launch_cleanup',
                '_unclassified_create_handles','_retained_parent_bindings','_retained_child_tokens',
                '_birth_preparations')):
            _fail('FINITE_A_ANCILLARY_UNKNOWN', 'original launch still retains ancillary ownership')
        for original_api in (path_api,mic,api):
            if any(getattr(original_api,name,None) is not None for name in
                    ('pending_root','pending_relative','_finite_pending_sd','pending')):
                _fail('FINITE_A_ANCILLARY_UNKNOWN', 'original native output storage remains pending')
        call=_original_job_close_call(api,job,expected=getattr(factory,'_job_close_call',None))
        if (call.phase!='PRE_SDK' or call.entry.state!='ACQUIRED' or call.entry.error is not None
                or registry_for(api).unresolved()!=(call.entry,)):
            _fail('FINITE_A_ANCILLARY_UNKNOWN', 'only the original untouched acquired Job may remain')
        current=reader.registry.unresolved()
        if not entries or len(current)!=len(entries) or any(current[i] is not row[0] for i,row in enumerate(entries)):
            _fail('FINITE_A_PIN_SCOPE_CHANGED', 'original complete pinned resource range changed')
        for e,adapter,handle,value,closer,mode,label,number,domain,tid,kept in entries:
            if (e.registry is not reader.registry or e.adapter is not adapter or adapter is not path_api
                    or e.handle is not handle or e.value!=value or e.closer is not closer
                    or e.mode!=mode or mode!='BOOL' or e.label!=label
                    or label not in {'finite_root','finite_relative'} or e.seq!=number or number<=seq
                    or e.domain!=domain or domain!='native' or e.thread_id!=tid or tid!=thread
                    or e.keepalive is not kept or not kept or kept[0].value!=value
                    or e.state!='ACQUIRED' or e.error is not None):
                _fail('FINITE_A_PIN_SCOPE_CHANGED', 'original pin entry/output/closer binding is not wholly acquired')
        if attempt is not None:
            # Only this original known-closed parent/child/context may settle
            # the not-yet-closed diagnostic. Never clear a B UNKNOWN flag.
            from ._windows_t037_b_observation import _OriginalBObserver, settle_original_child_b
            observer=getattr(child,'_connected_b_observer',None)
            if observer is not None:
                parent=child._parent_binding
                if (type(observer) is not _OriginalBObserver or observer is not attempt.connected_b
                        or observer.process is not child or observer.factory is not factory
                        or observer.launch_api is not api or observer.profile is not self.profile
                        or observer.parent is not parent or not parent.closed
                        or observer.registry is not registry_for(api)
                        or observer.parent_context!=(parent.launch_api,parent.query,parent.pid,
                            getattr(parent.handle,'value',parent.handle),parent.birth,
                            getattr(parent,'_resource_owner',None))):
                    _fail('FINITE_A_DEPENDENCY_OWNER_CHANGED','original B/parent context changed before known settlement')
                settle_original_child_b(child,observer.launch_error)
            from ._windows_t037_resume import original_no_effect_attempt_witness
            if not original_no_effect_attempt_witness(child,retired_worker=owner):
                _fail('FINITE_A_DEPENDENCY_NOT_TERMINAL','spent original NO_EFFECT/retiredWorker witness is unavailable')
        self._deferred_finish_entered=True
        try:
            reader.finish()  # Its first invocation; state remains UNKNOWN until confirmed release.
            if (not reader.retired or _READS.get(id(reader)) is not None
                    or reader.registry.unresolved() or any(row[0].state!='RELEASED' for row in entries)):
                _fail('FINITE_A_FINISH_UNKNOWN', 'original reader finish did not confirm every pin retirement')
        except BaseException as error:
            self.cleanup_error=error
            raise
        self.state='RELEASED';_HELD.pop(id(factory));factory._held_static_a=None

    def check_current(self):
        if (self._seal is not _HELD_SEAL or _HELD.get(id(self.factory)) is not self
                or getattr(self.factory, '_held_static_a', None) is not self or self.state != 'ACQUIRED'
                or type(self.reader) is not NativeTargetReader or self.reader.owner is not self.factory
                or self.reader.retired or self.reader.scope.thread_id != threading.get_ident()
                or self.factory._scm_binding is not self.binding or self.factory.profile is not self.profile
                or self.profile.sha256 != self.digest or _ORIGINS.get(id(self.profile)) is not self.origin
                or _path(self.daemon._windows_t037_profile_path) != _path(self.origin[1])
                or _project_binding(self.profile, self.factory, self.daemon) != self.record):
            _fail('FINITE_A_LIFETIME_CHANGED', 'original retained A/profile/project/source/thread binding changed')
        self.reader.check_current()


def _require_held_finite_a(factory):
    from ._windows_appcontainer_gate import _check_factory_activity
    _check_factory_activity(factory)  # Shutdown/SCM stop only permits existing never-resumed cleanup.
    held=_HELD.get(id(factory))
    if type(held) is not _HeldFiniteA:
        _fail('FINITE_A_ORIGINAL_REQUIRED', 'normal V4 launch requires its original held finite A')
    held.check_current()
    return held


@contextmanager
def _held_finite_target_scope(profile, factory, daemon):
    """Internal same-thread pin lifetime, kept through the synchronous factory/cleanup."""
    contract = require_contract(profile)
    from ._windows_owned_resources import registry_for
    if (id(factory) in _HELD or getattr(factory, '_held_static_a', None) is not None
            or any(r.owner is factory for r in _READS.values()) or registry_for(factory).unresolved()):
        _fail('STATIC_NATIVE_OWNER_UNRESOLVED', 'original finite static resources are unresolved; no reacquisition')
    record = _project_binding(profile, factory, daemon)
    retained = _ORIGINS.get(id(profile))
    if (retained is None or retained[0]() is not profile or retained[2] != profile.sha256
            or _path(daemon._windows_t037_profile_path) != _path(retained[1])):
        _fail('FINITE_PROFILE_SOURCE_ORIGIN_REQUIRED', 'only the actual loader-retained source for this original profile is accepted')
    reader = NativeTargetReader(factory)
    held=_HeldFiniteA(profile,factory,daemon,reader,record,retained,seal=_HELD_SEAL)
    reader._held_a_result=held
    _HELD[id(factory)]=held;factory._held_static_a=held
    try:
        source = _source_fact(reader, profile, retained[1])
        if source != retained[3]:
            _fail('FINITE_PROFILE_SOURCE_CHANGED', 'live original profile source identity/content/security/label drifted')
        writers = _TCB | {profile.service_account_sid, profile.service_sid, profile.appcontainer_sid}
        observations = []
        for target in contract.files:
            fact = reader.read(target.path, directory=target.kind == 'directory', content=target.kind == 'file')
            _, owner = _descriptor(fact['descriptor'], writers=writers if set(target.roles) & _LEAVES else _TCB)
            if (fact['descriptor'] != bytes.fromhex(target.descriptor_hex)
                    or not _label_matches(fact['label'], target.mandatory_label)
                    or fact['content_sha256'] != target.content_sha256):
                _fail('FINITE_TARGET_PROTECTION_MISMATCH', 'current target descriptor/MIC/content differs from explicit intent', roles=list(target.roles))
            if 'project_root' in target.roles:
                root = profile.host_resource_profile.root
                volume, file_id = fact['fingerprint'][0]
                if (str(volume) != root.volume_id or str(file_id) != root.file_id or owner != root.owner_id):
                    _fail('FINITE_PROJECT_ROOT_IDENTITY_MISMATCH', 'actual pinned root volume/full FILE_ID_128/owner differs from startup root')
            observations.append({'roles': list(target.roles), 'fingerprint': fact['fingerprint'],
                                 'raw_security_sha256': hashlib.sha256(fact['descriptor']).hexdigest()})
        if _project_binding(profile, factory, daemon) != record:
            _fail('FINITE_PROJECT_BINDING_CHANGED', 'registered project record changed during the static read')
        held.observations=observations
        yield held
        if _project_binding(profile, factory, daemon) != record:
            _fail('FINITE_PROJECT_BINDING_CHANGED', 'registered project binding changed before completing static A')
    except BaseException as error:
        held.primary_error=error
        held._retain_failed_child(error)
        # A child/ancillary cleanup UNKNOWN must keep its original A pins too.
        from ._windows_t037 import NativeSuspendedChildLaunchError
        api=factory._api
        unsettled=(api is not None and (any(e.state == 'UNKNOWN' for e in registry_for(api).unresolved())
            or bool(getattr(api, '_retained_launch_cleanup', ()))
            or bool(getattr(api, '_unclassified_create_handles', ()))))
        if isinstance(error, NativeSuspendedChildLaunchError) or unsettled:
            held.state='UNKNOWN'
        raise
    finally:
        if held.state != 'UNKNOWN':
            try:reader.finish()
            except BaseException as error:
                held.cleanup_error=error
                if not reader.retired:held.state='UNKNOWN'
                if held.primary_error is None or not reader.retired:
                    # Preserve primary+cleanup on the original held scope, but
                    # unresolved resources must retain the old outward UNKNOWN
                    # refusal rather than publish a known getter/body failure.
                    raise error from held.primary_error
            finally:
                if reader.retired:
                    held.state='RELEASED';_HELD.pop(id(factory),None)
                    factory._held_static_a=None
                else:held.state='UNKNOWN'
        reader._held_a_result=held  # Original observer retains primary and cleanup failures.


def _finish_deferred_finite_a(factory):
    held=_HELD.get(id(factory))
    if held is None and getattr(factory,'_held_static_a',None) is None:
        return
    if type(held) is not _HeldFiniteA:
        _fail('FINITE_A_ORIGINAL_REQUIRED', 'only the original retained A may retire its pins')
    _HeldFiniteA._finish_after_original_child_exit(held,factory)


@contextmanager
def finite_target_scope(profile, factory, daemon):
    """Standalone diagnostic comparison retains no birth or Resume authority."""
    with _held_finite_target_scope(profile,factory,daemon) as held:
        yield {'scope': 'FINITE_STATIC_COMPARISON_ONLY_NOT_RESUME', 'targets': held.observations}


from ._windows_t037_native import CtypesWindowsPolicyApi


class FinitePolicyApi(CtypesWindowsPolicyApi):
    """Existing five-catalog producer plus a bounded captured SD getter."""
    def __init__(self, profile):
        super().__init__()
        from ._windows_t037_mic import _MemoryInfo
        self._kernel32.VirtualQuery.argtypes = [C.c_void_p, C.POINTER(_MemoryInfo), C.c_size_t]
        self._kernel32.VirtualQuery.restype = C.c_size_t
        self.k, self.a = self._kernel32, self._advapi
        self._finite_profile = profile
        self._finite_expected_keys = {(t.kind, t.key) for t in require_contract(profile).policy}
        self._finite_security_reads = {}

    def _wfp_security_sha(self, engine, kind, key):
        identity = (kind, guid_key(self._guid(key)))
        if identity not in self._finite_expected_keys:
            # Keep the enumerated identity/consumer/semantic row intact. None
            # means SD NOT_OBSERVED, never an invented protection digest.
            return None
        function = getattr(self._fwpu, 'Fwpm' + kind + 'GetSecurityInfoByKey0')
        raw = _read_sd(self, self, function, (engine, C.byref(key), 7, None, None, None, None),
                       closer=self._fwpu.FwpmFreeMemory0, family='wfp')
        normalized = canonical_security_descriptor_sha256(raw)
        previous = self._finite_security_reads.setdefault(identity, normalized)
        if previous != normalized:
            _fail('FINITE_OWNED_POLICY_SECURITY_CHANGED', 'same configured policy object security changed during observation')
        return normalized


def require_owned_policy(profile, snapshot, api):
    """Compare only the configured namespace; global diagnostics stay unverified."""
    contract = require_contract(profile)
    if type(api) is not FinitePolicyApi or api._finite_profile is not profile:
        _fail('FINITE_POLICY_PRODUCER_REQUIRED', 'current original native catalog/security reader is required')
    policy = profile.windows_policy
    provider, sublayer = guid_key(policy.wfp_provider_key), guid_key(policy.wfp_sublayer_key)
    filters = {guid_key(k) for k in policy.wfp_filter_keys}
    current = {}; selected = []
    for kind, rows, field in (('Provider', snapshot['providers'], 'provider_key'),
                              ('SubLayer', snapshot['sublayers'], 'sublayer_key'),
                              ('Filter', snapshot['filters'], 'filter_key')):
        for row in rows:
            key = guid_key(row[field])
            own = ((kind == 'Provider' and key == provider) or
                   (kind == 'SubLayer' and (key == sublayer or row.get('provider_key') and guid_key(row['provider_key']) == provider)) or
                   (kind == 'Filter' and (key in filters or row.get('provider_key') and guid_key(row['provider_key']) == provider
                                          or guid_key(row['sublayer_key']) == sublayer)))
            if own:
                identity = kind, key
                if identity in current:
                    _fail('FINITE_OWNED_POLICY_EXTRA_OBJECT', 'own policy contains duplicate identities')
                current[identity] = row['security_sha256']
                selected.append((kind, key, row))
    expected = {(t.kind, t.key): canonical_security_descriptor_sha256(bytes.fromhex(t.descriptor_hex)) for t in contract.policy}
    if set(current) != set(expected):
        _fail('FINITE_OWNED_POLICY_EXTRA_OBJECT', 'own provider/sublayer consumers differ from the exact configured set')
    if current != expected or any(api._finite_security_reads.get(k) != v for k, v in expected.items()):
        _fail('FINITE_OWNED_POLICY_SECURITY_MISMATCH', 'actual SDK canonical protection differs from the finite expected descriptor')
    return sorted(selected, key=lambda item: (item[0], item[1]))
