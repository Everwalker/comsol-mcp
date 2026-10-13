"""Actual owned consumers/coordinator/SQLite paths; native OS adapters are synthetic."""
from types import SimpleNamespace
import ctypes as C
import sqlite3,subprocess
import pytest
from comsol_mcp import _windows_t037 as win,_windows_t037_acl as acl
from comsol_mcp._session_server import OwnedServerLauncher,OwnedServerError
from tests.test_windows_suspended_ownership import _launch_api,_create,_unknown
from tests.test_session_server import _startup_runtime
from tests.test_session_server_control import _make_daemon,_start,_stop


def _actual_gate(monkeypatch,original):
    fake=acl.check_suspended_child_dacl
    adapter=next(c.cell_contents for c in fake.__closure__ if hasattr(c.cell_contents,'bound_statistics'))
    monkeypatch.setattr(acl,'check_suspended_child_dacl',original)
    monkeypatch.setattr(acl,'NativeChildAclApi',lambda:adapter)

@pytest.mark.parametrize('mode',['control','child_unknown','child_terminal'])
def test_inner_child_token_actual_owner_retry_and_factory_gate(monkeypatch,tmp_path,mode):
    original_acl=acl.check_suspended_child_dacl
    profile,api,events,state=_launch_api(monkeypatch,cleanup_fail=mode!='child_terminal')
    _actual_gate(monkeypatch,original_acl)
    original_open=api.a.OpenProcessToken;original_close=api.close_handle
    def open_token(p,access,out):
        result=original_open(p,access,out)
        if int(p)==200:C.cast(out,C.POINTER(api.W.HANDLE)).contents.value=57
        return result
    def close(handle):
        value=handle.value if hasattr(handle,'value') else handle
        if value==57 and mode!='control':
            events.append(('failed-close-child-token',57));raise OSError('actual native child token close failed')
        return original_close(handle)
    api.a.OpenProcessToken=open_token;api.close_handle=close
    launcher=OwnedServerLauncher(process_factory=lambda *a,**kw:_create(profile,api),platform_name='darwin')
    with pytest.raises(OwnedServerError) as captured:launcher.start(_startup_runtime(tmp_path),'project','session')
    error=captured.value
    assert error.uncertain and error.handle is not None
    process=error.handle.process
    assert process._owner is error.handle and process.pid==456 and process.birth_filetime_100ns==123
    retained=getattr(api,'_retained_launch_cleanup',[])
    api.close_job=lambda job:events.append(('close-job',job))
    factory=win.T037SessionProcessFactory(profile,'child-token-owner',api=api);factory._job=300
    if mode!='control':
        assert any(x[0]=='child_token' for x in retained)
        tokens=list(api._retained_child_tokens.values());assert len(tokens)==1
        token=tokens[0]
        assert type(token) is win._RetainedChildToken and token._process is process and token._handle.value==57 and not token.closed
        with pytest.raises(win.WindowsT037Error):token.release_owned(object())
        with pytest.raises(win.WindowsT037Error):factory.close()
        assert factory._job==300 and ('close-job',300) not in events
        assert bool(process._closed)==(mode=='child_terminal')
        api.close_handle=original_close
        token.release_owned(api);before=events.count(('close',57))
        token.release_owned(api)
        assert token.closed and events.count(('close',57))==before==1
        assert not api._retained_child_tokens
    api.close_handle=original_close;state['cleanup_fail']=False;launcher.stop(error.handle)
    factory.close();assert ('close-job',300) in events

@pytest.mark.parametrize('role',['server','worker'])
@pytest.mark.parametrize('fault',['control','resource_query','resource_deferred_write'])
def test_actual_memory_sqlite_release_unknown_retains_terminal_capability_until_all_original_jobs_clear(monkeypatch,tmp_path,role,fault):
    original_acl=acl.check_suspended_child_dacl
    if role=='server':
        profile,api,events,state=_launch_api(monkeypatch,cleanup_fail=True);_actual_gate(monkeypatch,original_acl)
        launcher=OwnedServerLauncher(process_factory=lambda *a,**kw:_create(profile,api),platform_name='darwin')
        daemon,project,*_=_make_daemon(tmp_path,monkeypatch,server_launcher=launcher)
        started=_start(daemon,project)
    else:
        from comsol_mcp._java_worker import PersistentJavaWorker
        profile,api,events,state,error=_unknown(monkeypatch)
        paths=SimpleNamespace(validate=lambda:None,classpath=lambda:('fixture','hash',1,'jars'),
            compilation_cache_fingerprint=lambda *args:('cache',{}),executable=lambda name:name,
            private_prefs=None,classpath_separator=':',resolved_global_lock_root=tmp_path)
        def factory(*a,**k):raise error
        worker=PersistentJavaWorker(paths,state_dir=tmp_path/'worker',process_factory=factory)
        monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stderr=''))
        daemon,project,*_=_make_daemon(tmp_path,monkeypatch,worker=worker)
        started=daemon.dispatch({'operation':'session.connect','arguments':{'project_id':project,'runtime_id':'fixture-runtime',
            'endpoint':{'host':'127.0.0.1','port':2040},'idempotency_key':'memory-worker-start'},'execution':{}})
    sid=started['data']['session_id'];key=(project,sid)
    owners=daemon._session_server_handles if role=='server' else daemon._session_worker_handles
    owner=owners[key];process=owner.process if role=='server' else owner._process;state['cleanup_fail']=False
    from comsol_mcp import _w26_memory_resources as memory
    from tests.test_w26_os_resources import _write_memory_config
    config=memory.TrustedMemoryResourceConfig.load(_write_memory_config(tmp_path))
    daemon._memory_resource_coordinator=memory.MemoryResourceCoordinator(config=config,store=daemon.store,authority=daemon.project_authority)
    def stop(label):
        if role=='server':return _stop(daemon,project,sid,key=label)
        return daemon.dispatch({'operation':'session.disconnect','arguments':{'project_id':project,'session_id':sid,
            'retire_worker':True,'idempotency_key':label},'execution':{}})
    denied=[]
    def authorizer(action,arg1,arg2,dbname,source):
        if action==sqlite3.SQLITE_READ and arg1=='job_events' and source is None:
            denied.append((action,arg1,arg2));return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    try:
        if fault=='resource_deferred_write':
            daemon.store.db.execute("CREATE TRIGGER resource_deferred_failure BEFORE INSERT ON job_events WHEN NEW.event='W26MemoryReservationReleaseDeferred' BEGIN SELECT RAISE(ABORT,'real SQLite deferred diagnostic failure'); END")
        if fault!='control':daemon.store.db.set_authorizer(authorizer)
        first=stop('memory-cleanup-'+role+'-'+fault)
        assert first['success'] and 'native_child_exit_evidence' in first['data'],first
        assert process._closed and type(process.returncode) is int
        count=sum(isinstance(x,tuple) and x[0]=='terminate' for x in events)
        if fault!='control':
            assert denied and owners.get(key) is owner
            assert (owner.process if role=='server' else owner._process) is process
            job_id=first['execution']['job_id']
            assert job_id in process._pending_resource_release_jobs
            expected='UNKNOWN' if fault=='resource_deferred_write' else 'DEFERRED'
            observed=process._terminal_resource_release_observations[job_id]
            assert observed['state']==expected
            assert observed['memory']['diagnostic_durable'] is (fault!='resource_deferred_write')
            second=stop('memory-cleanup-still-denied')
            assert second['success'] and owners.get(key) is owner and job_id in process._pending_resource_release_jobs
            assert second['execution']['job_id'] in process._pending_resource_release_jobs
            assert sum(isinstance(x,tuple) and x[0]=='terminate' for x in events)==count
            daemon.store.db.set_authorizer(None)
            if fault=='resource_deferred_write':daemon.store.db.execute('DROP TRIGGER resource_deferred_failure')
            terminal=stop('memory-cleanup-permission-restored')
            assert terminal['success'] and 'native_child_exit_evidence' in terminal['data']
            assert not terminal['data'].get('already_stopped') and not terminal['data'].get('already_retired')
        assert key not in owners and not process._pending_resource_release_jobs
        assert sum(isinstance(x,tuple) and x[0]=='terminate' for x in events)==count
    finally:
        daemon.store.db.set_authorizer(None);daemon.close()
