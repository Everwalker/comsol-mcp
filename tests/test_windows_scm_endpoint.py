"""SOFTWARE_ONLY raw Win64 exports; no Windows/HTTP/native observations."""
import base64
import ctypes as C
import gc
import json
import ntpath
import struct
import subprocess
from types import SimpleNamespace
import weakref

import pytest
from comsol_mcp import _windows_scm_endpoint as m
from comsol_mcp import _control_client as client
from comsol_mcp._execution_contract import ExecutionContractError

LOCATOR=r'C:\Users\Everwalker\private\locator.json'
HOME=r'C:\ProgramData\ComsolMCP_Test\service-state'
CLIENT='S-1-5-21-100-200-300-1001'
SERVICE='S-1-5-80-1-2-3-4-5'


def sid(value):
    parts=value.split('-');subs=[int(v) for v in parts[3:]]
    return bytes((1,len(subs)))+int(parts[2]).to_bytes(6,'big')+b''.join(v.to_bytes(4,'little') for v in subs)


def descriptor(owner,*,protected=True,extra=()):
    entries=[(m.SY,0x1F01FF),(m.BA,0x1F01FF)]
    if owner==CLIENT:entries.append((CLIENT,0x1F01FF))
    else:entries.extend(((CLIENT,0x120089),(SERVICE,0x120089)))
    entries.extend(extra)
    aces=b''.join(struct.pack('<BBHI',0,0,8+len(sid(s)),mask)+sid(s) for s,mask in entries)
    acl=struct.pack('<BBHHH',2,0,8+len(aces),len(entries),0)+aces
    o,g=sid(owner),sid(m.BA)
    return struct.pack('<BBHIIII',1,0,0x8004|(0x1000 if protected else 0),20,20+len(o),0,20+len(o)+len(g))+o+g+acl


class SDK:
    def __init__(self):
        self.calls=[];self.keep=[];self.handles={};self.regions={};self.seq=100
        self.wait_calls=[];self.process_calls=[]
        self.last_error=0;self.fault=None;self.native_effects=[]
        self.secret=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode()
        self.locator={'schema':m.SCHEMA,'service_name':'ComsolMCP_Test','control_home':HOME}
        self.endpoint={'pid':1234,'process_start_epoch_ms':2000,'port':4321,'token':self.secret}
        self.argv=[r'C:\Program Files\Python312\python.exe','-B','-I','-m','comsol_mcp._control_daemon',
                   '--home',HOME,'--windows-scm-service-name','ComsolMCP_Test']
        self.config_calls=0;self.fp_calls={};self.read_positions={}
        callbacks={
            'GetCurrentProcess':lambda:m.H(-1).value,'GetCurrentThread':lambda:m.H(-2).value,
            'OpenThreadToken':self.no_thread,'OpenProcessToken':self.open_token,'GetTokenInformation':self.token_info,
            'CreateFileW':self.root,'NtCreateFile':self.relative,'GetFileInformationByHandleEx':self.info,
            'GetSecurityInfo':self.security,'GetSecurityDescriptorLength':self.sd_length,'VirtualQuery':self.region,
            'ReadFile':self.read,'GetVolumeInformationW':self.volume,
            'CloseHandle':self.close,'CloseServiceHandle':self.close,'LocalFree':self.free,
            'OpenSCManagerW':lambda *a:self.handle(('manager',None)),
            'OpenServiceW':self.open_service,'QueryServiceConfigW':self.config,
            'QueryServiceConfig2W':self.sid_mode,'QueryServiceStatusEx':self.status,
            'LookupAccountNameW':self.lookup,'CommandLineToArgvW':self.args,'LocalSize':self.local_size,
            'OpenProcess':self.process,'GetProcessId':lambda h:1234,
            'WaitForSingleObject':self.wait,
            'GetProcessTimes':self.times,'QueryFullProcessImageNameW':self.image,
        }
        assert set(callbacks)==set(m.READONLY_SDK)
        self.exports={name:self.function(name,fn) for name,fn in callbacks.items()}

    def function(self,name,fn):
        def run(*args):
            self.calls.append(name)
            return fn(*args)
        return run
    def address(self,value):return getattr(value,'value',value)
    def handle(self,value):
        self.seq+=1;self.handles[self.seq]=value;return self.seq
    def alloc(self,raw):
        buffer=C.create_string_buffer(raw);self.keep.append(buffer)
        self.regions[C.addressof(buffer)]=buffer;return C.addressof(buffer)
    def payload(self,path):
        if path==LOCATOR:return json.dumps(self.locator).encode()
        if path==ntpath.join(HOME,'control.json'):return json.dumps(self.endpoint).encode()
        return b''
    def no_thread(self,*args):self.last_error=1008;return 0
    def open_token(self,process,rights,out):
        assert rights==8
        value=self.handle(('token',CLIENT if self.address(process)==m.H(-1).value else m.SY))
        C.cast(out,C.POINTER(m.H))[0]=value
        return 1
    def token_info(self,handle,kind,buffer,size,used):
        assert kind==1;raw=sid(self.handles[self.address(handle)][1]);floor=C.sizeof(m._SidAttributes)
        row=m._SidAttributes.from_buffer(buffer);row.sid=C.addressof(buffer)+floor
        C.memmove(row.sid,raw,len(raw));C.cast(used,C.POINTER(m.D))[0]=floor+len(raw);return 1
    def root(self,path,access,share,security,disposition,flags,template):
        assert access==0x20080 and share==3 and disposition==3 and flags==0x2200000
        return self.handle(('file',path))
    def relative(self,out,access,attributes,io,*args):
        assert access in (0x1200A0,0x120089) and args[3]==1
        a=C.cast(attributes,C.POINTER(m.p._ObjectAttributes)).contents
        assert a.Attributes==0x1040
        leaf=C.string_at(a.ObjectName.contents.Buffer,a.ObjectName.contents.Length).decode('utf-16-le')
        path=ntpath.join(self.handles[self.address(a.RootDirectory)][1],leaf)
        value=self.handle(('file',path));C.cast(out,C.POINTER(m.H))[0]=value
        C.cast(io,C.POINTER(m.p._IoStatusBlock)).contents.Information=1
        if self.fault=='acquire_effect_raise':raise OSError('software lost output')
        return 0
    def info(self,h,kind,out,size):
        path=self.handles[self.address(h)][1];directory=path not in (LOCATOR,ntpath.join(HOME,'control.json'))
        self.fp_calls[path]=self.fp_calls.get(path,0)+1
        if kind==18:
            row=C.cast(out,C.POINTER(m.p._FullIdInfo)).contents;row.volume=7
            value=sum(path.encode())+1
            if self.fault=='identity_drift' and path==LOCATOR and self.fp_calls[path]>9:value+=1
            for i,v in enumerate(value.to_bytes(16,'little')):row.file_id[i]=v
        elif kind==0:
            row=C.cast(out,C.POINTER(m.p._BasicInfo)).contents
            row.created=111;row.written=222;row.changed=333;row.attributes=16 if directory else 128
            if self.fault=='reparse' and path==HOME:row.attributes|=0x400
        elif kind==1:
            row=C.cast(out,C.POINTER(m.p._StandardInfo)).contents
            row.directory=directory;row.links=1;row.size=len(self.payload(path))
        else:raise AssertionError('unexpected information class')
        return 1
    def security(self,h,kind,flags,owner,group,dacl,sacl,out):
        assert kind==1 and flags==7
        path=self.handles[self.address(h)][1]
        user=CLIENT if path.startswith(r'C:\Users') else m.SY
        if self.fault=='target_owner' and path==HOME:user=CLIENT
        if self.fault=='locator_owner' and path==LOCATOR:user=m.SY
        if self.fault=='target_ancestor_owner' and path==r'C:\ProgramData':user=CLIENT
        extra=()
        if self.fault=='foreign_reader' and path==HOME:extra=(('S-1-5-11',0x120089),)
        if self.fault=='client_writer' and path==HOME:extra=((CLIENT,0x120116),)
        if self.fault=='target_ancestor_writer' and path==r'C:\ProgramData':extra=((CLIENT,0x1F01FF),)
        raw=descriptor(user,protected=not(self.fault=='unprotected' and path==HOME),extra=extra)
        C.cast(out,C.POINTER(m.H))[0]=self.alloc(raw)
        if self.fault=='sd_effect_raise':raise OSError('software lost allocation result')
        if self.fault=='sd_failure_output':return 5
        return 0
    def sd_length(self,pointer):return len(self.regions[self.address(pointer)].raw)-1
    def region(self,pointer,out,size):
        address=self.address(pointer);found=next(((a,b) for a,b in self.regions.items() if a<=address<a+C.sizeof(b)),None)
        if found is None:return 0
        a,b=found;row=C.cast(out,C.POINTER(m._MemoryInfo)).contents
        row.base=a;row.size=C.sizeof(b);row.state=0x1000;row.protect=4;return C.sizeof(row)
    def read(self,h,buffer,size,used,overlapped):
        address=self.address(h);raw=self.payload(self.handles[address][1]);pos=self.read_positions.get(address,0)
        block=raw[pos:pos+size];C.memmove(buffer,block,len(block));C.cast(used,C.POINTER(m.D))[0]=len(block)
        self.read_positions[address]=pos+len(block);return 1
    def volume(self,path,name,count,serial,maximum,flags,fs,size):
        C.cast(serial,C.POINTER(m.D))[0]=7;C.cast(flags,C.POINTER(m.D))[0]=8;fs.value='NTFS';return 1
    def close(self,handle):
        self.native_effects.append(('close',self.address(handle)))
        if self.fault=='close_effect_raise':raise OSError('software close effect then lost return')
        if self.fault=='close_zero':return 0
        return 1
    def free(self,handle):
        self.native_effects.append(('free',self.address(handle)))
        if self.fault=='free_effect_raise':raise OSError('software free effect then lost return')
        if self.fault=='free_foreign':return self.address(handle)+1
        return None
    def open_service(self,manager,name,rights):
        assert name=='ComsolMCP_Test' and rights==5
        return self.handle(('service',name))
    def config(self,service,buffer,size,used):
        strings=[subprocess.list2cmdline(self.argv),'LocalSystem']
        required=C.sizeof(m.Config)+sum(len(t.encode('utf-16-le'))+2 for t in strings)
        C.cast(used,C.POINTER(m.D))[0]=required
        if buffer is None:self.last_error=122;return 0
        row=m.Config.from_buffer(buffer);row.service_type=16;row.start_type=3;row.error_control=1
        offset=C.sizeof(m.Config)
        for attr,value in zip(('binary','account'),strings):
            data=value.encode('utf-16-le')+b'\0\0';C.memmove(C.addressof(buffer)+offset,data,len(data))
            setattr(row,attr,C.addressof(buffer)+offset);offset+=len(data)
        return 1
    def sid_mode(self,service,level,buffer,size,used):
        assert level==5;C.cast(used,C.POINTER(m.D))[0]=4
        if buffer is None:self.last_error=122;return 0
        m.SidInfo.from_buffer(buffer).sid_type=1 if self.fault=='sid_unrestricted' else 3;return 1
    def status(self,service,level,buffer,size,used):
        row=C.cast(buffer,C.POINTER(m.Status)).contents;row.service_type=16
        row.state=1 if self.fault=='service_stopped' else 4
        row.process_id=1235 if self.fault=='service_pid' else 1234
        C.cast(used,C.POINTER(m.D))[0]=C.sizeof(m.Status);return 1
    def lookup(self,system,name,buffer,n,domain,dn,use):
        assert name=='NT SERVICE\\ComsolMCP_Test';raw=sid(SERVICE)
        C.cast(n,C.POINTER(m.D))[0]=len(raw);C.cast(dn,C.POINTER(m.D))[0]=0
        if buffer is None:self.last_error=122;return 0
        C.memmove(buffer,raw,len(raw));return 1
    def args(self,binary,count):
        assert binary==subprocess.list2cmdline(self.argv)
        encoded=[s.encode('utf-16-le')+b'\0\0' for s in self.argv]
        raw_size=len(encoded)*8+sum(map(len,encoded));buffer=C.create_string_buffer(raw_size)
        base=C.addressof(buffer);offset=len(encoded)*8
        for i,data in enumerate(encoded):
            C.memmove(base+i*8,(base+offset).to_bytes(8,'little'),8);C.memmove(base+offset,data,len(data));offset+=len(data)
        self.keep.append(buffer);self.regions[base]=buffer
        C.cast(count,C.POINTER(m.B))[0]=len(encoded);return base
    def local_size(self,pointer):return C.sizeof(self.regions[self.address(pointer)])
    def process(self,rights,inherit,pid):
        assert rights==0x101000 and inherit==0 and pid==1234
        self.process_calls.append((rights,inherit,pid))
        return self.handle(('process',pid))
    def wait(self,handle,milliseconds):
        assert type(milliseconds) is int and milliseconds==0
        assert self.handles[self.address(handle)]==('process',1234)
        self.wait_calls.append((handle,milliseconds))
        return 258
    def times(self,handle,created,exited,kernel,user):
        birth=116444736000000000+(2001 if self.fault=='birth' else 2000)*10000
        C.memmove(created,birth.to_bytes(8,'little'),8);return 1
    def image(self,handle,flags,buffer,length):
        buffer.value=self.argv[0] if self.fault!='image' else r'C:\other\python.exe'
        C.cast(length,C.POINTER(m.D))[0]=len(buffer.value);return 1


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setattr(m,'_ROOTS',{})
    value=SDK();return value


def verify(sdk):return m._software_verify(LOCATOR,sdk.exports,lambda:sdk.last_error)


def test_complete_raw_reader_is_software_only_original_cleanup_and_secret_safe(sdk):
    endpoint=verify(sdk)
    assert endpoint._origin is m._SOFTWARE and endpoint['port']==4321
    assert sdk.secret not in repr(endpoint) and not m._ROOTS
    for name in ('OpenSCManagerW','OpenServiceW','GetProcessTimes','LookupAccountNameW','QueryServiceConfigW'):
        assert name in sdk.calls
    assert len(sdk.native_effects)==len(set(sdk.native_effects))


@pytest.mark.parametrize('fault',['target_owner','locator_owner','foreign_reader','client_writer','unprotected',
    'identity_drift','reparse','sid_unrestricted','service_stopped','service_pid','birth','image',
    'target_ancestor_owner','target_ancestor_writer'])
def test_real_parsers_refuse_wrong_sources_before_any_http(sdk,fault):
    sdk.fault=fault
    with pytest.raises(m.AttachError) as exc:verify(sdk)
    assert sdk.secret not in str(exc.value)


@pytest.mark.parametrize('field,value',[('port',True),('port',0),('port',65536),('pid',False),
    ('process_start_epoch_ms',0),('token','bad'),('extra','ignored')],
    ids=['boolean-port','zero-port','overflow-port','boolean-pid','zero-birth','invalid-secret','extra-field'])
def test_endpoint_closed_types(sdk,field,value):
    sdk.endpoint[field]=value
    with pytest.raises(m.AttachError):verify(sdk)


@pytest.mark.parametrize('fault',['acquire_effect_raise','sd_effect_raise','sd_failure_output',
    'close_effect_raise','close_zero','free_effect_raise','free_foreign'])
def test_original_unknown_is_rooted_no_reacquire_or_explicit_release_retry(sdk,fault):
    sdk.fault=fault
    with pytest.raises(m.AttachError) as exc:verify(sdk)
    owner=exc.value.owner;ref=weakref.ref(owner);del exc;gc.collect()
    assert ref() is owner and owner in m._ROOTS.values()
    before=list(sdk.calls)
    with pytest.raises(m.AttachError):m.recover_owned(owner)
    with pytest.raises(m.AttachError):verify(sdk)
    assert sdk.calls==before
    assert sdk.secret not in repr(owner)


def test_known_close_success_ack_uses_only_original_cached_result(sdk,monkeypatch):
    api=m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE)
    owner=m._Owner(api,LOCATOR)
    handle=owner.returned('OpenProcess','CloseHandle',0x101000,0,1234)
    def fail(event,original):raise RuntimeError('software bookkeeping failure')
    monkeypatch.setattr(owner,'checkpoint',fail)
    with pytest.raises(RuntimeError):owner.finish()
    before=list(sdk.calls);assert owner.resources[0].confirmed
    monkeypatch.setattr(owner,'checkpoint',lambda *a:None)
    m.recover_owned(owner)
    assert sdk.calls==before and owner.retired
    with pytest.raises(m.AttachError):m.recover_owned(owner)


def test_locator_duplicate_and_unsafe_paths_have_no_sdk_or_fallback(sdk):
    for path in ('',r'\\host\share\locator',r'C:\x\..\file',r'C:\x\file:ads',r'C:\x\\file'):
        before=list(sdk.calls)
        with pytest.raises(m.AttachError):m._software_verify(path,sdk.exports,lambda:0)
        assert sdk.calls==before
    with pytest.raises(m.AttachError):m._json(b'{"schema":1,"schema":2}',4096)


def test_normal_controlclient_full_source_path_attach_only_and_operation_once(sdk,monkeypatch):
    monkeypatch.setenv(m.ENV,LOCATOR)
    monkeypatch.setattr(m,'_native_api',lambda:m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE))
    monkeypatch.setattr(client,'control_home',lambda:pytest.fail('default home touched'))
    monkeypatch.setattr(client,'_spawn_control_daemon',lambda *a:pytest.fail('spawn'))
    calls=[]
    def request(endpoint,payload,timeout):
        calls.append(payload['operation'])
        assert type(endpoint) is m._Endpoint and endpoint._origin is m._SOFTWARE
        return {'success':True,'data':{}}
    monkeypatch.setattr(client,'_request',request)
    result=client.dispatch('project.inspect',{},dict(request_id='r',idempotency_key='k'))
    assert result['success'] is True and calls==['session_health','project.inspect']


@pytest.mark.parametrize('health',['false','lost'])
def test_full_attach_health_refusal_not_submitted_no_startup_no_token_output(sdk,monkeypatch,health):
    monkeypatch.setenv(m.ENV,LOCATOR)
    monkeypatch.setattr(m,'_native_api',lambda:m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE))
    monkeypatch.setattr(client,'control_home',lambda:pytest.fail('fallback'))
    calls=[]
    def request(endpoint,payload,timeout):
        calls.append(payload['operation'])
        if health=='lost':raise RuntimeError(sdk.secret)
        return {'success':False}
    monkeypatch.setattr(client,'_request',request)
    result=client.dispatch('project.inspect',{},dict(request_id='r',idempotency_key='k'))
    assert result['data']['control_startup_outcome']=='NOT_ATTEMPTED'
    assert result['data']['status']=='NOT_SUBMITTED' and calls==['session_health']
    assert sdk.secret not in json.dumps(result)


@pytest.mark.parametrize('name,shape',[(n,s) for n in ('CloseHandle','CloseServiceHandle','LocalFree')
    for s in ('bool','string','float','raise-before','raise-after')])
def test_malformed_or_unreturned_release_result_is_sticky_original_owner(sdk,name,shape):
    calls=[]
    def changed(*args):
        calls.append(args)
        if shape=='raise-before':raise OSError('software lost result before effect')
        sdk.native_effects.append(('effect',name))
        if shape=='raise-after':raise OSError('software lost result after effect')
        return {'bool':True,'string':'invalid','float':1.0}[shape]
    sdk.exports[name]=changed
    with pytest.raises(m.AttachError) as exc:verify(sdk)
    owner=exc.value.owner
    assert len(calls)==1
    before=list(sdk.calls)
    for _ in range(2):
        with pytest.raises(m.AttachError):m.recover_owned(owner)
    owner.api.sdk[name]=lambda *_:pytest.fail('replacement closer invoked')
    with pytest.raises(m.AttachError):m.recover_owned(owner)
    assert len(calls)==1 and sdk.calls==before and owner in m._ROOTS.values()


@pytest.mark.parametrize('change',['storage','closer','sdk','owner','forged-ack'])
def test_original_holder_binding_cannot_be_replaced_or_acknowledged(sdk,change):
    api=m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE)
    owner=m._Owner(api,LOCATOR);owner.returned('OpenProcess','CloseHandle',0x101000,0,1234)
    resource=owner.resources[0]
    if change=='storage':resource.storage=m.H(resource.value)
    elif change=='closer':resource.closer=lambda *_:1
    elif change=='sdk':owner.api.sdk['CloseHandle']=lambda *_:1
    elif change=='owner':resource.owner=object()
    else:resource.confirmed=resource.closed=True
    before=list(sdk.calls)
    with pytest.raises(m.AttachError):m.recover_owned(owner)
    assert sdk.calls==before and owner in m._ROOTS.values()


@pytest.mark.parametrize('after',[False,True])
def test_root_pop_ack_fault_keeps_original_finished_native_facts(sdk,monkeypatch,after):
    class Roots(dict):
        fail=True
        def pop(self,key):
            if self.fail:
                if after:super().pop(key)
                raise RuntimeError('software root acknowledgement failure')
            return super().pop(key)
    roots=Roots();monkeypatch.setattr(m,'_ROOTS',roots)
    api=m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE)
    owner=m._Owner(api,LOCATOR);owner.returned('OpenProcess','CloseHandle',0x101000,0,1234)
    with pytest.raises(RuntimeError):owner.finish()
    before=list(sdk.calls);assert roots[id(owner)] is owner
    roots.fail=False;m.recover_owned(owner)
    assert sdk.calls==before and owner.retired and not roots


@pytest.mark.parametrize('mutation',['extra','schema','service','home','token-canonical','unknown-host'])
def test_closed_locator_and_endpoint_lexical_contract(sdk,mutation):
    if mutation=='extra':sdk.locator['provider']={}
    elif mutation=='schema':sdk.locator['schema']='other'
    elif mutation=='service':sdk.locator['service_name']='UnrelatedService'
    elif mutation=='home':sdk.locator['control_home']=r'C:\state\..\elsewhere'
    elif mutation=='token-canonical':sdk.endpoint['token']='_'*43
    else:sdk.endpoint['host']='remote.example'
    with pytest.raises(m.AttachError) as exc:verify(sdk)
    assert sdk.secret not in str(exc.value)


@pytest.mark.parametrize('mutation',['home','service','module','extra-switch','duplicate-switch','image'])
def test_service_argv_not_an_endpoint_or_pid_json_assertion(sdk,mutation):
    if mutation=='home':sdk.argv[sdk.argv.index('--home')+1]=r'C:\other\state'
    elif mutation=='service':sdk.argv[-1]='ComsolMCP_Other'
    elif mutation=='module':sdk.argv[4]='unrelated.module'
    elif mutation=='extra-switch':sdk.argv+=['--caller-proof','yes']
    elif mutation=='duplicate-switch':sdk.argv+=['--home',HOME]
    else:sdk.argv[0]='relative-python.exe'
    with pytest.raises(m.AttachError):verify(sdk)


def test_non_native_provider_or_json_does_not_enter_verifier(sdk):
    with pytest.raises(m.AttachError):m._verify(LOCATOR,{'pid':1234,'verified':True})
    with pytest.raises(m.AttachError):m._Endpoint({'retired':True},sdk.endpoint)
    assert not sdk.calls


def test_post_dispatch_unknown_still_sends_requested_operation_only_once(sdk,monkeypatch):
    monkeypatch.setenv(m.ENV,LOCATOR)
    monkeypatch.setattr(m,'_native_api',lambda:m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE))
    calls=[]
    def request(endpoint,payload,timeout):
        calls.append(payload['operation'])
        if len(calls)==1:return {'success':True}
        raise RuntimeError(sdk.secret)
    monkeypatch.setattr(client,'_request',request)
    result=client.dispatch('project.inspect',{},dict(request_id='r',idempotency_key='k'))
    assert result['data']['status']=='UNKNOWN' and calls==['session_health','project.inspect']
    assert result['execution']==dict(request_id='r',idempotency_key='k')
    assert sdk.secret not in json.dumps(result)


def test_native_constructor_binds_closed_readonly_export_list_without_invoking_it(monkeypatch):
    bound=[]
    class Function:
        def __call__(self,*a):pytest.fail('constructor invoked SDK')
    class Library:
        def __init__(self,name):self.name=name
        def __getattr__(self,name):
            assert name in m.SPECS[self.name]
            bound.append(name);return Function()
    monkeypatch.setattr(m,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(C,'WinDLL',lambda name,**kw:Library(name),raising=False)
    monkeypatch.setattr(C,'get_last_error',lambda:0,raising=False)
    api=m._Api()
    assert set(bound)==set(m.READONLY_SDK) and set(api.sdk)==set(m.READONLY_SDK)
    assert m.SPECS['kernel32']['WaitForSingleObject']==(m.D,[m.H,m.D],'DWORD')
    assert api.sdk['WaitForSingleObject'].restype is C.c_uint32
    assert api.sdk['WaitForSingleObject'].argtypes==[C.c_void_p,C.c_uint32]
    assert not any(word in name for name in bound for word in ('Adjust','SetSecurity','CreateService','StartService','Delete','Fwpm'))


@pytest.mark.parametrize('fault',['sd_effect_raise','target_owner','service_pid','birth','client_writer'])
def test_normal_dispatch_source_refusal_does_not_health_or_touch_default(sdk,monkeypatch,fault):
    sdk.fault=fault
    monkeypatch.setenv(m.ENV,LOCATOR)
    monkeypatch.setattr(m,'_native_api',lambda:m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE))
    monkeypatch.setattr(client,'control_home',lambda:pytest.fail('default home touched'))
    monkeypatch.setattr(client,'_request',lambda *a:pytest.fail('unverified source reached HTTP'))
    monkeypatch.setattr(client,'_spawn_control_daemon',lambda *a:pytest.fail('replacement daemon'))
    result=client.dispatch('project.inspect',{},dict(request_id='r',idempotency_key='k'))
    assert result['data']['status']=='NOT_SUBMITTED'
    assert result['data']['control_startup_outcome']=='NOT_ATTEMPTED'
    assert sdk.secret not in json.dumps(result)


def test_invalid_allocated_pointer_success_has_no_localfree_permission(sdk):
    def bad_sd(handle,kind,flags,owner,group,dacl,sacl,out):
        C.cast(out,C.POINTER(m.H))[0]=1
        return 0
    sdk.exports['GetSecurityInfo']=bad_sd
    with pytest.raises(m.AttachError) as exc:verify(sdk)
    owner=exc.value.owner;before=list(sdk.calls)
    with pytest.raises(m.AttachError):m.recover_owned(owner)
    assert 'LocalFree' not in sdk.calls and sdk.calls==before
    assert owner.resources[-1].storage.value==1 and owner in m._ROOTS.values()


@pytest.mark.parametrize('field',['control','acl-size','ace-size','sid-revision','descriptor-extent'])
def test_bounded_actual_descriptor_decoder_refuses_raw_shapes(sdk,field):
    original=sdk.security
    def bad_sd(*args):
        status=original(*args)
        pointer=C.cast(args[-1],C.POINTER(m.H)).contents.value
        buffer=sdk.regions[pointer];raw=bytearray(buffer.raw[:-1])
        dacl=int.from_bytes(raw[16:20],'little')
        if field=='control':raw[2:4]=(0).to_bytes(2,'little')
        elif field=='acl-size':raw[dacl+2:dacl+4]=(65535).to_bytes(2,'little')
        elif field=='ace-size':raw[dacl+10:dacl+12]=(65535).to_bytes(2,'little')
        elif field=='sid-revision':raw[20]=2
        else:raw[16:20]=(len(raw)+32).to_bytes(4,'little')
        C.memmove(pointer,bytes(raw),len(raw));return status
    sdk.exports['GetSecurityInfo']=bad_sd
    with pytest.raises(m.AttachError):verify(sdk)


def test_last_access_time_change_is_not_source_content_drift(sdk):
    # The real fixed ABI parser deliberately never includes LastAccessTime.
    original=sdk.info
    counter=[0]
    def access_only(h,kind,out,size):
        result=original(h,kind,out,size)
        if kind==0:
            counter[0]+=1
            C.cast(out,C.POINTER(m.p._BasicInfo)).contents.accessed=counter[0]
        return result
    sdk.exports['GetFileInformationByHandleEx']=access_only
    assert verify(sdk)._origin is m._SOFTWARE


def _normal_wait_dispatch(sdk,monkeypatch):
    monkeypatch.setenv(m.ENV,LOCATOR)
    monkeypatch.setattr(m,'_native_api',lambda:m._SoftwareApi(sdk.exports,lambda:sdk.last_error,seal=m._SOFTWARE))
    monkeypatch.setattr(client,'control_home',lambda:pytest.fail('default home touched'))
    monkeypatch.setattr(client,'_spawn_control_daemon',lambda *a:pytest.fail('replacement daemon'))
    requests=[]
    def request(endpoint,payload,timeout):
        assert type(endpoint) is m._Endpoint and endpoint._origin is m._SOFTWARE
        requests.append(payload['operation'])
        return {'success':True,'data':{}}
    monkeypatch.setattr(client,'_request',request)
    result=client.dispatch('project.inspect',{},dict(request_id='wait-check',idempotency_key='wait-check'))
    return result,requests


@pytest.mark.parametrize('exit_output',[0,0x123456789ABCDEF0,0xFFFFFFFFFFFFFFFF],
                         ids=['zero-undefined-exit','arbitrary-undefined-exit','maximum-undefined-exit'])
def test_normal_raw_liveness_uses_same_original_zero_wait_not_live_exit_buffer(sdk,monkeypatch,exit_output):
    original=sdk.times
    def times(handle,created,exited,kernel,user):
        result=original(handle,created,exited,kernel,user)
        C.memmove(exited,exit_output.to_bytes(8,'little'),8)
        return result
    sdk.exports['GetProcessTimes']=sdk.function('GetProcessTimes',times)
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['success'] and requests==['session_health','project.inspect']
    assert sdk.process_calls==[(0x101000,0,1234)]
    assert len(sdk.wait_calls)==2 and sdk.wait_calls[0][0] is sdk.wait_calls[1][0]
    handle=sdk.wait_calls[0][0]
    assert all(ms==0 for _,ms in sdk.wait_calls)
    assert sdk.native_effects.count(('close',handle.value))==1
    assert sdk.calls.count('GetProcessTimes')==2 and not m._ROOTS


@pytest.mark.parametrize('stage',[1,2],ids=['first-check','second-check'])
@pytest.mark.parametrize('status',[0,0x80,0xFFFFFFFF,257,259],
                         ids=['signaled','abandoned','failed','unexpected-low','unexpected-high'])
def test_normal_raw_wait_known_nonlive_or_unknown_status_never_reaches_transport(sdk,monkeypatch,stage,status):
    original=sdk.wait
    def wait(handle,milliseconds):
        original(handle,milliseconds)
        return status if len(sdk.wait_calls)==stage else 258
    sdk.exports['WaitForSingleObject']=sdk.function('WaitForSingleObject',wait)
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['data']['status']=='NOT_SUBMITTED' and not requests
    assert result['data']['control_startup_outcome']=='NOT_ATTEMPTED'
    assert sdk.process_calls==[(0x101000,0,1234)] and len(sdk.wait_calls)==stage
    assert sdk.calls.count('GetProcessTimes')==stage-1
    assert len({id(h) for h,ms in sdk.wait_calls})==1
    assert all(ms==0 for h,ms in sdk.wait_calls)
    assert sdk.native_effects.count(('close',sdk.wait_calls[0][0].value))==1
    assert not m._ROOTS  # Known readonly result permits confirmed original cleanup.


@pytest.mark.parametrize('stage',[1,2],ids=['first-check','second-check'])
@pytest.mark.parametrize('outcome',['none','false','true','float','opaque','conversion-raise','sdk-raise','last-error-opaque'])
def test_normal_raw_wait_opaque_retains_original_owner_without_query_close_or_retry(sdk,monkeypatch,stage,outcome):
    class LostValue:
        @property
        def value(self):raise RuntimeError('software DWORD conversion lost')
    original=sdk.wait
    raw={'none':None,'false':False,'true':True,'float':258.0,'opaque':object(),'conversion-raise':LostValue()}
    def wait(handle,milliseconds):
        original(handle,milliseconds)
        if len(sdk.wait_calls)!=stage:return 258
        if outcome=='sdk-raise':raise OSError('software wait return lost')
        if outcome=='last-error-opaque':
            sdk.last_error=object()
            return 258
        return raw[outcome]
    function=sdk.function('WaitForSingleObject',wait)
    sdk.exports['WaitForSingleObject']=function
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['data']['status']=='NOT_SUBMITTED' and not requests
    assert result['data']['control_startup_outcome']=='NOT_ATTEMPTED'
    assert len(m._ROOTS)==1
    owner=next(iter(m._ROOTS.values()));weak=weakref.ref(owner)
    assert owner.opaque and owner.first_cause is not None
    original_cause=owner.first_cause
    wait_call=next(c for c in reversed(owner.calls) if c.name=='WaitForSingleObject')
    assert wait_call.entered and wait_call.result is None and wait_call.function is function
    assert wait_call.args==(sdk.wait_calls[-1][0],0)
    process=next(r for r in owner.resources if r.owned and r.storage is sdk.wait_calls[0][0])
    assert process.storage.value==process.value and process.close_call is None and not process.closed
    before=list(sdk.calls)
    with pytest.raises(m.AttachError):m.recover_owned(owner)
    with pytest.raises(m.AttachError):verify(sdk)
    gc.collect()
    assert weak() is owner and m._ROOTS[id(owner)] is owner and owner.first_cause is original_cause
    assert sdk.calls==before and sdk.native_effects.count(('close',process.value))==0
    assert sdk.calls.count('OpenProcess')==1 and len(sdk.wait_calls)==stage


def test_closed_software_exports_require_wait_without_any_acquisition(sdk):
    exports=dict(sdk.exports);del exports['WaitForSingleObject']
    with pytest.raises(m.AttachError) as exc:m._software_verify(LOCATOR,exports,lambda:0)
    assert exc.value.code=='SOFTWARE_BOUNDARY' and not sdk.calls and not m._ROOTS


def _state_permissions(sdk,*,paths=None,principal=SERVICE,mask=0x1301BF,service_flags=0,client_flags=0,deny=False):
    paths=set(paths or (HOME,ntpath.join(HOME,'control.json')))
    original=sdk.security
    def security(handle,kind,flags,owner,group,dacl,sacl,out):
        path=sdk.handles[sdk.address(handle)][1]
        if path not in paths:return original(handle,kind,flags,owner,group,dacl,sacl,out)
        assert kind==1 and flags==7
        user=CLIENT if path.startswith(r'C:\Users') else m.SY
        raw=bytearray(descriptor(user,extra=((principal,mask),)))
        offset=int.from_bytes(raw[16:20],'little');cursor=offset+8
        for ace_kind,ace_flags,access,subject in m.b.decode_sd(bytes(raw))['aces']:
            if subject==principal:raw[cursor+1]=service_flags
            if subject==CLIENT:raw[cursor+1]=client_flags
            cursor+=int.from_bytes(raw[cursor+2:cursor+4],'little')
        if deny:
            entry=struct.pack('<BBHI',1,0,8+len(sid(principal)),mask)+sid(principal)
            raw.extend(entry)
            struct.pack_into('<H',raw,offset+2,len(raw)-offset)
            struct.pack_into('<H',raw,offset+4,int.from_bytes(raw[offset+4:offset+6],'little')+1)
        C.cast(out,C.POINTER(m.H))[0]=sdk.alloc(bytes(raw))
        return 0
    sdk.exports['GetSecurityInfo']=sdk.function('GetSecurityInfo',security)


@pytest.mark.parametrize('service_flags',[0,3],ids=['explicit-service-data','service-data-child-inheritance'])
def test_normal_raw_exact_service_state_modify_and_undefined_exit_coexist(sdk,monkeypatch,service_flags):
    _state_permissions(sdk,service_flags=service_flags)
    original=sdk.times
    def times(handle,created,exited,kernel,user):
        result=original(handle,created,exited,kernel,user)
        C.memmove(exited,(0x123456789ABCDEF0).to_bytes(8,'little'),8)
        return result
    sdk.exports['GetProcessTimes']=sdk.function('GetProcessTimes',times)
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['success'] and requests==['session_health','project.inspect']
    assert len(sdk.wait_calls)==2 and sdk.wait_calls[0][0] is sdk.wait_calls[1][0]
    assert sdk.process_calls==[(0x101000,0,1234)] and not m._ROOTS


@pytest.mark.parametrize('path',[HOME,ntpath.join(HOME,'control.json')],ids=['state','endpoint'])
@pytest.mark.parametrize('extra',[0x40000,0x80000,0x1000000,0x10000000,0x40000000,0x40,0x200],
                         ids=['write-dacl','write-owner','system-security','generic-all','generic-write','delete-child','unrecognized'])
def test_normal_raw_service_data_mask_cannot_grant_security_or_extra_rights(sdk,monkeypatch,path,extra):
    _state_permissions(sdk,paths=[path],mask=0x1301BF|extra)
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['data']['status']=='NOT_SUBMITTED' and not requests
    assert result['data']['control_startup_outcome']=='NOT_ATTEMPTED'
    assert not m._ROOTS


@pytest.mark.parametrize('principal',[CLIENT,'S-1-5-11','S-1-5-32-545','S-1-1-0','S-1-15-2-1','S-1-5-80-9-8-7-6-5'])
def test_normal_raw_foreign_group_appcontainer_world_or_client_cannot_write_state(sdk,monkeypatch,principal):
    _state_permissions(sdk,principal=principal)
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['data']['status']=='NOT_SUBMITTED' and not requests
    assert not m._ROOTS


@pytest.mark.parametrize('path',[r'C:\ProgramData',LOCATOR],ids=['target-ancestor','client-private-source'])
def test_normal_raw_service_data_permission_does_not_extend_to_ancestor_or_locator(sdk,monkeypatch,path):
    _state_permissions(sdk,paths=[path])
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['data']['status']=='NOT_SUBMITTED' and not requests
    assert not m._ROOTS


def test_default_private_source_policy_never_inherits_target_service_data_exception():
    sd=m.b.decode_sd(descriptor(CLIENT,extra=((SERVICE,0x1301BF),)))
    with pytest.raises(ExecutionContractError) as exc:
        m._policy(sd,CLIENT,final=True,target=False,service_sid=SERVICE)
    assert exc.value.code=='HOST_PROVIDER_CONFIG_INVALID'


@pytest.mark.parametrize('client_flags',[1,2,3,8,16],ids=['object-inherit','container-inherit','both-inherit','inherit-only','inherited'])
def test_normal_raw_client_read_grant_cannot_inherit_to_private_database_or_state(sdk,monkeypatch,client_flags):
    _state_permissions(sdk,client_flags=client_flags)
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['data']['status']=='NOT_SUBMITTED' and not requests
    assert not m._ROOTS


@pytest.mark.parametrize('principal',[SERVICE,CLIENT,'S-1-1-0'])
def test_deny_ace_cannot_cancel_a_forbidden_allow_in_target_state(sdk,monkeypatch,principal):
    _state_permissions(sdk,principal=principal,mask=0x1F01FF,deny=True)
    result,requests=_normal_wait_dispatch(sdk,monkeypatch)
    assert result['data']['status']=='NOT_SUBMITTED' and not requests
    assert not m._ROOTS
