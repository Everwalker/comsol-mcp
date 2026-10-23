"""Original publisher software SDK/HTTP seams, not native authority."""
import ctypes as C
import io
import json
import ntpath
import os
from pathlib import Path
import struct
from dataclasses import replace
import threading
import hashlib
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_scm_endpoint as e
from comsol_mcp import _windows_scm_publisher as pub
from comsol_mcp import _windows_scm_publisher_native as n
from tests.test_windows_scm_endpoint import SDK as ReaderSDK,LOCATOR,CLIENT,descriptor as client_descriptor
from tests.test_windows_scm_publisher_native import HOME,PROFILE,SERVICE,USER,descriptor,mic


class File:
    def __init__(self,path,number,*,directory=False,reader=USER):
        self.path=path;self.number=number;self.directory=directory;self.data=b''
        self.sd=descriptor(directory=directory,reader=reader);self.label=mic()
        self.volume=7;self.links=1;self.reparse=False;self.deleted=False
    def __repr__(self):return '<private software SDK file>'


class Model:
    def __init__(self,home):
        self.home=home;self.files={};self.next_file=500;self.active=[];self.events=[]
        self.state=2;self.fault=None;self.reader_use=1;self.reader=USER;self.on_create=None
        for path in ['C:\\',r'C:\ProgramData',r'C:\ProgramData\ComsolMCP_Test',HOME]:
            f=self.add(path,directory=True)
            if path!=HOME:
                f.sd=descriptor(directory=True,reader=None,mask=0x1200A9)
        self.lock=None
    def add(self,path,**kw):
        self.next_file+=1;f=File(path,self.next_file,**kw);self.files[path]=f;return f
    def __repr__(self):return '<private software SDK namespace>'


class SDK(ReaderSDK):
    def __init__(self,model,*,system=True):
        super().__init__();self.model=model;self.system=system;self.positions={}
        self.argv+=['--windows-t037-profile',PROFILE]
        self.endpoint['pid']=os.getpid();self.runtime=None;self.borrowed={}
        callbacks=dict(CreateFileW=self.root,NtCreateFile=self.relative,GetFileInformationByHandleEx=self.info,
            GetSecurityInfo=self.security,ReadFile=self.read,CloseHandle=self.close,OpenProcessToken=self.open_token,
            GetProcessId=lambda h:os.getpid(),GetProcessTimes=self.times,
            WriteFile=self.write,FlushFileBuffers=self.flush,SetFilePointerEx=self.seek,
            LookupAccountSidW=self.account,ConvertStringSecurityDescriptorToSecurityDescriptorW=self.convert,
            NtSetInformationFile=self.set_information)
        self.exports.update({k:self.function(k,v) for k,v in callbacks.items()})
    def file(self,h):
        kind,f=self.handles[self.address(h)]
        if kind not in ('file','borrowed'):raise AssertionError('not software file handle')
        return f
    def take(self,f,access,share,*,borrowed=False):
        for sdk,h,other,a,s in self.model.active:
            if other is f:
                read=bool(access&1);write=bool(access&0x116);delete=bool(access&0x10000)
                if (read and not s&1) or (write and not s&2) or (delete and not s&4):return None
                if (a&1 and not share&1) or (a&0x116 and not share&2) or (a&0x10000 and not share&4):return None
        h=self.handle(('borrowed' if borrowed else 'file',f))
        self.model.active.append((self,h,f,access,share));return h
    def root(self,path,access,share,*args):
        return self.take(self.model.files[path],access,share)
    def relative(self,out,access,attributes,io,*args):
        a=C.cast(attributes,C.POINTER(n.p._ObjectAttributes)).contents
        leaf=C.string_at(a.ObjectName.contents.Buffer,a.ObjectName.contents.Length).decode('utf-16-le')
        path=ntpath.join(self.file(a.RootDirectory).path,leaf)
        disposition=args[3];create=disposition==2
        if path==ntpath.join(HOME,'control.lock') and self.model.home.joinpath('control.lock').exists() and path not in self.model.files:
            self.model.lock=self.model.add(path,reader=None)
        status=C.cast(io,C.POINTER(n.p._IoStatusBlock)).contents
        if create:
            if path in self.model.files:status.Status=C.c_int32(0xC0000035).value;return status.Status
            f=self.model.add(path,reader=self.model.reader);f.sd=self.created_sd;f.label=mic()
            self.model.events.append('create')
            if self.model.on_create:self.model.on_create(f)
        elif path not in self.model.files or self.model.files[path].deleted:
            status.Status=n.NOT_FOUND;status.Information=0;return n.NOT_FOUND
        else:f=self.model.files[path]
        if access==0x1200A2 and not f.directory:raise AssertionError('parent writer is not directory')
        h=self.take(f,access,args[2])
        if h is None:status.Status=C.c_int32(0xC0000043).value;return status.Status
        C.cast(out,C.POINTER(n.H))[0]=h;status.Status=0;status.Information=2 if create else 1
        if self.model.fault=='create-lost' and create:raise OSError('software acquisition return lost')
        return 0
    def info(self,h,kind,out,size):
        f=self.file(h)
        if kind==18:
            x=C.cast(out,C.POINTER(n.p._FullIdInfo)).contents;x.volume=f.volume
            for i,b in enumerate(f.number.to_bytes(16,'little')):x.file_id[i]=b
        elif kind==0:
            x=C.cast(out,C.POINTER(n.p._BasicInfo)).contents
            x.created=111;x.written=222;x.changed=333;x.attributes=(16 if f.directory else 128)|(0x400 if f.reparse else 0)
        elif kind==1:
            x=C.cast(out,C.POINTER(n.p._StandardInfo)).contents
            x.directory=f.directory;x.links=f.links;x.size=len(f.data);x.delete_pending=False
        else:raise AssertionError('information class')
        return 1
    def security(self,h,kind,flags,owner,group,dacl,sacl,out):
        assert kind==1 and flags in (7,16)
        f=self.file(h);data=f.sd if flags==7 else f.label
        C.cast(out,C.POINTER(n.H))[0]=self.alloc(data)
        if self.model.fault=='get-sd-return-failed':return 5
        if self.model.fault=='get-sd-written-raise':raise OSError('software SD output return lost')
        if self.model.fault=='get-sd-opaque':return object()
        return 0
    def open_token(self,process,rights,out):
        assert rights==8
        user=e.SY if self.system or self.address(process)!=e.H(-1).value else CLIENT
        C.cast(out,C.POINTER(n.H))[0]=self.handle(('token',user));return 1
    def times(self,h,created,exited,kernel,user):
        C.memmove(created,(116444736000000000+2000*10000).to_bytes(8,'little'),8)
        C.memmove(exited,(0x123456789ABCDEF0).to_bytes(8,'little'),8);return 1
    def read(self,h,buffer,count,used,overlapped):
        f=self.file(h);key=self.address(h);offset=self.positions.get(key,0);data=f.data[offset:offset+count]
        C.memmove(buffer,data,len(data));C.cast(used,C.POINTER(n.D))[0]=len(data);self.positions[key]=offset+len(data);return 1
    def write(self,h,buffer,count,used,overlapped):
        self.model.events.append('write');data=C.string_at(buffer,count)
        if self.model.fault=='short-write':data=data[:-1]
        self.file(h).data=data;C.cast(used,C.POINTER(n.D))[0]=len(data);return 1
    def flush(self,h):
        self.model.events.append('flush')
        if self.model.fault=='flush-fail':return 0
        return 1
    def seek(self,h,offset,new,origin):
        assert offset==0 and origin==0;self.positions[self.address(h)]=0;return 1
    def account(self,system,buffer,name,count,domain,dcount,use):
        if name is None:
            C.cast(count,C.POINTER(n.D))[0]=5;C.cast(dcount,C.POINTER(n.D))[0]=6;self.last_error=122;return 0
        name.value='user';domain.value='local'
        C.cast(count,C.POINTER(n.D))[0]=4;C.cast(dcount,C.POINTER(n.D))[0]=5
        C.cast(use,C.POINTER(n.D))[0]=self.model.reader_use;return 1
    def convert(self,text,revision,out,size):
        expected='O:SYG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1301bf;;;'+SERVICE+')'
        if self.model.reader:expected+='(A;;0x120089;;;'+self.model.reader+')'
        assert text==expected+'S:(ML;;NW;;;ME)' and revision==1
        self.created_sd=descriptor(directory=False,reader=self.model.reader)
        C.cast(out,C.POINTER(n.H))[0]=self.alloc(self.created_sd)
        C.cast(size,C.POINTER(n.D))[0]=len(self.created_sd);return 1
    def set_information(self,h,io,buffer,size,kind):
        f=self.file(h);status=C.cast(io,C.POINTER(n.p._IoStatusBlock)).contents
        if kind==10:
            x=C.cast(buffer,C.POINTER(n.p._RenameInfo)).contents
            assert x.ReplaceIfExists==1 and C.string_at(C.addressof(buffer._obj)+n.p._RenameInfo.FileName.offset,x.FileNameLength).decode('utf-16-le')=='control.json'
            assert self.file(x.RootDirectory).path==HOME
            self.model.events.append('rename')
            if self.model.fault=='rename-rejected':return C.c_int32(0xC0000043).value
            old=self.model.files.get(ntpath.join(HOME,'control.json'))
            if any(row[2] is old and not row[4]&4 for row in self.model.active):return C.c_int32(0xC0000043).value
            del self.model.files[f.path];f.path=ntpath.join(HOME,'control.json');self.model.files[f.path]=f
        elif kind==13:
            assert C.cast(buffer,C.POINTER(n.p._DispositionInfo)).contents.DeleteFile==1
            self.model.events.append('delete');f.deleted=True
        else:raise AssertionError('set information class')
        status.Status=0;status.Information=0
        if self.model.fault in ('rename-lost','delete-lost') and kind==(10 if self.model.fault=='rename-lost' else 13):raise OSError('software effect return lost')
        if self.model.fault=='rename-opaque' and kind==10:return object()
        if self.model.fault=='rename-io-unknown' and kind==10:status.Status=0x103
        return 0
    def close(self,h):
        value=self.address(h)
        if value in self.borrowed.values():raise AssertionError('borrowed HANDLE closed')
        self.native_effects.append(('close',value))
        self.model.active[:]=[row for row in self.model.active if not(row[0] is self and row[1]==value)]
        if self.model.fault=='close-lost':raise OSError('software close return lost')
        if self.model.fault=='close-opaque':return object()
        if self.model.fault=='close-zero':return 0
        return 1
    def borrow(self,fd):
        if fd not in self.borrowed:
            path=ntpath.join(HOME,'control.lock')
            if path not in self.model.files:
                assert self.model.home.joinpath('control.lock').is_file()
                self.model.lock=self.model.add(path,reader=None)
            f=self.model.files[path]
            self.borrowed[fd]=self.handle(('borrowed',f))
        return self.borrowed[fd]
    def wait(self,h,milliseconds):
        assert milliseconds==0;self.wait_calls.append((h,milliseconds));return 258
    def status(self,service,level,buffer,size,used):
        row=C.cast(buffer,C.POINTER(e.Status)).contents;row.service_type=16
        row.state=self.model.state;row.process_id=os.getpid()
        C.cast(used,C.POINTER(n.D))[0]=C.sizeof(e.Status);return 1
    def process(self,rights,inherit,pid):
        assert rights==0x101000 and inherit==0 and pid==os.getpid()
        self.process_calls.append((rights,inherit,pid));return self.handle(('process',pid))


def software_api(sdk,home,profile):
    return n._SoftwareApi(sdk.exports,lambda:sdk.last_error,sdk.borrow,
        {str(home):HOME,str(profile):PROFILE},seal=n._SOFTWARE)


def test_fake_or_constructed_runtime_refuses_before_sdk_or_lock(monkeypatch,tmp_path):
    monkeypatch.setattr(pub,'_api',lambda:pytest.fail('SDK reached'))
    with pytest.raises(n.PublisherError):pub.prepare_home(tmp_path,SimpleNamespace(service_name='ComsolMCP_Test',_active=True))


def test_borrowed_or_serialized_handle_is_never_a_mutator_owner(tmp_path,monkeypatch):
    model=Model(tmp_path);sdk=SDK(model);api=software_api(sdk,tmp_path,tmp_path/'profile.json')
    monkeypatch.setattr(n,'_OWNERS',{})
    ns=n._Namespace(api,tmp_path,'ComsolMCP_Test')
    with pytest.raises(n.PublisherError):ns.write(n.H(123),b'private')
    with pytest.raises(n.PublisherError):ns.effect(n.H(123),rename=True)
    assert not sdk.calls


class ScmSDK:
    def __init__(self,model):self.model=model;self.status=[];self.handler=None
    def start_service_ctrl_dispatcher(self,name,callback):
        assert name=='ComsolMCP_Test';callback(0,None)
    def register_service_ctrl_handler_ex(self,name,callback):
        assert name=='ComsolMCP_Test';self.handler=callback;return 19
    def set_service_status(self,handle,status):
        assert handle==19;self.status.append(status.current_state)
        self.model.state=status.current_state;self.model.events.append('SCM:'+str(status.current_state))


class SoftwareSocket:
    def __init__(self):self.fd=901
    def fileno(self):return self.fd
    def getsockname(self):return ('127.0.0.1',4321)


class SoftwareServer:
    """HTTP seam only: Handler and ControlDaemon dispatch below are production."""
    action=None
    def __init__(self,address,handler):
        assert address==('127.0.0.1',0)
        self.socket=SoftwareSocket();self.server_port=4321;self.handler=handler
        self.closed_count=0;self.shutdowns=0
    def server_close(self):self.closed_count+=1;self.socket.fd=-1
    def shutdown(self):self.shutdowns+=1
    def serve_forever(self):type(self).action(self)


@pytest.fixture
def original_chain(monkeypatch,tmp_path):
    from comsol_mcp import _windows_t037 as win
    from comsol_mcp import _managed_backend as backend
    from comsol_mcp import _windows_t037_broker as broker
    from comsol_mcp import _control_daemon as cd
    from tests.test_windows_network_intent import effective_group_fixture
    from tests.test_windows_host_change import _write_provider
    home=tmp_path/'state';home.mkdir(mode=0o700)
    profile_path=tmp_path/'t037.json';profile_path.write_text('{}');profile_path.chmod(0o600)
    profile,_,_=effective_group_fixture()
    resource=profile.host_resource_profile
    argv=SDK(Model(home)).argv
    argv_hash=hashlib.sha256(json.dumps(argv,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    images=tuple(replace(image,path=argv[0],argv_sha256=argv_hash) if image.role=='launcher' else image for image in resource.launch_images)
    resource=replace(resource,service_account_id=e.SY,launch_images=images,
        windows_isolation=replace(resource.windows_isolation,service_name='ComsolMCP_Test',
            service_start_name='LocalSystem',service_logon_sid=e.SY,service_sid=SERVICE))
    profile=replace(profile,host_resource_profile=resource)
    provider=_write_provider(tmp_path/'provisioned-provider',resource,[])
    provider_path=tmp_path/'provisioned-provider/provider.json'
    model=Model(home);sdk=SDK(model);api=software_api(sdk,home,profile_path)
    scm=ScmSDK(model)
    monkeypatch.setattr(n,'_OWNERS',{});monkeypatch.setattr(pub,'_ROOTS',{})
    monkeypatch.setattr(e,'_ROOTS',{})
    monkeypatch.setattr(pub,'_api',lambda:api)
    # Explicit trusted-loader/discovery OS seams; no new publisher predicate patched.
    monkeypatch.setattr(win,'load_trusted_profile',lambda path:profile)
    monkeypatch.setattr(backend,'collect_legacy_registry',lambda:{})
    monkeypatch.setenv('COMSOL_PROJECT_ROOT',str(tmp_path))
    monkeypatch.setattr(pub,'_Server',SoftwareServer)
    monkeypatch.setattr(pub,'_ServerInit',SoftwareServer.__init__)
    monkeypatch.setattr(pub,'_ServerClose',SoftwareServer.server_close)
    state=SimpleNamespace(home=home,profile_path=profile_path,profile=profile,model=model,sdk=sdk,
        api=api,scm=scm,cd=cd,provider=provider,provider_path=provider_path,owners=[])
    def run(action):
        box={}
        def main(runtime):
            state.runtime=runtime
            try:box['value']=action(runtime)
            except BaseException as error:box['error']=error
            finally:scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)
        b=broker.WindowsScmServiceBroker._for_software_test('ComsolMCP_Test',main,api=scm,token=broker._SCM_TEST_API_TOKEN)
        b.run()
        if 'error' in box:raise box['error']
        return box.get('value')
    state.run=run
    def prepare(runtime,*,daemon=True):
        owner=pub.prepare_home(home,runtime);state.owners.append(owner)
        if daemon:
            d=cd.ControlDaemon(home,registry={},host_provider_config=provider_path,
                windows_t037_profile=profile_path,project_root=tmp_path)
            state.daemon=d;owner.bind_original(d,profile.source_sha256)
        return owner
    state.prepare=prepare
    yield state
    # Test-only release of the real macOS file descriptor, never native ledger retry.
    # Ambiguous SOFTWARE owners stay rooted through every assertion before teardown.
    for owner in list(pub._ROOTS.values()):
        original=getattr(owner._namespace,'_borrowed',None)
        stream=original[0] if original else owner._stream
        if type(stream) is io.TextIOWrapper and not stream.closed:stream.close()
    if hasattr(state,'daemon'):state.daemon.close()
    provider.close()


def test_original_publisher_publish_withdraw_then_singleton_close(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        owner.publish(server);owner.before_running()
        assert owner._ready and not owner._stream.closed
        runtime.mark_running()
        s.daemon._windows_t037_host_control_issuer(runtime)
        s.daemon._retain_t037_scm_runtime(runtime)
        owner.close_listener_and_withdraw()
        assert owner._withdrawn and not owner._stream.closed
        owner.release_singleton()
        assert owner._stream.closed and not pub._ROOTS and not n._OWNERS
        assert server.closed_count==1
    s.run(main)
    assert s.model.events.count('rename')==1 and s.model.events.count('delete')==1


def add_locator(s):
    for path in [r'C:\Users',r'C:\Users\Everwalker',r'C:\Users\Everwalker\private',LOCATOR]:
        f=s.model.add(path,directory=path!=LOCATOR,reader=None)
        f.sd=client_descriptor(CLIENT)
        if path==LOCATOR:f.data=json.dumps(dict(schema=e.SCHEMA,service_name='ComsolMCP_Test',control_home=HOME)).encode()


def test_private_controller_serve_original_broker_to_normal_client_then_own_withdraw(original_chain,monkeypatch):
    from comsol_mcp import _control_client as client
    from comsol_mcp import _windows_t037_broker as broker
    s=original_chain;add_locator(s);observed=[];requests=[]
    reader=SDK(s.model,system=False)
    monkeypatch.setattr(e,'verify_endpoint',lambda path:e._software_verify(path,
        {k:reader.exports[k] for k in e.READONLY_SDK},lambda:reader.last_error))
    monkeypatch.setenv('COMSOL_SERVER_MCP_SCM_ENDPOINT_CONFIG',LOCATOR)
    def forbidden(*a,**k):pytest.fail('local startup fallback')
    monkeypatch.setattr(client,'control_home',forbidden)
    monkeypatch.setattr(client,'_spawn_control_daemon',forbidden)
    def bridge(request,timeout):
        # Production _request, Handler.do_POST and real daemon.dispatch remain.
        assert request.full_url=='http://127.0.0.1:4321/rpc'
        assert not e._ROOTS  # readonly consumer handles closed before health HTTP seam
        payload=json.loads(request.data);requests.append(payload['operation'])
        handler=observed[0].handler.__new__(observed[0].handler)
        handler.path='/rpc';handler.headers={'Authorization':request.get_header('Authorization'),'Content-Length':str(len(request.data))}
        handler.rfile=io.BytesIO(request.data);handler.wfile=io.BytesIO();status=[]
        handler.send_response=lambda code:status.append(code)
        handler.send_header=lambda *a:None;handler.end_headers=lambda:None
        handler.send_error=lambda *a:pytest.fail('handler rejected private software request')
        handler.do_POST()
        assert status==[200]
        return io.BytesIO(handler.wfile.getvalue())
    monkeypatch.setattr(client,'urlopen',bridge)
    def serve(server):
        observed.append(server)
        owner=next(iter(pub._ROOTS.values()))
        assert s.model.state==4 and owner._ready and not owner._stream.closed
        assert owner._daemon._windows_t037_scm_runtime is s.runtime
        assert not any(row[3]&0x10116 and row[2].path.endswith('control.json') for row in s.model.active)
        result=client.dispatch('session_health',{}, {})
        assert result.get('success') is True
        s.scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)
    monkeypatch.setattr(SoftwareServer,'action',staticmethod(serve))
    def main(runtime):
        s.cd.serve(s.home,host_provider_config=s.provider_path,windows_t037_profile=s.profile_path,
            _scm_runtime=runtime,_scm_profile_source_sha256=s.profile.source_sha256)
    s.run(main)
    assert requests==['session_health','session_health']
    assert not pub._ROOTS and not n._OWNERS and not e._ROOTS
    assert s.model.files[ntpath.join(HOME,'control.json')].deleted
    assert observed[0].closed_count==1
    assert s.model.events.index('rename')<s.model.events.index('SCM:4')<s.model.events.index('delete')
    assert not s.home.joinpath('control.json.tmp').exists()


@pytest.mark.parametrize('reader',[None,USER])
def test_zero_or_one_actual_user_reader_from_provisioned_home_only(original_chain,reader):
    s=original_chain;s.model.reader=reader
    s.model.files[HOME].sd=descriptor(directory=True,reader=reader)
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        sd=n.e.b.decode_sd(owner._final_fact[1]);home=n.e.b.decode_sd(s.model.files[HOME].sd)
        assert sum(row[3]==USER for row in sd['aces'])==(1 if reader else 0)
        assert all(row[1]==0 for row in sd['aces'])
        assert all(row[1]==0 for row in home['aces'] if row[3]==USER)
        lock=n.e.b.decode_sd(s.model.lock.sd)
        assert not any(row[3]==USER for row in lock['aces'])
        owner.close_listener_and_withdraw();owner.release_singleton()
    s.run(main)


@pytest.mark.parametrize('kind',[2,4,5,9])
def test_home_reader_must_resolve_to_actual_sid_type_user_before_lock(original_chain,kind):
    s=original_chain;s.model.reader_use=kind
    def main(runtime):
        with pytest.raises(n.PublisherError):s.prepare(runtime,daemon=False)
        assert not s.home.joinpath('control.lock').exists()
        assert not s.model.events.count('create') and not n._OWNERS
    s.run(main)


@pytest.mark.parametrize('fault',['create-lost','short-write','flush-fail','rename-rejected','rename-lost','rename-opaque','rename-io-unknown'])
def test_original_effect_unknown_preserves_graph_and_never_replays(original_chain,fault):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);s.model.fault=fault
        with pytest.raises(n.PublisherError):owner.publish(server)
        calls=list(s.sdk.calls);events=list(s.model.events)
        with pytest.raises(n.PublisherError):owner.publish(server)
        with pytest.raises(n.PublisherError):owner.finish_after_daemon()
        assert s.model.events==events and s.sdk.calls==calls
        assert pub._ROOTS[id(owner)] is owner and not owner._stream.closed
        assert owner._namespace.owner.opaque
    s.run(main)


@pytest.mark.parametrize('drift',['reparse','links','volume','label','sd'])
def test_stage_identity_sd_mic_or_volume_invalid_never_renames(original_chain,drift):
    s=original_chain
    def mutate(f):
        if drift=='reparse':f.reparse=True
        elif drift=='links':f.links=2
        elif drift=='volume':f.volume=8
        elif drift=='label':f.label=mic(4096)
        elif drift=='sd':f.sd=descriptor(directory=False,reader=USER,mask=0x1f01ff)
    s.model.on_create=mutate
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        with pytest.raises(n.PublisherError):owner.publish(server)
        assert not s.model.events.count('rename')
        assert not owner._stream.closed
    s.run(main)


@pytest.mark.parametrize('fault',['delete-lost','close-lost','close-opaque','close-zero'])
def test_withdraw_unknown_never_retries_or_releases_singleton(original_chain,fault):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        s.model.fault=fault
        with pytest.raises(n.PublisherError):owner.close_listener_and_withdraw()
        events=list(s.model.events);calls=list(s.sdk.calls)
        with pytest.raises(n.PublisherError):owner.close_listener_and_withdraw()
        with pytest.raises(n.PublisherError):owner.release_singleton()
        assert s.model.events==events and s.sdk.calls==calls
        assert not owner._stream.closed and server.closed_count==1
    s.run(main)


@pytest.mark.parametrize('operation',['rename','delete'])
def test_known_effect_ack_fault_continues_original_metadata_only(original_chain,operation,monkeypatch):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        if operation=='delete':owner.publish(server)
        def checkpoint(event,original):
            if event=='publisher_'+operation+'_ack':raise RuntimeError('software bookkeeping fault')
        monkeypatch.setattr(owner._namespace.owner,'checkpoint',checkpoint)
        with pytest.raises(n.PublisherError):
            owner.publish(server) if operation=='rename' else owner.close_listener_and_withdraw()
        assert s.model.events.count(operation)==1 and not owner._namespace.owner.opaque
        monkeypatch.setattr(owner._namespace.owner,'checkpoint',lambda *a:None)
        if operation=='rename':owner.recover_publish_ack();owner.close_listener_and_withdraw()
        else:owner.recover_withdraw_ack()
        owner.release_singleton()
        assert s.model.events.count(operation)==1 and owner._stream.closed
    s.run(main)


def test_foreign_endpoint_drift_preserves_foreign_and_singleton(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        f=s.model.files[ntpath.join(HOME,'control.json')];f.number+=1
        with pytest.raises(n.PublisherError):owner.close_listener_and_withdraw()
        assert not f.deleted and not s.model.events.count('delete') and not owner._stream.closed
        with pytest.raises(n.PublisherError):owner.release_singleton()
    s.run(main)


@pytest.mark.parametrize('change',['stream','handle','class','init','runtime','home','secret'])
def test_original_singleton_and_source_substitution_rejected_before_publish(original_chain,monkeypatch,change):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        if change=='stream':owner._stream=SimpleNamespace(closed=False,fileno=lambda:1)
        elif change=='handle':owner._lock.handle=SimpleNamespace(closed=False,fileno=lambda:1)
        elif change=='class':monkeypatch.setattr(pub.backend,'ProcessLock',lambda *a:None)
        elif change=='init':monkeypatch.setattr(pub._Lock,'__init__',lambda *a:None)
        elif change=='runtime':owner._runtime=SimpleNamespace(_active=True)
        elif change=='home':owner._pyhome=Path(str(owner._pyhome)+'-other')
        elif change=='secret':monkeypatch.setattr(pub.secrets,'token_urlsafe',lambda *a:'caller')
        with pytest.raises(n.PublisherError):owner.publish(server)
        assert not s.model.events.count('create') and not s.model.events.count('rename')
    s.run(main)


def test_validated_old_endpoint_is_replaceable_without_using_its_pid(original_chain):
    s=original_chain;old=s.model.add(ntpath.join(HOME,'control.json'))
    old.data=json.dumps(dict(port=2345,token=s.sdk.secret,pid=9999,process_start_epoch_ms=1000)).encode()
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        assert s.model.files[old.path] is not old and not s.sdk.process_calls
        owner.close_listener_and_withdraw();owner.release_singleton()
    s.run(main)


def test_consumer_without_delete_share_blocks_replacement_without_force_or_retry(original_chain):
    s=original_chain;old=s.model.add(ntpath.join(HOME,'control.json'))
    old.data=json.dumps(dict(port=2345,token=s.sdk.secret,pid=9999,process_start_epoch_ms=1000)).encode()
    reader=SDK(s.model,system=False)
    reader_api=e._SoftwareApi({k:reader.exports[k] for k in e.READONLY_SDK},lambda:reader.last_error,seal=e._SOFTWARE)
    observer=e._Owner(reader_api,HOME)
    e._open_path(observer,ntpath.join(HOME,'control.json'),False)
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        with pytest.raises(n.PublisherError):owner.publish(server)
        assert s.model.files[old.path] is old and s.model.events.count('rename')==1
        observer.finish()
        with pytest.raises(n.PublisherError):owner.recover_publish_ack()
        assert s.model.events.count('rename')==1 and not owner._stream.closed
    s.run(main)


@pytest.mark.parametrize('fault',['corrupt','extra','owner','DACL','LABEL','hardlink','reparse'])
def test_old_endpoint_invalid_preserved_before_stage_write(original_chain,fault):
    s=original_chain;old=s.model.add(ntpath.join(HOME,'control.json'))
    old.data=json.dumps(dict(port=2345,token=s.sdk.secret,pid=9999,process_start_epoch_ms=1000)).encode()
    if fault=='corrupt':old.data=b'not-json'
    elif fault=='extra':old.data=old.data[:-1]+b',"extra":1}'
    elif fault=='owner':old.sd=descriptor(directory=False,owner=USER)
    elif fault=='DACL':old.sd=descriptor(directory=False,mask=0x1f01ff)
    elif fault=='LABEL':old.label=mic(mask=3)
    elif fault=='hardlink':old.links=2
    elif fault=='reparse':old.reparse=True
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        with pytest.raises(n.PublisherError):owner.publish(server)
        assert s.model.files[old.path] is old and not old.deleted
        assert not s.model.events.count('write') and not s.model.events.count('rename')
        owner.abort_before_publication()
        assert owner._stream.closed and not pub._ROOTS
    s.run(main)


def test_exclusive_stage_collision_is_safe_and_never_truncates(original_chain,monkeypatch):
    s=original_chain
    # The generator is explicitly the SOFTWARE source; caller has no path argument.
    generator=lambda count:'a'*32
    monkeypatch.setattr(pub,'_StageName',generator);monkeypatch.setattr(pub.secrets,'token_hex',generator)
    collision=s.model.add(ntpath.join(HOME,'.scm-endpoint-'+'a'*32+'.tmp'),reader=None)
    collision.data=b'foreign-stage'
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        with pytest.raises(n.PublisherError):owner.publish(server)
        assert bool(collision.data==b'foreign-stage') and not collision.deleted
        assert not s.model.events.count('write') and not s.model.events.count('rename')
        owner.abort_before_publication()
    s.run(main)


@pytest.mark.parametrize('event',['release_ack','retire_ack'])
def test_confirmed_native_close_metadata_fault_never_reenters_original_sdk(original_chain,monkeypatch,event):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        owner.close_listener_and_withdraw()
        native=owner._namespace.owner
        def checkpoint(actual,original):
            if actual==event:raise RuntimeError('software known-close bookkeeping fault')
        monkeypatch.setattr(native,'checkpoint',checkpoint)
        with pytest.raises(n.PublisherError):owner.release_singleton()
        effects=list(s.sdk.native_effects)
        assert not owner._stream.closed
        monkeypatch.setattr(native,'checkpoint',lambda *a:None)
        owner.release_singleton()
        # Unclosed other original resources may now finish, but no captured
        # successful closer (including the one with lost ack) is entered twice.
        assert len(s.sdk.native_effects)==len(set(s.sdk.native_effects))
        assert all(s.sdk.native_effects.count(effect)==1 for effect in effects)
        assert owner._stream.closed
    s.run(main)


def test_closed_or_reopened_original_stream_cannot_publish(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        owner._stream.close()
        replacement=s.home.joinpath('control.lock').open('a+')
        try:
            owner._lock.handle=replacement
            with pytest.raises(n.PublisherError):owner.publish(server)
            assert not s.model.events.count('write')
        finally:replacement.close()
    s.run(main)


def test_singleton_is_source_acquired_once_and_cannot_be_supplied_by_caller(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime,daemon=False)
        assert owner._lock_args==(owner._lock,s.home/'control.lock')
        assert type(owner._lock) is pub.backend.ProcessLock and owner._lock_result==(None,)
        with pytest.raises(n.PublisherError):owner._lock_once()
        owner.abort_before_publication()
        assert owner._stream.closed
    s.run(main)


def test_unknown_singleton_close_return_never_reenters_or_drops_original(original_chain,monkeypatch):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        owner.close_listener_and_withdraw();calls=[]
        original=pub._Close
        def close(lock):
            calls.append(lock);original(lock);raise OSError('software original close return lost')
        # Fault the captured original SDK-equivalent close boundary; retain the
        # exact class/function binding checks rather than allow caller metadata.
        monkeypatch.setattr(pub,'_Close',close);monkeypatch.setattr(pub._Lock,'close',close)
        with pytest.raises(n.PublisherError):owner.release_singleton()
        with pytest.raises(n.PublisherError):owner.release_singleton()
        assert calls==[owner._lock] and pub._ROOTS[id(owner)] is owner
    s.run(main)


def test_public_errors_and_owner_repr_never_expose_private_payload(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        secret=owner.transport_secret();s.model.fault='rename-lost'
        with pytest.raises(n.PublisherError) as error:owner.publish(server)
        assert secret not in str(error.value) and secret not in repr(error.value) and secret not in repr(owner)
        assert error.value.__cause__ is None and error.value.owner is owner
        assert owner._namespace._effects['rename'][0].entered
    s.run(main)


@pytest.mark.parametrize('mismatch',['sid','account','start_name','profile_path','source','launcher','argv'])
def test_actual_profile_source_and_native_actor_must_agree_before_listener(original_chain,mismatch):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime,daemon=False)
        d=s.cd.ControlDaemon(s.home,registry={},host_provider_config=s.provider_path,
            windows_t037_profile=s.profile_path,project_root=s.home.parent)
        s.daemon=d;p=d._windows_t037_profile;r=p.host_resource_profile;policy=r.windows_isolation
        if mismatch=='sid':policy=replace(policy,service_sid='S-1-5-80-9-8-7-6-5')
        elif mismatch=='account':
            policy=replace(policy,service_logon_sid=USER);r=replace(r,service_account_id=USER,windows_isolation=policy)
        elif mismatch=='start_name':policy=replace(policy,service_start_name='Other')
        elif mismatch=='profile_path':d._windows_t037_profile_path=str(s.profile_path)+'-other'
        elif mismatch=='source':p=replace(p,source_sha256='f'*64)
        elif mismatch in ('launcher','argv'):
            images=tuple(replace(image,**({'path':r'C:\other\python.exe'} if mismatch=='launcher'
                else {'argv_sha256':'f'*64})) if image.role=='launcher' else image for image in r.launch_images)
            r=replace(r,launch_images=images)
        d._windows_t037_profile=replace(p,host_resource_profile=replace(r,windows_isolation=policy))
        with pytest.raises(n.PublisherError):owner.bind_original(d,s.profile.source_sha256)
        assert owner._server is None and not s.model.events.count('create')
        owner.abort_before_publication()
    s.run(main)


def test_real_non_system_token_does_not_inherit_runtime_metadata_authority(original_chain):
    s=original_chain;s.sdk.system=False
    def main(runtime):
        with pytest.raises(n.PublisherError):s.prepare(runtime,daemon=False)
        assert not s.home.joinpath('control.lock').exists() and not n._OWNERS
    s.run(main)


def test_original_callback_stop_blocks_publish_without_new_namespace_effect(original_chain):
    from comsol_mcp import _windows_t037_broker as broker
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        s.scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)
        with pytest.raises(n.PublisherError):owner.publish(server)
        assert not s.model.events.count('create') and not s.model.events.count('rename')
        owner.abort_before_publication()
    s.run(main)


@pytest.mark.parametrize('what',['home','lock','borrowed_handle','fd','source_argv'])
def test_original_pins_and_stream_are_fresh_before_any_publish_write(original_chain,what):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object)
        if what=='home':s.model.files[HOME].number+=1
        elif what=='lock':s.model.lock.number+=1
        elif what=='borrowed_handle':s.sdk.borrowed[owner._stream.fileno()]+=10
        elif what=='fd':owner._stream.write('changed');owner._stream.flush()
        elif what=='source_argv':s.sdk.argv[-1]=r'C:\other\profile.json'
        with pytest.raises(n.PublisherError):owner.publish(server)
        assert not s.model.events.count('write') and not s.model.events.count('rename')
    s.run(main)


def test_original_processlock_is_still_held_when_namespace_withdraw_fails(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        s.model.files[ntpath.join(HOME,'control.json')].label=mic(mask=5)
        with pytest.raises(n.PublisherError):owner.close_listener_and_withdraw()
        # The actual unmodified ProcessLock constructor must fail to acquire the
        # same inode. It cannot replace this retained original or release it.
        from comsol_mcp._execution_contract import ExecutionContractError
        with pytest.raises(ExecutionContractError):pub.backend.ProcessLock(s.home/'control.lock')
        assert not owner._stream.closed and pub._ROOTS[id(owner)] is owner
    s.run(main)


@pytest.mark.parametrize('fault',['get-sd-return-failed','get-sd-written-raise','get-sd-opaque'])
def test_full_prevalidation_lost_sdk_allocation_retains_original_no_free_or_lock(original_chain,fault):
    s=original_chain;s.model.fault=fault
    def main(runtime):
        with pytest.raises(n.PublisherError) as error:s.prepare(runtime,daemon=False)
        owner=error.value.owner
        assert owner._namespace.owner.opaque and owner._lock is None
        assert not s.home.joinpath('control.lock').exists()
        assert any(r.storage.value for r in owner._namespace.owner.resources if r.closer_name=='LocalFree')
        assert not s.sdk.native_effects
        calls=list(s.sdk.calls)
        with pytest.raises(n.PublisherError):owner.abort_before_publication()
        assert s.sdk.calls==calls and not s.sdk.native_effects
    s.run(main)


def test_private_listener_entry_cannot_bypass_source_publisher(original_chain,monkeypatch):
    from comsol_mcp import _windows_t037_broker as broker
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime)
        monkeypatch.setattr(s.cd,'ThreadingHTTPServer',lambda *a:pytest.fail('HTTP constructor reached'))
        with pytest.raises(broker.WindowsScmError):
            s.cd._serve_control_listener(s.home,s.daemon,owner.transport_secret(),runtime,None,s.profile.source_sha256)
        assert not s.model.events.count('write') and not s.model.events.count('create')
        owner.abort_before_publication()
    s.run(main)


def test_named_final_drift_blocks_running_even_with_original_readonly_pin(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);owner.publish(server)
        replacement=s.model.add(ntpath.join(HOME,'control.json'))
        replacement.data=b'foreign'
        with pytest.raises(n.PublisherError):owner.before_running()
        assert 4 not in s.scm.status and not owner._stream.closed
    s.run(main)


def test_write_handle_close_unknown_cannot_mark_ready_or_running(original_chain):
    s=original_chain
    def main(runtime):
        owner=s.prepare(runtime);server=owner.listener(object);s.model.fault='close-lost'
        with pytest.raises(n.PublisherError):owner.publish(server)
        events=list(s.model.events);calls=list(s.sdk.calls)
        with pytest.raises(n.PublisherError):owner.before_running()
        with pytest.raises(n.PublisherError):owner.recover_publish_ack()
        assert s.sdk.calls==calls and s.model.events==events
        assert 4 not in s.scm.status and not owner._stream.closed
    s.run(main)


@pytest.mark.parametrize('outcome',['raise','opaque'])
def test_original_singleton_effect_then_lost_constructor_result_keeps_original(original_chain,monkeypatch,outcome):
    s=original_chain;original=pub._Init;entered=[]
    def init(lock,path):
        entered.append(lock);original(lock,path)
        if outcome=='raise':raise OSError('software constructor result lost after original lock')
        return object()
    monkeypatch.setattr(pub,'_Init',init);monkeypatch.setattr(pub._Lock,'__init__',init)
    def main(runtime):
        with pytest.raises(n.PublisherError) as captured:s.prepare(runtime,daemon=False)
        owner=captured.value.owner
        assert entered==[owner._lock] and owner._held_unknown and not owner._stream.closed
        assert pub._ROOTS[id(owner)] is owner
        calls=list(s.sdk.calls)
        with pytest.raises(n.PublisherError):owner.abort_before_publication()
        assert s.sdk.calls==calls and entered==[owner._lock]
    s.run(main)


def test_owned_listener_effect_with_opaque_constructor_result_never_reconstructs(original_chain,monkeypatch):
    s=original_chain;original=pub._ServerInit;entered=[]
    def init(server,address,handler):
        entered.append(server);original(server,address,handler);return object()
    monkeypatch.setattr(pub,'_ServerInit',init);monkeypatch.setattr(SoftwareServer,'__init__',init)
    def main(runtime):
        owner=s.prepare(runtime)
        with pytest.raises(n.PublisherError):owner.listener(object)
        assert entered==[owner._server] and owner._held_unknown and not owner._stream.closed
        with pytest.raises(n.PublisherError):owner.listener(object)
        with pytest.raises(n.PublisherError):owner.abort_before_publication()
        assert entered==[owner._server] and owner._server.closed_count==0
    s.run(main)
