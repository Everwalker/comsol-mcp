"""Full same-process software SCM/daemon/A inputs; NEVER native authority.

OS APIs/listener are explicit software boundaries. The unavailable finite
filesystem/MIC target is never made PASS. Separate topology controls explicitly
continue past A in test memory to exercise the existing original Job producer.
"""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import ctypes as C
import hashlib
import json
import os
import socket
import subprocess
import threading

import pytest

from comsol_mcp import _windows_t037 as win, _windows_t037_resume as resume
from comsol_mcp import _windows_t037_admission as admission, _windows_t037_native as native
from comsol_mcp import _control_daemon as control, _windows_t037_broker as broker
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_t037_broker import _ScmApi
from tests.test_windows_host_change import _write_provider
from tests.test_windows_network_intent import effective_group_fixture

SOURCES={str(Path(m.__file__).relative_to(Path(win.__file__).parents[1])):
         hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
         for m in (win,resume,admission,control,broker)}


@pytest.fixture(autouse=True)
def no_native_or_external_effect(monkeypatch):
    def denied(*a,**k):pytest.fail('software-only SCM/prebirth must not call WinDLL, Popen, remote or RPC')
    monkeypatch.setattr(C,'WinDLL',denied,raising=False)
    monkeypatch.setattr(subprocess,'Popen',denied)
    monkeypatch.setattr(subprocess,'run',denied)
    monkeypatch.setattr(socket,'create_connection',denied)


def record(tmp_path, **facts):
    def scalar(value):
        if isinstance(value,C._SimpleCData):return value.value
        raise TypeError(type(value).__name__+' is not a software raw scalar')
    (tmp_path/'scm-case.json').write_text(json.dumps(dict(scope='SOFTWARE_ONLY_NOT_NATIVE_OR_ADMISSION',
        source_hashes=SOURCES,native_authority=False,**facts),indent=2,default=scalar)+'\n')


@contextmanager
def running_factory_chain(daemon, factory, tmp_path, monkeypatch, *, profile_path='SOFTWARE_PINNED_PROFILE'):
    """Original broker/ServiceMain runtime, retained by the exact real daemon.

    Only the SCM export boundary is synthetic. A remains unavailable; callers
    that explicitly continue past it do so in software test memory only.
    """
    profile=factory.profile
    tmp_path.mkdir(parents=True,exist_ok=True)
    provider=_write_provider(tmp_path/'scm-provider',profile.host_resource_profile,[])
    scm=_ScmApi();ready=threading.Event();release=threading.Event();errors=[];runtimes=[]
    with monkeypatch.context() as m:
        m.setattr(daemon,'_scoped_host_provider',provider)
        m.setattr(daemon.project_authority,'authorization_verifier',provider)
        m.setattr(daemon,'_windows_t037_profile_path',profile_path)
        def service_main(runtime):
            runtimes.append(runtime)
            runtime.mark_running()
            daemon._retain_t037_scm_runtime(runtime)
            key=next(k for k,v in daemon._session_t037_process_factories.items() if v is factory)
            admission._bind_session_factory(daemon,factory,key)
            ready.set()
            if not release.wait(10):raise TimeoutError('software SCM fixture did not release')
        service=broker.WindowsScmServiceBroker._for_software_test(
            profile.service_name,service_main,api=scm,token=broker._SCM_TEST_API_TOKEN)
        def run():
            try:service.run()
            except BaseException as error:errors.append(error);ready.set()
        thread=threading.Thread(target=run);thread.start()
        try:
            assert ready.wait(3) and not errors,errors
            yield runtimes[0],scm,provider
        finally:
            if scm.handler is not None:scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)
            release.set();thread.join(3)
            provider.close()
            assert not thread.is_alive() and not errors,errors


def daemon_factory(tmp_path, monkeypatch, profile=None):
    profile=effective_group_fixture()[0] if profile is None else profile
    daemon=control.ControlDaemon(tmp_path/'control',registry={})
    daemon._windows_t037_profile=profile
    factory=daemon._new_t037_session_factory('project','scm-session')
    monkeypatch.setattr(win,'os',NS(**dict(vars(os),name='nt')))
    return daemon,factory


def static_sdk(monkeypatch, profile, *, fault=None):
    """Real observer/parser functions over explicit readonly OS boundary rows."""
    _profile,wfp,snapshot=effective_group_fixture();events=[]
    policy=profile.windows_policy;launcher=profile._launch_image('launcher')
    pid=os.getpid()
    scm=dict(service_name=profile.service_name,service_start_name=profile.service_start_name,
        service_start_sid=policy.service_logon_sid,service_sid_type=policy.service_sid_type,
        service_sid=profile.service_sid,state='RUNNING',process_id=pid,
        binary_path=launcher.path,service_binary_image_path=launcher.path,
        service_binary_argv_sha256=launcher.argv_sha256,service_security_sha256='a'*64,
        service_security_semantics='OWNER_DACL_CANONICAL_ACE_ORDER_READ_CONTROL')
    if fault=='wrong_scm_pid':scm['process_id']+=1
    if fault=='wrong_service':scm['service_name']='Other'
    if fault=='wrong_scm_argv':scm['service_binary_argv_sha256']='e'*64
    if fault=='missing_default':snapshot['filters'].pop(0)
    token=dict(state='OBSERVED',user_sid=policy.service_logon_sid,group_sids=[profile.service_sid])
    if fault=='wrong_token':token['user_sid']='S-1-1-0'
    class ProcessSdk:
        def close(self,h):
            events.append(('close',h))
            if fault=='release_unknown' and h==72:raise OSError('SOFTWARE token CloseHandle UNKNOWN')
        def open_process(self,pid):
            owned.own(self,71,self.close,label='static_process');return 71
        def open_process_token(self,p):
            owned.own(self,72,self.close,label='static_token');return 72
        def close_handle(self,h):owned.registry_for(self).release(self,h)
        def read_token(self,h):return token.copy()
        def process_times(self,h):return (123,None)
        def process_image_path(self,h):return launcher.path
        def is_process_in_job(self,*a):return False
    sdk=ProcessSdk();observer=win.WindowsT037Observer(platform_name='windows',api=sdk)
    calls={'scm':0}
    def service(name):
        calls['scm']+=1;events.append(('scm',name))
        result=scm.copy()
        if fault=='changed_scm' and calls['scm']>1:result['service_security_sha256']='b'*64
        return result
    wfp.observe_service=service
    actual=native.WindowsNativePolicyAdapter(api=wfp)
    monkeypatch.setattr(win,'WindowsT037Observer',lambda:observer)
    monkeypatch.setattr(native,'WindowsNativePolicyAdapter',lambda:actual)
    monkeypatch.setattr(win,'windows_host_fingerprint',lambda **k:
                        'e'*64 if fault=='wrong_host' else profile.host_fingerprint_sha256)
    return events,sdk


@pytest.mark.parametrize('fault',[None,'wrong_host','wrong_service','wrong_scm_pid','wrong_scm_argv',
                                 'wrong_token','changed_scm','missing_default','release_unknown'])
def test_actual_factory_A_reads_static_inputs_without_unborn_child_or_vendor_and_still_refuses_missing_target(monkeypatch,tmp_path,fault):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    events,sdk=static_sdk(monkeypatch,factory.profile,fault=fault)
    try:
        with running_factory_chain(daemon,factory,tmp_path,monkeypatch):
            with pytest.raises(Exception) as error:factory.preflight()
            assert factory._job is None and factory._api is None and not resume._pending
            if fault is None:
                assert error.value.details['reasons']==['FINITE_FILESYSTEM_MIC_TARGET_PRODUCER_UNAVAILABLE']
                facts=error.value.details['observed']
                assert len(facts['matched_filter_keys'])==7
                assert 'WFP_EFFECTIVE_ARBITRATION_NOT_PROVEN' in facts['wfp_reasons']
                assert set(error.value.details['deferred'])=={'ORIGINAL_SUSPENDED_CHILD_B','PURPOSE_BOUND_QUALIFICATION_C','RESUME_ISSUER_UNAVAILABLE'}
            if fault=='release_unknown':
                registry=owned.registry_for(factory);entry,=registry.unresolved()
                assert entry.value==72 and entry.adapter is sdk and factory._prebirth_observers
                before=list(events)
                with pytest.raises(Exception):factory.preflight()
                with pytest.raises(Exception):factory.close()
                assert events==before and entry.state=='UNKNOWN'
            record(tmp_path,case='actual_A_static_inputs',fault=fault,
                   reason=getattr(error.value,'details',None),events=events,no_job_or_child=True)
    finally:daemon.close()


@pytest.mark.parametrize('drift',['profile','source','provider','provider_bytes','provider_closed','stopped',
                                 'runtime_pointer','daemon_pointer','factory_registration','closing'])
def test_original_runtime_chain_drift_refuses_before_static_sdk_job_or_child(monkeypatch,tmp_path,drift):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    monkeypatch.setattr(admission,'_observe_static_inputs',lambda *a:pytest.fail('invalid original chain must not query SDK'))
    try:
        with running_factory_chain(daemon,factory,tmp_path,monkeypatch) as (runtime,scm,provider):
            if drift=='profile':daemon._windows_t037_profile=replace(factory.profile)
            elif drift=='source':daemon._windows_t037_profile_path='foreign-source'
            elif drift=='provider':daemon._scoped_host_provider=object()
            elif drift=='provider_bytes':provider._path.write_bytes(provider._path.read_bytes()+b' ')
            elif drift=='provider_closed':provider.close()
            elif drift=='stopped':scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)
            elif drift=='runtime_pointer':daemon._windows_t037_scm_runtime=object()
            elif drift=='daemon_pointer':daemon._windows_t037_scm_binding=tuple(list(runtime._daemon_binding))
            elif drift=='factory_registration':daemon._session_t037_process_factories.clear()
            else:daemon._closing=True
            with pytest.raises(ExecutionContractError):factory.preflight()
            assert factory._job is None and factory._api is None
            record(tmp_path,case='runtime_drift',drift=drift,no_sdk_job_child=True)
    finally:
        daemon._closing=False  # Undo only the software close-boundary fault before normal local fixture teardown.
        daemon.close()


def test_unbound_console_lookalike_forged_and_preRUNNING_runtime_cannot_bind_or_query_native(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with pytest.raises(ExecutionContractError):factory.preflight()
        for value in (None,{},NS(is_active=True,service_name=factory.profile.service_name)):
            with pytest.raises(ExecutionContractError):daemon._retain_t037_scm_runtime(value)
        scm=_ScmApi();observed=[]
        def main(runtime):
            with pytest.raises(ExecutionContractError):daemon._retain_t037_scm_runtime(runtime)
            forged=broker.ScmServiceRuntime(runtime._broker,broker._SCM_RUNTIME_SEAL)
            with pytest.raises(ExecutionContractError):daemon._retain_t037_scm_runtime(forged)
            observed.append(runtime._state)
        service=broker.WindowsScmServiceBroker._for_software_test('ComsolHost',main,api=scm,token=broker._SCM_TEST_API_TOKEN)
        with pytest.raises(ExecutionContractError):service.run()  # Callback never reported RUNNING.
        assert observed==['START_PENDING'] and factory._job is None and factory._api is None
        record(tmp_path,case='console_forged_preRunning',states=observed)
    finally:daemon.close()


def test_runtime_is_original_once_nonserializable_and_invalid_after_SCM_return(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    other=control.ControlDaemon(tmp_path/'foreign-daemon',registry={})
    try:
        with running_factory_chain(daemon,factory,tmp_path,monkeypatch) as (runtime,scm,provider):
            assert daemon._windows_t037_scm_runtime is runtime is factory._scm_binding[0][0]
            assert admission._require_factory_runtime(factory)[0] is runtime
            with pytest.raises(TypeError):json.dumps(runtime)
            with pytest.raises(ExecutionContractError):daemon._retain_t037_scm_runtime(runtime)
            other._windows_t037_profile=factory.profile;other._windows_t037_profile_path=daemon._windows_t037_profile_path
            other._scoped_host_provider=provider;other.project_authority.authorization_verifier=provider
            with pytest.raises(ExecutionContractError):other._retain_t037_scm_runtime(runtime)
        with pytest.raises(ExecutionContractError):admission._require_factory_runtime(factory)
        assert not runtime.is_active and factory._job is None
        record(tmp_path,case='original_once_stopped',runtime_state=runtime._state,no_job_child=True)
    finally:daemon.close();other.close()


def test_original_ServiceMain_serve_listener_RUNNING_real_daemon_factory_chain(monkeypatch,tmp_path):
    profile,wfp,snapshot=effective_group_fixture()
    monkeypatch.setattr(win,'os',NS(**dict(vars(os),name='nt')))
    initial=_write_provider(tmp_path/'startup-provider',profile.host_resource_profile,[])
    provider_path=initial._path;initial.close()
    monkeypatch.setattr(win,'load_trusted_profile',lambda path:profile)
    events,sdk=static_sdk(monkeypatch,profile)
    monkeypatch.setattr(control,'process_identity',lambda pid:{'start_epoch_ms':42})
    monkeypatch.setattr('comsol_mcp._managed_backend.collect_legacy_registry',lambda:{})
    observed={};scm=_ScmApi()
    class Server:
        server_port=12345
        def __init__(self,*args):
            daemon=observed['daemon']
            assert daemon._windows_t037_scm_runtime is None
            factory=daemon._new_t037_session_factory('project','scm-listener-session')
            observed['factory']=factory
            assert factory._scm_binding is None
            with pytest.raises(ExecutionContractError):factory.preflight()
        def serve_forever(self):
            daemon=observed['daemon'];factory=daemon._new_t037_session_factory('project','scm-listener-session')
            runtime=observed['runtime']
            assert runtime._state=='RUNNING' and daemon._windows_t037_scm_runtime is runtime
            assert admission._require_factory_runtime(factory)[0] is runtime
            with pytest.raises(ExecutionContractError) as error:factory.preflight()
            assert error.value.details['reasons']==['FINITE_FILESYSTEM_MIC_TARGET_PRODUCER_UNAVAILABLE']
            assert factory._api is None and factory._job is None and not resume._pending
            observed['reason']=error.value.details
            assert scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)==0
        def shutdown(self):pass
        def server_close(self):pass
    monkeypatch.setattr(control,'ThreadingHTTPServer',Server)
    def service_main(runtime):
        observed['runtime']=runtime
        assert runtime._state=='START_PENDING'
        control.serve(tmp_path/'served-home',host_provider_config=provider_path,
            windows_t037_profile='SOFTWARE_PINNED_PROFILE',_scm_runtime=runtime,
            _scm_profile_source_sha256=profile.source_sha256,
            _owner_observer=lambda daemon:observed.update(daemon=daemon))
    service=broker.WindowsScmServiceBroker._for_software_test(
        profile.service_name,service_main,api=scm,token=broker._SCM_TEST_API_TOKEN)
    service.run()
    assert [s.current_state for s in scm.status]==[
        broker.SERVICE_START_PENDING,broker.SERVICE_RUNNING,broker.SERVICE_STOP_PENDING,broker.SERVICE_STOPPED]
    with pytest.raises(ExecutionContractError):admission._require_factory_runtime(observed['factory'])
    record(tmp_path,case='full_ServiceMain_serve_daemon_factory',states=[s.current_state for s in scm.status],
           static_failure=observed['reason'],same_original_runtime=True,no_job_child_resume=True,events=events)


@pytest.mark.parametrize('fault',[None,'configure_false','readback_drift'])
def test_existing_original_Job_producer_follows_explicit_software_A_continuation_and_never_Resume(monkeypatch,tmp_path,fault):
    from tests.test_windows_job_limits import prepared_child_api
    profile,api,events,model,job_sdk=prepared_child_api(monkeypatch)
    daemon,factory=daemon_factory(tmp_path,monkeypatch,profile)
    factory._api=api;timeline=[]
    # This is ONLY a topology/Job regression. The real A missing-target gate
    # above remains tested and refused. Nothing enters the native issuer registry.
    monkeypatch.setattr(factory,'preflight',lambda:timeline.append('EXPLICIT_SOFTWARE_A_CONTINUATION_NOT_ACCEPTANCE'))
    original_job=api.k.CreateJobObjectW;original_child=api.a.CreateProcessAsUserW
    def create_job(*args):timeline.append('ORIGINAL_JOB_CREATE');return original_job(*args)
    def create_child(*args):timeline.append('ORIGINAL_CREATE_SUSPENDED');return original_child(*args)
    api.k.CreateJobObjectW=create_job;api.a.CreateProcessAsUserW=create_child
    if fault=='configure_false':job_sdk.set_result=0
    if fault=='readback_drift':job_sdk.mutate=lambda row:setattr(row.BasicLimitInformation,'ActiveProcessLimit',99)
    try:
        with running_factory_chain(daemon,factory,tmp_path,monkeypatch):
            with pytest.raises(ExecutionContractError) as error:factory([profile._launch_image('server').path])
            assert job_sdk.calls.count('create')==1
            if fault is None:
                assert timeline==['EXPLICIT_SOFTWARE_A_CONTINUATION_NOT_ACCEPTANCE','ORIGINAL_JOB_CREATE','ORIGINAL_CREATE_SUSPENDED']
                assert error.value.code=='NATIVE_CHILD_PROOF_UNAVAILABLE'
                assert factory._job==300 and job_sdk.ids==[] and 'RESUME' not in events
                # The subsequent path checks this same Job, refuses an unknown
                # actual member and cannot create a second Job/child.
                job_sdk.ids=[999];before=list(timeline)
                with pytest.raises(ExecutionContractError):factory([profile._launch_image('server').path])
                assert timeline==before+['EXPLICIT_SOFTWARE_A_CONTINUATION_NOT_ACCEPTANCE']
                job_sdk.ids=[];factory.close()
            else:
                assert 'ORIGINAL_CREATE_SUSPENDED' not in timeline
                assert factory._job is None and owned.registry_for(api).unresolved()
                original_entry=owned.registry_for(api).unresolved()[0]
                before=list(job_sdk.calls)
                with pytest.raises(ExecutionContractError):factory([profile._launch_image('server').path])
                assert job_sdk.calls==before and owned.registry_for(api).unresolved()[0] is original_entry
            assert not resume._pending
            record(tmp_path,case='Job_topology_only_not_A_acceptance',fault=fault,timeline=timeline,
                   sdk_calls=job_sdk.calls,issuer_unchanged_refuses=True)
    finally:daemon.close()


@pytest.mark.parametrize('fault',[None,'foreign_pid','not_member','unreturned','terminated','capacity'])
def test_subsequent_same_Job_requires_actual_original_returned_child_and_capacity(monkeypatch,tmp_path,fault):
    from tests.test_windows_native_child_lifecycle import worker_case
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    assert worker.start()['status']=='HEALTHY'
    child,=processes;api=factory._api
    # Existing accepted SOFTWARE resumed-owner fixture; no new native issuer
    # registration, actual Resume, COMSOL or arbitrary project code is used.
    api._job_process_ids=lambda job:([child.pid,900,901,902] if fault=='capacity' else
                                    [999] if fault=='foreign_pid' else [child.pid])
    api.is_process_in_job=lambda handle,job:fault!='not_member'
    if fault=='unreturned':child._returned=False
    if fault=='terminated':child._termination_requested=True
    if fault is None:
        factory._require_original_job_members()
        assert not child._closed and child._owner is worker
    else:
        with pytest.raises(ExecutionContractError):factory._require_original_job_members()
        assert api._retained_children[id(child)] is child and not child._closed
    assert not resume._pending
    record(tmp_path,case='original_returned_members_only',fault=fault,same_job=True,
           software_existing_resumed_owner_fixture_only=True,no_new_birth=True)
