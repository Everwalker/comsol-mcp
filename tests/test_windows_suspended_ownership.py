from __future__ import annotations
import ctypes as C
import io,threading
from types import SimpleNamespace
import pytest
from comsol_mcp import _windows_t037 as win
from comsol_mcp._windows_t037_native import DWORD,BOOL,HANDLE,WORD,BYTE
from tests.test_windows_child_acl import _observer


def _launch_api(monkeypatch,*,cleanup_fail=False,acl_fail=False):
    profile,acl_api,acl_launch,acl_events=_observer()
    from dataclasses import replace
    from comsol_mcp._windows_job_limits import JobLimitsV1, EXTENDED_LIMIT
    profile=replace(profile,job_limits=JobLimitsV1(4096,16384,4),wrapper_schema=win.PROFILE_SCHEMA_V3)
    events=[];state={'alive':True,'cleanup_fail':cleanup_fail,'acl':None,'assigned':False}
    job_data={'bytes':bytes(EXTENDED_LIMIT())}
    class PI(C.Structure):
        _fields_=[('hProcess',HANDLE),('hThread',HANDLE),('dwProcessId',DWORD),('dwThreadId',DWORD)]
    class A:
        def OpenProcessToken(self,p,access,out):
            events.append(('open-token',int(p),access));C.cast(out,C.POINTER(HANDLE)).contents.value=55;return 1
        def DuplicateTokenEx(self,t,access,attrs,level,kind,out):C.cast(out,C.POINTER(HANDLE)).contents.value=56;return 1
        def ConvertStringSidToSidW(self,text,out):C.cast(out,C.POINTER(C.c_void_p)).contents.value=77;return 1
        def CreateProcessAsUserW(self,*args):
            events.append('CREATE_SUSPENDED');pi=C.cast(args[-1],C.POINTER(PI)).contents
            assert args[6]&win._Win32LaunchApi.CREATE_SUSPENDED
            pi.hProcess=200;pi.hThread=201;pi.dwProcessId=456;pi.dwThreadId=9;return 1
    class K:
        def GetCurrentProcess(self):return 1
        def AssignProcessToJobObject(self,job,p):events.append('assign-job');state['assigned']=True;return 1
        def CreateJobObjectW(self,*args):return 300
        def SetInformationJobObject(self,h,kind,p,size):job_data['bytes']=C.string_at(p,size);return 1
        def QueryInformationJobObject(self,h,kind,p,size,out):
            C.memmove(p,job_data['bytes'],size);C.cast(out,C.POINTER(DWORD)).contents.value=size;return 1
        def CloseHandle(self,h):
            api.close_handle(h);return 1
        def ResumeThread(self,t):events.append('RESUME');return 1
        def DeleteProcThreadAttributeList(self,p):events.append('delete-attribute')
        def LocalFree(self,p):events.append('free-sid');return None
    class Image:
        def source_fingerprint(self,h):return ((1,2),10,3,4,1)
        def close(self,h):events.append(('close-image',h))
    api=win._Win32LaunchApi.__new__(win._Win32LaunchApi)
    api.C=C;api.W=SimpleNamespace(HANDLE=HANDLE,DWORD=DWORD,BOOL=BOOL,WORD=WORD,BYTE=BYTE,UINT=DWORD,LPCWSTR=C.c_wchar_p,LPWSTR=C.c_wchar_p)
    api.a=A();api.k=K()
    def check(ok,label):
        if not ok:raise win.WindowsT037Error('NATIVE_TEST_FAILURE',label)
    api._check=check
    api._open_image=lambda path:(Image(),[],12,((1,2),10,3,4,1),path)
    api._hash_image_handle=lambda *_:profile.server_image_sha256
    api.read_token=lambda t:{'state':'OBSERVED','user_sid':profile.service_account_sid,'is_appcontainer':True,'appcontainer_sid':profile.appcontainer_sid,'capability_sids':list(profile.capability_sids)}
    api._stdio_source=lambda *_,**__:(100,False)
    api._duplicate_inheritable=lambda s:HANDLE(101)
    api._attribute_list=lambda *args:(None,C.c_void_p(123))
    api._environment_buffer=lambda env:None
    api.process_image_path=lambda p:profile._launch_image('server').path
    api.is_process_in_job=lambda *_:True
    api.open_process=lambda pid:777
    api.job_snapshot=lambda job:{'state':'SOFTWARE_READBACK_ONLY'}
    monkeypatch.setattr('comsol_mcp._windows_t037_child.NativeChildProcessApi',lambda:SimpleNamespace(
        current_pid=lambda:55,process_pid=lambda handle:456,parent_pid=lambda pid:55))
    api.process_times=lambda p:(10,None) if p==777 else (123,None)
    api.close_handle=lambda h:events.append(('close',h.value if hasattr(h,'value') else h))
    api.poll_process=lambda h:None if state['alive'] else 1
    def terminate(h,code):
        events.append(('terminate',h))
        if state['cleanup_fail']:raise RuntimeError('synthetic exact-handle termination failure')
        state['alive']=False
    api.terminate_process=terminate
    api.wait_process=lambda h,timeout,args:1 if not state['alive'] else (_ for _ in ()).throw(TimeoutError('still live'))
    from comsol_mcp._windows_t037_acl import check_suspended_child_dacl as real_acl_gate
    def acl_gate(p,process,token,pid,launch):
        events.append('AccessCheckGate')
        if acl_fail:raise win.WindowsT037Error('CHILD_ACL_RIGHTS_REFUSED','synthetic native DACL denial')
        state['acl']=real_acl_gate(p,process,token,pid,launch,api=acl_api)
        return state['acl']
    monkeypatch.setattr('comsol_mcp._windows_t037_acl.check_suspended_child_dacl',acl_gate)
    api._job_process_ids=lambda job:[456] if state["assigned"] and state["alive"] else []
    assert api.create_job(profile=profile)==300
    return profile,api,events,state


def _create(profile,api):
    return api.create_suspended_appcontainer_child([profile._launch_image('server').path],{},profile=profile,job=300,role='server')


@pytest.mark.parametrize('acl_fail',[False,True])
def test_actual_production_launch_gate_refuses_resume_and_confirms_exact_cleanup(monkeypatch,acl_fail):
    profile,api,events,state=_launch_api(monkeypatch,acl_fail=acl_fail)
    with pytest.raises(win.WindowsT037Error) as captured:_create(profile,api)
    assert captured.value.code==('CHILD_ACL_RIGHTS_REFUSED' if acl_fail else 'NATIVE_CHILD_PROOF_UNAVAILABLE')
    assert 'CREATE_SUSPENDED' in events and 'AccessCheckGate' in events and 'RESUME' not in events
    assert ('terminate',200) in events and ('close',200) in events and ('close',201) in events
    assert not state['alive']


def _unknown(monkeypatch):
    profile,api,events,state=_launch_api(monkeypatch,cleanup_fail=True)
    with pytest.raises(win.NativeSuspendedChildLaunchError) as captured:_create(profile,api)
    error=captured.value
    assert error.details['effect_possible'] is True and error.details['birth_filetime_100ns']==123
    assert ('close',200) not in events and ('close',201) not in events and 'RESUME' not in events
    return profile,api,events,state,error


def test_unknown_child_has_single_owner_and_blocks_job_close_until_terminal(monkeypatch):
    profile,api,events,state,error=_unknown(monkeypatch)
    factory=win.T037SessionProcessFactory(profile,'session-test',api=api);factory._job=300
    original_job_close=api.close_job
    api.close_job=lambda h:(original_job_close(h),events.append(('close-job',h)))
    with pytest.raises(win.WindowsT037Error):factory.close()
    assert factory._job==300 and not factory._closed
    owner=object();process=error.transfer_to(owner)
    with pytest.raises(win.WindowsT037Error):error.transfer_to(object())
    with pytest.raises(win.WindowsT037Error):process.close()
    assert process._owner is owner
    with pytest.raises(RuntimeError):process.cleanup_owned(owner)
    assert not process._closed and process._owner is owner
    state['cleanup_fail']=False;state['alive']=False  # SDK observes exit; no uncertain termination retry.
    assert process.cleanup_owned(owner)==1
    assert process.cleanup_owned(owner)==1
    assert events.count(('close',200))==1 and events.count(('close',201))==1
    factory.close();factory.close()
    assert events.count(('close-job',300))==1


def test_server_actual_start_transfers_unknown_child_to_managed_handle_then_cleanup_is_terminal(monkeypatch,tmp_path):
    from tests.test_session_server import _startup_runtime
    from comsol_mcp._session_server import OwnedServerLauncher,_StartupObservationError
    profile,api,events,state,error=_unknown(monkeypatch)
    def factory(*_,**__):raise error
    launcher=OwnedServerLauncher(process_factory=factory,platform_name='darwin')
    with pytest.raises(_StartupObservationError) as captured:launcher.start(_startup_runtime(tmp_path),'project','session')
    handle=captured.value.handle
    assert captured.value.uncertain and handle.process is error._process
    assert handle.process._owner is handle and handle.endpoint is None and not handle.retired
    with pytest.raises(Exception):launcher.stop(handle)
    assert not handle.retired and not handle.process._closed
    state['cleanup_fail']=False;state['alive']=False  # SDK observes exit; no uncertain termination retry.
    outcome=launcher.stop(handle)
    assert outcome['never_resumed_native_child'] and outcome['exit_confirmed'] and handle.retired


def test_server_simplified_cleanup_refuses_dictionary_and_resume_attempted_capability(monkeypatch,tmp_path):
    from comsol_mcp._session_server import OwnedServerLauncher,ManagedServerHandle
    profile,api,events,state,error=_unknown(monkeypatch)
    launcher=OwnedServerLauncher(platform_name='darwin')
    fake={'pid':456,'never_resumed':True}
    handle=ManagedServerHandle(fake,'runtime','exe',None,suspended_launch_recovery=fake)
    with pytest.raises(Exception):launcher.stop(handle)
    handle=ManagedServerHandle(None,'runtime','exe',None)
    handle.process=error.transfer_to(handle);handle.suspended_launch_recovery=handle.process
    handle.process.note_resume_attempt()
    before=list(events)
    with pytest.raises(Exception):launcher.stop(handle)
    assert events==before and not handle.retired


def test_actual_java_worker_start_retains_native_child_and_close_failure_never_drops_owner(monkeypatch,tmp_path):
    from comsol_mcp._java_worker import PersistentJavaWorker,JavaWorkerError
    import subprocess
    profile,api,events,state,error=_unknown(monkeypatch)
    paths=SimpleNamespace(validate=lambda:None,classpath=lambda:('synthetic','hash',1,'jars'),
       compilation_cache_fingerprint=lambda *args:('cache',{}),executable=lambda name:name,
       private_prefs=None,classpath_separator=':',resolved_global_lock_root=tmp_path)
    def factory(*_,**__):raise error
    worker=PersistentJavaWorker(paths,state_dir=tmp_path/'worker',process_factory=factory)
    monkeypatch.setattr(subprocess,'run',lambda *_,**__:SimpleNamespace(returncode=0,stderr=''))
    with pytest.raises(JavaWorkerError):worker.start()
    process=worker._process
    assert process is error._process and process._owner is worker
    with pytest.raises(RuntimeError):worker.close()
    assert worker._process is process and not process._closed
    state['cleanup_fail']=False;state['alive']=False  # SDK observes exit; no uncertain termination retry.
    worker.close();worker.close()
    assert worker._process is None and process._closed


def test_actual_daemon_server_start_unknown_and_authorized_stop_keep_store_and_live_owner(monkeypatch,tmp_path):
    from tests.test_session_server_control import _make_daemon,_start,_stop
    from comsol_mcp._session_server import OwnedServerLauncher
    profile,api,events,state,error=_unknown(monkeypatch)
    def factory(*_,**__):raise error
    launcher=OwnedServerLauncher(process_factory=factory,platform_name='darwin')
    daemon,project,*_=_make_daemon(tmp_path,monkeypatch,server_launcher=launcher)
    try:
        started=_start(daemon,project)
        assert not started['success'] and started['error']['code']=='EXECUTION_STATE_UNKNOWN'
        sid=started['data']['session_id'];key=(project,sid);handle=daemon._session_server_handles[key]
        assert daemon.store.job(started['execution']['job_id'])['status']=='UNKNOWN'
        rows=daemon.store.db.execute("SELECT metadata FROM job_events WHERE event='NativeChildOwnershipUnknown'").fetchall()
        import json
        assert json.loads(rows[-1][0])['birth_filetime_100ns']==123
        failed=_stop(daemon,project,sid,key='native-stop-unknown')
        assert not failed['success'] and failed['error']['code']=='EXECUTION_STATE_UNKNOWN'
        assert daemon._session_server_handles[key] is handle and not handle.process._closed
        state['cleanup_fail']=False;state['alive']=False  # SDK observes exit; no uncertain termination retry.
        stopped=_stop(daemon,project,sid,key='native-stop-terminal')
        assert stopped['success'],stopped
        assert daemon.session_lifecycle.get(project,sid)['state']=='STOPPED'
        assert key not in daemon._session_server_handles and handle.process._closed
    finally:daemon.close()


def test_actual_daemon_worker_before_connect_dispatch_retains_unknown_owner_and_store(monkeypatch,tmp_path):
    from tests.test_session_server_control import _make_daemon
    from comsol_mcp._java_worker import PersistentJavaWorker
    import subprocess
    profile,api,events,state,error=_unknown(monkeypatch)
    paths=SimpleNamespace(validate=lambda:None,classpath=lambda:('synthetic','hash',1,'jars'),
       compilation_cache_fingerprint=lambda *args:('cache',{}),executable=lambda name:name,
       private_prefs=None,classpath_separator=':',resolved_global_lock_root=tmp_path)
    def process_factory(*_,**__):raise error
    worker=PersistentJavaWorker(paths,state_dir=tmp_path/'worker',process_factory=process_factory)
    monkeypatch.setattr(subprocess,'run',lambda *_,**__:SimpleNamespace(returncode=0,stderr=''))
    daemon,project,*_=_make_daemon(tmp_path,monkeypatch,worker=worker)
    try:
        response=daemon.dispatch({'operation':'session.connect','arguments':{'project_id':project,'runtime_id':'fixture-runtime','endpoint':{'host':'127.0.0.1','port':2040},'idempotency_key':'native-worker-start'},'execution':{}})
        assert not response['success'] and response['error']['code']=='EXECUTION_STATE_UNKNOWN',response
        assert response['error']['safe_retry'] is False
        assert response['data']['engine_dispatched'] is False and response['data']['child_effect_possible'] is True
        sid=response['data']['session_id'];key=(project,sid)
        assert daemon._session_worker_handles[key] is worker
        assert daemon.store.job(response['execution']['job_id'])['status']=='UNKNOWN'
        rows=daemon.store.db.execute("SELECT metadata FROM job_events WHERE event='NativeChildOwnershipUnknown'").fetchall()
        assert rows
        def retire(label):
            return daemon.dispatch({'operation':'session.disconnect','arguments':{'project_id':project,
                'session_id':sid,'retire_worker':True,'idempotency_key':label},'execution':{}})
        failed=retire('native-retire-fail')
        assert not failed['success'] and failed['error']['execution_state_unknown'] is True,failed
        assert daemon._session_worker_handles[key] is worker and worker._process is error._process
        state['cleanup_fail']=False;state['alive']=False  # SDK observes exit; no uncertain termination retry.
        original=daemon.store.add_event
        def fail_exit(job,event,metadata):
            if event=='NativeChildExitConfirmed':raise OSError('durable Worker exit event failed')
            return original(job,event,metadata)
        monkeypatch.setattr(daemon.store,'add_event',fail_exit)
        failed=retire('native-retire-durable-fail')
        assert not failed['success'] and worker._process is error._process and error._process._closed,failed
        monkeypatch.setattr(daemon.store,'add_event',original)
        terminal=retire('native-retire-terminal')
        assert terminal['success'] and terminal['data']['client_state']=='RETIRED',terminal
        assert key not in daemon._session_worker_handles and worker._process is None
    finally:daemon.close()


def test_native_wait_timeout_and_birth_unknown_keep_original_handle_and_cannot_close_job(monkeypatch):
    profile,api,events,state=_launch_api(monkeypatch)
    api.process_times=lambda p:(10,None) if p==777 else (_ for _ in ()).throw(RuntimeError('birth observation unavailable'))
    def terminate(h,code):events.append(('terminate-requested',h))
    api.terminate_process=terminate
    with pytest.raises(win.NativeSuspendedChildLaunchError) as captured:_create(profile,api)
    error=captured.value
    assert error.details['birth_filetime_100ns'] is None
    assert ('close',200) not in events and ('close',201) not in events
    factory=win.T037SessionProcessFactory(profile,'session-timeout',api=api);factory._job=300
    original_job_close=api.close_job
    api.close_job=lambda h:(original_job_close(h),events.append(('close-job',h)))
    with pytest.raises(win.WindowsT037Error):factory.close()
    assert factory._job==300 and not factory._closed
    owner=object();process=error.transfer_to(owner)
    state['alive']=False
    assert process.cleanup_owned(owner)==1
    factory.close()
    assert ('close-job',300) in events


def test_actual_daemon_native_exit_event_write_failure_keeps_live_capability_for_terminal_retry(monkeypatch,tmp_path):
    from tests.test_session_server_control import _make_daemon,_start,_stop
    from comsol_mcp._session_server import OwnedServerLauncher
    profile,api,events,state,error=_unknown(monkeypatch)
    def factory(*_,**__):raise error
    launcher=OwnedServerLauncher(process_factory=factory,platform_name='darwin')
    daemon,project,*_=_make_daemon(tmp_path,monkeypatch,server_launcher=launcher)
    try:
        started=_start(daemon,project);sid=started['data']['session_id'];key=(project,sid)
        state['cleanup_fail']=False;state['alive']=False  # SDK observes exit; no uncertain termination retry.
        original=daemon.store.add_event
        def fail_once(job,event,metadata):
            if event=='NativeChildExitConfirmed':raise RuntimeError('synthetic durable exit-event failure')
            return original(job,event,metadata)
        monkeypatch.setattr(daemon.store,'add_event',fail_once)
        failed=_stop(daemon,project,sid,key='native-terminal-write-fail')
        assert not failed['success'] and failed['error']['code']=='EXECUTION_STATE_UNKNOWN'
        handle=daemon._session_server_handles[key]
        assert handle.retired and handle.process._closed
        assert failed['data']['native_cleanup_state']=='EXIT_CONFIRMED'
        monkeypatch.setattr(daemon.store,'add_event',original)
        before=events.count(('terminate',200))
        stopped=_stop(daemon,project,sid,key='native-terminal-write-retry')
        assert stopped['success'],stopped
        assert events.count(('terminate',200))==before
        assert key not in daemon._session_server_handles
    finally:daemon.close()
