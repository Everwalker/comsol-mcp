"""Actual static server/ControlDaemon/Worker receivers and owned Python children."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from tools import run_native_w24_static_shape_setup_campaign as campaign
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._java_worker import PersistentJavaWorker, JavaWorkerPaths


@pytest.fixture
def original_graph(tmp_path):
    work=tmp_path/'work';evidence=tmp_path/'evidence';work.mkdir();evidence.mkdir();(work/'project').mkdir()
    server=campaign.W24StaticShapeSetupServer(work,evidence,event_log=evidence/'events.jsonl')
    daemon=ControlDaemon(tmp_path/'control',project_root=work/'project')
    children=[subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for _ in range(2)]
    server.proc=children[0];server._original_server_popen=children[0];server._original_server_pid=children[0].pid;server._server_birth_attempted=True
    worker=PersistentJavaWorker(JavaWorkerPaths(campaign.INSTALL_ROOT,campaign.JAVA11,project_root=work/'project'),state_dir=work/'worker')
    worker._process=children[1]
    key=('owned-software-project','owned-software-session')
    backend=ManagedBackend(tmp_path/'registered-backend',daemon.store,worker=worker,project_root=work/'project')
    daemon._session_worker_handles[key]=worker;daemon._session_backends[key]=backend
    daemon._remember_session_worker_child(key,worker)
    assert daemon._session_worker_child_identities[key]['process'] is children[1]
    record,_=daemon.store.begin(request_id='retention-unknown',idempotency_key='retention-unknown',request_hash='owned-software-only',operation='model.create')
    daemon.store.finish(record['operation_id'],status='UNKNOWN',result={'success':False,'error':{'code':'EXECUTION_STATE_UNKNOWN','safe_retry':False}})
    default_backend=daemon._default_backend
    before=daemon.store.job(record['job_id']);cause=RuntimeError('original managed native outcome unknown (software-only children)')
    campaign._capture_hold_handles(server,daemon,cause)
    yield server,daemon,worker,backend,key,children,evidence,cause,record,before
    # Explicit test-owned harmless children only; never native.close/old PID signals.
    for child in children:
        if child.poll() is None:child.terminate()
        child.wait(timeout=5)
    daemon._session_worker_handles.clear();daemon._session_worker_child_identities.clear();daemon._session_backends.clear()
    daemon._default_backend=default_backend
    ControlDaemon.close(daemon)


def start_hold(graph,*,unknown=False,stop=None):
    server,daemon,worker,backend,key,children,evidence,cause,record,before=graph
    result={}
    def run():
        try:
            if unknown:result['value']=campaign._unknown_hold(daemon=daemon,server=server,monitor=None,run_dir=evidence,work=server.work,project_id=None,session=None,candidate_sha256='a'*64,reason='SOFTWARE_NO_ENGINE',first_cause=cause,stop_event=stop)
            else:result['value']=campaign._hold_forever_with_handles(None,server=server,daemon=daemon,first_cause=cause,run_dir=evidence,stop_event=stop)
        except BaseException as exc:result['escaped']=exc
    thread=threading.Thread(target=run);thread.start()
    assert daemon.closed.wait(3)
    return thread,result


def test_actual_unknown_file_collision_does_not_discard_original_children_or_rewrite_domain(original_graph):
    server,daemon,worker,backend,key,children,evidence,cause,record,before=original_graph
    collision=evidence/'unknown_handle_hold.json';collision.write_bytes(b'original immutable collision\n')
    thread,result=start_hold(original_graph,unknown=True)
    try:
        assert thread.is_alive()
        for effect in [lambda:worker._request({}),worker.start,worker.close,worker.health,worker.client,backend.close,lambda:daemon.dispatch({}),lambda:daemon._dispatch_session_control('x',{}, {})]:
            with pytest.raises(campaign.CandidateError,match='fenced'):effect()
        daemon.monitor.join(2);assert not daemon.monitor.is_alive()
        for child in children:child.terminate();child.wait(5)
        thread.join(3);assert not thread.is_alive() and 'escaped' not in result
        value=result['value'];assert value['status']=='FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL'
        state=server._unknown_hold_state
        assert state['first_cause'] is cause and state['server_process'] is children[0]
        assert state['workers'][key][0] is worker and state['workers'][key][1] is children[1]
        assert any(x['phase']=='unknown_hold_receipt' for x in state['diagnostics'])
        assert collision.read_bytes()==b'original immutable collision\n'
        assert daemon.store.job(record['job_id'])==before
        assert value['signals_sent'] is False and value['new_sdk_calls'] is False
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(5)
        thread.join(3)


@pytest.mark.parametrize('fault',['sleep','poll','write','monitor_attribute','clock'])
def test_diagnostic_failures_do_not_replace_firstcause_or_exit_alive_owner(original_graph,monkeypatch,fault):
    server,daemon,worker,backend,key,children,evidence,cause,record,before=original_graph
    real_sleep=time.sleep;real_poll=children[1].poll;seen=[]
    if fault=='sleep':
        def sleep(delay):
            seen.append('sleep')
            if len(seen)==1:raise OSError('actual yield diagnostic failure')
            return real_sleep(delay)
        monkeypatch.setattr(campaign.time,'sleep',sleep)
    elif fault=='poll':
        def poll():
            seen.append('poll')
            if len(seen)==1:raise OSError('original Popen poll diagnostic failure')
            return real_poll()
        monkeypatch.setattr(children[1],'poll',poll)
    elif fault=='write':
        (evidence/'original_owner_observation.json').mkdir()
    elif fault=='clock':
        # Hold has no clock dependency: budget expiry never licenses owner disposal.
        monkeypatch.setattr(campaign.time,'time',lambda:(_ for _ in ()).throw(OSError('wall clock unavailable')))
    else:
        class BrokenMonitor:
            @property
            def _stop(self):raise OSError('monitor attribute unavailable')
        monitor=BrokenMonitor()
    result={};stop=threading.Event()
    def run():result['value']=campaign._hold_forever_with_handles(monitor if fault=='monitor_attribute' else None,server=server,daemon=daemon,first_cause=cause,run_dir=evidence,stop_event=stop)
    thread=threading.Thread(target=run);thread.start();assert daemon.closed.wait(3);real_sleep(.1)
    try:
        assert thread.is_alive() and server._unknown_hold_state['first_cause'] is cause
        for child in children:child.terminate();child.wait(5)
        if fault=='monitor_attribute':
            real_sleep(.3);assert thread.is_alive();stop.set()
        thread.join(3);assert not thread.is_alive()
        if fault!='monitor_attribute':assert result['value']['status']=='FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL'
        assert daemon.store.job(record['job_id'])==before
    finally:stop.set();thread.join(3)


@pytest.mark.parametrize('fault',['worker','server_process','worker_process','collection','backend','missing_child_proof','partial_connect','pid_rewrite','default_backend','service'])
def test_original_graph_replacement_or_partialbirth_never_false_terminal(original_graph,fault):
    server,daemon,worker,backend,key,children,evidence,cause,record,before=original_graph
    stop=threading.Event()
    if fault=='worker':daemon._session_worker_handles[key]=object()
    elif fault=='server_process':server.proc=None
    elif fault=='worker_process':worker._process=None
    elif fault=='collection':daemon._session_worker_handles=dict(daemon._session_worker_handles)
    elif fault=='backend':daemon._session_backends[key]=object()
    elif fault=='missing_child_proof':server._unknown_hold_state['workers'][key]=(worker,children[1],None,children[1].pid)
    elif fault=='partial_connect':daemon._worker_admission_inflight=1
    elif fault=='pid_rewrite':server._original_server_pid=-1
    elif fault=='default_backend':daemon._default_backend=object()
    elif fault=='service':backend.service=object()
    thread,result=start_hold(original_graph,stop=stop)
    try:
        for child in children:child.terminate();child.wait(5)
        time.sleep(.3);assert thread.is_alive() and 'value' not in result
        assert server._unknown_hold_state['first_cause'] is cause
        assert daemon.store.job(record['job_id'])==before
    finally:
        daemon._worker_admission_inflight=0;stop.set();thread.join(3)


def test_same_primitive_reachable_from_both_actual_execute_failure_branches():
    import ast,inspect
    tree=ast.parse(inspect.getsource(campaign.execute_candidate))
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_unknown_hold']
    assert len(calls)==2
    assert all(any(k.arg=='first_cause' for k in n.keywords) for n in calls)
    for call in calls:
        assert any(isinstance(n,ast.Try) and call in list(ast.walk(n)) and any(isinstance(h.type,ast.Name) and h.type.id=='BaseException' for h in n.handlers) for n in ast.walk(tree))


def test_actual_never_born_graph_can_return_failure_without_false_partialbirth_claim(tmp_path):
    work=tmp_path/'work';evidence=tmp_path/'evidence';work.mkdir();evidence.mkdir();(work/'project').mkdir()
    server=campaign.W24StaticShapeSetupServer(work,evidence,event_log=evidence/'events.jsonl')
    daemon=ControlDaemon(tmp_path/'control',project_root=work/'project')
    try:
        result=campaign._hold_forever_with_handles(None,server=server,daemon=daemon,run_dir=evidence,first_cause=RuntimeError('verified no birth'))
        assert result['status']=='FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL'
        assert result['children']==[] and result['original_proof_complete'] is True
    finally:ControlDaemon.close(daemon)


def test_partial_server_birth_without_original_popen_cannot_become_no_children(tmp_path):
    work=tmp_path/'work';evidence=tmp_path/'evidence';work.mkdir();evidence.mkdir();(work/'project').mkdir()
    server=campaign.W24StaticShapeSetupServer(work,evidence,event_log=evidence/'events.jsonl');server._server_birth_attempted=True
    daemon=ControlDaemon(tmp_path/'control',project_root=work/'project');stop=threading.Event();result={}
    def run():result['value']=campaign._hold_forever_with_handles(None,server=server,daemon=daemon,run_dir=evidence,stop_event=stop)
    thread=threading.Thread(target=run);thread.start();assert daemon.closed.wait(3)
    try:time.sleep(.3);assert thread.is_alive() and not server._unknown_hold_state['complete']
    finally:stop.set();thread.join(3);ControlDaemon.close(daemon)


def test_actual_natural_python_child_exit_needs_no_hold_signals(tmp_path):
    work=tmp_path/'work';evidence=tmp_path/'evidence';work.mkdir();evidence.mkdir();(work/'project').mkdir()
    server=campaign.W24StaticShapeSetupServer(work,evidence,event_log=evidence/'events.jsonl')
    daemon=ControlDaemon(tmp_path/'control',project_root=work/'project')
    process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.25)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    server.proc=server._original_server_popen=process;server._original_server_pid=process.pid;server._server_birth_attempted=True
    try:
        result=campaign._hold_forever_with_handles(None,server=server,daemon=daemon,run_dir=evidence)
        assert process.poll()==0 and result['signals_sent'] is False
        assert result['status']=='FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL'
    finally:
        if process.poll() is None:process.terminate()
        process.wait(5);ControlDaemon.close(daemon)


@pytest.mark.parametrize('unknown_branch',[False,True])
def test_actual_execute_tail_diagnostic_exception_routes_to_same_original_poll_loop(original_graph,unknown_branch):
    """Production AST branch, only a true FS diagnostic failure injected; no native route claim."""
    import ast,inspect
    server,daemon,worker,backend,key,children,evidence,cause,record,before=original_graph
    body=ast.parse(inspect.getsource(campaign.execute_candidate)).body[0].body
    branch=next(n for n in body if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='is_unknown')
    names=['is_unknown','daemon','server','monitor','worker_state','session','project_id','run_dir','work','reviewed_sha256','error','first_cause','status','cleanup']
    fn=ast.FunctionDef(name='actual_tail',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=x) for x in names],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[branch,ast.Return(value=ast.Name(id='status',ctx=ast.Load()))],decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]));environment=dict(campaign.__dict__)
    collision=evidence/'branch-diagnostic-collision';collision.write_bytes(b'original collision')
    def failed_diagnostic(**kwargs):
        with collision.open('xb') as stream:stream.write(b'never overwrite')
    environment['_unknown_hold']=failed_diagnostic
    exec(compile(module,inspect.getsourcefile(campaign.execute_candidate),'exec'),environment)
    result={}
    def run():
        result['status']=environment['actual_tail'](unknown_branch,daemon,server,None,'CONNECTED',{},key[0],evidence,server.work,'a'*64,{'message':str(cause)},cause,'SOFTWARE_ONLY',None)
    thread=threading.Thread(target=run);thread.start();assert daemon.closed.wait(3)
    try:
        assert thread.is_alive() and server._unknown_hold_state['first_cause'] is cause
        for child in children:child.terminate();child.wait(5)
        thread.join(3);assert not thread.is_alive()
        assert result['status'] in {'FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL','UNKNOWN_PRESERVED_CLEANUP_NOT_AUTHORIZED_OR_UNPROVEN'}
        assert collision.read_bytes()==b'original collision'
        assert daemon.store.job(record['job_id'])==before
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(5)
        thread.join(3)


@pytest.mark.parametrize('lock_name',['_session_connect_lock','_admission_lock'])
def test_actual_busy_owner_lock_does_not_delay_fence_or_discard_owner(original_graph,lock_name):
    server,daemon,worker,backend,key,children,evidence,cause,record,before=original_graph
    acquired=threading.Event();release=threading.Event()
    def own_lock():
        with getattr(daemon,lock_name):
            acquired.set();release.wait(5)
    lock_thread=threading.Thread(target=own_lock);lock_thread.start();assert acquired.wait(2)
    stop=threading.Event();started=time.monotonic()
    thread,result=start_hold(original_graph,stop=stop)
    try:
        assert time.monotonic()-started<1
        time.sleep(.1)
        state=server._unknown_hold_state
        assert thread.is_alive() and not state['complete'] and not state['graph_changed']
        assert state['workers'][key][1] is children[1] and state['server_process'] is children[0]
        assert daemon._closing and daemon.closed.is_set()
        for effect in [worker.health,worker.start,backend.close,lambda:daemon.dispatch({})]:
            with pytest.raises(campaign.CandidateError,match='fenced'):effect()
        receipt=json.loads((evidence/'original_owner_observation.json').read_text())
        assert not receipt['original_proof_complete'] and receipt['safe_retry'] is False
        for child in children:child.terminate();child.wait(5)
        time.sleep(.3);assert thread.is_alive()
        release.set();lock_thread.join(2);thread.join(3)
        assert not thread.is_alive() and 'escaped' not in result
        assert result['value']['status']=='FAIL_UNKNOWN_PRESERVED_ORIGINAL_CHILDREN_TERMINAL'
        assert state['first_cause'] is cause and daemon.store.job(record['job_id'])==before
    finally:
        release.set();stop.set();lock_thread.join(2);thread.join(3)


def test_exception_text_failure_retains_same_original_children(original_graph,monkeypatch):
    server,daemon,worker,backend,key,children,evidence,cause,record,before=original_graph
    class BrokenText(RuntimeError):
        def __str__(self):raise OSError('secondary text conversion failure')
    bad=BrokenText();server._unknown_hold_state['first_cause']=bad
    real_poll=children[1].poll;calls=[]
    def poll():
        calls.append(1)
        if len(calls)==1:raise bad
        return real_poll()
    monkeypatch.setattr(children[1],'poll',poll)
    thread,result=start_hold(original_graph)
    try:
        time.sleep(.1);assert thread.is_alive()
        state=server._unknown_hold_state
        assert state['first_cause'] is bad and state['workers'][key][1] is children[1]
        for child in children:child.terminate();child.wait(5)
        thread.join(3);assert not thread.is_alive() and 'escaped' not in result
        assert result['value']['first_cause']=='<BrokenText: text unavailable>'
        assert any(d['error']=='<BrokenText: text unavailable>' for d in state['diagnostics'])
        assert daemon.store.job(record['job_id'])==before
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(5)
        thread.join(3)
