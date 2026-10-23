"""Normal original V4 CREATE/B/refusal software chain; no native acceptance."""
import ctypes as C
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import ntpath
import struct
import threading
from types import SimpleNamespace

import pytest

from comsol_mcp import _windows_t037 as win
from comsol_mcp import _windows_t037_b_observation as b
from comsol_mcp import _windows_t037_child_evidence as e
from comsol_mcp import _windows_t037_resume as resume
from comsol_mcp import _windows_t037_targets as targets
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_suspended_ownership import _launch_api
from tests.test_windows_t037_targets import full_chain, software_only, sd
from tests.test_windows_scm_birth_concurrency import _async_call, _join


def _sid(value):
    parts=value.split('-');authority=int(parts[2]);sub=[int(x) for x in parts[3:]]
    return bytes((1,len(sub)))+authority.to_bytes(6,'big')+b''.join(struct.pack('<I',x) for x in sub)


def _text(storage,value):
    raw=value.encode('utf-16-le');C.memmove(storage,raw+b'\0\0',len(raw)+2)
    return len(raw)//2


class _QuerySDK:
    """Only SDK exports/vtable dispatch and ABI initialization are simulated.

    Production borrow/observe/identity/token/policy/COM/image/ownership remain
    actual functions. This constructor seam does not exercise Windows DLL ABI.
    """
    def __init__(self,monkeypatch,api,profile,static,fault=None):
        self.launch=api;self.profile=profile;self.fault=fault;self.calls=[];self.closed=[]
        self.opens=0;self.stat_reads=0;self.identity_reads=0;self.last_error=122
        self.positions={};self.info_counts={};self.allocations={};self.releases=[];self.uninit=[]
        self.raw_command=None;self.on_init=self.on_query=None;self.query=None
        self.payload=static.contents[ntpath.normcase(profile._launch_image('server').path)]
        self.native=r'\Device\fixture\server.exe'
        monkeypatch.setattr(C,'get_last_error',lambda:self.last_error,raising=False)
        def initialize(query,owner_api=None):
            assert owner_api is api and getattr(api,'_connected_b_observers')
            self.query=query;query.source='SOFTWARE_SDK_BOUNDARY_NOT_NATIVE'
            owned.attach_registry(query,owned.registry_for(api))
            query.k=SimpleNamespace(GetProcessId=self.pid,GetThreadId=lambda h:9,
                GetProcessIdOfThread=lambda h:999 if self.fault=='association' else 456,
                GetProcessTimes=self.process_times,GetThreadTimes=self.thread_times,
                WaitForSingleObject=lambda h,t:0 if self.fault=='exited' else 258,
                CloseHandle=self.close,GetProcessMitigationPolicy=self.policy,
                GetFileInformationByHandle=self.file_info,GetFileType=lambda h:1,
                GetFinalPathNameByHandleW=lambda h,s,c,f:_text(s,self.native),
                SetFilePointerEx=self.seek,ReadFile=self.read_file,
                QueryFullProcessImageNameW=self.process_name,VirtualQueryEx=self.region,
                CreateFileW=lambda *args:0 if self.fault=='null_image' else 944)
            query.a=SimpleNamespace(OpenProcessToken=self.open_token,GetTokenInformation=self.token,
                LookupPrivilegeNameW=self.privilege_name)
            query.ps=SimpleNamespace(GetMappedFileNameW=lambda h,a,s,c:_text(s,self.native))
            query.ole=SimpleNamespace(CoInitializeEx=lambda *a:0,CoUninitialize=lambda:self.uninit.append(True),
                CoCreateInstance=lambda c,u,m,i,out:self.pointer(out,9201),CoSetProxyBlanket=lambda *a:0)
            query.auto=SimpleNamespace(SysAllocString=self.allocate,SysFreeString=self.free,
                SysStringLen=lambda p:self.allocations[p][1],VariantClear=self.clear)
            query._method=self.method  # Explicit COM vtable software boundary only.
            if self.on_init:self.on_init(query)
            if self.fault=='ctor':raise OSError('software SDK initialization unavailable')
        monkeypatch.setattr(e.WindowsChildEvidenceApi,'__init__',initialize)
        create=api.a.CreateProcessAsUserW
        def created(*args):
            self.raw_command=args[2].value
            result=create(*args)
            if self.fault=='sdk_buffer':args[2].value='SDK MUTATED'
            return result
        api.a.CreateProcessAsUserW=created
        # Both adapters model the same thread-local last-error cell. A failed
        # B getter must not leave error=5 on a later primary size-probe that
        # actually returns ERROR_INSUFFICIENT_BUFFER=122.
        primary_info=api.a.GetTokenInformation
        def primary_query(*args):
            result=primary_info(*args)
            if args[2] is None:self.last_error=122
            return result
        api.a.GetTokenInformation=primary_query

    def pid(self,handle):self.calls.append('identity');return 457 if self.fault=='pid' else 456
    def process_times(self,h,birth,*tail):
        self.identity_reads+=1
        C.cast(birth,C.POINTER(e.U64)).contents.value=999 if self.fault=='identity_drift' and self.identity_reads>1 else 123
        return 1
    def thread_times(self,h,birth,*tail):C.cast(birth,C.POINTER(e.U64)).contents.value=124;return 1
    def open_token(self,p,access,out):
        assert p==200 and access==8
        self.opens+=1;self.stat_reads=0;self.calls.append('token')
        C.cast(out,C.POINTER(e.PTR)).contents.value=900+self.opens;return 1
    def sid_attr(self,text,base,attrs=0x60):return struct.pack('<QI4x',base+16,attrs)+_sid(text)
    def groups(self,sids,base):
        rows=[];tail=b'';offset=8+16*len(sids)
        for text in sids:
            raw=_sid(text);rows.append(struct.pack('<QI4x',base+offset+len(tail),4));tail+=raw
        return struct.pack('<I4x',len(sids))+b''.join(rows)+tail
    def token_bytes(self,kind,base,read=False):
        if kind==10:
            if read:self.stat_reads+=1
            raw=bytearray(56);struct.pack_into('<Q',raw,0,3);struct.pack_into('<Q',raw,8,4)
            modified=18 if self.fault=='token_drift' and self.opens==2 else 17
            struct.pack_into('<Q',raw,48,modified);return bytes(raw)
        if kind==1:return self.sid_attr('S-1-5-999' if self.fault=='user' else self.profile.service_account_sid,base,0)
        if kind==2:
            from tests.test_windows_t037_token_groups import group_bytes
            return group_bytes((('S-1-5-32-545',7),('S-1-5-5-1-2',0xC0000007),
                (self.profile.service_sid,7),('S-1-16-4096',0x60)),base)
        if kind==11:return self.groups(('S-1-5-99-7',),base)  # No invented restricted-SID target.
        if kind==30:return self.groups(('S-1-15-999',) if self.fault=='capabilities' else self.profile.capability_sids,base)
        if kind==25:return self.sid_attr('S-1-16-4096',base)  # Main-selected Low target.
        if kind==27:return struct.pack('<I',3)
        if kind==29:return struct.pack('<I',0 if self.fault=='not_appcontainer' else 1)
        if kind==3:return struct.pack('<IQI',1,23,0x80000002)
        raise AssertionError(kind)
    def token(self,token,kind,storage,capacity,used):
        data=self.token_bytes(kind,C.addressof(storage) if storage is not None else 0,storage is not None)
        C.cast(used,C.POINTER(e.U32)).contents.value=len(data)
        if storage is None:self.last_error=122;return 0
        if self.fault=='token_partial' and kind==25:self.last_error=5;return 0
        assert len(data)<=capacity;C.memmove(storage,data,len(data));return 1
    def privilege_name(self,system,luid,storage,used):
        C.cast(used,C.POINTER(e.U32)).contents.value=_text(storage,'SeChangeNotifyPrivilege');return 1
    def policy(self,p,kind,out,size):
        self.calls.append('policy');assert p==200 and kind==13 and size==4
        C.cast(out,C.POINTER(e.U32)).contents.value=2 if self.fault=='policy' else 0x80000001 if self.fault=='reserved_policy' else 1
        return 1
    def file_info(self,h,out):
        self.info_counts[h]=self.info_counts.get(h,0)+1
        f=C.cast(out,C.POINTER(e._FileInfo)).contents
        f.attributes,f.creation,f.access,f.write=32,10,11,12
        f.volume,f.low,f.links,f.id_low=1,len(self.payload),1,99 if self.fault=='backing' and h==944 else 2
        if self.fault=='pin_image_drift' and h==12 and self.info_counts[h]>2:f.id_low=3
        return 1
    def seek(self,h,*a):self.positions[h]=0;return 1
    def read_file(self,h,storage,capacity,used,overlap):
        self.calls.append(('image_read',h));data=self.payload if self.positions[h]==0 else b''
        C.memmove(storage,data,len(data));C.cast(used,C.POINTER(e.U32)).contents.value=len(data)
        self.positions[h]+=len(data);return 1
    def process_name(self,h,flags,storage,used):
        C.cast(used,C.POINTER(e.U32)).contents.value=_text(storage,self.native if flags else self.profile._launch_image('server').path);return 1
    def region(self,h,address,out,size):
        f=C.cast(out,C.POINTER(e._MemoryInfo)).contents
        f.base,f.allocation_base,f.size,f.state,f.type=4096,4096,8192,0x1000,0x1000000;return size
    def close(self,h):
        self.closed.append(h)
        if self.fault=='token_close_unknown' and h==901:raise OSError('software CloseHandle result unknown')
        return 1
    def allocate(self,value):
        s=C.create_string_buffer(value.encode('utf-16-le')+b'\0\0');p=C.addressof(s)
        self.allocations[p]=(s,len(value.encode('utf-16-le'))//2);return p
    def free(self,p):assert p in self.allocations;self.allocations.pop(p)
    def clear(self,p):
        v=C.cast(p,C.POINTER(e._Variant)).contents
        if self.fault=='variant_unknown':return -1
        if v.vt==8 and v.data[0]:self.free(v.data[0])
        v.vt=0;return 0
    def pointer(self,out,value):C.cast(out,C.POINTER(e.PTR)).contents.value=value;return 0
    def method(self,p,index,result,*types):
        if index==2:
            def release(value):self.releases.append(value);return 0
            return release
        if p.value==9201 and index==3:return lambda this,ns,u,p,l,f,a,c,out:self.pointer(out,9202)
        if p.value==9202 and index==20:
            def query(this,language,text,flags,context,out):
                self.calls.append('WMI');assert flags==0x30
                if self.on_query:self.on_query()
                return -2147217405 if self.fault=='com_error' else self.pointer(out,9203)
            return query
        if p.value==9203 and index==4:
            def next_row(this,timeout,count,objects,used):
                assert timeout==1500 and count==2;objects[0]=9204
                C.cast(used,C.POINTER(e.U32)).contents.value=1;return 1
            return next_row
        assert p.value==9204 and index==4
        def get(this,name,flags,out,typ,flavor):
            v=C.cast(out,C.POINTER(e._Variant)).contents
            if name=='ProcessId':v.vt,v.data[0]=3,999 if self.fault=='wmi_pid' else 456
            elif name=='ExecutablePath' or name=='CommandLine' and self.fault=='null_command':v.vt=1
            else:
                value='16010101000000.000011+000' if self.fault=='wmi_birth' else '16010101000000.000012+000'
                if name=='CommandLine':value='actual other command' if self.fault=='argv' else self.raw_command
                v.vt,v.data[0]=8,self.allocate(value)
            return 0
        return get


@contextmanager
def connected_chain(monkeypatch,tmp_path,fault=None,cleanup_fail=False):
    with full_chain(monkeypatch,tmp_path,child_acl=True) as (daemon,factory,static,snapshot,events):
        profile,api,launch_events,state=_launch_api(monkeypatch,profile_override=factory.profile,
            cleanup_fail=cleanup_fail,acl_descriptor=sd())
        factory._api=api;api.hash_image=lambda path:profile.server_image_sha256
        query=_QuerySDK(monkeypatch,api,profile,static,fault)
        command=[profile._launch_image('server').path,'--finite-b','quoted space Ω "value"','']
        yield daemon,factory,api,query,command,launch_events,state,static


def launch_refusal(factory,command):
    with pytest.raises(ExecutionContractError) as error:factory(command)
    return error.value


def known_child_cleanup(api,factory):
    # The shared original Job correctly remains owned until factory.close.
    assert [(r.label,r.value,r.state) for r in owned.registry_for(api).unresolved()] == [
        ('owned_session_job',factory._job,'ACQUIRED')]
    assert not owned.registry_for(factory).unresolved()
    factory.close()
    assert not owned.registry_for(api).unresolved()


def test_normal_v4_connected_original_B_full_observer_matches_but_issuer_still_denies(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path) as (daemon,factory,api,sdk,command,events,state,static):
        error=launch_refusal(factory,command)
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and 'RESUME' not in events and not resume._pending
        owner=api._last_connected_b_observer;result=owner.result
        assert owner.state=='RELEASED' and not api._connected_b_observers
        assert type(owner.api) is e.WindowsChildEvidenceApi and owner.borrowed.owner_api is api
        assert result.finite_state=='MATCHED_FINITE_CHECKS_ONLY' and not result.issues
        assert result.qualification=='UNQUALIFIED' and result.state=='DIAGNOSTIC_ONLY_NOT_ADMISSION'
        assert dict((c.name,c.matches) for c in result.observations.comparisons)['expected_modified_id'] is None
        assert result.observations.token_before.modified_id==17 and result.observations.token_before.integrity_rid==4096
        assert result.observations.token_before.mandatory_policy==3 and result.observations.token_before.privileges[0].attributes==0x80000002
        assert result.observations.command_line.command_line==sdk.raw_command
        assert result.observations.image.pinned_before.sha256==hashlib.sha256(sdk.payload).hexdigest()
        assert sdk.calls.count('WMI')==1 and sdk.opens==2 and len(sdk.uninit)==1
        assert sdk.closed==[901,944,902] and len(sdk.releases)==len(set(sdk.releases))==4
        assert events.count('CREATE_SUSPENDED')==1 and not state['alive']
        assert events.count(('close',200))==events.count(('close',201))==1
        with pytest.raises(FrozenInstanceError):result.finite_state='PASS'
        with pytest.raises(FrozenInstanceError):result.qualification='QUALIFIED'
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('fault,key',[('pid','created_pid'),('association','thread_process_association'),
    ('identity_drift','identity_stable_and_live'),('user','token_user_before'),('capabilities','token_capabilities_before'),
    ('not_appcontainer','token_is_appcontainer_before'),('token_drift','token_stable'),('policy','fixed_child_creation_policy'),
    ('reserved_policy','fixed_child_creation_policy'),('argv','remote_command_line'),('wmi_pid','wmi_pid_birth'),
    ('wmi_birth','wmi_pid_birth'),('backing','mapped_main_backing_matches_pin'),('pin_image_drift','mapped_main_backing_matches_pin')])
def test_normal_v4_actual_finite_mismatch_stays_raw_diagnostic_and_never_resume(monkeypatch,tmp_path,fault,key):
    with connected_chain(monkeypatch,tmp_path,fault) as (daemon,factory,api,sdk,command,events,state,static):
        error=launch_refusal(factory,command);result=api._last_connected_b_observer.result
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE'
        assert {c.name:c.matches for c in result.finite_comparisons}[key] is False
        assert result.finite_state=='MISMATCH' and result.qualification=='UNQUALIFIED'
        assert 'RESUME' not in events and not resume._pending and not state['alive']
        assert events.count('CREATE_SUSPENDED')==1
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('fault',['token_partial','com_error','null_command','null_image','exited','ctor'])
def test_normal_v4_partial_null_and_sdk_failures_remain_unverified(monkeypatch,tmp_path,fault):
    with connected_chain(monkeypatch,tmp_path,fault) as (daemon,factory,api,sdk,command,events,state,static):
        error=launch_refusal(factory,command);owner=api._last_connected_b_observer;result=owner.result
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and result.issues
        assert result.finite_state=='UNVERIFIED' and result.qualification=='UNQUALIFIED'
        assert owner.state=='RELEASED' and not state['alive'] and 'RESUME' not in events
        known_child_cleanup(api,factory)


def test_original_creation_strings_and_digests_are_immutable_not_sdk_writeback(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path,'sdk_buffer') as (daemon,factory,api,sdk,command,events,state,static):
        captured=[]
        def inspect():
            owner=next(iter(api._connected_b_observers.values()));p=owner.process;captured.append(p)
            with pytest.raises(AttributeError):p._creation_parameters=('changed',(), 'changed')
            with pytest.raises(AttributeError):p._creation_parameter_digests={}
            with pytest.raises(TypeError):p._creation_parameter_digests['command_line_sha256']='0'*64
        sdk.on_query=inspect
        error=launch_refusal(factory,command);p,=captured;result=api._last_connected_b_observer.result
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE'
        assert p._call_storage[2].value=='SDK MUTATED'
        assert p._creation_parameters[2]==sdk.raw_command and result.observations.command_line.command_line==sdk.raw_command
        assert result.finite_state=='MATCHED_FINITE_CHECKS_ONLY'
        assert dict(result.creation_parameter_digests)['command_line_sha256']==hashlib.sha256(sdk.raw_command.encode('utf-16-le')).hexdigest()
        assert 'RESUME' not in events
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('fault',['token_close_unknown','variant_unknown'])
def test_actual_query_release_unknown_retains_original_adapter_A_child_and_no_reentry(monkeypatch,tmp_path,fault):
    with connected_chain(monkeypatch,tmp_path,fault) as (daemon,factory,api,sdk,command,events,state,static):
        error=launch_refusal(factory,command)
        assert type(error) is win.NativeSuspendedChildLaunchError
        p=error._process;owner=p._connected_b_observer
        assert api._connected_b_observers[id(p)] is owner and owner.state=='UNKNOWN'
        assert owner.api is sdk.query and owner.error is not None and owner.result.qualification=='UNQUALIFIED'
        assert not state['alive'] and p._closed  # Original never-resumed child cleanup, query release still UNKNOWN.
        held=targets._HELD[id(factory)];assert held.state=='UNKNOWN' and not held.reader.retired
        assert any(r.state=='UNKNOWN' and r.adapter is sdk.query for r in owned.registry_for(api).unresolved())
        before=(list(sdk.calls),list(sdk.closed),list(sdk.releases),list(events),list(static.calls))
        for operation in (lambda:factory(command),factory.close,lambda:b.observe_original_child_b(p,factory=factory)):
            with pytest.raises(Exception):operation()
        assert before==(sdk.calls,sdk.closed,sdk.releases,events,static.calls)
        assert 'RESUME' not in events and not resume._pending
        if fault=='token_close_unknown':assert sdk.closed==[901] and sdk.opens==1 and 'WMI' not in sdk.calls
        else:
            assert 'WMI' in sdk.calls and sdk.opens==1 and not sdk.uninit
            assert owner.result.observations.token_after is None


@pytest.mark.parametrize('fault',['factory_job','factory_profile','original_owner','process_entry','thread_entry','image_entry','parent_birth','argv','digest','observer_root'])
def test_actual_original_binding_drift_before_B_getters_refuses_and_retains_unknown(monkeypatch,tmp_path,fault):
    with connected_chain(monkeypatch,tmp_path) as (daemon,factory,api,sdk,command,events,state,static):
        captured=[]
        def drift(query):
            owner=next(iter(api._connected_b_observers.values()));captured.append(owner);p=owner.process
            if fault=='factory_job':factory._job=301
            elif fault=='factory_profile':
                from dataclasses import replace
                factory.profile=replace(factory.profile)
            elif fault=='original_owner':object.__setattr__(p,'_owner',object())
            elif fault=='process_entry':object.__setattr__(p,'_process_entry',p._thread_entry)
            elif fault=='thread_entry':object.__setattr__(p,'_thread_entry',p._process_entry)
            elif fault=='image_entry':object.__setattr__(p,'_image_entry',p._process_entry)
            elif fault=='parent_birth':p._parent_binding.birth+=1
            elif fault=='argv':p.args.append('unexpected caller mutation')
            elif fault=='digest':
                from types import MappingProxyType
                object.__setattr__(p,'_creation_parameter_digests',MappingProxyType({}))
            else:api._connected_b_observers[id(p)]=object()
        sdk.on_init=drift
        error=launch_refusal(factory,command);owner,=captured
        assert error.code in ('NATIVE_CHILD_CLEANUP_UNKNOWN','B_ORIGINAL_CONTEXT_CHANGED','NATIVE_CHILD_OWNER_UNKNOWN','B_CREATION_PARAMETERS_CHANGED')
        assert not sdk.calls and not sdk.closed and 'RESUME' not in events and not resume._pending
        assert events.count('CREATE_SUSPENDED')==1 and owner.error is not None
        assert owner.result is not None and owner.result.qualification=='UNQUALIFIED'
        assert owner.process._connected_b_observer is owner  # Actual original object, not a JSON replacement.
        if fault in ('factory_job','factory_profile','original_owner','process_entry','thread_entry','image_entry','argv','observer_root'):
            assert type(error) is win.NativeSuspendedChildLaunchError
            assert targets._HELD[id(factory)].state=='UNKNOWN'
        else:
            assert not state['alive']
            known_child_cleanup(api,factory)


@pytest.mark.parametrize('operation',['close','pause','birth','stop','closing'])
def test_blocked_actual_WMI_keeps_original_B_A_reservation_and_outside_runtime_registry_locks(monkeypatch,tmp_path,operation):
    from comsol_mcp import _windows_appcontainer_gate as gate, _windows_t037_broker as broker
    with connected_chain(monkeypatch,tmp_path) as (daemon,factory,api,sdk,command,events,state,static):
        entered=threading.Event();release=threading.Event();calls=[];captures=[]
        def blocked():
            owner=next(iter(api._connected_b_observers.values()));captures.append(owner)
            assert owner.thread==threading.get_ident() and owner.held.reader.scope.thread_id==threading.get_ident()
            entered.set();assert release.wait(4)
        sdk.on_query=blocked
        first=_async_call(lambda:factory(command),name='connected-original-B');calls.append(first)
        try:
            assert entered.wait(2);owner,=captures;runtime=factory._profile_participant.gate.runtime
            assert factory._held_static_a is owner.held and not owner.held.reader.retired
            assert daemon._worker_admission_inflight==1 and runtime._profile_gate.activity[1]==first[0].ident
            for lock in (runtime._lock,owned.registry_for(api)._lock):
                acquired=lock.acquire(timeout=.25)
                if acquired:lock.release()
                assert acquired, 'No outer runtime/registry lock may span the blocked WMI getter'
            before=list(sdk.calls)
            with pytest.raises(ExecutionContractError):b.observe_original_child_b(owner.process,factory=factory)
            assert sdk.calls==before and owner.error is None  # Foreign caller cannot poison original observer.
            if operation in ('close','pause','birth'):
                action={'close':factory.close,'pause':lambda:gate._pause_terminal_profile(runtime),'birth':lambda:factory(command)}[operation]
                other=_async_call(action,name='connected-B-contender');calls.append(other)
                assert other[1].wait(.5) and len(other[3])==1 and isinstance(other[3][0],ExecutionContractError)
                assert not factory._closed and sdk.calls==before and events.count('CREATE_SUSPENDED')==1
            elif operation=='stop':
                assert runtime._broker._api.handler(broker.SERVICE_CONTROL_STOP,0,None,None)==broker.ERROR_SUCCESS
            else:
                closing=threading.Event();wait=daemon._dispatch_condition.wait
                def observed_wait(*a,**k):
                    if threading.current_thread().name=='connected-B-daemon-close':closing.set()
                    return wait(*a,**k)
                monkeypatch.setattr(daemon._dispatch_condition,'wait',observed_wait)
                other=_async_call(daemon.close,name='connected-B-daemon-close');calls.append(other)
                assert closing.wait(1) and daemon._closing and not other[1].is_set()
        finally:
            release.set();_join(*calls)
        assert len(first[3])==1 and isinstance(first[3][0],ExecutionContractError)
        assert 'RESUME' not in events and not state['alive'] and owner.state=='RELEASED'
        assert sdk.calls.count('WMI')==1 and owner.held.reader.retired
        assert not api._connected_b_observers and not owned.registry_for(factory).unresolved()
        known_child_cleanup(api,factory)


def test_normal_hook_rejects_wrong_factory_and_caller_query_policy_inputs_before_B_acquisition(monkeypatch,tmp_path):
    with connected_chain(monkeypatch,tmp_path) as (daemon,factory,api,sdk,command,events,state,static):
        real=b.observe_original_child_b;seen=[]
        def checked(process,*,factory):
            before=(list(sdk.calls),list(sdk.closed));assert not hasattr(process,'_connected_b_observer')
            wrong=object.__new__(win.T037SessionProcessFactory)
            with pytest.raises(ExecutionContractError) as error:real(process,factory=wrong)
            assert error.value.code=='B_ORIGINAL_OWNER_REQUIRED'
            for extra in ({'api':object()},{'observations':{'state':'VERIFIED'}},{'expected_modified_id':17},{'policy_flags':1}):
                with pytest.raises(TypeError):real(process,factory=factory,**extra)
            assert (sdk.calls,sdk.closed)==before and not hasattr(process,'_connected_b_observer')
            seen.append(process)
            return real(process,factory=factory)  # Actual producer/observer; no always-success replacement.
        monkeypatch.setattr(b,'observe_original_child_b',checked)
        error=launch_refusal(factory,command)
        assert error.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and len(seen)==1
        assert api._last_connected_b_observer.result.finite_state=='MATCHED_FINITE_CHECKS_ONLY'
        assert 'RESUME' not in events and not resume._pending
        known_child_cleanup(api,factory)


@pytest.mark.parametrize('fault',['source','image','project'])
def test_actual_held_A_is_rechecked_after_B_before_unchanged_issuer(monkeypatch,tmp_path,fault):
    with connected_chain(monkeypatch,tmp_path) as (daemon,factory,api,sdk,command,events,state,static):
        def drift():
            if fault=='project':
                import json
                authority=daemon.project_authority
                record=authority.get_project(factory.profile.host_resource_profile.project_id);record['revision']+=1
                with authority.store.lock:
                    authority.store.db.execute('UPDATE projects SET revision=?,record_json=? WHERE project_id=?',
                        (record['revision'],json.dumps(record),record['project_id']))
            else:
                path=r'c:\comsol\config\profile.json' if fault=='source' else ntpath.normcase(factory.profile._launch_image('server').path)
                data=static.contents[path];static.contents[path]=bytes((data[0]^1,))+data[1:]
        sdk.on_query=drift
        error=launch_refusal(factory,command)
        assert error.details['reasons']==(['FINITE_A_LIFETIME_CHANGED'] if fault=='project' else ['FINITE_CONTENT_CHANGED'])
        owner=api._last_connected_b_observer
        assert owner.result.finite_state=='MATCHED_FINITE_CHECKS_ONLY' and owner.result.qualification=='UNQUALIFIED'
        assert owner.error is not None and owner.held.reader.retired and not state['alive']
        assert events.count('CREATE_SUSPENDED')==1 and 'RESUME' not in events and not resume._pending
        assert not api._connected_b_observers
        known_child_cleanup(api,factory)
