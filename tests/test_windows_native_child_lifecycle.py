"""Full consumer lifecycle over synthetic SDK boundaries, NEVER native authority.

The actual launch/storage/registry/return, Worker start/close and Server start/
stop functions execute. Issuer/Resume substitutes are explicit software-only
branches. No native child, subprocess, RPC, COMSOL or host policy is exercised.
"""
from pathlib import Path
from types import SimpleNamespace
import ctypes as C
import gc
import hashlib
import json
import socket
import subprocess

import pytest

from comsol_mcp import _windows_t037 as win, _windows_t037_resume as resume
from comsol_mcp import _java_worker as java, _session_server as server
from comsol_mcp._session_context import CanonicalSocket
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_fixed_listeners import worker_start
from tests.test_session_server import _startup_runtime

REAL_ISSUER = resume.issue_native_resume
SOURCE_HASHES = {str(Path(m.__file__).relative_to(Path(win.__file__).parents[1])):
                hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                for m in (win, java, server)}


@pytest.fixture(autouse=True)
def no_real_native_or_external_effect(monkeypatch):
    def denied(*args, **kwargs): pytest.fail('software child lifecycle must not start SDK/subprocess/RPC')
    monkeypatch.setattr(C, 'WinDLL', denied, raising=False)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(subprocess, 'run', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def record(tmp_path, child, api, **facts):
    value = dict(scope='SOFTWARE_FULL_FUNCTION_SDK_BOUNDARY_NOT_NATIVE',
        native_issuer_authority=False, source_hashes=SOURCE_HASHES,
        original_type=type(child).__name__, pid=child.pid,
        original_slot=dict(process=child._process_info.hProcess, thread=child._process_info.hThread,
                           pid=child._process_info.dwProcessId, tid=child._process_info.dwThreadId),
        birth=child.birth_filetime_100ns, returned=child._returned,
        resume_attempted=child._resume_attempted, never_resumed=child.never_resumed,
        closed=child._closed, returncode=child.returncode,
        thread_entry=child._thread_entry.state if child._thread_entry is not None else None,
        process_entry=child._process_entry.state if child._process_entry is not None else None,
        original_owner_retained=api._retained_children.get(id(child)) is child,
        registry=[dict(label=e.label, state=e.state) for e in registry_for(api).unresolved()], **facts)
    (tmp_path/'consumer-case.json').write_text(json.dumps(value,indent=2)+'\n')


def worker_case(monkeypatch, tmp_path):
    factory,worker,processes,commands,compiled,health,listeners=worker_start(monkeypatch,tmp_path)
    return factory,worker,processes,factory._api._software_child_model


def late_retirement_case(monkeypatch, tmp_path, fault='wait_timeout'):
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    api=factory._api;poll=api.poll_process
    controls=dict(poll_error=False);effects=[];queries=[]
    def observed(handle):
        queries.append(handle)
        if controls['poll_error']:raise OSError('SOFTWARE original HANDLE query temporarily unavailable')
        return poll(handle)
    def terminate(handle,code):
        effects.append(('terminate',handle,code))
        if fault=='terminate_exception':raise OSError('SOFTWARE original termination response unknown')
    def wait(handle,timeout,args):
        effects.append(('wait',handle))
        raise subprocess.TimeoutExpired(args,timeout)
    api.poll_process=observed;api.terminate_process=terminate;api.wait_process=wait
    assert worker.start()['status']=='HEALTHY'
    child,=processes
    controls['poll_error']=fault=='poll_error'
    with pytest.raises((OSError,subprocess.TimeoutExpired)):worker.close()
    assert child._returned and child._resume_confirmed and child._owner is worker
    assert child._process_entry.state=='ACQUIRED' and not child._closed
    return factory,worker,child,model,controls,effects,queries


@pytest.mark.parametrize('fault',['wait_timeout','terminate_exception','poll_error'])
def test_full_worker_late_original_exit_finishes_retirement_without_repeating_effect(monkeypatch,tmp_path,fault):
    factory,worker,child,model,controls,effects,queries=late_retirement_case(monkeypatch,tmp_path,fault)
    api=factory._api;unknown=worker._startup_unknown
    before=list(effects),list(api._software_handle_events)
    # Neither a temporarily restored query nor a still unavailable one permits
    # another terminate/wait or reopens start/RPC while this original is live.
    for unavailable in (False,True):
        controls['poll_error']=unavailable
        for operation in (worker.close,worker.start,lambda:worker._request({'type':'health'},timeout_s=0.01)):
            with pytest.raises(java.JavaWorkerError) as error:operation()
            assert error.value is unknown
        gc.collect()
        assert (effects,api._software_handle_events)==before and model['alive']
    controls['poll_error']=False;model['alive']=False
    for operation in (worker.start,lambda:worker._request({'type':'health'},timeout_s=0.01)):
        with pytest.raises(java.JavaWorkerError) as error:operation()
        assert error.value is unknown
    assert (effects,api._software_handle_events)==before
    queries_before=len(queries)
    worker.close()  # No caller-preloaded returncode: the original HANDLE must be queried here.
    assert len(queries)>queries_before and set(queries)=={200}
    assert effects==before[0]
    assert child._closed and child.returncode==1 and child._owner is worker
    assert worker._process is worker._fixed_process is worker._fixed_launch_owner is None
    assert worker._startup_unknown is None
    assert api._software_handle_events.count(('close',200))==api._software_handle_events.count(('close',201))==1
    final=list(api._software_handle_events);worker.close();gc.collect()
    assert final==api._software_handle_events and effects==before[0]
    factory.close();assert not registry_for(api).unresolved()
    record(tmp_path,child,api,consumer='worker',late_exit_after=fault,
           exact_original_query_then_release_once=True,retirement_effects=effects)


@pytest.mark.parametrize('drift',['error_instance','child_pointer','fixed_pointer','holder','output_slot',
                                 'registry_unknown','retirement_bookkeeping','process_close_attempt'])
def test_late_worker_exit_cannot_borrow_changed_retirement_origin_or_unsafe_original(monkeypatch,tmp_path,drift):
    factory,worker,child,model,controls,effects,queries=late_retirement_case(monkeypatch,tmp_path)
    api=factory._api;model['alive']=False
    if drift=='error_instance':
        old=worker._startup_unknown
        worker._startup_unknown=java.JavaWorkerError(str(old),reply=old.reply)
    elif drift=='child_pointer':worker._process=SimpleNamespace(returncode=1)
    elif drift=='fixed_pointer':worker._fixed_process=SimpleNamespace(returncode=1)
    elif drift=='holder':object.__setattr__(child,'_owner',object())
    elif drift=='output_slot':child._process_info.dwProcessId+=1
    elif drift=='registry_unknown':
        child._process_entry.state='UNKNOWN';child._process_entry.error=OSError('SOFTWARE original registry UNKNOWN')
    elif drift=='retirement_bookkeeping':child._retirement_error=RuntimeError('SOFTWARE original retirement record UNKNOWN')
    else:child._release_attempts.add('process')
    before=list(effects),list(api._software_handle_events),len(queries)
    for operation in (worker.close,worker.close,worker.start,lambda:worker._request({'type':'health'},timeout_s=0.01)):
        with pytest.raises(java.JavaWorkerError):operation()
    gc.collect()
    assert (effects,api._software_handle_events,len(queries))==before
    assert not child._closed and api._retained_children[id(child)] is child
    record(tmp_path,child,api,consumer='worker',late_retirement_drift=drift,
           no_query_or_second_effect=True,retirement_effects=effects)


@pytest.mark.parametrize('origin',['ready_wrong_port','health_exception','unreturned_resume','creation_unknown'])
def test_exact_exit_does_not_promote_startup_or_unreturned_unknown_to_worker_retirement(monkeypatch,tmp_path,origin):
    if origin=='ready_wrong_port':
        factory,worker,processes,*_=worker_start(monkeypatch,tmp_path,'wrong_port')
        model=factory._api._software_child_model
    else:
        factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    if origin=='health_exception':
        monkeypatch.setattr(worker,'health',lambda **kw:(_ for _ in ()).throw(RuntimeError('SOFTWARE ready health failed')))
    elif origin=='unreturned_resume':
        monkeypatch.setattr(win,'_Win32G2SessionLaunchOwner',lambda **kw:(_ for _ in ()).throw(RuntimeError('SOFTWARE G2 publication failed')))
    elif origin=='creation_unknown':
        create=factory._api.a.CreateProcessAsUserW
        def unknown_create(*args):
            create(*args)
            raise OSError('SOFTWARE original creation response unknown')
        factory._api.a.CreateProcessAsUserW=unknown_create
    with pytest.raises(java.JavaWorkerError):worker.start()
    child=worker._process;model['alive']=False
    if origin!='creation_unknown':assert child.poll()==1
    # A matching diagnostic/reason is not the original retirement path.
    worker._startup_unknown.reply={'status':'UNKNOWN','execution_state_unknown':True,
                                  'reason':'native_worker_retirement_unverified'}
    before=list(factory._api._software_handle_events)
    for operation in (worker.close,worker.start,lambda:worker._request({'type':'health'},timeout_s=0.01)):
        with pytest.raises(java.JavaWorkerError):operation()
    gc.collect()
    assert before==factory._api._software_handle_events and not child._closed
    assert child._owner is worker and factory._api._retained_children[id(child)] is child
    record(tmp_path,child,factory._api,consumer='worker',non_retirement_origin=origin,
           late_exit_is_not_startup_cleanup_authority=True)


@pytest.mark.parametrize('fault',['close_false','close_exception','registry_pop','owner_pop'])
@pytest.mark.parametrize('phase',['initial','late_exit'])
def test_full_worker_late_exit_never_retries_possibly_effective_close_or_retirement_bookkeeping(monkeypatch,tmp_path,fault,phase):
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    api=factory._api;close=api.k.CloseHandle;attempts=[];termination_attempts=[]
    if fault in ('close_false','close_exception'):
        def unconfirmed_close(handle):
            if handle==200:
                attempts.append(handle)
                if fault=='close_exception':raise OSError('SOFTWARE original CloseHandle response unknown')
                return 0
            return close(handle)
        api.k.CloseHandle=unconfirmed_close
    if phase=='late_exit':
        api.terminate_process=lambda handle,code:termination_attempts.append((handle,code))
        api.wait_process=lambda handle,timeout,args:(_ for _ in ()).throw(subprocess.TimeoutExpired(args,timeout))
    worker.start();child,=processes
    if phase=='late_exit':
        with pytest.raises(subprocess.TimeoutExpired):worker.close()
        assert child._process_entry.state=='ACQUIRED' and not attempts and model['alive']
        model['alive']=False
    if fault in ('registry_pop','owner_pop'):
        wanted=child._process_entry.seq if fault=='registry_pop' else id(child)
        class FailedPop(dict):
            def pop(self,key,*args):
                if key==wanted:raise RuntimeError('SOFTWARE post-close bookkeeping unknown')
                return super().pop(key,*args)
        if fault=='registry_pop':
            registry=registry_for(api);registry._entries=FailedPop(registry._entries)
        else:api._retained_children=FailedPop(api._retained_children)
    with pytest.raises(Exception):worker.close()
    assert not model['alive'] and child.returncode==1 and not child._closed
    assert 'process' in child._release_attempts
    before=list(api._software_handle_events),list(attempts)
    for operation in (worker.close,worker.close,worker.start,lambda:worker._request({'type':'health'},timeout_s=0.01)):
        with pytest.raises(java.JavaWorkerError):operation()
    gc.collect()
    assert (api._software_handle_events,attempts)==before
    assert worker._process is child and child._owner is worker and api._retained_children[id(child)] is child
    if fault in ('close_false','close_exception'):assert attempts==[200]
    else:assert api._software_handle_events.count(('close',200))==1
    assert termination_attempts==([(200,1)] if phase=='late_exit' else [])
    record(tmp_path,child,api,consumer='worker',possibly_effective_close_fault=fault,
           retirement_phase=phase,no_close_or_retirement_record_retry=True,close_attempts=attempts,
           termination_attempts=termination_attempts)


def server_case(monkeypatch, tmp_path):
    factory,_worker,processes,model=worker_case(monkeypatch,tmp_path/'sdk')
    api=factory._api;state=api._software_sdk
    api._hash_image_handle=lambda *a:factory.profile.server_image_sha256
    def launch(self,command,**options):
        state['image_path']=command[0]
        child=api.create_suspended_appcontainer_child(command,options,profile=self.profile,
            job=self._job,role='server',session_factory=self)
        state['producer_finished']=True;processes.append(child)
        Path(command[command.index('-portfile')+1]).write_text(str(factory.network_intent.server_listen_port))
        return child
    monkeypatch.setattr(win.T037SessionProcessFactory,'__call__',launch)
    control=dict(listener_remains=False,wrong_start_birth=False)
    def listeners(port):
        return [(456,CanonicalSocket('127.0.0.1',port))] if model['alive'] or control['listener_remains'] else []
    launcher=server.OwnedServerLauncher(process_factory=factory,platform_name='darwin',
        process_identity_reader=lambda pid:dict(alive=model['alive'],start_epoch_ms=123457 if control['wrong_start_birth'] else 123456),
        listener_reader=listeners,ready_timeout_s=0.03,poll_interval_s=0.001)
    return factory,api,model,processes,launcher,_startup_runtime(tmp_path/'server-runtime'),control


def test_full_worker_start_retire_owns_same_child_and_releases_terminal_original_once(monkeypatch,tmp_path):
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    api=factory._api
    assert worker.start()['status']=='HEALTHY'
    child,=processes
    assert worker._process is worker._fixed_process is child and child._owner is worker
    assert child._returned and child._resume_confirmed and not child.never_resumed
    assert child._thread_entry.state=='RELEASED' and child._process_entry.state=='ACQUIRED'
    with pytest.raises(win.WindowsT037Error):child.close()  # A borrower cannot impersonate the Worker owner.
    assert child.poll() is None
    worker.close()
    before=list(api._software_handle_events);worker.close();gc.collect()
    assert api._software_handle_events==before
    assert child._closed and child.returncode==1 and not model['alive']
    assert worker._process is worker._fixed_process is worker._fixed_launch_owner is None
    assert before.count(('close',200))==before.count(('close',201))==before.count(('terminate',200))==1
    assert win._native_g2_session_launch_owner(child) is None and not api._retained_children
    factory.close()
    assert not registry_for(api).unresolved()
    record(tmp_path,child,api,consumer='worker',full_start_and_retire=True,release_once=True)


@pytest.mark.parametrize('fault',['terminate_exception','wait_timeout','poll_error','process_close_false'])
def test_full_worker_retirement_unknown_keeps_original_and_blocks_restart_rpc_and_effect_retry(monkeypatch,tmp_path,fault):
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    api=factory._api;active={'failure':False};attempts=[]
    poll,close=api.poll_process,api.k.CloseHandle
    if fault=='terminate_exception':
        def terminate(h,code):attempts.append(('terminate',h));raise OSError('SOFTWARE termination effect unknown')
        api.terminate_process=terminate
    if fault=='wait_timeout':
        api.terminate_process=lambda h,code:attempts.append(('terminate',h))
        api.wait_process=lambda h,timeout,args:(_ for _ in ()).throw(subprocess.TimeoutExpired(args,timeout))
    if fault=='poll_error':
        def observed(h):
            if active['failure']:raise OSError('SOFTWARE original HANDLE wait query unavailable')
            return poll(h)
        api.poll_process=observed
    if fault=='process_close_false':
        def failed_close(h):
            if h==200:attempts.append(('close',h));return 0
            return close(h)
        api.k.CloseHandle=failed_close
    worker.start();child,=processes;active['failure']=True
    with pytest.raises(Exception):worker.close()
    assert worker._process is worker._fixed_process is child and child._owner is worker and not child._closed
    assert worker._startup_unknown is not None and api._retained_children[id(child)] is child
    before=list(api._software_handle_events),list(attempts)
    for operation in (worker.close,worker.start,lambda:worker._request({'type':'health'},timeout_s=0.01)):
        with pytest.raises(java.JavaWorkerError):operation()
    gc.collect()
    assert before==(api._software_handle_events,attempts)
    with pytest.raises(Exception):factory.close()
    assert attempts.count(('terminate',200))<=1 and attempts.count(('close',200))<=1
    record(tmp_path,child,api,consumer='worker',retirement_fault=fault,
           no_restart_rpc_or_native_effect_retry=True,attempts=attempts)


def test_worker_unmodified_issuer_failure_keeps_never_resumed_recovery_and_later_exact_exit_cleanup(monkeypatch,tmp_path):
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    api=factory._api;attempts=[]
    monkeypatch.setattr(resume,'issue_native_resume',REAL_ISSUER)
    def failed_terminate(h,code):attempts.append(h);raise OSError('SOFTWARE exact termination effect unknown')
    api.terminate_process=failed_terminate
    with pytest.raises(java.JavaWorkerError):worker.start()
    child=worker._process
    assert child.never_resumed and not child._returned and child._owner is worker
    assert worker._startup_unknown is not None and child._process_entry.state=='ACQUIRED'
    with pytest.raises(java.JavaWorkerError):worker.start()
    with pytest.raises(OSError):worker.close()
    assert attempts==[200] and not child._closed
    model['alive']=False  # A later exact original HANDLE exit observation; never a repeat terminate.
    worker.close()
    assert child._closed and worker._process is None and worker._startup_unknown is None
    assert api._software_handle_events.count(('close',200))==api._software_handle_events.count(('close',201))==1
    factory.close()
    record(tmp_path,child,api,consumer='worker',original_issuer_refused=True,
           exact_never_resumed_recovery_preserved=True,termination_attempts=attempts)


def test_worker_post_resume_bookkeeping_handoff_is_not_never_resumed_recovery(monkeypatch,tmp_path):
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    captured=[]
    def fail_receipt(**kwargs):captured.append(kwargs['process']);raise RuntimeError('SOFTWARE G2 receipt failed')
    monkeypatch.setattr(win,'_Win32G2SessionLaunchOwner',fail_receipt)
    with pytest.raises(java.JavaWorkerError):worker.start()
    child,=captured
    assert worker._process is child and child._owner is worker
    assert child._resume_attempted and not child.never_resumed and not child._returned
    before=list(factory._api._software_handle_events)
    for operation in (worker.close,worker.start,lambda:worker._request({'type':'health'},timeout_s=0.01)):
        with pytest.raises(java.JavaWorkerError):operation()
    gc.collect()
    assert factory._api._software_handle_events==before and model['alive']
    assert ('terminate',200) not in before and ('close',200) not in before
    record(tmp_path,child,factory._api,consumer='worker',post_resume_unknown_is_not_simplified_cleanup=True)


@pytest.mark.parametrize('already_exited',[False,True])
def test_full_server_start_stop_owns_same_resumed_original_and_closes_only_after_exact_exit(monkeypatch,tmp_path,already_exited):
    factory,api,model,processes,launcher,runtime,control=server_case(monkeypatch,tmp_path)
    handle=launcher.start(runtime,'project','session');child,=processes
    assert handle.process is child and child._owner is handle and handle.suspended_launch_recovery is None
    assert child._returned and not child.never_resumed and win._native_g2_session_launch_owner(child).role=='server'
    if already_exited:model['alive']=False
    outcome=launcher.stop(handle,timeout_s=0.01)
    assert outcome['exit_confirmed'] and outcome['listener_absent'] and outcome['server_stopped']
    assert handle.retired and child._closed and child.returncode==1
    events=list(api._software_handle_events);gc.collect()
    assert api._software_handle_events==events and events.count(('close',200))==events.count(('close',201))==1
    assert events.count(('terminate',200))==(0 if already_exited else 1)
    with pytest.raises(server.OwnedServerError):launcher.stop(handle)
    factory.close();assert not registry_for(api).unresolved()
    record(tmp_path,child,api,consumer='server',full_start_stop=True,already_exited=already_exited,
           outcome=outcome,release_once_after_original_exit=True)


@pytest.mark.parametrize('fault',['terminate_exception','wait_timeout','poll_error','process_close_false'])
def test_full_server_stop_unknown_never_loses_original_or_repeats_native_effect(monkeypatch,tmp_path,fault):
    factory,api,model,processes,launcher,runtime,control=server_case(monkeypatch,tmp_path)
    active={'failure':False};attempts=[];poll,close=api.poll_process,api.k.CloseHandle
    if fault=='terminate_exception':
        def terminate(h,code):attempts.append(('terminate',h));raise OSError('SOFTWARE original termination unknown')
        api.terminate_process=terminate
    if fault=='wait_timeout':
        api.terminate_process=lambda h,code:attempts.append(('terminate',h))
        api.wait_process=lambda h,timeout,args:(_ for _ in ()).throw(subprocess.TimeoutExpired(args,timeout))
    if fault=='poll_error':
        def observed(h):
            if active['failure']:raise OSError('SOFTWARE original process poll unavailable')
            return poll(h)
        api.poll_process=observed
    if fault=='process_close_false':
        def failed_close(h):
            if h==200:attempts.append(('close',h));return 0
            return close(h)
        api.k.CloseHandle=failed_close
    handle=launcher.start(runtime,'project','session');child,=processes;active['failure']=True
    with pytest.raises(server.OwnedServerError) as error:launcher.stop(handle,timeout_s=0.001)
    assert error.value.uncertain and error.value.handle is handle
    assert not handle.retired and not child._closed and child._owner is handle
    before=list(attempts)
    with pytest.raises(server.OwnedServerError):launcher.stop(handle,timeout_s=0.001)
    gc.collect()
    assert attempts==before and api._retained_children[id(child)] is child
    assert attempts.count(('terminate',200))<=1 and attempts.count(('close',200))<=1
    with pytest.raises(Exception):factory.close()
    record(tmp_path,child,api,consumer='server',stop_fault=fault,attempts=attempts,
           original_holder_retained_no_effect_retry=True)


def test_server_listener_absence_unknown_does_not_release_or_repeat_termination(monkeypatch,tmp_path):
    factory,api,model,processes,launcher,runtime,control=server_case(monkeypatch,tmp_path)
    handle=launcher.start(runtime,'project','session');child,=processes
    control['listener_remains']=True
    with pytest.raises(server.OwnedServerError):launcher.stop(handle)
    assert not handle.retired and not child._closed and child.returncode==1
    assert child._process_entry.state=='ACQUIRED' and api._software_handle_events.count(('close',200))==0
    assert api._software_handle_events.count(('terminate',200))==1
    control['listener_remains']=False  # Fresh bounded readonly listener absence, before any process close.
    assert launcher.stop(handle)['exit_confirmed']
    assert child._closed and api._software_handle_events.count(('close',200))==1
    assert api._software_handle_events.count(('terminate',200))==1
    factory.close()
    record(tmp_path,child,api,consumer='server',listener_unknown_held_until_absent=True)


def test_server_post_resume_unknown_handoff_cannot_enter_suspended_recovery_or_normal_stop(monkeypatch,tmp_path):
    factory,api,model,processes,launcher,runtime,control=server_case(monkeypatch,tmp_path)
    close=api.k.CloseHandle;attempts=[]
    def thread_close_unknown(h):
        if h==201:attempts.append(h);return 0
        return close(h)
    api.k.CloseHandle=thread_close_unknown
    with pytest.raises(server.OwnedServerError) as error:launcher.start(runtime,'project','session')
    handle=error.value.handle;child=handle.process
    assert child._owner is handle and handle.suspended_launch_recovery is child
    assert not child.never_resumed and not child._returned and child._thread_entry.state=='UNKNOWN'
    before=list(api._software_handle_events)
    with pytest.raises(server.OwnedServerError):launcher.stop(handle)
    gc.collect()
    assert api._software_handle_events==before and attempts==[201] and model['alive']
    assert not handle.retired and not child._closed
    record(tmp_path,child,api,consumer='server',post_resume_unknown_not_admitted_to_any_stop=True)


def test_full_public_controller_normal_resumed_server_stop_keeps_original_owner_contract(monkeypatch,tmp_path):
    from tests.test_session_server_control import _make_daemon,_start,_stop
    factory,api,model,processes,launcher,runtime,control=server_case(monkeypatch,tmp_path/'sdk-case')
    originals=[]
    class ActualSessionLauncher:
        def start(self,runtime,project_id,session_id,**kwargs):
            exact=win.T037SessionProcessFactory(factory.profile,session_id,api=api)
            exact._job=factory._job
            monkeypatch.setattr(exact,'preflight',lambda:None)  # SOFTWARE continuation, original gate separately tested.
            originals.append(exact)
            return launcher.start(runtime,project_id,session_id,process_factory=exact,**kwargs)
        def stop(self,*args,**kwargs):return launcher.stop(*args,**kwargs)
        def verify(self,*args,**kwargs):return launcher.verify(*args,**kwargs)
    daemon,project,*_=_make_daemon(tmp_path/'controller',monkeypatch,server_launcher=ActualSessionLauncher())
    try:
        started=_start(daemon,project)
        (tmp_path/'public-start.json').write_text(json.dumps(started,indent=2)+'\n')
        assert started['success'],started
        sid=started['data']['session_id'];key=(project,sid)
        handle=daemon._session_server_handles[key];child=handle.process
        assert child is processes[0] and child._owner is handle and not win.is_native_suspended_child(child)
        assert handle.suspended_launch_recovery is None
        stopped=_stop(daemon,project,sid,key='normal-original-resumed-stop')
        (tmp_path/'public-stop.json').write_text(json.dumps(stopped,indent=2)+'\n')
        assert stopped['success'],stopped
        assert key not in daemon._session_server_handles and handle.retired and child._closed
        assert daemon.session_lifecycle.get(project,sid)['state']=='STOPPED'
        original,=originals;original.close()
        assert not registry_for(api).unresolved()
        record(tmp_path,child,api,consumer='public_controller_server_stop',public_normal_stop_passed=True,
               control_source_modified=False,never_resumed_recovery_not_used=True)
    finally:daemon.close()


@pytest.mark.parametrize('role',['worker','server'])
@pytest.mark.parametrize('written_output',[False,True])
def test_full_consumer_create_exception_retains_exact_original_slot_without_cleanup_adoption_or_restart(monkeypatch,tmp_path,role,written_output):
    if role=='worker':
        factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
        api=factory._api
    else:
        factory,api,model,processes,launcher,runtime,control=server_case(monkeypatch,tmp_path)
    original_create=api.a.CreateProcessAsUserW;slots=[]
    def ambiguous_call(*args):
        slots.append(args[-1]._obj)
        if written_output:original_create(*args)
        raise OSError('SOFTWARE SDK call/return conversion unconfirmed after entry')
    api.a.CreateProcessAsUserW=ambiguous_call
    if role=='worker':
        with pytest.raises(java.JavaWorkerError):worker.start()
        child=worker._process
        actions=(worker.start,worker.close,lambda:worker._request({'type':'health'},timeout_s=0.01))
    else:
        with pytest.raises(server.OwnedServerError) as error:launcher.start(runtime,'project','session')
        holder=error.value.handle;child=holder.process
        assert holder.suspended_launch_recovery is child
        actions=(lambda:launcher.stop(holder),)
    assert child._process_info is slots[0] and len(slots)==1 and not child._creation_confirmed
    assert not child.never_resumed and child.birth_filetime_100ns is None
    if written_output:assert child._process_info.hProcess==200 and child._process_info.hThread==201
    else:assert child._process_info.hProcess is None and child._process_info.hThread is None
    before=list(api._software_handle_events)
    for action in actions:
        with pytest.raises(Exception):action()
    gc.collect()
    assert api._software_handle_events==before and len(slots)==1 and model['alive']
    assert ('terminate',200) not in before and ('close',200) not in before and ('close',201) not in before
    assert api._retained_children[id(child)] is child
    with pytest.raises(Exception):factory.close()
    record(tmp_path,child,api,consumer=role,creation_call_exception=True,written_output=written_output,
           exact_unconfirmed_output_storage_retained_no_effect_retry=True)


def test_copied_server_dataclass_cannot_terminate_actual_original_owner_before_release_guard(monkeypatch,tmp_path):
    from dataclasses import replace
    factory,api,model,processes,launcher,runtime,control=server_case(monkeypatch,tmp_path)
    original=launcher.start(runtime,'project','session');child,=processes
    copied=replace(original)
    assert copied is not original and copied.process is child and child._owner is original
    before=list(api._software_handle_events)
    with pytest.raises(server.OwnedServerError):launcher.stop(copied)
    record(tmp_path,child,api,consumer='server',copied_holder_did_not_terminate=model['alive'],
           handle_events_before=before,handle_events_after=api._software_handle_events)
    assert model['alive'] and api._software_handle_events==before
    assert not original.retired and not child._closed and child._owner is original
    assert launcher.stop(original)['exit_confirmed']
    factory.close()
