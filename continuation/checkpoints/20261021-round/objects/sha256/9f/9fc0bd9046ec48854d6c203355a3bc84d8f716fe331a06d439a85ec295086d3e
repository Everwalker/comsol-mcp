"""Actual production Job/profile/launch SDK boundary controls; SOFTWARE_ONLY."""
from dataclasses import replace
from types import SimpleNamespace
import ctypes as C
import gc
import weakref
import pytest
from comsol_mcp import _windows_job_limits as limits, _windows_t037 as win
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_host_change import _profile


def profile():
    return win.parse_profile_v3_document({'schema':win.PROFILE_SCHEMA_V3,
        'host_resource_profile':_profile().as_dict(),
        'job_limits':limits.JobLimitsV1(4096,16384,4).as_dict()},source_sha256='d'*64,
        security_semantics='SOFTWARE_ONLY')


class SDK:
    def __init__(self):
        self.calls=[];self.data=bytes(limits.EXTENDED_LIMIT());self.ids=[]
        self.set_result=1;self.query_result=1;self.length=144;self.create_result=300
        self.close_result=1;self.mutate=None
    def CreateJobObjectW(self,*args):self.calls.append('create');return self.create_result
    def CloseHandle(self,h):self.calls.append(('close',h));return self.close_result
    def SetInformationJobObject(self,h,kind,p,size):
        self.calls.append(('set',h,kind,size));self.data=C.string_at(p,size)
        if self.mutate:
            info=limits.EXTENDED_LIMIT.from_buffer_copy(self.data);self.mutate(info);self.data=bytes(info)
        return self.set_result
    def QueryInformationJobObject(self,h,kind,p,size,out):
        self.calls.append(('query',h,kind,size));C.memmove(p,self.data,size)
        C.cast(out,C.POINTER(C.c_uint32)).contents.value=self.length
        return self.query_result


def api():
    value=win._Win32LaunchApi.__new__(win._Win32LaunchApi)
    value.k=SDK();value.C=C;value.W=SimpleNamespace(DWORD=C.c_uint32)
    value._job_process_ids=lambda job:list(value.k.ids)
    return value


def assert_no_effect(value):
    assert value.k.calls==[] and not owned.registry_for(value).unresolved()


def test_v3_is_explicit_same_internal_type_and_v2_schema_hash_remains_exact():
    old=win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY')
    assert old.sha256==old.host_resource_profile.sha256
    assert old.as_safe_dict()['schema']==win.PROFILE_SCHEMA_V2
    assert 'job_limits' not in old.as_safe_dict()
    p=profile();assert type(p) is win.WindowsT037ProfileV2
    assert p.as_safe_dict()['schema']==win.PROFILE_SCHEMA_V3
    assert p.as_safe_dict()['job_limits']==p.job_limits.as_dict()
    assert p.as_safe_dict()['job_limits_sha256']==p.job_limits.sha256
    assert p.sha256!=old.sha256 and p.as_safe_dict()['profile_sha256']==p.sha256
    changed=replace(p,job_limits=limits.JobLimitsV1(4096,16384,5))
    assert changed.sha256!=p.sha256


@pytest.mark.parametrize('targets',[None,{}, {'schema':limits.SCHEMA},
    {'schema':limits.SCHEMA, 'process_memory_limit_bytes':None,'job_memory_limit_bytes':8192,'active_process_limit':4},
    {'schema':limits.SCHEMA, 'process_memory_limit_bytes':True,'job_memory_limit_bytes':8192,'active_process_limit':4},
    {'schema':limits.SCHEMA, 'process_memory_limit_bytes':4096,'job_memory_limit_bytes':8192,'active_process_limit':0},
    {'schema':limits.SCHEMA, 'process_memory_limit_bytes':4096,'job_memory_limit_bytes':8192,'active_process_limit':2**32},
    {'schema':limits.SCHEMA, 'process_memory_limit_bytes':2**64,'job_memory_limit_bytes':2**64,'active_process_limit':4},
    {'schema':limits.SCHEMA, 'process_memory_limit_bytes':16384,'job_memory_limit_bytes':8192,'active_process_limit':4},
    {'schema':limits.SCHEMA, 'process_memory_limit_bytes':4096,'job_memory_limit_bytes':8192,'active_process_limit':4,'verified':True}])
def test_missing_malformed_targets_rejected_at_closed_profile_parser(targets):
    with pytest.raises(ExecutionContractError):win.parse_profile_v3_document({
        'schema':win.PROFILE_SCHEMA_V3,'host_resource_profile':_profile().as_dict(),'job_limits':targets},source_sha256='d'*64)


@pytest.mark.parametrize('document',[
    {'schema':win.PROFILE_SCHEMA_V3,'host_resource_profile':_profile().as_dict()},
    {'schema':win.PROFILE_SCHEMA_V3,'host_resource_profile':_profile().as_dict(),'job_limits':None}])
def test_unconfigured_wrapper_uses_typed_not_configured(document):
    with pytest.raises(ExecutionContractError) as e:win.parse_profile_v3_document(document,source_sha256='d'*64)
    assert e.value.code=='JOB_LIMITS_NOT_CONFIGURED'


@pytest.mark.parametrize('p',[None,win.WindowsT037ProfileV2(_profile(),'d'*64,{},'SOFTWARE_ONLY'),{}])
def test_create_and_direct_birth_refuse_missing_targets_before_any_kernel_call(p):
    value=api()
    with pytest.raises(ExecutionContractError) as e:value.create_job(profile=p)
    assert e.value.code=='JOB_LIMITS_NOT_CONFIGURED';assert_no_effect(value)
    with pytest.raises(ExecutionContractError):value.create_suspended_appcontainer_child(['server.exe'],{},profile=p,job=300,role='server')
    assert_no_effect(value)


def test_actual_create_set_readback_owned_handle_bound_profile_then_original_close():
    value=api();p=profile();job=value.create_job(profile=p)
    assert job==300 and value.k.calls==['create',('query',300,9,144),('set',300,9,144),('query',300,9,144)]
    entry,=owned.registry_for(value).unresolved()
    assert entry.value==job and entry.keepalive==(p,) and entry.label=='owned_session_job'
    evidence=limits.require_configured_job(value,job,p)
    assert evidence['native'] is False and evidence['verification']=='IMPLEMENTED_UNVERIFIED'
    assert evidence['job_limits_sha256']==p.job_limits.sha256
    info=limits.EXTENDED_LIMIT.from_buffer_copy(value.k.data)
    assert info.BasicLimitInformation.LimitFlags==limits.FLAGS
    assert not info.BasicLimitInformation.PerJobUserTimeLimit and not info.BasicLimitInformation.PerProcessUserTimeLimit
    value.close_job(job);assert value.k.calls[-1]==('close',300)
    assert not owned.registry_for(value).unresolved() and not value._job_limit_bindings


@pytest.mark.parametrize('fault',['set_false','set_none','set_bool','set_str','set_large',
    'query_false','query_none','short','long','foreign_flags','kill_close','breakaway','silent_breakaway',
    'job_memory','process_memory','active_count','time','working_set','fresh_nonzero','existing_child','late_child'])
def test_sdk_faults_never_birth_success_or_reconfigure_and_keep_original_cleanup(fault):
    value=api();p=profile()
    if fault.startswith('set_'):value.k.set_result={'set_false':0,'set_none':None,'set_bool':True,'set_str':'1','set_large':2**32}[fault]
    if fault.startswith('query_'):value.k.query_result={'query_false':0,'query_none':None}[fault]
    if fault in {'short','long'}:value.k.length=143 if fault=='short' else 145
    field={'foreign_flags':('flag',0x10),'kill_close':('flag',0x2000),'breakaway':('flag',0x800),
           'silent_breakaway':('flag',0x1000),'job_memory':('JobMemoryLimit',1),
           'process_memory':('ProcessMemoryLimit',1),'active_count':('ActiveProcessLimit',1),
           'time':('PerJobUserTimeLimit',1),'working_set':('MaximumWorkingSetSize',1)}.get(fault)
    if field:
        def mutate(info):
            name,val=field
            if name=='flag':info.BasicLimitInformation.LimitFlags|=val
            elif name in {'JobMemoryLimit','ProcessMemoryLimit'}:setattr(info,name,val)
            else:setattr(info.BasicLimitInformation,name,val)
        value.k.mutate=mutate
    if fault=='fresh_nonzero':
        i=limits.EXTENDED_LIMIT();i.PeakJobMemoryUsed=1;value.k.data=bytes(i)
    if fault=='existing_child':value.k.ids=[42]
    if fault=='late_child':
        original=value.k.SetInformationJobObject
        def set_info(*args):result=original(*args);value.k.ids=[42];return result
        value.k.SetInformationJobObject=set_info
    with pytest.raises(ExecutionContractError):value.create_job(profile=p)
    entry,=owned.registry_for(value).unresolved();assert entry.value==300 and entry.keepalive==(p,)
    binding=value._job_limit_bindings[300];assert binding['state']=='UNKNOWN'
    before=list(value.k.calls)
    with pytest.raises(ExecutionContractError):value.create_job(profile=p)
    with pytest.raises(ExecutionContractError):value.create_suspended_appcontainer_child(['server.exe'],{},profile=p,job=300,role='server')
    assert value.k.calls==before and not any('RESUME'==x for x in value.k.calls)
    value.k.ids=[]
    value.close_job(300);assert value.k.calls[-1]==('close',300)
    assert not owned.registry_for(value).unresolved()


@pytest.mark.parametrize('fault',['wrong_job','same_values_foreign_profile','source_changed','limits_changed','wrong_owner','raw_metadata'])
def test_exact_identity_profile_registry_rejects_before_query_or_birth(fault):
    value=api();p=profile();job=value.create_job(profile=p);before=list(value.k.calls)
    if fault=='wrong_job':job=301
    if fault=='same_values_foreign_profile':p=replace(p)
    if fault=='source_changed':p=replace(p,source_sha256='e'*64)
    if fault=='limits_changed':p=replace(p,job_limits=limits.JobLimitsV1(1024,16384,4))
    if fault=='wrong_owner':value=api()
    if fault=='raw_metadata':value._job_limit_bindings[300]={'state':'CONFIGURED'}
    with pytest.raises((ExecutionContractError,KeyError)):
        value.create_suspended_appcontainer_child(['server.exe'],{},profile=p,job=job,role='server')
    assert value.k.calls==(before if fault!='wrong_owner' else [])


def test_readback_drift_is_sticky_no_retry_then_close_failure_requires_original_closer():
    value=api();p=profile();job=value.create_job(profile=p)
    info=limits.EXTENDED_LIMIT.from_buffer_copy(value.k.data);info.BasicLimitInformation.LimitFlags|=0x2000;value.k.data=bytes(info)
    with pytest.raises(ExecutionContractError):limits.require_configured_job(value,job,p)
    before=list(value.k.calls)
    with pytest.raises(ExecutionContractError):limits.require_configured_job(value,job,p)
    assert value.k.calls==before
    value.k.close_result=0
    with pytest.raises(ExecutionContractError):value.close_job(job)
    entry,=owned.registry_for(value).unresolved();assert entry.state=='UNKNOWN'
    # Replacing API field cannot redirect the already captured closer.
    value.k.CloseHandle=lambda *a:pytest.fail('replacement closer used')
    value.k.close_result=1;value.close_job(job)
    assert not owned.registry_for(value).unresolved()


def test_factory_original_unknown_owner_survives_gc_and_explicit_close_is_not_resume():
    value=api();p=profile();factory=win.T037SessionProcessFactory(p,'job-limit-session',api=value)
    factory._job=value.create_job(profile=p)
    factory_ref=weakref.ref(factory);owner={'factory':factory};del factory;gc.collect()
    assert factory_ref() is owner['factory'] and owned.registry_for(value).unresolved()
    value.k.close_result=0
    with pytest.raises(ExecutionContractError):owner['factory'].close()
    assert not owner['factory']._closed and owner['factory']._job==300
    entry,=owned.registry_for(value).unresolved();value.k.close_result=1
    owned.registry_for(value).retry_owned(entry,value)
    owner['factory'].close();assert owner['factory']._closed and owner['factory']._job is None


def test_v3_factory_current_native_policy_issuer_still_refuses_no_job_or_child():
    value=api();p=profile();factory=win.T037SessionProcessFactory(p,'job-limit-issuer',api=value)
    with pytest.raises(ExecutionContractError):factory(['server.exe'])
    assert_no_effect(value)


def prepared_child_api(monkeypatch, *, cleanup_fail=False):
    from tests.test_windows_suspended_ownership import _launch_api
    old,value,events,state=_launch_api(monkeypatch,cleanup_fail=cleanup_fail)
    value.close_job(300)
    p=replace(old,job_limits=limits.JobLimitsV1(4096,16384,4),wrapper_schema=win.PROFILE_SCHEMA_V3)
    sdk=SDK();value.k.CreateJobObjectW=sdk.CreateJobObjectW
    value.k.QueryInformationJobObject=sdk.QueryInformationJobObject
    value.k.SetInformationJobObject=sdk.SetInformationJobObject
    value.k.CloseHandle=sdk.CloseHandle
    value._job_process_ids=lambda job:list(sdk.ids)
    original_assign=value.k.AssignProcessToJobObject
    def assign(job,child):
        result=original_assign(job,child);sdk.ids=[456];return result
    value.k.AssignProcessToJobObject=assign
    original_terminate=value.terminate_process
    def terminate(*a):
        original_terminate(*a);sdk.ids=[]
    value.terminate_process=terminate
    return p,value,events,state,sdk


@pytest.mark.parametrize('unknown',[False,True])
def test_real_factory_api_child_refusal_keeps_original_job_and_strong_controller_root(tmp_path,monkeypatch,unknown):
    from comsol_mcp._control_daemon import ControlDaemon
    p,value,events,state,sdk=prepared_child_api(monkeypatch,cleanup_fail=unknown)
    daemon=ControlDaemon(tmp_path/'control');daemon._windows_t037_profile=p
    factory=daemon._new_t037_session_factory('p','configured-original');factory._api=value
    monkeypatch.setattr(factory,'preflight',lambda:None) # SOFTWARE continuation of the separately tested unavailable policy gate.
    monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'))
    try:
        if unknown:
            with pytest.raises(win.NativeSuspendedChildLaunchError) as e:factory([p._launch_image('server').path])
            child=e.value._process;original_error=e.value
            ref=weakref.ref(factory);del factory;gc.collect();factory=daemon._session_t037_process_factories[('p','configured-original')]
            assert ref() is factory and child._job_handle==factory._job==300
            assert not child._resume_attempted and sdk.ids==[456]
            with pytest.raises(ExecutionContractError):factory.close()
            assert sdk.calls.count(('close',300))==0 and factory._job==300
            state['cleanup_fail']=False
            owner=object();assert original_error.transfer_to(owner) is child
            assert child.cleanup_owned(owner)==1 and sdk.ids==[]
        else:
            with pytest.raises(win.WindowsT037Error) as e:factory([p._launch_image('server').path])
            assert e.value.code=='NATIVE_CHILD_PROOF_UNAVAILABLE' and sdk.ids==[]
        assert 'CREATE_SUSPENDED' in events and 'RESUME' not in events
        assert sdk.calls[:4]==['create',('query',300,9,144),('set',300,9,144),('query',300,9,144)]
        daemon._close_t037_session_factory(('p','configured-original'))
        assert sdk.calls[-1]==('close',300) and not owned.registry_for(value).unresolved()
        assert not daemon._session_t037_process_factories
    finally:daemon.close()


@pytest.mark.parametrize('fault',['none','null_profile','set_failure','readback_mismatch'])
def test_actual_owned_server_controller_store_failed_job_no_child_on_config_fault(tmp_path,monkeypatch,fault):
    from comsol_mcp._session_server import OwnedServerLauncher
    from tests.test_session_server_control import _make_daemon,_start
    p,value,events,state,sdk=prepared_child_api(monkeypatch)
    if fault=='none':p=replace(p,job_limits=None,wrapper_schema=win.PROFILE_SCHEMA_V2)
    if fault=='null_profile':p=None
    if fault=='set_failure':sdk.set_result=0
    if fault=='readback_mismatch':sdk.mutate=lambda row:setattr(row,'JobMemoryLimit',1)
    def factory(*args,**kwargs):
        job=value.create_job(profile=p)
        return value.create_suspended_appcontainer_child(['server.exe'],{},profile=p,job=job,role='server')
    launcher=OwnedServerLauncher(process_factory=factory,platform_name='darwin')
    daemon,project,*_=_make_daemon(tmp_path,monkeypatch,server_launcher=launcher)
    try:
        result=_start(daemon,project)
        assert not result['success'] and 'CREATE_SUSPENDED' not in events and 'RESUME' not in events
        assert daemon.store.job(result['execution']['job_id'])['status']=='FAILED'
        assert not daemon._session_server_handles
        if fault in {'none','null_profile'}:assert sdk.calls==[]
        else:
            entry,=owned.registry_for(value).unresolved();assert entry.value==300
            value.close_job(300)
    finally:daemon.close()


def test_actual_controller_store_child_unknown_and_explicit_cleanup_retains_job_owner(tmp_path,monkeypatch):
    from comsol_mcp._session_server import OwnedServerLauncher
    from tests.test_session_server_control import _make_daemon,_start,_stop
    p,value,events,state,sdk=prepared_child_api(monkeypatch,cleanup_fail=True)
    # Real factory and original launcher; only prebirth unavailable-policy continuation is software-injected.
    factory=win.T037SessionProcessFactory(p,'controller-child',api=value)
    monkeypatch.setattr(factory,'preflight',lambda:None)
    monkeypatch.setattr(win,'os',SimpleNamespace(name='nt'))
    launcher=OwnedServerLauncher(process_factory=factory,platform_name='darwin')
    # Session infrastructure filesystem OS dependency is shared module os; restore normal OS after call via local proxy.
    import os
    monkeypatch.setattr(win,'os',SimpleNamespace(**dict(vars(os),name='nt')))
    daemon,project,*_=_make_daemon(tmp_path,monkeypatch,server_launcher=launcher)
    try:
        started=_start(daemon,project);assert not started['success']
        sid=started['data']['session_id'];key=(project,sid);handle=daemon._session_server_handles[key]
        assert daemon.store.job(started['execution']['job_id'])['status']=='UNKNOWN'
        assert handle.process._job_handle==factory._job==300 and not handle.process._resume_attempted
        assert sdk.ids==[456] and owned.registry_for(value).unresolved()
        with pytest.raises(ExecutionContractError):factory.close()
        failed=_stop(daemon,project,sid,key='owned-job-cleanup-failure')
        assert not failed['success'] and daemon._session_server_handles[key] is handle
        state['cleanup_fail']=False
        stopped=_stop(daemon,project,sid,key='owned-job-cleanup-confirmed')
        assert stopped['success'] and sdk.ids==[] and handle.process._closed
        factory.close();assert sdk.calls[-1]==('close',300)
        assert not owned.registry_for(value).unresolved()
    finally:daemon.close()


@pytest.mark.parametrize('raw',[None,0,-1,True,'300',2**64-1,2**64,SimpleNamespace(value=300),C.c_void_p(None)])
def test_malformed_create_return_retained_before_validation_no_config_or_arbitrary_close(raw):
    value=api();p=profile();value.k.create_result=raw
    with pytest.raises(ExecutionContractError):value.create_job(profile=p)
    entry,=owned.registry_for(value).unresolved()
    assert entry.state=='UNKNOWN' and entry.value is None and entry.keepalive==(p,)
    before=list(value.k.calls)
    with pytest.raises(ExecutionContractError):value.create_job(profile=p)
    with pytest.raises(ExecutionContractError):owned.registry_for(value).retry_owned(entry,value)
    assert value.k.calls==before==['create']


@pytest.mark.parametrize('result',[None,True,'1',2**32,0])
def test_unknown_close_return_sticks_to_original_job_and_cannot_appear_success(result):
    value=api();p=profile();job=value.create_job(profile=p);value.k.close_result=result
    with pytest.raises(ExecutionContractError):value.close_job(job)
    entry,=owned.registry_for(value).unresolved();assert entry.state=='UNKNOWN'
    assert value._job_limit_bindings[job]['state']=='CONFIGURED'
    before=list(value.k.calls)
    with pytest.raises(ExecutionContractError):limits.require_configured_job(value,job,p)
    assert value.k.calls==before
    value.k.close_result=1;value.close_job(job)
    assert not owned.registry_for(value).unresolved()


def test_capacity_refusal_before_child_birth_and_live_job_close_refused():
    value=api();p=profile();job=value.create_job(profile=p);value.k.ids=[1,2,3,4]
    with pytest.raises(ExecutionContractError) as e:
        value.create_suspended_appcontainer_child(['server.exe'],{},profile=p,job=job,role='server')
    assert e.value.code=='JOB_LIMITS_ACTIVE_CAPACITY_EXHAUSTED'
    with pytest.raises(ExecutionContractError):value.close_job(job)
    assert ('close',job) not in value.k.calls
    value.k.ids=[];value.close_job(job)


@pytest.mark.parametrize('ids',[None,{},['456'],[0],[456,456],[2**32],[True]])
def test_native_membership_unknown_never_configures_or_closes_arbitrary_job(ids):
    value=api();p=profile();value._job_process_ids=lambda job:ids
    with pytest.raises(ExecutionContractError):value.create_job(profile=p)
    entry,=owned.registry_for(value).unresolved();assert entry.value==300
    assert not any(type(call) is tuple and call[0]=='set' for call in value.k.calls)
    with pytest.raises(ExecutionContractError):value.close_job(300)
    assert ('close',300) not in value.k.calls
    value._job_process_ids=lambda job:[];value.close_job(300)


@pytest.mark.parametrize('boundary',[4,5])
def test_actual_child_late_readback_drift_blocks_birth_or_assignment_and_original_cleanup(monkeypatch,boundary):
    p,value,events,state,sdk=prepared_child_api(monkeypatch)
    job=value.create_job(profile=p)
    original=sdk.QueryInformationJobObject;count={'query':2}
    def query(*args):
        count['query']+=1
        if count['query']==boundary:
            row=limits.EXTENDED_LIMIT.from_buffer_copy(sdk.data);row.BasicLimitInformation.LimitFlags|=0x2000;sdk.data=bytes(row)
        return original(*args)
    value.k.QueryInformationJobObject=query
    with pytest.raises(ExecutionContractError):
        value.create_suspended_appcontainer_child([p._launch_image('server').path],{},profile=p,job=job,role='server')
    assert ('CREATE_SUSPENDED' in events)==(boundary==5)
    assert 'assign-job' not in events and 'RESUME' not in events
    if boundary==5:assert ('terminate',200) in events and not state['alive']
    entry,=owned.registry_for(value).unresolved();assert entry.value==300
    value.close_job(job)


def test_concurrent_actual_job_creation_uses_same_original_registry_lock_and_no_second_job():
    import threading
    value=api();p=profile();entered=threading.Event();release=threading.Event();results=[]
    original=value.k.CreateJobObjectW
    def create(*args):
        entered.set();assert release.wait(2);return original(*args)
    value.k.CreateJobObjectW=create
    def attempt():
        try:results.append(value.create_job(profile=p))
        except ExecutionContractError as e:results.append(e.code)
    first=threading.Thread(target=attempt);second=threading.Thread(target=attempt)
    first.start();assert entered.wait(2);second.start();release.set();first.join(2);second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert sorted(map(str,results))==['300','JOB_LIMITS_OWNER_UNKNOWN']
    assert value.k.calls.count('create')==1 and len(owned.registry_for(value).unresolved())==1
    value.close_job(300)


def test_v3_plan_reports_explicit_intent_and_v2_plan_is_still_unconfigured():
    p=profile();manifest=win.build_changes_manifest(p)
    assert manifest['profile_wrapper_schema']==win.PROFILE_SCHEMA_V3
    row=next(x for x in manifest['changes'] if x['id']=='job-memory')
    assert row['state']=='TARGET_CONFIGURED_NATIVE_NOT_RUN' and row['targets']==p.job_limits.as_dict()
    assert manifest['native_windows_state']=='NOT_RUN'
    old=replace(p,job_limits=None,wrapper_schema=win.PROFILE_SCHEMA_V2)
    old_manifest=win.build_changes_manifest(old)
    assert 'job_limits_sha256' not in old_manifest
    assert next(x for x in old_manifest['changes'] if x['id']=='job-memory')['state']=='NOT_CONFIGURED'


def test_original_registry_confirmed_job_release_reconciles_factory_without_querying_closed_handle():
    value=api();p=profile();factory=win.T037SessionProcessFactory(p,'registry-reconcile',api=value)
    factory._job=value.create_job(profile=p)
    entry,=owned.registry_for(value).unresolved()
    owned.registry_for(value).retry_owned(entry,value)
    before=list(value.k.calls)
    value._job_process_ids=lambda *a:pytest.fail('closed Job HANDLE queried')
    factory.close();factory.close()
    assert value.k.calls==before and factory._closed and factory._job is None
    assert not value._job_limit_bindings and not owned.registry_for(value).unresolved()
