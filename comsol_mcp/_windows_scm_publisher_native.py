"""Finite SCM publisher SDK boundary. No grant/apply/issuer authority.

Only the publisher's original owner may use these objects. Borrowed CRT
handles are observed, never acquired into the native close ledger. Secret
file bytes stay in private buffers, never digests or exception messages.
"""
from __future__ import annotations

import copy
import ctypes as C
import ntpath
import os
import re
import struct

from . import _windows_scm_endpoint as e
from . import _windows_parameter_publish as p

D, B, H = e.D, e.B, e.H
_NATIVE, _SOFTWARE = object(), object()
_OWNERS = {}
SPECS = copy.deepcopy(e.SPECS)
SPECS['kernel32'].update({
    'WriteFile': (B,[H,H,D,C.POINTER(D),H],'BOOL'),
    'FlushFileBuffers': (B,[H],'BOOL'),
    'SetFilePointerEx': (B,[H,C.c_int64,C.POINTER(C.c_int64),D],'BOOL'),
})
SPECS['advapi32'].update({
    'LookupAccountSidW': (B,[C.c_wchar_p,H,C.c_wchar_p,C.POINTER(D),C.c_wchar_p,C.POINTER(D),C.POINTER(D)],'BOOL'),
    'ConvertStringSecurityDescriptorToSecurityDescriptorW': (B,[C.c_wchar_p,D,C.POINTER(H),C.POINTER(D)],'BOOL'),
})
SPECS['ntdll']['NtSetInformationFile'] = (B,[H,C.POINTER(p._IoStatusBlock),H,D,B],'BOOL')
EXPORTS = tuple(k for specs in SPECS.values() for k in specs)
KINDS = {k:v[2] for specs in SPECS.values() for k,v in specs.items()}
NOT_FOUND = C.c_int32(0xC0000034).value


class PublisherError(RuntimeError):
    def __init__(self, code='SCM_PUBLISHER_REFUSED', *, owner=None):
        super().__init__('Original SCM endpoint publisher refused ('+code+').')
        self.code, self.owner = code, owner


def bad(code):
    raise PublisherError(code)


class _Api(e._Api):
    def __init__(self):
        if os.name!='nt' or C.sizeof(H)!=8:bad('WINDOWS_REQUIRED')
        import msvcrt
        self.seal=_NATIVE
        self.libraries=tuple(C.WinDLL(k,use_last_error=True) for k in SPECS)
        self.sdk={}
        for dll,specs in zip(self.libraries,SPECS.values()):
            for name,(result,args,_) in specs.items():
                fn=getattr(dll,name);fn.restype,fn.argtypes=result,args;self.sdk[name]=fn
        self._bindings=dict(self.sdk);self.error=C.get_last_error
        self.get_osfhandle=msvcrt.get_osfhandle
        self._borrow=self.get_osfhandle

    def path(self,value):return e._path(os.fspath(value))

    def check(self):
        super().check()
        if self.get_osfhandle is not self._borrow:bad('CRT_BINDING')


class _SoftwareApi(_Api):
    def __init__(self,exports,error,borrow,paths,*,seal):
        if seal is not _SOFTWARE or set(exports)!=set(EXPORTS):bad('SOFTWARE_BOUNDARY')
        self.seal=_SOFTWARE;self.sdk=dict(exports);self._bindings=dict(exports)
        self.error=error;self.libraries=(exports,)
        self.get_osfhandle=self._borrow=borrow
        self._paths=dict(paths)

    def path(self,value):return e._path(self._paths.get(os.fspath(value),os.fspath(value)))


class _Call(e._Call):
    def run(self):
        self.owner.check()
        if self.function is not self._binding[0] or self.args is not self._binding[1] or self.entered:bad('SDK_REPLAY_OR_BINDING')
        self.entered=True
        try:
            self.raw=self.function(*self.args)
            raw=getattr(self.raw,'value',self.raw);kind=KINDS[self.name]
            if kind=='PTR' and raw is None:value=None
            else:
                low,high=((-2**31,2**31-1) if kind=='BOOL' else (0,2**32-1) if kind=='DWORD' else (0,2**64-1))
                if type(raw) is not int or not low<=raw<=high:bad('SDK_RESULT_UNKNOWN')
                value=raw
            if kind=='PSEUDO' and value!=H(-1 if self.name=='GetCurrentProcess' else -2).value:bad('SDK_RESULT_UNKNOWN')
            error=self.owner.api.error()
            if type(error) is not int or not 0<=error<2**32:bad('SDK_RESULT_UNKNOWN')
            self._result,self.error=(value,),error
            return value
        except BaseException as failure:
            self.owner.opaque=True;self.owner.remember(failure)
            raise


class _Owner(e._Owner):
    def __init__(self,api,path):
        self.api=self._api=api;self.locator=path;self.home=None
        self.calls,self.resources,self.pins=[],[],[]
        self._calls,self._resources=self.calls,self.resources
        self.opaque,self.first_cause,self.retired=False,None,False
        self.thread=e.threading.get_ident();self._roots=_OWNERS;self._error=api.error
        self._origin=api.seal;self._bindings=dict(api.sdk);self._retiring=False
        self.holds=[]
        _OWNERS[id(self)]=self

    def call(self,name,*args):
        call=_Call(self,name,args);return call.run(),call

    def check(self):
        if (self.api is not self._api or _OWNERS is not self._roots or _OWNERS.get(id(self)) is not self
                or self.calls is not self._calls or self.resources is not self._resources
                or self.api.seal is not self._origin or e.threading.get_ident()!=self.thread
                or self.api.error is not self._error or any(self.api.sdk.get(k) is not v for k,v in self._bindings.items())):bad('OWNER_BINDING')
        self.api.check()
        if self.opaque or any(c.entered and c.result is None for c in self.calls):bad('ORIGINAL_OWNER_UNKNOWN')
        for resource in self.resources:
            if resource.owned:resource.check()

    def finish(self):
        self.check()
        for resource in reversed(self.resources):resource.release()
        self.checkpoint('retire_ack',self)
        self._retiring=True
        try:_OWNERS.pop(id(self));self.retired=True
        except BaseException:
            _OWNERS[id(self)]=self
            raise


def label(raw):
    if type(raw) is not bytes or not 20<=len(raw)<=65536:bad('LABEL_BOUNDS')
    revision,_,control,_,_,offset,_=struct.unpack_from('<BBHIIII',raw)
    if revision!=1 or not control&0x8000 or not control&0x10 or not 20<=offset<=len(raw)-8:bad('LABEL_REQUIRED')
    rev,_,size,count,_=struct.unpack_from('<BBHHH',raw,offset)
    if rev not in (2,4) or count!=1 or not 24<=size<=len(raw)-offset:bad('LABEL_SHAPE')
    kind,flags,length,mask=struct.unpack_from('<BBHI',raw,offset+8)
    sid,end=e.b._sid(raw,offset+16)
    if kind!=17 or flags&~0x10 or length!=size-8 or end!=offset+size or mask!=1:bad('LABEL_POLICY')
    if not sid.startswith('S-1-16-') or sid.count('-')!=3:bad('LABEL_POLICY')
    level=int(sid.rsplit('-',1)[1])
    if not 8192<=level<=16384:bad('LABEL_POLICY')
    return level


def template(raw,service_sid,*,directory,reader=None):
    sd=e.b.decode_sd(raw)
    if sd['owner']!=e.SY or sd['group'] not in (e.SY,e.BA) or not sd['control']&0x1000:bad('HOME_TEMPLATE')
    seen={};extra=[]
    for kind,flags,mask,sid in sd['aces']:
        if kind!=0 or sid in seen:bad('CLOSED_STATE_DACL')
        seen[sid]=(flags,mask)
        if sid in (e.SY,e.BA):
            if (flags,mask)!=((3 if directory else 0),0x1F01FF):bad('CLOSED_STATE_DACL')
        elif sid==service_sid:
            if flags!=(3 if directory else 0) or not mask or mask&~0x1301BF:bad('SERVICE_DATA_MASK')
        else:
            if sid=='S-1-1-0' or sid.startswith(('S-1-15-','S-1-5-80-')):bad('CLIENT_ACCOUNT_ROLE')
            if flags or mask!=(0x1200A0 if directory else 0x120089):bad('CLIENT_DATA_MASK')
            extra.append(sid)
    if not {e.SY,e.BA,service_sid}<=set(seen) or len(extra)>1:bad('CLOSED_STATE_DACL')
    actual=extra[0] if extra else None
    if not directory and actual!=reader:bad('ENDPOINT_READER')
    return actual


class _Namespace:
    """One original SDK/handle graph; no native lock ownership is inferred."""
    def __init__(self,api,home,service_name):
        if not ((type(api) is _Api and api.seal is _NATIVE) or (type(api) is _SoftwareApi and api.seal is _SOFTWARE)):bad('API_ORIGIN')
        self.api=api;self.home=api.path(home);self.owner=_Owner(api,self.home)
        self.owner.home=self.home;self.name=service_name
        self.parent=None;self.writer_parent=None;self.reader=None;self.sid=None
        self._home_fact=None;self._borrowed=None;self._effects={}
        self._absence=None;self._actor_fact=None;self._own_stage_name=None
        self._created_stage=None

    def __repr__(self):return '<original private SCM publisher namespace>'

    def _label(self,handle):
        o=self.owner
        sd=o.output('GetSecurityInfo','LocalFree',lambda out:(handle,1,16,None,None,None,None,C.byref(out)),status=True)
        e._readable(o,sd.value,20);size,_=o.call('GetSecurityDescriptorLength',sd)
        e._readable(o,sd.value,size);raw=C.string_at(sd,size)
        return raw,label(raw)

    def _security(self,handle,directory):
        raw,_=e._descriptor(self.owner,handle)
        template(raw,self.sid,directory=directory,reader=self.reader)
        mic,level=self._label(handle)
        return raw,mic,level

    def _user_type(self,sid):
        o=self.owner;raw=e.b._sid_bytes(sid);buffer=C.create_string_buffer(raw)
        n,domain,kind=D(),D(),D();o.holds.append(buffer)
        ok,call=o.call('LookupAccountSidW',None,buffer,None,C.byref(n),None,C.byref(domain),C.byref(kind))
        if ok or call.error!=122 or not 1<n.value<=32768 or domain.value>32768:bad('READER_ACCOUNT_TYPE')
        name=C.create_unicode_buffer(n.value);realm=C.create_unicode_buffer(max(1,domain.value))
        cap,dcap=n.value,domain.value
        o.bool('LookupAccountSidW',None,buffer,name,C.byref(n),realm,C.byref(domain),C.byref(kind))
        # Docs define required-size output on insufficient buffer, not a new
        # success-count convention. Use bounded terminated buffers and SidTypeUser.
        if (kind.value!=1 or not 0<n.value<=cap or domain.value>dcap
                or not 0<len(name.value)<cap or len(realm.value)>=max(1,dcap)):bad('READER_ACCOUNT_TYPE')

    def actor(self,runtime):
        from ._windows_t037_broker import _require_original_runtime,CtypesWindowsScmApi
        _require_original_runtime(runtime,running=False)
        if self.api.seal is _NATIVE and type(runtime._broker._api) is not CtypesWindowsScmApi:bad('NATIVE_SCM_SOURCE')
        o=self.owner;process,_=o.call('GetCurrentProcess')
        if e._user(o,H(process))!=e.SY:bad('SYSTEM_REQUIRED')
        thread,_=o.call('GetCurrentThread');token=e._Resource(o,'CloseHandle')
        ok,call=o.call('OpenThreadToken',H(thread),8,1,C.byref(token.storage))
        if ok:
            token.capture(call,True);bad('IMPERSONATION_REFUSED')
        if call.error!=1008 or token.storage.value is not None:token.capture(call,False)
        token.call=token._call=call;token.closed=token.empty=True
        self.pid=os.getpid();self.birth,self.image=e._process(o,H(process),self.pid)
        manager=o.returned('OpenSCManagerW','CloseServiceHandle',None,None,1)
        service=o.returned('OpenServiceW','CloseServiceHandle',manager,self.name,5)
        config=e._buffer(o,'QueryServiceConfigW',lambda b,n,u:(service,b,n,u));row=e.Config.from_buffer_copy(config)
        account=e.text(config,len(config),row.account);binary=e.text(config,len(config),row.binary)
        args=e._argv(o,binary)
        if row.service_type!=16 or account.casefold() not in ('localsystem','nt authority\\system'):bad('SCM_SOURCE')
        if len(args)<9 or args[1:5]!=['-B','-I','-m','comsol_mcp._control_daemon'] or len(args[5:])%2:bad('SCM_SOURCE')
        options=dict(zip(args[5::2],args[6::2]))
        allowed={'--home','--windows-scm-service-name','--windows-t037-profile','--host-provider-config',
            '--disk-resource-config','--memory-resource-config','--windows-scm-bootstrap-request'}
        if not set(options)<=allowed or '--windows-t037-profile' not in options:bad('SCM_SOURCE')
        if len(options)*2!=len(args)-5 or options.get('--home')!=self.home or options.get('--windows-scm-service-name')!=self.name or ntpath.normcase(args[0])!=ntpath.normcase(self.image):bad('SCM_SOURCE')
        sid_mode=e._buffer(o,'QueryServiceConfig2W',lambda b,n,u:(service,5,b,n,u))
        if len(sid_mode)!=C.sizeof(e.SidInfo) or e.SidInfo.from_buffer_copy(sid_mode).sid_type!=3:bad('SCM_SOURCE')
        self.sid=e._service_sid(o,self.name);self._service=service;self._argv=tuple(args)
        self._profile_path=e._path(options['--windows-t037-profile'])
        actual=(self.pid,self.birth,self.image,self.sid,self._argv)
        if self._actor_fact is not None and actual!=self._actor_fact:bad('ACTOR_SOURCE_DRIFT')
        self._actor_fact=actual
        return self.pid,self.birth

    def prepare_home(self):
        o=self.owner;chain=self._open_home_chain()
        for index,h in enumerate(chain):
            fp=e._fingerprint(o,h,True);raw,sd=e._descriptor(o,h)
            if index==len(chain)-1:
                self.reader=template(raw,self.sid,directory=True)
                if self.reader:self._user_type(self.reader)
                mic,level=self._label(h);self._home_fact=(fp[:2],raw,mic,level)
            else:e._policy(sd,e.SY,final=False,target=True,service_sid=self.sid)
        fs=C.create_unicode_buffer(256);flags,serial,maximum=D(),D(),D()
        o.bool('GetVolumeInformationW',self.home[:3],None,0,C.byref(serial),C.byref(maximum),C.byref(flags),fs,len(fs))
        if not flags.value&8:bad('PERSISTENT_ACLS_REQUIRED')
        self.parent=chain[-1];self._home_chain=tuple(chain)
        lock=self.open('control.lock',share=3)
        if lock is not None:
            e._fingerprint(o,lock,False)
            raw,_=e._descriptor(o,lock);template(raw,self.sid,directory=False,reader=None)
            self._label(lock);self.release(lock)
        return self

    def _open_home_chain(self):
        o=self.owner;path=self.home
        chain=[o.returned('CreateFileW','CloseHandle',path[:3],0x20080,3,None,3,0x2200000,None)]
        for leaf in path[3:].split('\\'):
            raw=leaf.encode('utf-16-le');buffer=C.create_string_buffer(raw+b'\0\0')
            name=p._UnicodeString(len(raw),len(raw)+2,C.cast(buffer,C.POINTER(C.c_wchar)))
            attrs=p._ObjectAttributes(C.sizeof(p._ObjectAttributes),chain[-1],C.pointer(name),0x1040,None,None)
            io=p._IoStatusBlock();io.Status=0x7fffffff;io.Information=C.c_size_t(-1).value
            r=e._Resource(o,'CloseHandle');o.holds.append((buffer,name,attrs,io))
            status,call=o.call('NtCreateFile',C.byref(r.storage),0x1200A0,C.byref(attrs),C.byref(io),None,0,3,1,1|0x20|0x200000,None,0)
            if status>0:o.opaque=r.unknown=True;bad('HOME_ACQUISITION_UNKNOWN')
            handle=r.capture(call,status==0)
            if io.Status!=0 or io.Information!=1:o.opaque=True;bad('HOME_ACQUISITION_UNKNOWN')
            chain.append(handle)
        return chain

    def _write_parent(self):
        if self.writer_parent is not None:bad('PARENT_WRITER_REUSED')
        o=self.owner;leaf=ntpath.basename(self.home)
        buffer=C.create_string_buffer(leaf.encode('utf-16-le')+b'\0\0')
        name=p._UnicodeString(len(buffer.raw)-3,len(buffer.raw)-1,C.cast(buffer,C.POINTER(C.c_wchar)))
        attrs=p._ObjectAttributes(C.sizeof(p._ObjectAttributes),self._home_chain[-2],C.pointer(name),0x1040,None,None)
        io=p._IoStatusBlock();io.Status=0x7fffffff;io.Information=C.c_size_t(-1).value
        r=e._Resource(o,'CloseHandle');o.holds.append((buffer,name,attrs,io))
        status,call=o.call('NtCreateFile',C.byref(r.storage),0x1200A2,C.byref(attrs),C.byref(io),None,0,3,1,1|0x20|0x200000,None,0)
        if status>0:
            o.opaque=r.unknown=True;bad('PARENT_WRITER_UNKNOWN')
        h=r.capture(call,status==0)
        if io.Status!=0 or io.Information!=1:o.opaque=True;bad('PARENT_WRITER_UNKNOWN')
        fp=e._fingerprint(o,h,True);raw,mic,level=self._security(h,True)
        if (fp[:2],raw,mic,level)!=self._home_fact:bad('HOME_DRIFT')
        self.writer_parent=h

    def close_write_parent(self):
        if self.writer_parent is not None:self.release(self.writer_parent)

    def open(self,leaf,*,write=False,delete=False,create_sd=None,share=5):
        if type(leaf) is not str or leaf not in ('control.lock','control.json',self._own_stage_name):bad('CLOSED_NAMESPACE_LEAF')
        if '/' in leaf or '\\' in leaf:bad('CLOSED_NAMESPACE_LEAF')
        p._component(leaf);o=self.owner
        buffer=C.create_string_buffer(leaf.encode('utf-16-le')+b'\0\0')
        name=p._UnicodeString(len(buffer.raw)-3,len(buffer.raw)-1,C.cast(buffer,C.POINTER(C.c_wchar)))
        parent=self.writer_parent if create_sd else self.parent
        attrs=p._ObjectAttributes(C.sizeof(p._ObjectAttributes),parent,C.pointer(name),0x1040,
            create_sd or None,None)
        io=p._IoStatusBlock();io.Status=0x7fffffff;io.Information=C.c_size_t(-1).value
        r=e._Resource(o,'CloseHandle');o.holds.append((buffer,name,attrs,io,create_sd))
        access=0x120089|(0x10000 if delete else 0)|(0x116 if write else 0)
        status,call=o.call('NtCreateFile',C.byref(r.storage),access,C.byref(attrs),C.byref(io),None,0,share,
            2 if create_sd else 1,0x40|0x20|0x200000,None,0)
        if status>0:
            o.opaque=r.unknown=True;bad('ACQUISITION_COMPLETION_UNKNOWN')
        if not create_sd and status==NOT_FOUND and r.storage.value is None:
            r.call=r._call=call;r.closed=r.empty=True
            self._absence=(call,r)
            return None
        handle=r.capture(call,status==0)
        if io.Status!=0 or io.Information!=(2 if create_sd else 1):
            o.opaque=True;bad('ACQUISITION_COMPLETION_UNKNOWN')
        return handle

    def release(self,handle):
        matches=[r for r in self.owner.resources if r.storage is handle and r.owned]
        if len(matches)!=1:bad('ORIGINAL_OWNED_HANDLE_REQUIRED')
        matches[0].release()

    def fingerprint(self,handle):return e._fingerprint(self.owner,handle,False)

    def owned_file(self,handle,access):
        self.owner.check()
        rows=[r for r in self.owner.resources if r.storage is handle and r.owned and not r.closed]
        if len(rows)!=1:bad('ORIGINAL_FILE_HANDLE_REQUIRED')
        r=rows[0];r.check()
        if r.call is None or r.call.name!='NtCreateFile' or r.call.result!=(0,) or r.call.args[1]&access!=access:bad('ORIGINAL_FILE_ACCESS_REQUIRED')
        return r

    def bytes(self,handle):
        self.owned_file(handle,1)
        size=self.fingerprint(handle)[2]
        self.owner.bool('SetFilePointerEx',handle,0,None,0)
        return e._read_file(self.owner,handle,size,16384)

    def inspect(self,handle):
        first=self.fingerprint(handle);raw,mic,level=self._security(handle,False);data=self.bytes(handle)
        if self.fingerprint(handle)!=first:bad('FILE_DRIFT')
        return first,raw,mic,level,data

    def borrow_stream(self,stream):
        if stream.closed:bad('ORIGINAL_STREAM_CLOSED')
        fd=stream.fileno();before=os.fstat(fd);raw=self.api.get_osfhandle(fd)
        if type(raw) is not int or not 0<raw<H(-1).value:bad('BORROWED_HANDLE_UNKNOWN')
        borrowed=H(raw);leaf=self.open('control.lock',share=3)
        if leaf is None:bad('SINGLETON_NAME_MISSING')
        if self.fingerprint(leaf)[:2]!=self.fingerprint(borrowed)[:2]:bad('SINGLETON_IDENTITY')
        security,_=e._descriptor(self.owner,borrowed);template(security,self.sid,directory=False,reader=None)
        mic,level=self._label(borrowed)
        if os.fstat(fd)!=before or self.api.get_osfhandle(fd)!=raw or stream.fileno()!=fd:bad('SINGLETON_STREAM_DRIFT')
        self.release(leaf)
        self._borrowed=(stream,fd,borrowed,before)
        self._lock_fact=(self.fingerprint(borrowed),security,mic,level)

    def check(self):
        self.owner.check()
        fp=e._fingerprint(self.owner,self.parent,True);raw,mic,level=self._security(self.parent,True)
        if (fp[:2],raw,mic,level)!=self._home_fact:bad('HOME_DRIFT')
        if self._borrowed:
            stream,fd,h,initial=self._borrowed
            if (stream.closed or stream.fileno()!=fd or self.api.get_osfhandle(fd)!=h.value
                    or os.fstat(fd)!=initial):bad('SINGLETON_STREAM_DRIFT')
            raw,_=e._descriptor(self.owner,h);mic,level=self._label(h)
            if (self.fingerprint(h),raw,mic,level)!=self._lock_fact:bad('SINGLETON_IDENTITY_DRIFT')

    def create_stage(self,leaf):
        if self._own_stage_name is not None or not re.fullmatch(r'\.scm-endpoint-[0-9a-f]{32}\.tmp',leaf):bad('STAGE_NAME_OR_REPLAY')
        self._own_stage_name=leaf
        o=self.owner
        self._write_parent()
        text='O:SYG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1301bf;;;'+self.sid+')'
        if self.reader:text+='(A;;0x120089;;;'+self.reader+')'
        text+='S:(ML;;NW;;;ME)'
        size=D();sd=o.output('ConvertStringSecurityDescriptorToSecurityDescriptorW','LocalFree',lambda out:(text,1,C.byref(out),C.byref(size)))
        if not 20<=size.value<=65536:bad('CREATE_SD_BOUNDS')
        handle=self.open(leaf,write=True,delete=True,create_sd=sd)
        self._created_stage=handle
        if self.fingerprint(handle)[0]!=self._home_fact[0][0]:bad('STAGE_VOLUME')
        return handle

    def write(self,handle,data):
        self.owned_file(handle,2)
        if type(data) is not bytes or not 1<=len(data)<=16384:bad('PAYLOAD_BOUNDS')
        buffer=C.create_string_buffer(data);used=D();self.owner.holds.append((buffer,used))
        try:
            self.owner.bool('WriteFile',handle,buffer,len(data),C.byref(used),None)
            if used.value!=len(data):bad('SHORT_WRITE')
            self.owner.bool('FlushFileBuffers',handle)
        except BaseException:
            self.owner.opaque=True
            raise

    def effect(self,handle,*,rename):
        self.owned_file(handle,0x10000)
        effect_name='rename' if rename else 'delete'
        if effect_name in self._effects:bad('EFFECT_REPLAY')
        if rename:
            encoded='control.json'.encode('utf-16-le');offset=p._RenameInfo.FileName.offset
            buffer=C.create_string_buffer(max(C.sizeof(p._RenameInfo),offset+len(encoded)+2))
            info=C.cast(buffer,C.POINTER(p._RenameInfo)).contents
            if self.writer_parent is None:bad('PARENT_WRITER_REQUIRED')
            info.ReplaceIfExists=1;info.RootDirectory=self.writer_parent;info.FileNameLength=len(encoded)
            C.memmove(C.addressof(buffer)+offset,encoded,len(encoded));kind=10
        else:
            buffer=p._DispositionInfo(1);kind=13
        io=p._IoStatusBlock();io.Status=0x7fffffff;io.Information=C.c_size_t(-1).value
        call=_Call(self.owner,'NtSetInformationFile',(handle,C.byref(io),C.byref(buffer),C.sizeof(buffer),kind))
        record=(call,handle,io,buffer,kind)
        self._effects[effect_name]=record
        try:
            status=call.run()
            if status!=0 or io.Status!=0 or io.Information>C.sizeof(buffer):bad('EFFECT_COMPLETION_UNKNOWN')
        except BaseException:
            self.owner.opaque=True
            raise
        return record

    def finish(self):self.owner.finish()
