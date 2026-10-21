"""Actual policy/controller/native helper ownership, synthetic SDK boundaries only."""
import ctypes as C,gc,weakref
from types import SimpleNamespace
import pytest
from comsol_mcp import _windows_t037 as win,_windows_t037_native as native,_windows_owned_resources as owned
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_preflight_lifetime import _sdk
from tests.test_windows_host_change import _profile


def _daemon_sdk(tmp_path,monkeypatch,unknown):
    profile=win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY')
    daemon=ControlDaemon(tmp_path/'control');daemon._windows_t037_profile=profile
    monkeypatch.setattr(daemon,'_authorize_server_control',lambda *a:None)
    state,calls,refs,free=_sdk(monkeypatch,profile,unknown)
    api=refs[1]();api.k.GetCurrentProcess=lambda:-1
    storage=C.create_unicode_buffer(profile.service_account_sid)
    api.a.ConvertSidToStringSidW=lambda p,out:(setattr(C.cast(out,C.POINTER(C.c_void_p)).contents,'value',C.addressof(storage)) or 1)
    return daemon,profile,state,calls,refs,free

@pytest.mark.parametrize('unknown',[True,False])
def test_actual_first_policy_owner_gc_repeat_shutdown_and_original_recovery(tmp_path,monkeypatch,unknown):
    daemon,profile,state,calls,refs,free=_daemon_sdk(tmp_path,monkeypatch,unknown)
    with pytest.raises(ExecutionContractError) as captured:
        daemon._dispatch_session_start({'project_id':'p','runtime_id':'r','idempotency_key':'first'}, {})
    assert captured.value.code=='T037_POLICY_UNVERIFIED' and not captured.value.details['dispatch_performed']
    assert not daemon._session_t037_process_factories
    assert daemon.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert daemon.store.db.execute('SELECT count(*) FROM operations').fetchone()[0]==0
    del captured;gc.collect();before=list(calls)
    if unknown:
        owner=daemon._t037_policy_observation_owner;assert owner is not None
        assert all(ref() is not None for ref in refs)
        entry,=owner.registry.unresolved();assert entry.closer is free and entry.adapter is refs[1]()
        assert entry.registry.owner is owner and owner.observer is refs[0]()
        with pytest.raises(ExecutionContractError) as repeated:daemon._require_t037_policy('session.connect')
        assert repeated.value.details['reason_codes']==['NATIVE_POLICY_OWNER_RECOVERY_REQUIRED'] and calls==before
        with pytest.raises(owned.NativeResourceError):owner.recover_owned(object())
        with pytest.raises(owned.NativeResourceError):owner.recover_owned(daemon)
        assert calls[-1][0]=='free' and daemon._t037_policy_observation_owner is owner
        before=list(calls);daemon.close();gc.collect()
        assert daemon._t037_policy_observation_owner is owner and all(ref() is not None for ref in refs) and calls==before
        state['unknown']=False;owner.recover_owned(daemon)
        assert daemon._t037_policy_observation_owner is None and not owner.registry.unresolved()
        with pytest.raises(owned.NativeResourceError):owner.recover_owned(daemon)
        assert calls.count(calls[-1])==3 # initial failure, explicit failure, explicit success only.
    else:
        assert daemon._t037_policy_observation_owner is None
        assert all(ref() is None for ref in refs)
        daemon.close();assert calls==before

@pytest.mark.parametrize('unknown',[True,False])
def test_actual_require_retains_temporary_native_adapter_scm_resources(tmp_path,monkeypatch,unknown):
    cls=native.WindowsNativePolicyAdapter
    daemon,profile,state,calls,refs,free=_daemon_sdk(tmp_path,monkeypatch,False)
    api=refs[1]();api.volume_snapshot=lambda path:{'volume_guid_path':'volume','filesystem':'NTFS','serial_number':1,'flags':0,'total_bytes':100,'caller_available_bytes':90,'free_bytes':90};del api
    policy=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi)
    close_state={'unknown':unknown};scm=[]
    def close(h):scm.append(h);return 0 if close_state['unknown'] else 1
    policy._advapi=SimpleNamespace(OpenSCManagerW=lambda *a:31,OpenServiceW=lambda *a:32,
        QueryServiceConfigW=lambda *a:False,CloseServiceHandle=close)
    monkeypatch.setattr(C,'get_last_error',lambda:122,raising=False)
    box=[policy];api_ref=weakref.ref(policy);del policy
    monkeypatch.setattr(native,'WindowsNativePolicyAdapter',lambda:cls(api=box.pop()))
    with pytest.raises(ExecutionContractError):daemon._require_t037_policy('host.observe')
    gc.collect();assert sorted(scm)==[31,32]
    if unknown:
        owner=daemon._t037_policy_observation_owner
        assert owner is not None and api_ref() is not None
        assert owner.observer._native_policy_adapters[0].api is api_ref()
        assert sorted(e.value for e in owner.registry.unresolved())==[31,32]
        before=list(scm)
        with pytest.raises(ExecutionContractError):daemon._require_t037_policy('runtime.probe')
        assert scm==before
        close_state['unknown']=False;owner.recover_owned(daemon)
        assert sorted(scm)==[31,31,32,32] and not owner.registry.unresolved()
    else:assert daemon._t037_policy_observation_owner is None and api_ref() is None
    assert daemon.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    daemon.close()

@pytest.mark.parametrize('result',[0,1,None,False,True,C.c_uint32(0),C.c_uint32(7)])
def test_registry_dword0_requires_actual_zero_status(result):
    api=SimpleNamespace();r=owned.registry_for(api);entry=r.acquire(api,123,lambda h:result,mode='DWORD0')
    if type(getattr(result,'value',result)) is int and getattr(result,'value',result)==0:entry.release_owned(api);assert not r.unresolved()
    else:
        with pytest.raises(owned.NativeResourceError):entry.release_owned(api)
        assert r.unresolved()==(entry,)


def test_numeric_resources_from_distinct_sdk_domains_cannot_alias():
    api=SimpleNamespace();r=owned.registry_for(api);calls=[]
    a=r.acquire(api,10,lambda h:calls.append('scm') or 1,mode='BOOL',domain='scm')
    b=r.acquire(api,10,lambda h:calls.append('engine') or 0,mode='DWORD0',domain='wfp_engine')
    assert a is not b and len(r.unresolved())==2
    a.release_owned(api);b.release_owned(api);assert calls==['scm','engine']


def test_actual_wfp_page_free_retains_original_callback_and_pointer(monkeypatch):
    from tests.test_windows_t037_native import _page_allocation_cleanup_probe
    api,sdk,address=_page_allocation_cleanup_probe(monkeypatch,enum_status=88,free_raises=True)
    with pytest.raises(native.NativePolicyError):api.observe_wfp_snapshot()
    registry=owned.registry_for(api);entry,=registry.unresolved()
    assert entry.value==address and entry.domain=='wfp_memory' and entry.state=='UNKNOWN'
    sdk.FwpmFreeMemory0=lambda *a:pytest.fail('new callback must not replace original')
    with pytest.raises(RuntimeError):registry.retry_owned(entry,api)
    assert len(sdk.free_values)==2 and entry in registry.unresolved()


def test_actual_enum_failure_keeps_original_engine_until_same_enum_release():
    state={'fail':True};calls=[]
    sdk=SimpleNamespace()
    def open_engine(*a):C.cast(a[-1],C.POINTER(native.HANDLE)).contents.value=40;return 0
    def create(engine,template,out):C.cast(out,C.POINTER(native.HANDLE)).contents.value=41;return 0
    def enum(*a):return 88
    def destroy(engine,h):calls.append(('enum',engine.value,h.value));return 77 if state['fail'] else 0
    sdk.FwpmEngineOpen0=open_engine;sdk.FwpmEngineClose0=lambda h:calls.append(('engine',h.value)) or 0
    sdk.FwpmTransactionBegin0=lambda *a:0;sdk.FwpmTransactionAbort0=lambda *a:0
    sdk.FwpmProviderCreateEnumHandle0=create;sdk.FwpmProviderEnum0=enum;sdk.FwpmProviderDestroyEnumHandle0=destroy
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi);api._fwpu=sdk
    with pytest.raises(native.NativePolicyError):api.observe_wfp_snapshot()
    registry=owned.registry_for(api);entries=registry.unresolved()
    assert len(entries)==2 and calls==[('enum',40,41)]
    engine=next(e for e in entries if e.label=='wfp_engine');enumerator=next(e for e in entries if e.label.startswith('wfp_enum:'))
    with pytest.raises(native.NativePolicyError):registry.retry_owned(engine,api)
    assert calls==[('enum',40,41)]
    state['fail']=False;registry.retry_owned(enumerator,api);registry.retry_owned(engine,api)
    assert calls==[('enum',40,41),('enum',40,41),('engine',40)] and not registry.unresolved()

@pytest.mark.parametrize('operation',['sid','argv','security','app_id'])
@pytest.mark.parametrize('unknown',[True,False])
def test_native_pointer_owners_registered_before_validation_and_original_release(monkeypatch,operation,unknown):
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi);calls=[];state={'unknown':unknown}
    text=C.create_unicode_buffer('S-1-5-21-1000');address=C.addressof(text)
    def localfree(h):calls.append(('local',h.value));return h.value if state['unknown'] else None
    def fwpfree(p):
        h=C.cast(p,C.POINTER(C.c_void_p)).contents.value;calls.append(('wfp',h))
        if state['unknown']:raise RuntimeError('original FwpmFreeMemory boundary failed')
    api._kernel32=SimpleNamespace(LocalFree=localfree);api._fwpu=SimpleNamespace(FwpmFreeMemory0=fwpfree)
    def convert(sid,out):C.cast(out,C.POINTER(C.c_void_p)).contents.value=address;return 1
    api._advapi=SimpleNamespace(IsValidSid=lambda p:1,ConvertSidToStringSidW=convert,GetSecurityDescriptorLength=lambda p:0)
    if operation=='sid':action=lambda:api._sid_string(C.c_void_p(8))
    elif operation=='argv':
        vector=(C.c_void_p*1)(address);vector_address=C.addressof(vector)
        def commandline(line,count):C.cast(count,C.POINTER(C.c_int32)).contents.value=0;return C.cast(vector,C.POINTER(C.c_void_p))
        monkeypatch.setattr(C,'WinDLL',lambda *a,**k:SimpleNamespace(CommandLineToArgvW=commandline),raising=False)
        action=lambda:api._command_line_to_argv('x') # valid allocation, deliberately invalid argc.
        address=vector_address
    elif operation=='security':
        def descriptor(*args):C.cast(args[-1],C.POINTER(C.c_void_p)).contents.value=address;return 0
        api._fwpu.FwpmFilterGetSecurityInfoByKey0=descriptor
        action=lambda:api._wfp_security_sha(40,'Filter',native.GUID()) # invalid descriptor length after acquisition.
    else:
        blob=native.FWP_BYTE_BLOB(0,None);address=C.addressof(blob)
        def appid(path,out):C.cast(out,C.POINTER(C.c_void_p)).contents.value=address;return 0
        api._fwpu.FwpmGetAppIdFromFileName0=appid
        action=lambda:api.observe_application_id('C:\\launcher.exe') # invalid allocated blob after acquisition.
    if operation=='sid' and not unknown:assert action()=='S-1-5-21-1000'
    else:
        with pytest.raises(Exception):action()
    registry=owned.registry_for(api)
    if unknown:
        entry,=registry.unresolved();assert entry.value==address and entry.state=='UNKNOWN'
        assert calls==[('local' if operation in {'sid','argv'} else 'wfp',address)]
        state['unknown']=False;registry.retry_owned(entry,api);registry.retry_owned(entry,api)
        assert len(calls)==2 and not registry.unresolved()
    else:assert not registry.unresolved() and len(calls)==1

@pytest.mark.parametrize('fault',['rows','token','reader_sid','normal'])
def test_actual_wts_nested_token_reader_joins_original_registry(monkeypatch,tmp_path,fault):
    daemon,profile,state,read_calls,refs,free=_daemon_sdk(tmp_path,monkeypatch,fault=='reader_sid')
    reader=refs[1]();reader_storage=C.create_unicode_buffer(profile.service_account_sid)
    reader.a.ConvertSidToStringSidW=lambda p,out:(setattr(C.cast(out,C.POINTER(C.c_void_p)).contents,'value',C.addressof(reader_storage)) or 1)
    monkeypatch.setattr(win,'_Win32ReadOnlyApi',lambda:reader)
    class Row(C.Structure):_fields_=[('SessionId',native.DWORD),('pWinStationName',C.c_void_p),('State',C.c_int)]
    rows=(Row*1)(Row(7,None,0));calls=[];release_state={'fail':fault in {'rows','token'}}
    def enumerate_sessions(server,reserved,version,out,count):
        C.cast(out,C.POINTER(C.c_void_p)).contents.value=C.addressof(rows)
        C.cast(count,C.POINTER(native.DWORD)).contents.value=1025 if fault=='rows' else 1
        return 1
    def free_rows(ptr):
        calls.append(('rows',ptr.value))
        if fault=='rows' and release_state['fail']:raise RuntimeError('original WTSFreeMemory failed')
    def query(sid,out):C.cast(out,C.POINTER(native.HANDLE)).contents.value=92;return 1
    def close(token):calls.append(('token',token.value));return 0 if fault=='token' and release_state['fail'] else 1
    wts=SimpleNamespace(WTSEnumerateSessionsW=enumerate_sessions,WTSFreeMemory=free_rows,WTSQueryUserToken=query)
    kernel=SimpleNamespace(CloseHandle=close)
    monkeypatch.setattr(C,'WinDLL',lambda name,**kwargs:wts if name=='wtsapi32' else kernel,raising=False)
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi)
    if fault=='normal':assert api.observe_active_interactive_sids()==(profile.service_account_sid,)
    else:
        with pytest.raises(Exception):api.observe_active_interactive_sids()
    registry=owned.registry_for(api)
    if fault!='normal':
        entry,=registry.unresolved()
        assert entry.value==(C.addressof(rows) if fault=='rows' else 92 if fault=='token' else C.addressof(reader_storage))
        assert entry.state=='UNKNOWN'
        if fault=='reader_sid':assert owned.registry_for(reader) is registry and api._token_readers[0] is reader
        release_state['fail']=False;state['unknown']=False
        registry.retry_owned(entry,api);assert not registry.unresolved()
    else:assert not registry.unresolved()
    daemon.close()

@pytest.mark.parametrize('kind',['Provider','SubLayer','Filter','ProviderContext','Layer'])
@pytest.mark.parametrize('unknown',[True,False])
def test_all_five_catalog_enum_callbacks_retain_exact_engine_and_original_destroy(kind,unknown):
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi);sdk=SimpleNamespace();calls=[];state={'unknown':unknown}
    def create(engine,template,out):C.cast(out,C.POINTER(native.HANDLE)).contents.value=41;return 0
    def enum(engine,handle,size,out,count):C.cast(count,C.POINTER(native.DWORD)).contents.value=0;return 0
    def destroy(engine,h):calls.append((engine.value,h.value));return 7 if state['unknown'] else 0
    setattr(sdk,'Fwpm'+kind+'CreateEnumHandle0',create);setattr(sdk,'Fwpm'+kind+'Enum0',enum);setattr(sdk,'Fwpm'+kind+'DestroyEnumHandle0',destroy);api._fwpu=sdk;engine=native.HANDLE(40)
    if unknown:
        with pytest.raises(native.NativePolicyError):api._enum(engine,kind,native._WfpBudget.start(kind_count=5))
        registry=owned.registry_for(api);entry,=registry.unresolved();assert engine in entry.keepalive and entry.label=='wfp_enum:'+kind
        setattr(sdk,'Fwpm'+kind+'DestroyEnumHandle0',lambda *a:pytest.fail('replacement callback used'))
        state['unknown']=False;registry.retry_owned(entry,api);registry.retry_owned(entry,api)
        assert calls==[(40,41),(40,41)] and not registry.unresolved()
    else:assert api._enum(engine,kind,native._WfpBudget.start(kind_count=5))==[] and calls==[(40,41)] and not owned.registry_for(api).unresolved()

@pytest.mark.parametrize('unknown',[True,False])
def test_actual_lookup_account_sid_localfree_is_confirmed_not_discarded(monkeypatch,unknown):
    api=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi);calls=[];state={'unknown':unknown}
    text=C.create_unicode_buffer('S-1-5-21-1000');address=C.addressof(text)
    def lookup(system,account,sid,needed,domain,domain_needed,use):
        C.cast(needed,C.POINTER(native.DWORD)).contents.value=12
        C.cast(domain_needed,C.POINTER(native.DWORD)).contents.value=1
        return 0 if sid is None else 1
    def convert(sid,out):C.cast(out,C.POINTER(C.c_void_p)).contents.value=address;return 1
    def free(h):calls.append(h.value);return h.value if state['unknown'] else None
    api._advapi=SimpleNamespace(LookupAccountNameW=lookup,ConvertSidToStringSidW=convert)
    api._kernel32=SimpleNamespace(LocalFree=free);monkeypatch.setattr(C,'get_last_error',lambda:122,raising=False)
    if unknown:
        with pytest.raises(owned.NativeResourceError):api._lookup_sid('SERVICE')
        registry=owned.registry_for(api);entry,=registry.unresolved();assert entry.value==address and len(entry.keepalive)==3
        state['unknown']=False;registry.retry_owned(entry,api);assert calls==[address,address] and not registry.unresolved()
    else:assert api._lookup_sid('SERVICE')=='S-1-5-21-1000' and calls==[address]


def test_actual_factory_prebirth_nested_scm_adapter_has_same_long_lived_owner(tmp_path,monkeypatch):
    from comsol_mcp import _windows_t037_resume as resume
    cls=native.WindowsNativePolicyAdapter
    daemon,profile,state,calls,refs,free=_daemon_sdk(tmp_path,monkeypatch,False)
    policy=native.CtypesWindowsPolicyApi.__new__(native.CtypesWindowsPolicyApi);sdk_calls=[];failed={'value':True}
    def close(handle):sdk_calls.append(handle);return 0 if failed['value'] else 1
    policy._advapi=SimpleNamespace(OpenSCManagerW=lambda *a:31,OpenServiceW=lambda *a:32,
        QueryServiceConfigW=lambda *a:False,CloseServiceHandle=close)
    box=[policy];ref=weakref.ref(policy);del policy
    monkeypatch.setattr(native,'WindowsNativePolicyAdapter',lambda:cls(api=box.pop()))
    factory=daemon._new_t037_session_factory('p','session-prebirth-scm');factory_ref=weakref.ref(factory)
    with pytest.raises(Exception):resume.refuse_unverified_prebirth(profile,owner=factory)
    assert sorted(sdk_calls)==[31,32]
    registry=owned.registry_for(factory);del factory;gc.collect()
    assert ref() is not None and factory_ref() is not None
    assert factory_ref()._prebirth_observers[0]._native_policy_adapters[0].api is ref()
    assert sorted(e.value for e in registry.unresolved())==[31,32] and registry.owner is factory_ref()
    with pytest.raises(win.WindowsT037Error):daemon._close_t037_session_factory(('p','session-prebirth-scm'))
    assert sorted(sdk_calls)==[31,32]
    failed['value']=False
    for entry in reversed(registry.unresolved()):registry.retry_owned(entry,factory_ref())
    daemon._close_t037_session_factory(('p','session-prebirth-scm'))
    assert sorted(sdk_calls)==[31,31,32,32] and not registry.unresolved()
    daemon.close()
