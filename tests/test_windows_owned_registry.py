"""Consolidated acquisition/release inventory; only SDK boundaries are synthetic."""
from types import SimpleNamespace
import ctypes as C,threading
import pytest
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp import _windows_t037 as win,_windows_t037_acl as acl
from comsol_mcp._session_server import OwnedServerLauncher,OwnedServerError
from tests.test_windows_suspended_ownership import _launch_api,_create
from tests.test_session_server import _startup_runtime


def _ancillary_entries(api):
    entries=owned.registry_for(api).unresolved()
    jobs=tuple(e for e in entries if e.label=='owned_session_job')
    if jobs:
        assert len(jobs)==1 and jobs[0].value==300 and jobs[0].registry.owner is api
        assert api._job_limit_bindings[300]['entry'] is jobs[0]
    children = {id(entry) for child in getattr(api,'_retained_children',{}).values()
                for entry in (child._process_entry,child._thread_entry) if entry is not None}
    return tuple(e for e in entries if e.label!='owned_session_job' and id(e) not in children)


def _factory_guard(profile,api):
    factory=win.T037SessionProcessFactory(profile,'owned-registry-test',api=api);factory._job=300
    with pytest.raises(win.WindowsT037Error) as error:factory.close()
    assert error.value.code=='NATIVE_RESOURCE_RELEASE_UNKNOWN' and factory._job==300


def _actual_gate(monkeypatch,original):
    fake=acl.check_suspended_child_dacl
    adapter=next(x.cell_contents for x in fake.__closure__ if hasattr(x.cell_contents,'bound_statistics'))
    monkeypatch.setattr(acl,'check_suspended_child_dacl',original)
    monkeypatch.setattr(acl,'NativeChildAclApi',lambda:adapter)
    return adapter

@pytest.mark.parametrize('fault',['current_token','owned_stdio','attribute_abort'])
def test_prechild_original_returns_are_owned_before_any_subsequent_validation(monkeypatch,tmp_path,fault):
    original=acl.check_suspended_child_dacl
    profile,api,events,state=_launch_api(monkeypatch,cleanup_fail=True);_actual_gate(monkeypatch,original)
    normal=api.close_handle
    def close(handle):
        value=getattr(handle,'value',handle)
        if value==(55 if fault=='current_token' else 100) and fault!='attribute_abort':raise OSError('native release failed')
        return normal(handle)
    api.close_handle=close
    if fault=='owned_stdio':api._stdio_source=lambda *a,**k:(100,True)
    if fault=='attribute_abort':
        monkeypatch.setattr(C,'get_last_error',lambda:122,raising=False)
        def init(pointer,count,flags,out):C.cast(out,C.POINTER(C.c_size_t)).contents.value=32;return bool(pointer)
        api.k.InitializeProcThreadAttributeList=init;api.k.UpdateProcThreadAttribute=lambda *a:0
        def delete(pointer):raise OSError('attribute native delete failed')
        api.k.DeleteProcThreadAttributeList=delete;api._attribute_list=win._Win32LaunchApi._attribute_list.__get__(api)
    launcher=OwnedServerLauncher(process_factory=lambda *a,**k:_create(profile,api),platform_name='darwin')
    with pytest.raises(OwnedServerError) as captured:launcher.start(_startup_runtime(tmp_path),'project','session')
    assert not captured.value.uncertain and captured.value.handle is None and 'CREATE_SUSPENDED' not in events
    entries=_ancillary_entries(api);assert len(entries)==1 and entries[0].state=='UNKNOWN'
    assert entries[0].label=={'current_token':'current_token','owned_stdio':'owned_stdio_source','attribute_abort':'attribute_list'}[fault]
    if fault=='attribute_abort':assert entries[0].keepalive and len(entries[0].keepalive[0])==32
    _factory_guard(profile,api)

@pytest.mark.parametrize('fault',['open_source','fingerprint','hash_close'])
def test_image_root_registry_exists_before_open_source_fingerprint_and_hash_cleanup(monkeypatch,fault):
    from comsol_mcp import _windows_parameter_publish as files_module
    profile,api,events,state=_launch_api(monkeypatch);opened=[];attempted=[];failure={'on':True}
    class Files:
        def open_security_path(self,*a,**k):opened.extend([51,52]);return [51,52]
        def open_source(self,*a):
            if fault=='open_source':raise OSError('image open failed')
            opened.append(53);return 53
        def source_fingerprint(self,h):return ((1,2),10,3,4,2 if fault=='fingerprint' else 1)
        def close(self,h):
            attempted.append(h)
            if failure['on'] and h==(53 if fault=='hash_close' else 52):raise OSError('native image close failed')
    files=Files();monkeypatch.setattr(files_module,'_API',lambda:files)
    api._open_image=win._Win32LaunchApi._open_image.__get__(api);api._hash_image_handle=lambda *a:profile.server_image_sha256
    with pytest.raises(Exception):
        if fault=='hash_close':api.hash_image(r'C:\review\server.exe')
        else:api._open_image(r'C:\review\server.exe')
    assert set(attempted)==set(opened)
    entries=_ancillary_entries(api);assert len(entries)==1 and entries[0].adapter is files
    _factory_guard(profile,api);failure['on']=False
    owned.registry_for(api).retry_owned(entries[0],api)
    assert not _ancillary_entries(api)

@pytest.mark.parametrize('fault',['info','ancestor_close'])
def test_security_child_is_registered_before_info_and_every_ancestor_close_is_attempted(fault):
    from comsol_mcp import _windows_parameter_publish as files_module
    api=files_module._API.__new__(files_module._API);closed=[]
    api.open=lambda *a,**k:71;api._open_relative=lambda *a,**k:72
    api.info=lambda *a,**k:(_ for _ in ()).throw(OSError('info failed'))
    def close(h):
        closed.append(h)
        if fault=='ancestor_close' and h==71:raise OSError('ancestor close failed')
    api.close=close
    with pytest.raises(OSError):api.open_security_path(r'C:\review',directory=True)
    assert closed==[72,71]
    entries=_ancillary_entries(api)
    assert (len(entries),entries[0].value if entries else None)==((1,71) if fault=='ancestor_close' else (0,None))


def test_native_relative_open_registers_success_output_before_io_information_check(monkeypatch):
    from comsol_mcp import _windows_parameter_publish as files_module
    monkeypatch.setattr(C,'get_last_error',lambda:5,raising=False)
    monkeypatch.setattr(C,'WinError',lambda code:OSError(code,'native CloseHandle false'),raising=False)
    api=files_module._API.__new__(files_module._API);attempted=[]
    def create(out,access,attr,io,*args):
        C.cast(out,C.POINTER(files_module.HANDLE)).contents.value=73
        C.cast(io,C.POINTER(files_module._IoStatusBlock)).contents.Information=99
        return 0
    api.nt=SimpleNamespace(NtCreateFile=create);api.k=SimpleNamespace(CloseHandle=lambda h:attempted.append(h.value) or 0)
    api.check=lambda ok:None if ok else (_ for _ in ()).throw(OSError('native CloseHandle false'))
    with pytest.raises(OSError):api._open_relative(71,'child',directory=True)
    entries=_ancillary_entries(api)
    assert attempted==[73] and len(entries)==1 and entries[0].value==73 and entries[0].state=='UNKNOWN'

@pytest.mark.parametrize('fault',['impersonation','chain'])
def test_dacl_helper_joins_same_launch_registry_and_terminal_child_does_not_release_ancillary(monkeypatch,tmp_path,fault):
    original=acl.check_suspended_child_dacl;profile,api,events,state=_launch_api(monkeypatch,cleanup_fail=True)
    selected=_actual_gate(monkeypatch,original);attempted=[]
    if fault=='impersonation':
        normal=selected.close_token
        def close(h):
            attempted.append(h)
            if h==88:raise OSError('actual impersonation release failed')
            return normal(h)
        selected.close_token=close
    else:
        normal=selected.files.close
        def close(h):
            attempted.append(h)
            if h==11:raise OSError('actual chain release failed')
            return normal(h)
        selected.files.close=close
    launcher=OwnedServerLauncher(process_factory=lambda *a,**k:_create(profile,api),platform_name='darwin')
    with pytest.raises(OwnedServerError) as captured:launcher.start(_startup_runtime(tmp_path),'project','session')
    holder=captured.value.handle;assert captured.value.uncertain and holder.process._owner is holder
    registry=owned.registry_for(api)
    assert registry is owned.registry_for(selected) is owned.registry_for(selected.files)
    entries=_ancillary_entries(api);assert len(entries)==1
    if fault=='chain':assert 11 in attempted and 10 in attempted
    state['cleanup_fail']=False;state['alive']=False  # SDK observes exit without repeating termination.
    launcher.stop(holder);assert holder.process._closed
    _factory_guard(profile,api)


def test_sid_string_nonnull_localfree_retains_pointer_and_refuses_factory(monkeypatch):
    profile,api,events,state=_launch_api(monkeypatch);storage=C.create_unicode_buffer('S-1-5-21-100-200-300-1001')
    address=C.addressof(storage);calls=[]
    api.a.IsValidSid=lambda p:1;api.a.GetLengthSid=lambda p:8
    def convert(pointer,out):C.cast(out,C.POINTER(C.c_void_p)).contents.value=address;return 1
    api.a.ConvertSidToStringSidW=convert;api.k.LocalFree=lambda p:calls.append(p.value) or p.value
    with pytest.raises(owned.NativeResourceError):api._sid_text(C.c_void_p(99))
    entries=_ancillary_entries(api);assert calls==[address] and entries[0].value==address
    assert entries[0].mode=='LOCALFREE';_factory_guard(profile,api)


def test_toolhelp_failed_close_retains_exact_snapshot_on_shared_launch_registry(monkeypatch):
    from comsol_mcp import _windows_t037_child as child
    query_class=child.NativeChildProcessApi
    profile,api,events,state=_launch_api(monkeypatch);query=query_class.__new__(query_class)
    def first(h,out):
        row=C.cast(out,C.POINTER(child.PROCESSENTRY32W)).contents;row.th32ProcessID=456;row.th32ParentProcessID=55;return 1
    query.k=SimpleNamespace(CreateToolhelp32Snapshot=lambda *a:81,Process32FirstW=first,CloseHandle=lambda h:0)
    owned.attach_registry(query,owned.registry_for(api))
    with pytest.raises(child.ChildObservationError):query.parent_pid(456)
    entries=_ancillary_entries(api);assert len(entries)==1 and entries[0].value==81
    assert entries[0].adapter is query;_factory_guard(profile,api)

@pytest.mark.parametrize('fault',['bound_token','descriptor'])
def test_acl_bound_token_and_descriptor_allocate_owner_before_validation(monkeypatch,fault):
    from tests.test_windows_t037_native import _security_descriptor
    profile,launch,events,state=_launch_api(monkeypatch);selected=acl.NativeChildAclApi.__new__(acl.NativeChildAclApi)
    owned.attach_registry(selected,owned.registry_for(launch));closed=[]
    if fault=='bound_token':
        def token(p,access,out):C.cast(out,C.POINTER(acl.HANDLE)).contents.value=99;return 1
        selected.a=SimpleNamespace(OpenProcessToken=token);selected.k=SimpleNamespace(CloseHandle=lambda h:closed.append(h.value) or 0)
        selected.statistics=lambda t:b'actual-token-query'
        with pytest.raises(owned.NativeResourceError):selected.bound_statistics(200,55)
        expected=99
    else:
        raw=_security_descriptor();storage=C.create_string_buffer(raw);expected=C.addressof(storage)
        def security(h,kind,flags,o,g,d,s,sd):
            C.cast(sd,C.POINTER(C.c_void_p)).contents.value=expected
            C.cast(o,C.POINTER(C.c_void_p)).contents.value=10;C.cast(g,C.POINTER(C.c_void_p)).contents.value=20;return 0
        selected.a=SimpleNamespace(GetSecurityInfo=security,GetSecurityDescriptorLength=lambda sd:0)
        selected.k=SimpleNamespace(LocalFree=lambda p:closed.append(p.value) or p.value)
        with pytest.raises(Exception):list(selected.descriptor(77))
    entries=_ancillary_entries(launch)
    assert len(entries)==1 and entries[0].value==expected and closed==[expected]
    _factory_guard(profile,launch)


def test_registry_simultaneous_failures_attempt_every_release_retain_originals_and_reject_metadata():
    api=object();adapter=SimpleNamespace();registry=owned.registry_for(adapter);attempted=[];state={'fail':True}
    def close(handle):
        attempted.append(handle)
        if state['fail']:return False
        return True
    scope=registry.scope();entries=[registry.acquire(adapter,h,close,mode='BOOL',label=str(h)) for h in (10,11,12)]
    failures=registry.drain(scope)
    assert attempted==[12,11,10] and len(failures)==3 and len(registry.unresolved())==3
    registry.drain(scope);assert attempted==[12,11,10] # Never silently retry UNKNOWN.
    for fake in ({'handle':10,'closed':True},SimpleNamespace(registry=registry,state='RELEASED')):
        with pytest.raises(owned.NativeResourceError):registry.retry_owned(fake,adapter)
    with pytest.raises(owned.NativeResourceError):registry.retry_owned(entries[0],api)
    state['fail']=False
    for entry in entries:registry.retry_owned(entry,adapter);registry.retry_owned(entry,adapter)
    assert attempted==[12,11,10,10,11,12] and not registry.unresolved()


def test_same_api_scoped_cleanup_cannot_close_another_threads_acquisitions():
    adapter=SimpleNamespace();registry=owned.registry_for(adapter);scope=registry.scope();calls=[]
    mine=registry.acquire(adapter,10,lambda h:calls.append(h),label='mine')
    other=[]
    def acquire_other():other.append(registry.acquire(adapter,11,lambda h:calls.append(h),label='other'))
    t=threading.Thread(target=acquire_other);t.start();t.join()
    registry.drain(scope)
    assert calls==[10] and registry.unresolved()==(other[0],)
    registry.retry_owned(other[0],adapter);assert calls==[10,11]

@pytest.mark.parametrize('fault',['normal','sid_free'])
def test_actual_prebirth_factory_retains_original_observer_api_across_gc_and_explicit_retry(monkeypatch,fault):
    import gc,weakref
    from comsol_mcp import _windows_t037_resume as resume,_windows_t037_native as native
    from tests.test_windows_t037_native import _FakeApi,FP
    from tests.test_windows_host_change import _profile
    from comsol_mcp._windows_t037_native import HANDLE,DWORD,BOOL
    profile=win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY');factory=win.T037SessionProcessFactory(profile,'prebirth-live-owner')
    api=win._Win32ReadOnlyApi.__new__(win._Win32ReadOnlyApi)
    api.C=C;api.W=SimpleNamespace(HANDLE=HANDLE,DWORD=DWORD,BOOL=BOOL,LPWSTR=C.c_wchar_p)
    text=C.create_unicode_buffer(profile.service_account_sid);address=C.addressof(text)
    state={'fail':fault=='sid_free'};calls=[]
    def free(pointer):calls.append(('free',pointer.value));return pointer.value if state['fail'] else None
    def token(process,access,out):C.cast(out,C.POINTER(HANDLE)).contents.value=91;return 1
    def info(token,kind,buffer,capacity,out):
        size=4 if kind==29 else 16 if kind==2 else 24
        C.cast(out,C.POINTER(DWORD)).contents.value=size
        if buffer is None:return 0
        C.memset(buffer,0,size)
        if kind in {1,25}:C.cast(buffer,C.POINTER(C.c_void_p)).contents.value=int(C.cast(buffer,C.c_void_p).value)+16
        return 1
    def convert(pointer,out):C.cast(out,C.POINTER(C.c_void_p)).contents.value=address;return 1
    api.k=SimpleNamespace(OpenProcess=lambda *a:90,CloseHandle=lambda h:calls.append(('close',getattr(h,'value',h))) or 1,
        LocalFree=free)
    api.a=SimpleNamespace(OpenProcessToken=token,GetTokenInformation=info,IsValidSid=lambda p:1,
        GetLengthSid=lambda p:8,ConvertSidToStringSidW=convert)
    api._check=lambda success,label:None if success else (_ for _ in ()).throw(OSError(label))
    api.process_times=lambda handle:(123,None)
    api.process_image_path=lambda handle:profile._launch_image('launcher').path
    api.is_process_in_job=lambda *args:False
    monkeypatch.setattr(C,'get_last_error',lambda:122,raising=False)
    observer_class=win.WindowsT037Observer
    observer=observer_class(platform_name='windows',api=api)
    observer_ref=weakref.ref(observer);api_ref=weakref.ref(api)
    observer_box=[observer]
    def make_observer():return observer_box.pop()
    monkeypatch.setattr(win,'WindowsT037Observer',make_observer)
    monkeypatch.setattr(win,'windows_host_fingerprint',lambda **_kw:profile.host_fingerprint_sha256)
    adapter_class=native.WindowsNativePolicyAdapter
    monkeypatch.setattr(native,'WindowsNativePolicyAdapter',lambda:adapter_class(api=_FakeApi()))
    with pytest.raises(Exception):resume.refuse_unverified_prebirth(profile,owner=factory)
    registry=owned.registry_for(factory)
    assert factory._prebirth_observers[0] is observer
    assert owned.registry_for(api) is registry and registry.owner is factory
    assert factory.profile.source_sha256==profile.source_sha256
    assert ('close',90) in calls and ('close',91) in calls
    del observer,api;gc.collect()
    assert observer_ref() is not None and api_ref() is not None
    if fault=='sid_free':
        entries=registry.unresolved();assert len(entries)==1 and entries[0].value==address
        assert entries[0].adapter is api_ref() and entries[0].closer is free
        with pytest.raises(win.WindowsT037Error):factory.close()
        state['fail']=False
        registry.retry_owned(entries[0],factory);before=calls.count(('free',address))
        registry.retry_owned(entries[0],factory)
        assert calls.count(('free',address))==before==2
    assert not registry.unresolved();factory.close();assert factory._closed


def test_default_prebirth_cannot_spawn_a_temporary_native_observer_without_lifetime_owner(monkeypatch):
    from comsol_mcp import _windows_t037_resume as resume
    from tests.test_windows_host_change import _profile
    def forbidden():raise AssertionError('native observer must not be created without private lifetime owner')
    monkeypatch.setattr(win,'WindowsT037Observer',forbidden)
    with pytest.raises(Exception) as error:resume.refuse_unverified_prebirth(win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY'))
    assert error.value.details['reasons']==['NATIVE_OBSERVATION_OWNER_REQUIRED']


def _standalone_publisher():
    import importlib.util
    from pathlib import Path
    source=Path(win.__file__).with_name('_windows_parameter_publish.py')
    spec=importlib.util.spec_from_file_location('standalone_parameter_publish_r8',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert not module.__package__
    return module


def test_standalone_spec_publication_keeps_single_file_sdk_contract(monkeypatch):
    from contextlib import contextmanager
    import hashlib
    module=_standalone_publisher();api=module._API.__new__(module._API)
    calls=[];payload=b'owned standalone control';data=bytearray()
    def ntcreate(output,*args):
        C.cast(output,C.POINTER(module.HANDLE)).contents.value=701
        C.cast(args[2],C.POINTER(module._IoStatusBlock)).contents.Information=2
        calls.append('create');return 0
    api.nt=SimpleNamespace(NtCreateFile=ntcreate)
    api.k=SimpleNamespace(CloseHandle=lambda h:calls.append(('close',h.value)) or 1)
    api.exists_child=lambda *args:False
    api.info=lambda *args:((2,3),len(data))
    api.write=lambda handle,block:data.extend(block)
    api.flush=lambda handle:None
    api.rename=lambda *args:calls.append('rename')
    api.readback=lambda handle:(hashlib.sha256(data).hexdigest(),len(data))
    api.child_identity=lambda *args:(2,3)
    @contextmanager
    def pinned(*args,**kwargs):yield api,700,('control.bin',),()
    monkeypatch.setattr(module,'pinned_parent',pinned)
    result=module.publish('C:\\project','control.bin',payload,_api=api)
    assert result['sha256']==hashlib.sha256(payload).hexdigest()
    assert bytes(data)==payload and calls==['create','rename',('close',701)]
    assert not hasattr(api,'_native_owned_registry')


def test_standalone_explicit_invalid_registry_refuses_before_native_effect():
    module=_standalone_publisher();api=module._API.__new__(module._API);calls=[]
    api._native_owned_registry={'owner':'wire'}
    api.k=SimpleNamespace(CreateFileW=lambda *args:calls.append('create') or 702,
                          CloseHandle=lambda *args:calls.append('close') or 1)
    api.nt=SimpleNamespace(NtCreateFile=lambda *args:calls.append('relative') or 0)
    for operation in (lambda:api.open('C:\\x'),lambda:api._open_relative(700,'x'),lambda:api.close(702)):
        with pytest.raises(owned.NativeResourceError):operation()
    assert calls==[]
