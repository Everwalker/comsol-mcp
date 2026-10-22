"""Bounded Windows source-port intent. No authorization or native admission."""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading

from ._execution_contract import ExecutionContractError
from ._windows_owned_resources import NativeResourceError, registry_for


_OWNER_SEAL = object()
_query_lock = threading.RLock()
_dynamic_range_owner = None


def _ownership_unknown(message):
    raise NativeResourceError('NATIVE_RESOURCE_RELEASE_UNKNOWN', message, safe_retry=False, stage='pre_dispatch')


class _FileHandleTransfer:
    """Original PyHANDLE and fixed SDK closer, never a replacement wrapper."""
    def __init__(self, returned, close, *, seal):
        if seal is not _OWNER_SEAL:
            _ownership_unknown('original file acquisition provenance required')
        self.returned = returned
        self.close_function = close
        self.value = None
        self.detached_return = None
        self.close_return = None
        self.state = 'ACQUIRED'
        self._seal = seal

    def capture_value(self, returned):
        if returned is not self.returned:
            _ownership_unknown('integer must come from the original returned PyHANDLE')
        self.value = int(returned)
        return self.value

    def detach(self):
        try:
            self.detached_return = self.returned.Detach()
            if type(self.detached_return) is not int or self.detached_return != self.value:
                _ownership_unknown('original PyHANDLE Detach did not confirm the same HANDLE')
        except BaseException:
            self.state = 'TRANSFER_UNKNOWN'
            raise
        self.state = 'DETACHED'

    def close(self, value):
        if (self._seal is not _OWNER_SEAL or type(value) is not int or value != self.value
                or self.state not in {'DETACHED', 'CLOSE_UNKNOWN'}):
            _ownership_unknown('only the original confirmed detached HANDLE can use its SDK closer')
        try:
            self.close_return = self.close_function(value)
            if type(self.close_return) is not int or self.close_return == 0:
                _ownership_unknown('original SDK CloseHandle did not confirm release')
        except BaseException:
            self.state = 'CLOSE_UNKNOWN'
            raise
        self.state = 'RELEASED'
        return self.close_return


class _DynamicRangeOwner:
    def __init__(self, *, seal):
        if seal is not _OWNER_SEAL:
            _ownership_unknown('private original query owner required')
        import ctypes as C
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.close_function = self.kernel.CloseHandle
        self.close_function.argtypes = [C.c_void_p]
        self.close_function.restype = C.c_int
        self.registry = registry_for(self)
        self.pending_return = None
        self.operation_error = None
        self.state = 'ACQUIRED'
        self._seal = seal

    def create_file(self, sdk, *args):
        # The original owner retains the SDK return before int/Detach, holder
        # construction or registry validation can fail. No PyHANDLE.Close call.
        self.pending_return = sdk.CreateFile(*args)
        holder = _FileHandleTransfer(self.pending_return, self.close_function, seal=_OWNER_SEAL)
        entry = self.registry.acquire(sdk, self.pending_return, holder.close, mode='BOOL',
            label='protected_netsh_path', domain='netsh_path',
            keepalive=(self.pending_return, holder, self.close_function, self.kernel, sdk),
            value_reader=holder.capture_value)
        try:
            holder.detach()
        except BaseException as exc:
            entry.state = 'UNKNOWN'
            entry.error = exc
            raise
        self.pending_return = None
        return entry.handle

    def recover_owned(self, owner):
        """Explicit in-process recovery via this original owner/closer only."""
        global _dynamic_range_owner
        with _query_lock:
            if (self._seal is not _OWNER_SEAL or owner is not self
                    or _dynamic_range_owner is not self or self.state != 'UNKNOWN'):
                _ownership_unknown('original unresolved query owner required')
            if self.pending_return is not None:
                _ownership_unknown('unconfirmed original PyHANDLE transfer cannot be reconstructed or detached again')
            errors = []
            for entry in reversed(self.registry.unresolved()):
                try:
                    self.registry.retry_owned(entry, self)
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                _ownership_unknown('original query owner still retains an uncertain HANDLE')
            self.state = 'RELEASED'
            _dynamic_range_owner = None


def validate_port_range(value):
    if (not isinstance(value, tuple) or len(value) != 2
            or any(type(p) is not int or not 1 <= p <= 65535 for p in value)
            or value[0] > value[1] or value == (1, 65535)):
        raise ValueError('port range must be an ordered bounded UINT16 pair, excluding the full port space')
    return value


def validate_source_port_ranges(grant):
    """Validate new range semantics without changing existing exact grants."""
    local = grant.local_port_range
    remote = grant.remote_port_range
    for ports, bounds in ((grant.local_ports, local), (grant.remote_ports, remote)):
        if bounds is not None:
            validate_port_range(bounds)
            if ports:
                raise ValueError('port list and range on the same side are mutually exclusive')
    if local is None and remote is None:
        return
    if (type(grant.protocol) is not int or grant.protocol != 6 or grant.address_family != 'IPV4'
            or grant.local_addresses != ('127.0.0.1/32',)
            or grant.remote_addresses != ('127.0.0.1/32',)):
        raise ValueError('source ranges require exact IPv4 TCP loopback peers')
    if grant.direction == 'OUTBOUND':
        source, destination, forbidden = local, grant.remote_ports, remote
    elif grant.direction == 'INBOUND':
        source, destination, forbidden = remote, grant.local_ports, local
    else:
        raise ValueError('source ranges support CONNECT/RECV_ACCEPT directions only')
    if (source is None or forbidden is not None or len(destination) != 1
            or type(destination[0]) is not int or not 1 <= destination[0] <= 65535):
        raise ValueError('range must select only the source side of one fixed destination')


@dataclass(frozen=True)
class SessionNetworkIntent:
    server_listen_port: int
    worker_listen_port: int
    source_port_range: tuple[int, int]
    profile_sha256: str
    profile_source_sha256: str
    bind_address: str = '127.0.0.1'
    verification_state: str = 'NOT_VERIFIED'

    def __post_init__(self):
        validate_port_range(self.source_port_range)
        if (any(type(p) is not int or not 1 <= p <= 65535 for p in
                (self.server_listen_port, self.worker_listen_port))
                or self.server_listen_port == self.worker_listen_port
                or self.bind_address != '127.0.0.1' or self.verification_state != 'NOT_VERIFIED'
                or any(not isinstance(s, str) or not re.fullmatch('[0-9a-f]{64}', s)
                       for s in (self.profile_sha256, self.profile_source_sha256))):
            raise ValueError('fixed listener intent must retain exact bindings and NOT_VERIFIED')

    def as_dict(self):
        return {**self.__dict__, 'source_port_range': list(self.source_port_range)}


def _fail(message):
    raise ExecutionContractError('T037_NETWORK_INTENT_INVALID', message, safe_retry=False, stage='pre_dispatch')


def derive_session_network_intent(profile):
    """Derive data from the existing typed profile; a dict is never authority.

    Primary must pass its existing pinned/trusted startup profile, compare argv
    and readiness, and reobserve the OS range before any later admission. This
    pure derivation also accepts parsed software intent for tests, but cannot
    promote it to trusted state or authorize a process birth.
    """
    from ._windows_t037 import WindowsT037ProfileV2
    from ._w26_os_resources import validate_profile
    from ._windows_filter_group import normalize_grant
    if type(profile) is not WindowsT037ProfileV2:
        _fail('existing typed Windows T037 v2/v3 profile required; caller dictionaries are not accepted')
    host = profile.host_resource_profile
    validate_profile(host)
    if host.platform != 'windows' or host.windows_isolation is None:
        _fail('Windows isolation profile required')
    normalized = [normalize_grant(g, host) for g in host.windows_isolation.wfp_grants]
    required = {('worker', 'OUTBOUND'), ('server', 'INBOUND'), ('worker', 'INBOUND')}
    selected = {}
    extras = []
    for g in normalized:
        has_range = 'local_port_range' in g or 'remote_port_range' in g
        if not has_range:
            extras.append(g)
            continue
        key = (g['role'], g['direction'])
        if key not in required or key in selected:
            _fail('three source-range session flows must be unique and have their exact roles')
        selected[key] = g
    if set(selected) != required:
        _fail('Worker-out, Server-in and Worker-in source-range flows are all required')
    out = selected[('worker', 'OUTBOUND')]
    server = selected[('server', 'INBOUND')]
    worker = selected[('worker', 'INBOUND')]
    ranges = (out['local_port_range'], server['remote_port_range'], worker['remote_port_range'])
    if ranges[0] != ranges[1] or ranges[0] != ranges[2] or out['remote_port'] != server['local_port']:
        _fail('session source ranges and Worker-out/Server-in destination must agree exactly')
    for g in extras:
        key = (g['role'], g['direction'])
        if key in required and g['local_address'] == g['remote_address'] == '127.0.0.1/32':
            destination = g['remote_port'] if g['direction'] == 'OUTBOUND' else g['local_port']
            expected = out['remote_port'] if key != ('worker', 'INBOUND') else worker['local_port']
            if destination == expected:
                _fail('an extra exact grant conflicts with a required session source-range flow')
    try:
        return SessionNetworkIntent(server['local_port'], worker['local_port'], ranges[0],
                                    profile.sha256, profile.source_sha256)
    except ValueError as exc:
        _fail(str(exc))


def parse_ipv4_tcp_dynamic_range(text):
    """Strict English/Simplified-Chinese netsh show output, no default guess."""
    if not isinstance(text, str) or len(text) > 16384 or '\x00' in text:
        raise ValueError('bounded dynamic-port output required')
    lines = [' '.join(s.split()) for s in text.lstrip('\ufeff').splitlines() if s.strip()]
    headers = {'protocol tcp dynamic port range', '协议 tcp 动态端口范围'}
    if len(lines) != 4 or lines[0].casefold() not in headers or not re.fullmatch('-{3,}', lines[1]):
        raise ValueError('unknown, ambiguous or incomplete IPv4 TCP dynamic-port output')
    start = re.fullmatch(r'(?:Start Port|启动端口)\s*:\s*([0-9]{1,5})', lines[2], re.IGNORECASE)
    count = re.fullmatch(r'(?:Number of Ports|端口数)\s*:\s*([0-9]{1,5})', lines[3], re.IGNORECASE)
    if not start or not count:
        raise ValueError('exact start/count rows required')
    low, size = int(start[1]), int(count[1])
    if size < 1:
        raise ValueError('positive dynamic-port count required')
    return validate_port_range((low, low + size - 1))


def _get_windows_oem_code_page():
    import ctypes as C
    function = C.WinDLL('kernel32', use_last_error=True).GetOEMCP
    function.argtypes = []
    function.restype = C.c_uint32
    code_page = function()
    if type(code_page) is not int or not 1 <= code_page <= 65535:
        _fail('native OEM code-page observation is invalid')
    return code_page


def decode_ipv4_tcp_dynamic_range_output(raw, oem_code_page):
    """Pure decoding of retained output; it does not establish observation freshness."""
    import codecs
    if (type(raw) is not bytes or len(raw) > 16384 or type(oem_code_page) is not int
            or not 1 <= oem_code_page <= 65535):
        raise ValueError('bounded bytes and an actual native OEM code page required')
    try:
        codecs.lookup('cp' + str(oem_code_page))
    except LookupError as exc:
        raise ValueError('unsupported native OEM code page') from exc
    results = set()
    for encoding in {'utf-8-sig', 'cp' + str(oem_code_page)}:
        try:
            results.add(parse_ipv4_tcp_dynamic_range(raw.decode(encoding, errors='strict')))
        except (UnicodeError, ValueError):
            pass
    if len(results) != 1:
        raise ValueError('protected netsh output was unsupported or ambiguous')
    return results.pop()


@dataclass(frozen=True)
class DynamicTcpRangeObservation:
    source_port_range: tuple[int, int]
    executable_path: str
    executable_sha256: str
    output_sha256: str
    verification_state: str = 'NOT_VERIFIED'

    def __post_init__(self):
        validate_port_range(self.source_port_range)
        if self.verification_state != 'NOT_VERIFIED':
            raise ValueError('OS dynamic-port metadata is not native isolation proof')


def _read_protected_dynamic_range(owner):
    """One query of protected System32 netsh; no caller executable, PATH or shell.

    Pin path components without SHARE_DELETE and the image without SHARE_WRITE; reject
    reparses and untrusted write grants, and retain those handles through the
    query. No service, registry, firewall or dynamic-port setting is changed.
    """
    import os
    import ntpath
    import hashlib
    import subprocess
    if os.name != 'nt':
        _fail('Windows read-only dynamic-port observation required')
    import win32api
    import win32con
    import win32file
    import win32security
    windows = ntpath.normpath(win32api.GetWindowsDirectory())
    system = ntpath.normpath(win32api.GetSystemDirectory())
    if ntpath.normcase(system) != ntpath.normcase(ntpath.join(windows, 'System32')):
        _fail('native System32 path is not the expected protected Windows directory')
    executable = ntpath.join(system, 'netsh.exe')
    installer = win32security.ConvertSidToStringSid(win32security.LookupAccountName(None, r'NT SERVICE\TrustedInstaller')[0])
    trusted = {'S-1-5-18', 'S-1-5-32-544', installer}
    drive, tail = ntpath.splitdrive(executable)
    if not re.fullmatch('[A-Za-z]:', drive) or not tail.startswith('\\'):
        _fail('protected local System32 path required')
    handles = []
    def fingerprint(h, directory):
        info = win32file.GetFileInformationByHandle(h)
        if info[0] & 0x400 or bool(info[0] & 0x10) != directory:
            _fail('protected netsh path contains a reparse or wrong object type')
        sd = win32security.GetSecurityInfo(h, 1, 7)
        if win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner()) not in trusted:
            _fail('protected System32 component has an untrusted owner')
        acl = sd.GetSecurityDescriptorDacl()
        if acl is None:
            _fail('protected System32 component has a null DACL')
        for index in range(acl.GetAceCount()):
            ace = acl.GetAce(index)
            if len(ace) != 3 or ace[0][0] not in (0, 1):
                _fail('unsupported System32 security descriptor ACE')
            (kind, flags), mask, sid = ace
            if kind == 0 and not flags & 8 and win32security.ConvertSidToStringSid(sid) not in trusted:
                # Pinned ancestors may allow creation of unrelated siblings;
                # they may not allow replacement, ACL or attribute mutation.
                dangerous = 0x500D0150 if directory else 0x500D0156
                if type(mask) is not int or mask & ~0xF01F01FF or mask & dangerous:
                    _fail('untrusted mutation access to pinned System32 component')
        security = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(sd, 1, 7)
        return (info[4], info[8], info[9], info[5], info[6], hashlib.sha256(security.encode()).hexdigest())
    current = drive + '\\'
    parts = [current]
    for part in tail.strip('\\').split('\\'):
        current = ntpath.join(current, part)
        parts.append(current)
    for index, path in enumerate(parts):
        directory = index < len(parts)-1
        flags = 0x00200000 | (0x02000000 if directory else 0)
        access = 0x20080 | (0x80000000 if not directory else 0)
        share = win32con.FILE_SHARE_READ | (win32con.FILE_SHARE_WRITE if directory else 0)
        h = owner.create_file(win32file, path, access, share, None, win32con.OPEN_EXISTING, flags, None)
        handles.append((h, directory, None))
        handles[-1] = (h, directory, fingerprint(h, directory))
    image_size = (handles[-1][2][3] << 32) | handles[-1][2][4]
    if not 1 <= image_size <= 32*1024*1024:
        _fail('protected netsh image size is outside the supported bound')
    image = hashlib.sha256()
    read_bytes = 0
    while True:
        try:
            _, block = win32file.ReadFile(handles[-1][0], 65536)
        except Exception as exc:
            if getattr(exc, 'winerror', None) == 38:
                break
            raise
        if not block:
            break
        read_bytes += len(block)
        if read_bytes > image_size:
            _fail('protected netsh image extent changed during hashing')
        image.update(block)
    if read_bytes != image_size:
        _fail('protected netsh image ended before its pinned extent')
    env = {'SystemRoot': windows, 'WINDIR': windows, 'PATH': system}
    completed = subprocess.run([executable, 'int', 'ipv4', 'show', 'dynamicport', 'tcp'],
        shell=False, cwd=system, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
    if completed.returncode or completed.stderr.strip() or len(completed.stdout) > 16384:
        _fail('protected netsh IPv4 TCP show query failed or exceeded its output bound')
    try:
        bounds = decode_ipv4_tcp_dynamic_range_output(completed.stdout, _get_windows_oem_code_page())
    except ValueError as exc:
        _fail(str(exc))
    if any(fingerprint(h, directory) != before for h, directory, before in handles):
        _fail('protected netsh image/path security identity drifted during observation')
    return DynamicTcpRangeObservation(bounds, executable, image.hexdigest(), hashlib.sha256(completed.stdout).hexdigest())


def read_windows_ipv4_tcp_dynamic_range():
    """One protected query; uncertain resources stay with their original owner.

    No caller PATH/shell, mutation or admission. A prior uncertain transfer or
    close blocks reacquisition until that original owner's explicit recovery.
    """
    import os
    global _dynamic_range_owner
    with _query_lock:
        if _dynamic_range_owner is not None:
            _ownership_unknown('previous query still retains its original native resources')
        if os.name != 'nt':
            _fail('Windows read-only dynamic-port observation required')
        owner = _DynamicRangeOwner(seal=_OWNER_SEAL)
        _dynamic_range_owner = owner
        scope = owner.registry.scope()
        try:
            return _read_protected_dynamic_range(owner)
        except BaseException as exc:
            owner.operation_error = exc
            raise
        finally:
            errors = owner.registry.drain(scope)  # UNKNOWN entries never auto-retry.
            if errors or owner.pending_return is not None or owner.registry.unresolved():
                owner.state = 'UNKNOWN'
                _ownership_unknown('query retains original uncertain resources; no new acquisition is allowed')
            owner.state = 'RELEASED'
            _dynamic_range_owner = None


def revalidate_source_port_range(intent):
    """Future admission prerequisite only; this function does not admit or launch."""
    if type(intent) is not SessionNetworkIntent:
        _fail('typed session network intent required')
    observed = read_windows_ipv4_tcp_dynamic_range()
    if observed.source_port_range != intent.source_port_range:
        _fail('actual IPv4 TCP dynamic source range differs from the pinned profile intent')
    return observed
