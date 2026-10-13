"""Real daemon/Store ownership; SDK/policy continuation is SOFTWARE_ONLY."""
import ctypes as C,gc,os,sqlite3,weakref
from types import SimpleNamespace
import pytest
from comsol_mcp import _windows_t037 as win,_windows_owned_resources as owned
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError,canonical_request_hash


def _sdk(monkeypatch,profile,unknown):
    from comsol_mcp import _windows_t037_native as native
    from tests.test_windows_t037_native import _FakeApi
    from comsol_mcp._windows_t037_native import HANDLE,DWORD,BOOL
    api=win._Win32ReadOnlyApi.__new__(win._Win32ReadOnlyApi)
    api.C=C;api.W=SimpleNamespace(HANDLE=HANDLE,DWORD=DWORD,BOOL=BOOL,LPWSTR=C.c_wchar_p)
    text=C.create_unicode_buffer(profile.service_account_sid);address=C.addressof(text)
    state={'unknown':unknown};calls=[]
    def free(pointer):calls.append(('free',pointer.value));return pointer.value if state['unknown'] else None
    def token(process,access,out):C.cast(out,C.POINTER(HANDLE)).contents.value=91;return 1
    def info(token,kind,buffer,capacity,out):
        size=4 if kind==29 else 16 if kind==2 else 24
        C.cast(out,C.POINTER(DWORD)).contents.value=size
        if buffer is None:return 0
        C.memset(buffer,0,size)
        if kind in {1,25}:C.cast(buffer,C.POINTER(C.c_void_p)).contents.value=int(C.cast(buffer,C.c_void_p).value)+16
        return 1
    def convert(pointer,out):C.cast(out,C.POINTER(C.c_void_p)).contents.value=address;return 1
    api.k=SimpleNamespace(OpenProcess=lambda *a:90,CloseHandle=lambda h:calls.append(('close',getattr(h,'value',h))) or 1,LocalFree=free)
    api.a=SimpleNamespace(OpenProcessToken=token,GetTokenInformation=info,IsValidSid=lambda p:1,GetLengthSid=lambda p:8,ConvertSidToStringSidW=convert)
    api._check=lambda ok,label:None if ok else (_ for _ in ()).throw(OSError(label))
    api.process_times=lambda h:(123,None);api.process_image_path=lambda h:profile._launch_image('launcher').path;api.is_process_in_job=lambda *a:False
    monkeypatch.setattr(C,'get_last_error',lambda:122,raising=False)
    observer=win.WindowsT037Observer(platform_name='windows',api=api)
    box=[observer];refs=(weakref.ref(observer),weakref.ref(api))
    monkeypatch.setattr(win,'WindowsT037Observer',lambda:box.pop())
    monkeypatch.setattr(win,'windows_host_fingerprint',lambda **_kw:profile.host_fingerprint_sha256)
    cls=native.WindowsNativePolicyAdapter
    monkeypatch.setattr(native,'WindowsNativePolicyAdapter',lambda:cls(api=_FakeApi()))
    return state,calls,refs,free


@pytest.mark.parametrize('unknown',[True,False])
@pytest.mark.parametrize('boundary',['preflight','sqlite','conflict','reused'])
def test_actual_daemon_factory_survives_preflight_and_store_boundaries_gc(tmp_path,monkeypatch,unknown,boundary):
    from tests.test_windows_host_change import _profile
    profile=win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY')
    daemon=ControlDaemon(tmp_path/'control');daemon._windows_t037_profile=profile
    monkeypatch.setattr(daemon,'_authorize_server_control',lambda *a:None)
    monkeypatch.setattr(daemon,'_require_t037_policy',lambda *a:None) # Policy boundary only; never mint proof.
    state,calls,refs,free=_sdk(monkeypatch,profile,unknown)
    monkeypatch.setattr(win,'os',SimpleNamespace(**dict(vars(os),name='nt')))
    original=win.T037SessionProcessFactory.preflight
    if boundary!='preflight':
        # Reach real Store failure/reuse branches without issuing native proof
        # or launching. Public preflight remains a refusal in production.
        def software_continuation(factory):
            with pytest.raises(Exception):original(factory)
        monkeypatch.setattr(win.T037SessionProcessFactory,'preflight',software_continuation)
    oldkey=('p','session-existing');other=daemon._new_t037_session_factory(*oldkey)
    other_ref=weakref.ref(other);del other
    if boundary in {'conflict','reused'}:
        digest=canonical_request_hash('session.start',{'runtime_id':'r','options':{},'resources':{}},None,None,project_id='p')
        daemon.store.begin(request_id='old',idempotency_key='k',request_hash=digest if boundary=='reused' else 'f'*64,operation='session.start',metadata={'project_id':'p','session_id':oldkey[1]})
    if boundary=='sqlite':daemon.store.db.execute("CREATE TRIGGER fail_admit BEFORE INSERT ON operations BEGIN SELECT RAISE(ABORT,'actual admission failure'); END")
    try:
        if boundary=='reused':result=daemon._dispatch_session_start({'project_id':'p','runtime_id':'r','idempotency_key':'k'},{});assert result is not None
        else:
            with pytest.raises(Exception):daemon._dispatch_session_start({'project_id':'p','runtime_id':'r','idempotency_key':'k'}, {})
        keys=[key for key in daemon._session_t037_process_factories if key!=oldkey]
        assert len(keys)==int(unknown)
        assert daemon._session_t037_process_factories[oldkey] is other_ref() and not other_ref()._closed
        assert daemon.store.db.execute('SELECT count(*) FROM operations').fetchone()[0]==int(boundary in {'conflict','reused'})
        assert daemon.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==int(boundary in {'conflict','reused'})
        before=list(calls);gc.collect()
        if unknown:
            key=keys[0];factory=daemon._session_t037_process_factories[key];factory_ref=weakref.ref(factory)
            assert factory.session_id==key[1] and key!=oldkey
            assert daemon._new_t037_session_factory(*key) is factory
            registry=owned.registry_for(factory);entry,=registry.unresolved()
            assert entry.closer is free and entry.adapter is refs[1]()
            del factory;gc.collect();assert factory_ref() is not None and all(ref() is not None for ref in refs)
            with pytest.raises(win.WindowsT037Error):daemon._close_t037_session_factory(key)
            assert calls==before and daemon._session_t037_process_factories[key] is factory_ref()
            state['unknown']=False;registry.retry_owned(entry,factory_ref());daemon._close_t037_session_factory(key)
            assert key not in daemon._session_t037_process_factories and calls[-1][0]=='free'
            daemon._close_t037_session_factory(key);assert calls.count(calls[-1])==2
        else:assert calls==before # Empty temporary factory unregistered without native release retry.
        assert not other_ref()._closed
        daemon._close_t037_session_factory(oldkey)
    finally:daemon.close()


def test_policy_refusal_precedes_factory_registration_and_native_effect(tmp_path,monkeypatch):
    from tests.test_windows_host_change import _profile
    daemon=ControlDaemon(tmp_path/'control');daemon._windows_t037_profile=win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY')
    monkeypatch.setattr(daemon,'_authorize_server_control',lambda *a:None)
    monkeypatch.setattr(daemon,'_require_t037_policy',lambda *a:(_ for _ in ()).throw(ExecutionContractError('T037_POLICY_UNVERIFIED','policy refused')))
    monkeypatch.setattr(win.T037SessionProcessFactory,'preflight',lambda *a:pytest.fail('native preflight occurred'))
    try:
        with pytest.raises(ExecutionContractError):daemon._dispatch_session_start({'project_id':'p','runtime_id':'r','idempotency_key':'k'}, {})
        assert not daemon._session_t037_process_factories
        assert daemon.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    finally:daemon.close()
