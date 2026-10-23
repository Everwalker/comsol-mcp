"""Explicit readonly SCM attachment. No serialized/native admission authority.

All SDK objects belong to one finite reader, rooted before its first call.
The public entry accepts only a locator path. Software exports have a separate
private construction path and never acquire native provenance.
"""
from __future__ import annotations

from collections.abc import Mapping
import base64
import ctypes as C
import json
import ntpath
import os
import re
import threading

from . import _windows_parameter_publish as p
from . import _windows_host_bootstrap as b
from ._host_scoped_provider import _validate_windows_acl_policy
from ._windows_installer_context import _sid, _SidAttributes
from ._windows_service_sid_native import Config, Status, SidInfo, text
from ._windows_t037_mic import _MemoryInfo

ENV = 'COMSOL_SERVER_MCP_SCM_ENDPOINT_CONFIG'
SCHEMA = 'comsol-mcp.scm-client-endpoint/1'
_NATIVE, _SOFTWARE = object(), object()
_ROOTS, _LOCK = {}, threading.RLock()
SY, BA = 'S-1-5-18', 'S-1-5-32-544'
TI = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
D, B, H = C.c_uint32, C.c_int32, C.c_void_p


class AttachError(RuntimeError):
    def __init__(self, code='SCM_ATTACH_REFUSED', *, owner=None):
        super().__init__('Explicit SCM attach-only source verification refused (' + code + ').')
        self.code, self.owner = code, owner


def _bad(code):
    raise AttachError(code)


def _path(value):
    if (type(value) is not str or not re.fullmatch(r'[A-Za-z]:\\.+', value)
            or len(value) >= 240 or ntpath.normpath(value) != value):
        _bad('PATH_INVALID')
    try:
        value.encode('utf-16-le','strict')
    except UnicodeError:
        _bad('PATH_INVALID')
    for part in value[3:].split('\\'):
        try:
            p._component(part)
        except Exception:
            _bad('PATH_INVALID')
    return value


def _json(raw, maximum):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                _bad('JSON_DUPLICATE')
            result[key] = value
        return result
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        _bad('FILE_BOUNDS')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: _bad('JSON_INVALID'))
    except AttachError:
        raise
    except Exception:
        _bad('JSON_INVALID')


# Original ABI structures are shared; no mutation-capable constructor is used.
SPECS = {
    'kernel32': {
        'CreateFileW': (H, [C.c_wchar_p,D,D,H,D,D,H], 'PTR'),
        'CloseHandle': (B, [H], 'BOOL'),
        'GetFileInformationByHandleEx': (B, [H,B,H,D], 'BOOL'),
        'ReadFile': (B, [H,H,D,C.POINTER(D),H], 'BOOL'),
        'GetVolumeInformationW': (B,[C.c_wchar_p,C.c_wchar_p,D,C.POINTER(D),C.POINTER(D),C.POINTER(D),C.c_wchar_p,D],'BOOL'),
        'GetCurrentProcess': (H, [], 'PSEUDO'),
        'GetCurrentThread': (H, [], 'PSEUDO'),
        'OpenProcess': (H, [D,B,D], 'PTR'),
        'GetProcessId': (D, [H], 'DWORD'),
        'GetProcessTimes': (B,[H,*([C.POINTER(p._FileTime)]*4)],'BOOL'),
        'QueryFullProcessImageNameW': (B,[H,D,C.c_wchar_p,C.POINTER(D)],'BOOL'),
        'LocalFree': (H, [H], 'PTR'), 'LocalSize': (C.c_size_t,[H],'SIZE'),
        'VirtualQuery': (C.c_size_t,[H,C.POINTER(_MemoryInfo),C.c_size_t],'SIZE'),
    },
    'advapi32': {
        'OpenProcessToken': (B,[H,D,C.POINTER(H)],'BOOL'),
        'OpenThreadToken': (B,[H,D,B,C.POINTER(H)],'BOOL'),
        'GetTokenInformation': (B,[H,B,H,D,C.POINTER(D)],'BOOL'),
        'GetSecurityInfo': (D,[H,D,D,*([C.POINTER(H)]*5)],'DWORD'),
        'GetSecurityDescriptorLength': (D,[H],'DWORD'),
        'OpenSCManagerW': (H,[C.c_wchar_p,C.c_wchar_p,D],'PTR'),
        'OpenServiceW': (H,[H,C.c_wchar_p,D],'PTR'),
        'CloseServiceHandle': (B,[H],'BOOL'),
        'QueryServiceConfigW': (B,[H,H,D,C.POINTER(D)],'BOOL'),
        'QueryServiceConfig2W': (B,[H,D,H,D,C.POINTER(D)],'BOOL'),
        'QueryServiceStatusEx': (B,[H,D,H,D,C.POINTER(D)],'BOOL'),
        'LookupAccountNameW': (B,[C.c_wchar_p,C.c_wchar_p,H,C.POINTER(D),C.c_wchar_p,C.POINTER(D),C.POINTER(D)],'BOOL'),
    },
    'ntdll': {'NtCreateFile': (B,[C.POINTER(H),D,C.POINTER(p._ObjectAttributes),C.POINTER(p._IoStatusBlock),H,D,D,D,D,H,D],'BOOL')},
    'shell32': {'CommandLineToArgvW': (H,[C.c_wchar_p,C.POINTER(B)],'PTR')},
}
READONLY_SDK = tuple(name for library in SPECS.values() for name in library)


class _Api:
    def __init__(self):
        if os.name != 'nt' or C.sizeof(H) != 8:
            _bad('WINDOWS_REQUIRED')
        self.seal = _NATIVE
        self.libraries = tuple(C.WinDLL(name, use_last_error=True) for name in SPECS)
        self.sdk = {}
        for dll, specs in zip(self.libraries, SPECS.values()):
            for name, (result, args, _kind) in specs.items():
                function = getattr(dll, name)
                function.restype, function.argtypes = result, args
                self.sdk[name] = function
        self.error = C.get_last_error
        self._bindings = dict(self.sdk)

    def check(self):
        if set(self.sdk) != set(self._bindings) or any(self.sdk[k] is not v for k,v in self._bindings.items()):
            _bad('SDK_BINDING')


class _SoftwareApi(_Api):
    def __init__(self, exports, error, *, seal):
        if seal is not _SOFTWARE or set(exports) != set(READONLY_SDK):
            _bad('SOFTWARE_BOUNDARY')
        self.seal, self.sdk, self.error = _SOFTWARE, dict(exports), error
        self._bindings, self.libraries = dict(self.sdk), (exports,)


class _Call:
    def __init__(self, owner, name, args):
        self.owner, self.name, self.args = owner, name, args
        self.function = owner.api.sdk[name]
        self._binding = (self.function, args)
        self.entered, self._result, self.raw, self.error = False, None, None, None
        owner.calls.append(self)  # args include original byref/output holders

    @property
    def result(self):
        return self._result

    def run(self):
        self.owner.check()
        if self.function is not self._binding[0] or self.args is not self._binding[1] or self.entered:
            _bad('SDK_REPLAY_OR_BINDING')
        self.entered = True
        try:
            self.raw = self.function(*self.args)
            raw = getattr(self.raw, 'value', self.raw)
            kind = next(spec[self.name][2] for spec in SPECS.values() if self.name in spec)
            if kind == 'PTR' and raw is None:
                value = None
            else:
                low, high = ((-2**31,2**31-1) if kind=='BOOL' else
                             (0,2**32-1) if kind=='DWORD' else (0,2**64-1))
                if type(raw) is not int or not low <= raw <= high:
                    _bad('SDK_RESULT_UNKNOWN')
                value = raw
            if kind=='PSEUDO' and value!=H(-1 if self.name=='GetCurrentProcess' else -2).value:
                _bad('SDK_RESULT_UNKNOWN')
            error = self.owner.api.error()
            if type(error) is not int or not 0 <= error < 2**32:
                _bad('SDK_RESULT_UNKNOWN')
            self._result, self.error = (value,), error
            return value
        except BaseException as error:
            self.owner.opaque = True
            self.owner.remember(error)
            raise


class _Resource:
    def __init__(self, owner, closer):
        self.owner, self.storage = owner, H()
        self._owner, self._storage = owner, self.storage
        self.closer = self._closer = owner.api.sdk[closer]
        self.closer_name = closer
        self.call = self.close_call = None
        self._call = None
        self.value = None
        self.owned = self.confirmed = self.closed = self.unknown = self.empty = False
        owner.resources.append(self)  # before the native acquisition

    def capture(self, call, success):
        self.call = self._call = call
        if not success:
            if self.storage.value is not None:
                self.unknown = True
                self.owner.opaque = True
            else:
                self.closed = self.empty = True
            _bad('ACQUISITION_REFUSED')
        value = self.storage.value
        if (type(value) is not int or not 0 < value < H(-1).value
                or self.closer_name=='LocalFree' and (not 65536<=value<2**64-65536 or value%8)):
            self.unknown = self.owner.opaque = True
            _bad('ACQUISITION_UNKNOWN')
        self.value, self.owned = value, True
        return self.storage

    def check(self):
        if (self.owner is not self._owner or self.storage is not self._storage or self.call is not self._call
                or self.owner.api.sdk.get(self.closer_name) is not self._closer
                or self.closer is not self._closer or self.storage.value != self.value):
            _bad('RESOURCE_BINDING')

    def release(self):
        self.check()
        if self.confirmed or self.owned and self.closed:
            if (self.close_call is None or self.close_call.result is None
                    or self.close_call.function is not self._closer
                    or self.close_call.args != (self._storage,)
                    or not (self.close_call.result[0] in (None,0) if self.closer_name=='LocalFree'
                            else type(self.close_call.result[0]) is int and self.close_call.result[0]!=0)):
                _bad('RESOURCE_ACK_UNKNOWN')
        if self.closed:
            if self.owned and not self.confirmed or not self.owned and not self.empty:
                _bad('RESOURCE_ACK_UNKNOWN')
            return
        if self.unknown or not self.owned:
            _bad('RESOURCE_UNKNOWN')
        if not self.confirmed:
            if self.close_call is not None:
                _bad('RELEASE_UNKNOWN')
            self.close_call = _Call(self.owner, self.closer_name, (self.storage,))
            value = self.close_call.run()
            success = value in (None,0) if self.closer_name=='LocalFree' else value != 0
            if not success:
                self.unknown = self.owner.opaque = True
                _bad('RELEASE_UNKNOWN')
            self.confirmed = True
        if (self.close_call is None or self.close_call.result is None
                or self.close_call.function is not self._closer
                or self.close_call.args != (self._storage,)):
            _bad('RESOURCE_ACK_UNKNOWN')
        self.owner.checkpoint('release_ack', self)
        self.closed = True


class _Owner:
    def __init__(self, api, locator):
        self.api = self._api = api
        self.locator, self.home = locator, None
        self.calls, self.resources, self.pins = [], [], []
        self._calls, self._resources = self.calls, self.resources
        self.opaque, self.first_cause, self.retired = False, None, False
        self.thread = threading.get_ident()
        self._roots, self._error = _ROOTS, api.error
        self._origin = api.seal
        self._bindings = dict(api.sdk)
        self._retiring = False
        _ROOTS[id(self)] = self

    def __repr__(self):
        return '<original SCM readonly owner>'

    def remember(self, error):
        if self.first_cause is None:
            self.first_cause = error

    def checkpoint(self, event, original):
        """Metadata acknowledgement fault seam; no native call."""

    def check(self):
        if (self.api is not self._api or _ROOTS is not self._roots or _ROOTS.get(id(self)) is not self
                or self.calls is not self._calls or self.resources is not self._resources
                or self.api.seal is not self._origin
                or threading.get_ident()!=self.thread or self.api.error is not self._error
                or any(self.api.sdk.get(k) is not v for k,v in self._bindings.items())):
            _bad('OWNER_BINDING')
        self.api.check()
        if self.opaque or any(call.entered and call.result is None for call in self.calls):
            _bad('ORIGINAL_OWNER_UNKNOWN')
        for resource in self.resources:
            if resource.owned:resource.check()

    def call(self, name, *args):
        call = _Call(self, name, args)
        value = call.run()
        return value, call

    def bool(self, name, *args):
        value, _ = self.call(name,*args)
        if not value:
            _bad('READ_REFUSED')

    def returned(self, name, closer, *args):
        resource = _Resource(self, closer)
        value, call = self.call(name,*args)
        resource.storage.value = value
        return resource.capture(call, value not in (None,0,H(-1).value))

    def output(self, name, closer, args, *, status=False):
        resource = _Resource(self,closer)
        value, call = self.call(name,*args(resource.storage))
        return resource.capture(call, value==0 if status else value!=0)

    def finish(self):
        self.check()
        for resource in reversed(self.resources):
            resource.release()
        self.checkpoint('retire_ack', self)
        self._retiring = True
        try:
            _ROOTS.pop(id(self))
            self.retired = True
        except BaseException:
            _ROOTS[id(self)] = self
            raise


def recover_owned(owner):
    """Only original acknowledged successes may continue; never opaque replay."""
    if type(owner) is not _Owner or _ROOTS.get(id(owner)) is not owner:
        _bad('ORIGINAL_OWNER_REQUIRED')
    owner.finish()


def _readable(owner, pointer, size):
    if type(pointer) is not int or not 65536 <= pointer < 2**64-65536 or not 0 < size <= 65536:
        _bad('MEMORY_BOUNDS')
    cursor, end = pointer, pointer+size
    for _ in range(16):
        if cursor >= end:
            return
        info = _MemoryInfo()
        n,_ = owner.call('VirtualQuery',H(cursor),C.byref(info),C.sizeof(info))
        base=info.base or 0
        if (n!=C.sizeof(info) or not base<=cursor<base+info.size or base+info.size>=2**64-65536 or info.state!=0x1000
                or info.protect not in (2,4,8,0x20,0x40,0x80)):
            _bad('MEMORY_BOUNDS')
        cursor=min(end,base+info.size)
    _bad('MEMORY_BOUNDS')


def _descriptor(owner, handle):
    sd = owner.output('GetSecurityInfo','LocalFree',
        lambda out:(handle,1,7,None,None,None,None,C.byref(out)),status=True)
    _readable(owner,sd.value,20)
    size,_=owner.call('GetSecurityDescriptorLength',sd)
    _readable(owner,sd.value,size)
    raw=C.string_at(sd,size)
    try:
        decoded=b.decode_sd(raw)
    except Exception:
        _bad('DESCRIPTOR_INVALID')
    return raw,decoded


def _policy(sd, current, *, final, target, service_sid):
    # Locator is client-private; SYSTEM target ancestors remain inside the
    # SYSTEM/admin TCB. A client read grant must never become ancestor ownership
    # or permission to replace a protected edge after its pins close.
    trusted={SY,BA} if target else {current,SY,BA}
    rows=[dict(type=t,flags=f,access_mask=m,sid_trusted=s in trusted) for t,f,m,s in sd['aces']]
    if not target or not final:
        _validate_windows_acl_policy(owner_trusted=sd['owner'] in trusted,
            owner_is_daemon=sd['owner']==current,require_daemon_owner=final,
            dacl_present=True,ace_rows=rows,pinned_ancestor_traversal=not final,
            owner_is_trustedinstaller=sd['owner']==TI)
        return
    if sd['owner']!=SY or not sd['control']&0x1000:
        _bad('TARGET_PRIVATE_OWNER_OR_DACL')
    for kind,flags,mask,sid in sd['aces']:
        if kind==1 or flags&8:
            continue
        if sid not in {SY,BA,current,service_sid}:
            _bad('TARGET_READER')
        if sid not in {SY,BA}:
            # Generic read/execute only; never map/cancel a write with deny ACEs.
            mapped=mask&~(0x80000000|0x20000000)
            if mask&0x80000000:mapped|=0x120089
            if mask&0x20000000:mapped|=0x1200A0
            if mapped&~0x1200A9:
                _bad('TARGET_WRITER')


def _fingerprint(owner,handle,directory):
    full,basic,standard=p._FullIdInfo(),p._BasicInfo(),p._StandardInfo()
    for kind,buffer in ((18,full),(0,basic),(1,standard)):
        owner.bool('GetFileInformationByHandleEx',handle,kind,C.byref(buffer),C.sizeof(buffer))
    if (basic.attributes&0x400 or bool(basic.attributes&16)!=directory
            or bool(standard.directory)!=directory or standard.delete_pending
            or standard.links!=1 or standard.size<0 or not any(full.file_id)):
        _bad('FILE_IDENTITY')
    return (full.volume,bytes(full.file_id),standard.size,basic.created,basic.written,basic.changed,basic.attributes)


def _open_path(owner,path,directory):
    _path(path)
    chain=[owner.returned('CreateFileW','CloseHandle',path[:3],0x20080,3,None,3,0x2200000,None)]
    for index,leaf in enumerate(path[3:].split('\\')):
        isdir=index<len(path[3:].split('\\'))-1 or directory
        raw=leaf.encode('utf-16-le');buffer=C.create_string_buffer(raw+b'\0\0')
        name=p._UnicodeString(len(raw),len(raw)+2,C.cast(buffer,C.POINTER(C.c_wchar)))
        attrs=p._ObjectAttributes(C.sizeof(p._ObjectAttributes),chain[-1],C.pointer(name),0x1040,None,None)
        io=p._IoStatusBlock()
        resource=_Resource(owner,'CloseHandle')
        value,call=owner.call('NtCreateFile',C.byref(resource.storage),0x1200A0 if isdir else 0x120089,
            C.byref(attrs),C.byref(io),None,0,3,1,(1 if isdir else 0x40)|0x20|0x200000,None,0)
        child=resource.capture(call,value>=0)
        if io.Information!=1:
            _bad('RELATIVE_OPEN')
        chain.append(child)
    return chain


def _read_file(owner,handle,size,limit):
    if not 1<=size<=limit:
        _bad('FILE_BOUNDS')
    raw=bytearray()
    while len(raw)<=size:
        buffer=C.create_string_buffer(min(4096,size+1-len(raw)));used=D()
        owner.bool('ReadFile',handle,buffer,len(buffer),C.byref(used),None)
        if used.value>len(buffer):
            _bad('FILE_BOUNDS')
        raw.extend(buffer.raw[:used.value])
        if not used.value:
            break
    if len(raw)!=size:
        _bad('FILE_DRIFT')
    return bytes(raw)


def _file(owner,path,*,directory,current,target=False,service_sid=None):
    first=None
    for _ in range(2):
        chain=_open_path(owner,path,directory)
        fs=C.create_unicode_buffer(256);serial,maximum,flags=D(),D(),D()
        owner.bool('GetVolumeInformationW',path[:3],None,0,C.byref(serial),C.byref(maximum),C.byref(flags),fs,len(fs))
        if not flags.value&8:
            _bad('PERSISTENT_ACLS_REQUIRED')
        facts=[]
        for i,handle in enumerate(chain):
            isdir=i<len(chain)-1 or directory
            fp=_fingerprint(owner,handle,isdir);raw,sd=_descriptor(owner,handle)
            if facts and fp[0]!=facts[0][0][0]:
                _bad('FILE_VOLUME')
            _policy(sd,current,final=i==len(chain)-1,target=target,service_sid=service_sid)
            facts.append((fp,raw))
            owner.pins.append((handle,isdir,fp,raw))
        content=None if directory else _read_file(owner,chain[-1],facts[-1][0][2],16384)
        fact=(tuple(facts),content)
        if first is not None and first!=fact:
            _bad('FILE_DRIFT')
        first=fact
    return first[1]


def _user(owner,process):
    token=owner.output('OpenProcessToken','CloseHandle',lambda out:(process,8,C.byref(out)))
    buffer=C.create_string_buffer(65536);used=D()
    owner.bool('GetTokenInformation',token,1,buffer,len(buffer),C.byref(used))
    if not C.sizeof(_SidAttributes)<=used.value<=len(buffer):
        _bad('TOKEN_BOUNDS')
    return _sid(buffer,used.value,_SidAttributes.from_buffer_copy(buffer).sid,C.sizeof(_SidAttributes))


def _buffer(owner,name,args):
    used=D();result,call=owner.call(name,*args(None,0,C.byref(used)))
    if result!=0 or call.error!=122 or not 4<=used.value<=65536:
        _bad('SCM_BUFFER')
    buffer=C.create_string_buffer(used.value);returned=D()
    owner.bool(name,*args(buffer,len(buffer),C.byref(returned)))
    return buffer


def _argv(owner,binary):
    count=B();pointer=owner.returned('CommandLineToArgvW','LocalFree',binary,C.byref(count))
    size,_=owner.call('LocalSize',pointer)
    if not 1<=count.value<=64 or not count.value*8<=size<=65536:
        _bad('ARGV_BOUNDS')
    _readable(owner,pointer.value,size);raw=C.string_at(pointer,size)
    rows=[]
    for i in range(count.value):
        address=int.from_bytes(raw[i*8:i*8+8],'little');offset=address-pointer.value
        if not count.value*8<=offset<size or offset%2:
            _bad('ARGV_BOUNDS')
        end=offset
        while end+2<=size and raw[end:end+2]!=b'\0\0':end+=2
        if end+2>size:_bad('ARGV_BOUNDS')
        rows.append(raw[offset:end].decode('utf-16-le','strict'))
    return rows


def _service(owner,service,name,home):
    config=_buffer(owner,'QueryServiceConfigW',lambda buf,n,out:(service,buf,n,out))
    if len(config)<C.sizeof(Config):_bad('SCM_BUFFER')
    row=Config.from_buffer_copy(config)
    account=text(config,len(config),row.account);binary=text(config,len(config),row.binary)
    if row.service_type!=16 or account.casefold() not in ('localsystem','nt authority\\system'):
        _bad('SCM_IDENTITY')
    args=_argv(owner,binary)
    if (len(args)<7 or args[1:5]!=['-B','-I','-m','comsol_mcp._control_daemon']
            or len(args[5:])%2 or len(set(args[5::2]))!=len(args[5::2])
            or any(k not in {'--home','--windows-scm-service-name','--windows-t037-profile','--host-provider-config',
                            '--memory-resource-config','--disk-resource-config','--windows-scm-bootstrap-request'} for k in args[5::2])):
        _bad('SCM_ARGV')
    options=dict(zip(args[5::2],args[6::2]))
    if options.get('--home')!=home or options.get('--windows-scm-service-name')!=name:
        _bad('SCM_ARGV')
    _path(args[0])
    sidbuf=_buffer(owner,'QueryServiceConfig2W',lambda buf,n,out:(service,5,buf,n,out))
    if len(sidbuf)!=C.sizeof(SidInfo) or SidInfo.from_buffer_copy(sidbuf).sid_type!=3:
        _bad('SCM_RESTRICTED_SID')
    status=Status();used=D()
    owner.bool('QueryServiceStatusEx',service,0,C.byref(status),C.sizeof(status),C.byref(used))
    if used.value!=C.sizeof(status) or status.service_type!=16 or status.state!=4 or not status.process_id:
        _bad('SCM_NOT_RUNNING')
    config_value=(row.service_type,row.start_type,row.error_control,row.tag,binary,account,
        text(config,len(config),row.load_group),tuple(text(config,len(config),row.dependencies,multi=True)),
        text(config,len(config),row.display))
    return status.process_id,tuple(args),bytes(status),config_value


def _service_sid(owner,name):
    n,domain_size,use=D(),D(),D()
    ok,call=owner.call('LookupAccountNameW',None,'NT SERVICE\\'+name,None,C.byref(n),None,C.byref(domain_size),C.byref(use))
    if ok or call.error!=122 or not 12<=n.value<=68 or domain_size.value>32768:
        _bad('SERVICE_SID')
    cap,dcap=n.value,domain_size.value
    buffer=C.create_string_buffer(cap);domain=C.create_unicode_buffer(max(1,dcap))
    owner.bool('LookupAccountNameW',None,'NT SERVICE\\'+name,buffer,C.byref(n),domain,C.byref(domain_size),C.byref(use))
    if n.value!=cap or domain_size.value>dcap:_bad('SERVICE_SID')
    sid=_sid(buffer,n.value,C.addressof(buffer),0)
    if not re.fullmatch(r'S-1-5-80-(\d+-){4}\d+',sid):_bad('SERVICE_SID')
    return sid


def _process(owner,handle,pid):
    actual,_=owner.call('GetProcessId',handle)
    times=tuple(p._FileTime() for _ in range(4))
    owner.bool('GetProcessTimes',handle,*(C.byref(t) for t in times))
    creation=int.from_bytes(bytes(times[0]),'little');exit_time=int.from_bytes(bytes(times[1]),'little')
    if actual!=pid or exit_time or creation<116444736000000000:
        _bad('PROCESS_IDENTITY')
    image=C.create_unicode_buffer(32768);length=D(len(image))
    owner.bool('QueryFullProcessImageNameW',handle,0,image,C.byref(length))
    if not 0<length.value<len(image) or len(image.value)!=length.value:_bad('PROCESS_IMAGE')
    return (creation-116444736000000000)//10000,image.value


class _Endpoint(Mapping):
    def __init__(self,owner,values):
        if type(owner) is not _Owner or not owner.retired or owner.opaque:
            _bad('ENDPOINT_SOURCE')
        self._origin=owner.api.seal
        self.__values=values
    def __repr__(self):return '<private SCM transport endpoint>'
    def __getitem__(self,key):return self.__values[key]
    def __iter__(self):return iter(self.__values)
    def __len__(self):return len(self.__values)


def _verify(locator,api):
    if not ((type(api) is _Api and api.seal is _NATIVE)
            or (type(api) is _SoftwareApi and api.seal is _SOFTWARE)):
        _bad('API_ORIGIN')
    locator=_path(locator)
    with _LOCK:
        if any(o.locator.casefold()==locator.casefold() for o in _ROOTS.values()):
            _bad('ORIGINAL_OWNER_RETAINED')
        owner=_Owner(api,locator)
        try:
            current_process,_=owner.call('GetCurrentProcess')
            current=_user(owner,H(current_process))
            thread,_=owner.call('GetCurrentThread');thread_token=_Resource(owner,'CloseHandle')
            result,call=owner.call('OpenThreadToken',H(thread),8,1,C.byref(thread_token.storage))
            if result:
                thread_token.capture(call,True)
                _bad('IMPERSONATION_REFUSED')
            if call.error!=1008 or thread_token.storage.value is not None:
                thread_token.capture(call,False)
            thread_token.call=thread_token._call=call
            thread_token.closed=thread_token.empty=True
            doc=_json(_file(owner,locator,directory=False,current=current),4096)
            if type(doc) is not dict or set(doc)!={'schema','service_name','control_home'} or doc['schema']!=SCHEMA:
                _bad('LOCATOR_SCHEMA')
            name=doc['service_name'];home=_path(doc['control_home'])
            if type(name) is not str or not re.fullmatch(r'ComsolMCP_[A-Za-z0-9_-]{1,60}',name):
                _bad('SERVICE_NAME')
            if any(o is not owner and o.home and o.home.casefold()==home.casefold() for o in _ROOTS.values()):
                _bad('ORIGINAL_OWNER_RETAINED')
            owner.home=home
            manager=owner.returned('OpenSCManagerW','CloseServiceHandle',None,None,1)
            service=owner.returned('OpenServiceW','CloseServiceHandle',manager,name,1|4)
            before=_service(owner,service,name,home);sid=_service_sid(owner,name)
            _file(owner,home,directory=True,current=current,target=True,service_sid=sid)
            raw=_file(owner,ntpath.join(home,'control.json'),directory=False,current=current,target=True,service_sid=sid)
            values=_json(raw,16384)
            if type(values) is not dict or set(values)!={'port','token','pid','process_start_epoch_ms'}:
                _bad('ENDPOINT_SCHEMA')
            for key,upper in (('port',65535),('pid',2**32-1),('process_start_epoch_ms',2**53-1)):
                if type(values[key]) is not int or not 1<=values[key]<=upper:_bad('ENDPOINT_SCHEMA')
            if type(values['token']) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{43}',values['token']):
                _bad('ENDPOINT_SCHEMA')
            if base64.urlsafe_b64encode(base64.b64decode(values['token']+'=',altchars=b'-_',validate=True)).rstrip(b'=').decode()!=values['token']:
                _bad('ENDPOINT_SCHEMA')
            if values['pid']!=before[0]:_bad('PROCESS_IDENTITY')
            process=owner.returned('OpenProcess','CloseHandle',0x1000,0,values['pid'])
            identity=_process(owner,process,values['pid'])
            if (identity[0]!=values['process_start_epoch_ms'] or ntpath.normcase(identity[1])!=ntpath.normcase(before[1][0])
                    or _user(owner,process)!=SY):_bad('PROCESS_IDENTITY')
            if _service(owner,service,name,home)!=before or _process(owner,process,values['pid'])!=identity:
                _bad('SCM_OR_PROCESS_DRIFT')
            for handle,directory,fp,sd in owner.pins:
                if _fingerprint(owner,handle,directory)!=fp or _descriptor(owner,handle)[0]!=sd:
                    _bad('FILE_DRIFT')
            owner.finish()
            return _Endpoint(owner,values)
        except BaseException as error:
            owner.remember(error)
            try:
                if not owner.retired:owner.finish()
            except BaseException:
                pass  # exact owner/resources remain rooted; no retries
            raise AttachError(getattr(error,'code','SOURCE_UNKNOWN'),owner=owner) from None


def _native_api():
    return _Api()


def verify_endpoint(locator):
    """Windows native source only. No SDK/provider/result argument from wire."""
    return _verify(_path(locator),_native_api())


def _software_verify(locator,exports,error):
    return _verify(locator,_SoftwareApi(exports,error,seal=_SOFTWARE))
