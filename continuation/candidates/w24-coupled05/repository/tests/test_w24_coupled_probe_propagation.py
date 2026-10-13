"""Production caller->callee probe propagation; only native/OS/SDK boundaries fake."""
import json,hashlib,os,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import run_native_resume_smoke as smoke
from tools import w24_coupled_admission as admission
from tools import run_native_w24_cure_preflight as runner
from comsol_mcp import _g2_isolation as isolation
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from test_w24_coupled_lifecycle_residuals import owner4
from test_w24_coupled_admission import Process


def _setup_server(owner,tmp_path):
    server=smoke.NativeLoopbackServer(tmp_path/'new-work',tmp_path,event_log=tmp_path/'new-server-events.jsonl')
    server.work.mkdir();server.runtime.mkdir();(server.runtime/'server.port').write_text('56388')
    server.proc=owner.server.proc;server.port=56388;server.process_identity=None
    return server


def _sdk_rows(command):
    if command[0]=='/bin/ps':
        pid=command[2]
        return f'{pid} 1 Mon Oct 12 00:00:00 2026 /sdk/comsol mphserver\n'
    if '-a' in command:
        return 'COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\njava 101 fixture 5u IPv4 0 0t0 TCP 127.0.0.1:56388 (LISTEN)\n'
    return ('COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n'
            'java 101 fixture 5u IPv4 0 0t0 TCP 127.0.0.1:56388 (LISTEN)\n'
            'java 101 fixture 6u IPv4 0 0t0 TCP 127.0.0.1:56388->127.0.0.1:60000 (ESTABLISHED)\n'
            'java 102 fixture 7u IPv4 0 0t0 TCP 127.0.0.1:60000->127.0.0.1:56388 (ESTABLISHED)\n')


def _receipt(server):
    cmd='/sdk/comsol mphserver'
    server.receipt_path.parent.mkdir(parents=True,exist_ok=True)
    server.receipt_path.write_text(json.dumps({'schema_version':2,'status':'RUNNING','process':{'pid':101,'port':56388,'birth':'Mon Oct 12 00:00:00 2026','command':cmd,'command_sha256':hashlib.sha256(cmd.encode()).hexdigest()}}))


def test_actual_listener_identity_nested_PS_is_capped_and_late_return_mints_no_receipt(owner4,monkeypatch,tmp_path):
    unused,clock,ps=owner4;server=_setup_server(unused,tmp_path);server.proc=None;server.port=None
    owner=admission._new_coupled_admission(server,tmp_path/'listener');owner.evidence.mkdir();child=Process(101);calls=[];clock.mono=1295.
    # The new owner's pre-Popen deadline is fixed at constructor1295+1800;
    # set its already-fixed fixture clock to original reserve minus5 seconds.
    clock.mono=owner.deadline_monotonic-605.
    monkeypatch.setattr(smoke.subprocess,'Popen',lambda *a,**kw:child)
    def sdk(command,**kw):
        calls.append({'argv':command,'timeout':kw.get('timeout')});assert kw['timeout']==5.
        if command[0]=='/bin/ps':clock.mono=owner.deadline_monotonic-600.
        return SimpleNamespace(returncode=0,stderr='',stdout=_sdk_rows(command))
    monkeypatch.setattr(isolation.subprocess,'run',sdk)
    try:
        with pytest.raises(TimeoutError):server.start_and_verify_listener(admission_owner=owner)
        assert len(calls)==2 and owner.handles['server'] is child
        assert not server.receipt_path.exists() and owner.stop_reason=='G2_native_probe_failed_or_late'
        with pytest.raises(RuntimeError):owner.before_effect('Worker-start-after-late-identity',20)
        (tmp_path/'actual_listener_nested_ps_control.json').write_text(json.dumps(calls,indent=2)+'\n')
    finally:
        owner.monitor.stop();server._server_log_handle.close()


@pytest.mark.parametrize('late', [False,True])
def test_actual_start_worker_nested_G2_PS_and_socket_probes_same_owner(owner4,monkeypatch,tmp_path,late):
    owner,clock,ps=owner4;server=_setup_server(owner,tmp_path);owner.server=server;owner.handles['server']=server.proc
    _receipt(server);ps.extra=f'102 1 102 1 {server.work}/PersistentComsolWorker\n';calls=[];clock.mono=1295.
    class SDKWorker:
        def __init__(self,*a,**kw):self._process=Process(102)
        def start(self,**kw):self._coupled_admission_owner=kw['admission_owner'];owner.bind_child(self._process,'worker');return {'ready':True}
        def client(self):return self
        def connect(self,*a,**kw):return {'connected':True}
        def getComsolVersion(self,*a,**kw):return 'COMSOL Multiphysics 6.4.0.293'
        def runtime_metadata(self):return {'pid':102,'port':57000}
    server.worker_type=SDKWorker;server.paths_type=lambda *a,**kw:object()
    def sdk(command,**kw):
        calls.append({'argv':command,'timeout':kw.get('timeout')});assert kw['timeout']==(5. if command[0]=='/bin/ps' else 3.)
        if command[0]=='/bin/ps':clock.mono=1300. if late else 1297.
        return SimpleNamespace(returncode=0,stderr='',stdout=_sdk_rows(command))
    monkeypatch.setattr(isolation.subprocess,'run',sdk)
    if late:
        with pytest.raises(TimeoutError):server.start_worker(admission_owner=owner)
        assert len(calls)==1 and not (server.evidence/'worker_connection.json').exists()
    else:
        response=server.start_worker(admission_owner=owner)
        assert response['isolation_proof']['verified'] is True and len(calls)==2
        assert server.worker._coupled_admission_owner is owner
    assert owner.handles['worker'] is server.worker._process


@pytest.mark.parametrize('drift',[None,'foreign_worker','bad_owner_json','late_ps','late_socket'])
def test_actual_ManagedBackend_G2_verifier_propagates_original_owner_and_refuses_receiver_changes(owner4,monkeypatch,tmp_path,drift):
    owner,clock,ps=owner4;server=_setup_server(owner,tmp_path);owner.server=server;owner.handles['server']=server.proc;_receipt(server)
    worker=SimpleNamespace(_process=Process(102),_coupled_admission_owner=owner,runtime_metadata=lambda:{'pid':102})
    server.worker=worker;ps.extra=f'102 1 102 1 {server.work}/PersistentComsolWorker\n';owner.bind_child(worker._process,'worker');clock.mono=1295.
    store=OperationStore(tmp_path/'real-store.sqlite3');backend=ManagedBackend(tmp_path/'actual-backend',store,worker=worker,project_root=tmp_path);backend.endpoint_key='127.0.0.1:56388';monkeypatch.setenv('COMSOL_MCP_ISOLATION_RECEIPT',str(server.receipt_path));calls=[]
    def sdk(command,**kw):
        calls.append(kw.get('timeout'))
        if command[0]=='/bin/ps':clock.mono=1300. if drift=='late_ps' else 1297.
        elif drift=='late_socket':clock.mono=1300.
        return SimpleNamespace(returncode=0,stderr='',stdout=_sdk_rows(command))
    monkeypatch.setattr(isolation.subprocess,'run',sdk)
    if drift=='foreign_worker':server.worker=SimpleNamespace(_process=Process(102))
    elif drift=='bad_owner_json':worker._coupled_admission_owner={'deadline_monotonic':99999999}
    try:
        if drift in ['late_ps','late_socket']:
            with pytest.raises(TimeoutError):backend._require_g2_isolation()
            assert calls==([5.] if drift=='late_ps' else [5.,3.])
            assert owner.stop_reason=='G2_native_probe_failed_or_late'
            with pytest.raises(RuntimeError):owner.before_effect('next-managed-route',20)
        elif drift:
            with pytest.raises(Exception):backend._require_g2_isolation()
            assert calls==[]
        else:
            result=backend._require_g2_isolation();assert result['verified'] is True and calls==[5.,3.]
    finally:store.close()


def test_postcap_actual_inventory_uses_short_readonly_observation_without_work_budget(owner4,monkeypatch):
    owner,clock,ps=owner4;clock.mono=2000.;calls=[]
    owner.fail('original_budget_expired',TimeoutError('original deadline'))
    def sdk(command,**kw):
        calls.append(kw['timeout'])
        raw=f'{os.getpid()} 1 python observing-cli\n' if command[0]=='/bin/ps' else 'COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n'
        return SimpleNamespace(returncode=0,stderr='',stdout=raw)
    monkeypatch.setattr(runner.subprocess,'run',sdk)
    inventory=runner._process_inventory(admission_owner=owner,observation_only=True)
    assert calls==[5.,5.] and inventory['quiescent'] is True
    with pytest.raises(RuntimeError):owner.before_effect('model-create-after-observation',30)
    assert owner.deadline_monotonic==1900. and owner.stop_reason=='original_budget_expired'


def test_actual_main_worker_identity_snapshot_propagation_callsite_is_mandatory():
    import ast,inspect
    tree=ast.parse(inspect.getsource(runner.run));calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_process_snapshot']
    assert any(any(k.arg=='admission_owner' and isinstance(k.value,ast.Name) and k.value.id=='admission_owner' for k in n.keywords) for n in calls)
    inventories=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_process_inventory']
    assert any(any(k.arg=='observation_only' and isinstance(k.value,ast.Constant) and k.value.value is True for k in n.keywords) for n in inventories)


def test_actual_run_main_Worker_snapshot_reaches_bounded_callee_and_late_probe_retains_originals(owner4,monkeypatch,tmp_path):
    """Real run/Controller/project/Store after prerequisite fixtures; OS/native birth only synthetic."""
    from uuid import uuid4
    unused,clock,ps=owner4
    work=Path('/private/tmp')/('comsol-mcp-w24-cure-probe-software-'+str(uuid4()));evidence=tmp_path/'main-worker-capture';owners=[];children=[];calls=[]
    monkeypatch.setenv(runner.TRUSTED_CODE_STARTUP_ENV,'1')
    monkeypatch.setattr(runner,'time',admission.time)
    monkeypatch.setattr(runner,'_verify_offline_candidate_freeze',lambda *a:{'setup_contract':{'scope':'PREREADINESS_SOFTWARE_FIXTURE_NO_ADMISSION'},'birth_budget_s':1800.,'cleanup_reserve_s':600.,'candidate_id':'W24-COUPLED-SETUP-05','runtime_environment':{},'sha256':'PREREADINESS_FIXTURE_SHA_ONLY'})
    monkeypatch.setattr(runner,'_runtime_environment_preflight',lambda *a:{'status':'PREREADINESS_FIXTURE_ONLY','python_executable':'fixture','python_version':'fixture','installed_distributions_sha256':'fixture','production_registry':{'entry_count':0}})
    monkeypatch.setattr(runner,'_process_inventory',lambda **kw:{'quiescent':True})
    monkeypatch.setattr(runner,'_compile_offline',lambda *a,**kw:{'exit_code':0,'output_classes':['INHERITED_UNCHANGED_CLASS_FIXTURE'],'javac_version':'fixture','fixture_sha256':runner.sha256(runner.FIXTURE)})
    def shadow(server):server.project.mkdir();return {'scope':'native filesystem SDK boundary'}
    monkeypatch.setattr(smoke.NativeLoopbackServer,'prepare_shadow',shadow)
    real_new=admission._new_coupled_admission
    def capture(server,evidence):
        owner=real_new(server,evidence);owners.append(owner);return owner
    monkeypatch.setattr(admission,'_new_coupled_admission',capture)
    def listener(server,*,admission_owner):
        owner=admission_owner;owner.prepare_server_birth();server.proc=Process(101);server.port=56388;children.append(server.proc)
        owner.on_server_birth()
        from datetime import datetime
        server.process_identity={'pid':101,'birth':datetime.fromtimestamp(owner.birth_epoch).astimezone().strftime('%a %b %d %H:%M:%S %Y'),'command':'/sdk/comsol mphserver'}
        return {'status':'SOFTWARE_NATIVE_BOUNDARY_ONLY'}
    monkeypatch.setattr(smoke.NativeLoopbackServer,'start_and_verify_listener',listener)
    def worker_start(server,*,admission_owner):
        server.worker=SimpleNamespace(_process=Process(102),_coupled_admission_owner=admission_owner)
        children.append(server.worker._process);ps.extra=f'102 1 102 1 {work}/PersistentComsolWorker\n';admission_owner.bind_child(server.worker._process,'worker')
        clock.mono=1295.
        return {'worker_runtime':{'pid':102,'port':57000},'engine_version':'COMSOL Multiphysics 6.4.0.293'}
    monkeypatch.setattr(smoke.NativeLoopbackServer,'start_worker',worker_start)
    def sdk(command,**kw):
        calls.append({'argv':command,'timeout':kw.get('timeout')})
        if command[:3]==['/bin/ps','-p','102']:
            assert kw['timeout']==5.;clock.mono=2000.
            return SimpleNamespace(returncode=0,stderr='',stdout=_sdk_rows(command))
        # After expiry this is pure original owner PS observation, <=1s.
        assert command==['/bin/ps','-axo','pid=,ppid=,pgid=,command='] and kw['timeout']==1.
        raw=f'{os.getpid()} 1 {os.getpid()} observing-cli\n'
        raw+=''.join(f'{p.pid} 1 {p.pid} original-owned\n' for p in children if p.poll() is None)
        return SimpleNamespace(returncode=0,stderr='',stdout=raw)
    monkeypatch.setattr(isolation.subprocess,'run',sdk)
    yields=[]
    def observe(delay):
        owner=owners[0]
        # Close fixture children before later assertions, even if a prereadiness guard rejected early.
        yields.append({'delay':delay,'server_held':owner.handles.get('server') is children[0],
                       'worker_held':len(children)==2 and owner.handles.get('worker') is children[1],
                       'retained':admission._RETAINED_OWNERS.get(id(owner)) is owner})
        for p in children:p.code=0
    monkeypatch.setattr(admission.time,'sleep',observe)
    result=runner.run(SimpleNamespace(work=str(work),evidence=str(evidence),candidate_freeze='PREREADINESS_FIXTURE_ONLY',expected_candidate_sha256='NO_NATIVE_ADMISSION'))
    assert calls[0]['argv'][2]=='102' and calls[0]['timeout']==5.
    owners[0].monitor.stop()
    assert len(yields)==1 and all(yields[0][k] for k in ['server_held','worker_held','retained']) and result['status']=='FAIL_OR_INCOMPLETE'
    assert result['coupled_unit_budget_exceeded'] is True
    assert result['coupled_owner_final_observation']['status']=='TERMINAL_REAPED_AND_ORIGINAL_GROUPS_ABSENT'
    assert isinstance(owners[0].first_cause,TimeoutError)
    assert not (evidence/'worker_connection.json').exists()
    (tmp_path/'actual_main_worker_bounded_probe_control.json').write_text(json.dumps({'calls':calls,'result':result},indent=2)+'\n')
