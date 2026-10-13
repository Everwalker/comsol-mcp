"""Original CPython PyHKEY semantics at synthetic SDK boundaries, real helper/core."""
import gc,sys,weakref
from types import SimpleNamespace
import pytest
from comsol_mcp import _windows_t037 as win,_windows_owned_resources as owned,_windows_host_identity_owner as identity
from comsol_mcp._execution_contract import ExecutionContractError

class _Key:
    def __init__(self,sdk,value):self.sdk=sdk;self.value=value
    def __int__(self):
        if self.sdk.mode=='int_failure':raise MemoryError('PyLong_FromVoidPtr boundary failure')
        return self.value
    def Detach(self):
        self.sdk.calls.append(('detach',self.value))
        if self.sdk.mode=='audit_failure':raise OSError('Detach audit refused before transfer')
        value=self.value;self.value=0
        if self.sdk.mode=='detach_return_failure':raise MemoryError('Detach PyLong return failed after transfer')
        return value+1 if self.sdk.mode=='detach_drift' else value

class _Winreg:
    HKEY_LOCAL_MACHINE=0x80000002;REG_SZ=1
    def __init__(self,mode):self.mode=mode;self.calls=[];self.key_ref=None;self.fail_close=mode=='close_failure'
    def OpenKey(self,*args):
        self.calls.append(('open',));key=_Key(self,77);self.key_ref=weakref.ref(key);return key
    def QueryValueEx(self,key,name):
        assert type(key) is int and key==77
        self.calls.append(('query',key));return 'software-machine-guid',1
    def CloseKey(self,key):
        # Faithfully model CPython: object form zeroes before underlying close;
        # integer form preserves the caller's original HKEY on failure.
        if isinstance(key,_Key):value=key.value;key.value=0
        else:assert type(key) is int;value=key
        self.calls.append(('close',value,type(key).__name__))
        if self.fail_close:raise OSError('RegCloseKey original integer failed')
        return None

@pytest.mark.parametrize('mode',['normal','close_failure','audit_failure','detach_drift','detach_return_failure','int_failure'])
def test_actual_fingerprint_retains_hkey_before_conversion_detach_query_and_close(monkeypatch,mode):
    sdk=_Winreg(mode);owner=SimpleNamespace();registry=owned.registry_for(owner)
    monkeypatch.setitem(sys.modules,'winreg',sdk)
    monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(win.platform,'node',lambda:'software-host')
    if mode=='normal':assert len(win.windows_host_fingerprint(resource_registry=registry))==64
    else:
        with pytest.raises(Exception):win.windows_host_fingerprint(resource_registry=registry)
    gc.collect()
    if mode=='normal':
        assert not registry.unresolved() and sdk.key_ref() is None
        assert sdk.calls==[('open',),('detach',77),('query',77),('close',77,'int')]
    else:
        entry,=registry.unresolved();key=sdk.key_ref()
        assert key is not None and entry.keepalive[0] is key and entry.domain=='winreg' and entry.state=='UNKNOWN'
        assert entry.value==(None if mode=='int_failure' else 77)
        before=list(sdk.calls)
        with pytest.raises(owned.NativeResourceError):win.windows_host_fingerprint(resource_registry=registry)
        assert sdk.calls==before
        with pytest.raises(owned.NativeResourceError):registry.retry_owned(entry,object())
        if mode=='close_failure':
            assert key.value==0 and sdk.calls[-1]==('close',77,'int')
            sdk.fail_close=False;registry.retry_owned(entry,owner);registry.retry_owned(entry,owner)
            assert sdk.calls[-1]==('close',77,'int') and sdk.calls.count(sdk.calls[-1])==2 and not registry.unresolved()
        else:
            assert not any(row[0] in {'query','close'} for row in sdk.calls)
            with pytest.raises(owned.NativeResourceError):registry.retry_owned(entry,owner)
            assert sdk.calls==before and registry.unresolved()==(entry,)


def test_default_standalone_owner_survives_gc_and_prevents_new_open_until_same_cap_recovery(monkeypatch):
    sdk=_Winreg('close_failure');monkeypatch.setitem(sys.modules,'winreg',sdk)
    monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'));monkeypatch.setattr(win.platform,'node',lambda:'software-host')
    monkeypatch.setattr(identity,'_standalone_owner',None)
    with pytest.raises(win.WindowsT037Error):win.windows_host_fingerprint()
    owner=identity._standalone_owner;owner_ref=weakref.ref(owner);entry,=owner.registry.unresolved();del owner;gc.collect()
    assert owner_ref() is identity._standalone_owner and sdk.key_ref() is not None
    before=list(sdk.calls)
    with pytest.raises(owned.NativeResourceError):win.windows_host_fingerprint()
    assert sdk.calls==before
    with pytest.raises(owned.NativeResourceError):owner_ref().recover_owned(object())
    sdk.fail_close=False;owner_ref().recover_owned(owner_ref())
    assert identity._standalone_owner is None and not entry.registry.unresolved()
    entry.release_owned(owner_ref(),retry=True);assert sdk.calls.count(('close',77,'int'))==2
    with pytest.raises(owned.NativeResourceError):owner_ref().recover_owned(owner_ref())
    assert len(win.windows_host_fingerprint())==64 and identity._standalone_owner is None
    assert sdk.calls.count(('open',))==2


def test_explicit_invalid_registry_precedes_any_actual_open(monkeypatch):
    sdk=_Winreg('normal');monkeypatch.setitem(sys.modules,'winreg',sdk);monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'))
    with pytest.raises(owned.NativeResourceError):win.windows_host_fingerprint(resource_registry={'owner':'wire'})
    assert sdk.calls==[]


def test_hkey_and_pointer_same_number_are_distinct_domains(monkeypatch):
    sdk=_Winreg('close_failure');owner=SimpleNamespace();registry=owned.registry_for(owner)
    pointer=registry.acquire(sdk,77,lambda h:None,mode='VOID',domain='wfp_memory')
    with pytest.raises(OSError):identity.read_machine_guid(sdk,registry=registry)
    assert len(registry.unresolved())==2 and pointer.value==77
    hkey=next(e for e in registry.unresolved() if e.domain=='winreg');assert hkey is not pointer
    sdk.fail_close=False;registry.retry_owned(hkey,owner);assert registry.unresolved()==(pointer,)
    pointer.release_owned(owner)


def test_actual_controller_first_guid_close_failure_holds_original_owner_zero_store_and_no_token_sdk(tmp_path,monkeypatch):
    from tests.test_windows_policy_owner import _daemon_sdk
    actual_fp=win.windows_host_fingerprint
    daemon,profile,state,calls,refs,free=_daemon_sdk(tmp_path,monkeypatch,False)
    monkeypatch.setattr(win,'windows_host_fingerprint',actual_fp)
    sdk=_Winreg('close_failure');monkeypatch.setitem(sys.modules,'winreg',sdk)
    monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'));monkeypatch.setattr(win.platform,'node',lambda:'software-host')
    with pytest.raises(ExecutionContractError) as captured:
        daemon._dispatch_session_start({'project_id':'p','runtime_id':'r','idempotency_key':'first-guid'}, {})
    assert 'NATIVE_POLICY_OWNER_RECOVERY_REQUIRED' in captured.value.details['reason_codes']
    del captured;gc.collect();owner=daemon._t037_policy_observation_owner
    entry,=owner.registry.unresolved();assert entry.adapter is sdk and sdk.key_ref() is not None
    assert all(ref() is not None for ref in refs) and calls==[]
    assert not daemon._session_t037_process_factories
    assert daemon.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    before=list(sdk.calls)
    with pytest.raises(ExecutionContractError):daemon._require_t037_policy('session.connect')
    assert sdk.calls==before and calls==[]
    daemon.close();assert daemon._t037_policy_observation_owner is owner
    sdk.fail_close=False;owner.recover_owned(daemon)
    assert sdk.calls.count(('close',77,'int'))==2 and daemon._t037_policy_observation_owner is None


def test_actual_native_adapter_second_fingerprint_uses_its_bound_registry(monkeypatch):
    from comsol_mcp import _windows_t037_native as native
    from tests.test_windows_host_change import _profile
    cls=native.WindowsNativePolicyAdapter;api_class=native.CtypesWindowsPolicyApi
    api=api_class.__new__(api_class);sdk=_Winreg('close_failure')
    monkeypatch.setitem(sys.modules,'winreg',sdk);monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(native,'os',SimpleNamespace(name='nt'));monkeypatch.setattr(native,'sys',SimpleNamespace(platform='win32'))
    monkeypatch.setattr(native,'CtypesWindowsPolicyApi',lambda:api)
    adapter=cls();profile=win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY')
    with pytest.raises(win.WindowsT037Error):adapter.observe(profile,current_host_fingerprint=profile.host_fingerprint_sha256)
    registry=owned.registry_for(api);entry,=registry.unresolved()
    assert entry.domain=='winreg' and identity._standalone_owner is None and entry.registry.owner is api
    sdk.CloseKey=lambda *a:pytest.fail('replacement CloseKey callback cannot authorize recovery')
    sdk.fail_close=False;registry.retry_owned(entry,api)
    assert sdk.calls.count(('close',77,'int'))==2 and not registry.unresolved()


def test_actual_factory_prebirth_fingerprint_uses_factory_registry_and_original_hkey(tmp_path,monkeypatch):
    from comsol_mcp import _windows_t037_resume as resume
    from tests.test_windows_policy_owner import _daemon_sdk
    actual_fp=win.windows_host_fingerprint
    daemon,profile,state,calls,refs,free=_daemon_sdk(tmp_path,monkeypatch,False)
    monkeypatch.setattr(win,'windows_host_fingerprint',actual_fp)
    sdk=_Winreg('close_failure');monkeypatch.setitem(sys.modules,'winreg',sdk);monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'))
    factory=daemon._new_t037_session_factory('p','session-prebirth-guid')
    with pytest.raises(Exception):resume.refuse_unverified_prebirth(profile,owner=factory)
    registry=owned.registry_for(factory);entry,=registry.unresolved();assert entry.registry.owner is factory and entry.domain=='winreg'
    del factory;gc.collect();factory=daemon._session_t037_process_factories[('p','session-prebirth-guid')]
    assert sdk.key_ref() is not None
    with pytest.raises(win.WindowsT037Error):daemon._close_t037_session_factory(('p','session-prebirth-guid'))
    assert sdk.calls.count(('close',77,'int'))==1
    sdk.fail_close=False;registry.retry_owned(entry,factory);daemon._close_t037_session_factory(('p','session-prebirth-guid'))
    assert sdk.calls.count(('close',77,'int'))==2 and not registry.unresolved()
    daemon.close()
