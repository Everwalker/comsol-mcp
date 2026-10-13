"""Actual owner/Worker/monitor/finalizer/run controls; native/OS boundaries fake."""
import os,json,subprocess,copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import w24_coupled_admission as admission
from tools import run_native_w24_static_shape_setup_campaign as campaign
from tools import run_native_w24_cure_preflight as runner
from comsol_mcp import _java_worker as java
from comsol_mcp import _platform_process as platform
from test_w24_coupled_admission import Process

@pytest.fixture
def owner4(monkeypatch,tmp_path):
    clock=SimpleNamespace(wall=100.,mono=100.)
    monkeypatch.setattr(admission,'time',SimpleNamespace(time=lambda:clock.wall,monotonic=lambda:clock.mono,sleep=lambda _:None))
    monkeypatch.setattr(platform,'process_identity',lambda pid:{'alive':True,'start_epoch_ms':100000})
    server=SimpleNamespace(proc=Process(101),work=tmp_path)
    ps=SimpleNamespace(extra='',group='',identity_error=False)
    real=campaign.SampledRssMonitor
    def ident(pid):return {'alive':True,'start_epoch_ms':None if ps.identity_error else 100000}
    def rss(*a,**kw):return SimpleNamespace(returncode=0,stderr='',stdout=f'101 1 101 1 server\n{os.getpid()} 1 99999 1 observer\n'+ps.extra)
    monkeypatch.setattr(campaign,'SampledRssMonitor',lambda **kw:real(**kw,ps_runner=rss,identity_reader=ident,session_reader=lambda pid:{202:201,203:201}.get(pid,pid)))
    monkeypatch.setattr(admission.os,'getsid',lambda pid:{202:201,203:201}.get(pid,pid))
    monkeypatch.setattr(admission.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stderr='',stdout=f'{os.getpid()} 1 99999 observer\n'+ps.group))
    owner=admission._new_coupled_admission(server,tmp_path);owner.on_server_birth()
    yield owner,clock,ps
    owner.monitor.stop();admission._RETAINED_OWNERS.pop(id(owner),None)


def test_original_worker_registered_before_log_close_throw(owner4,monkeypatch,tmp_path):
    owner,clock,ps=owner4;child=Process(102);ps.extra=f'102 1 102 1 {tmp_path}/PersistentComsolWorker\n';ps.group='102 1 102 live-original-Worker\n'
    paths=SimpleNamespace(validate=lambda:None,classpath=lambda:('sdk-classpath','h',1,'j'),compilation_cache_fingerprint=lambda *a:('private-cache',{}),executable=lambda name:Path('/sdk-boundary')/name,private_prefs=None,classpath_separator=':',resolved_global_lock_root=tmp_path/'locks')
    worker=java.PersistentJavaWorker(paths,state_dir=tmp_path/'worker');owner.server.worker=worker
    classes=worker.state_dir/'classes/private-cache';classes.mkdir(parents=True);(classes/'.compiled').write_text('cache');(classes/'cache_receipt.json').write_text('{}')
    original_open=Path.open
    class Log:
        def close(self):raise OSError('SDK_LOG_CLOSE_AFTER_POPEN')
    monkeypatch.setattr(Path,'open',lambda path,*a,**kw:Log() if path==worker.state_dir/'worker.stderr.log' else original_open(path,*a,**kw))
    births=[]
    monkeypatch.setattr(java.subprocess,'Popen',lambda command,**kw:births.append((command,kw)) or child)
    with pytest.raises(OSError,match='LOG_CLOSE_AFTER_POPEN'):worker.start(admission_owner=owner,startup_timeout_s=25)
    assert len(births)==1 and births[0][1]['start_new_session'] is True
    assert owner.handles['worker'] is worker._process is child and owner.groups['worker']==102
    owner.server.proc.code=0
    assert owner.observe_owned_terminal_once()['status']=='UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY'
    with pytest.raises(RuntimeError):owner.before_effect('connect-after-close-failure',30)


def test_binding_probe_failure_original_handle_survives_until_same_reap_and_group_absence(owner4,monkeypatch):
    owner,clock,ps=owner4;child=Process(201)
    monkeypatch.setattr(platform,'process_identity',lambda pid:{'alive':True,'start_epoch_ms':None})
    with pytest.raises(RuntimeError,match='birth'):owner.bind_child(child,'compiler')
    assert owner.handles['compiler'] is child and owner.groups['compiler']==201
    owner.server.proc.code=0;ps.group='202 1 201 original-group-descendant\n'
    assert owner.observe_owned_terminal_once()['status']=='UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY'
    child.code=0
    assert owner.observe_owned_terminal_once()['status']=='UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY'
    ps.group='';result=owner.observe_owned_terminal_once()
    assert result['status']=='TERMINAL_REAPED_AND_ORIGINAL_GROUPS_ABSENT' and owner.stop_reason=='child_birth_binding_failed'


def test_summary_and_observation_write_failures_cannot_escape_original_observe_loop(owner4,monkeypatch):
    owner,clock,ps=owner4;child=Process(201);owner.handles['compiler']=child;owner.groups['compiler']=201
    owner.server.proc.code=0;ps.group='202 1 201 original-group\n';first=RuntimeError('original wait UNKNOWN');owner.fail('original_wait_unknown',first)
    real_write=runner.write_json
    monkeypatch.setattr(runner,'write_json',lambda path,payload:(_ for _ in ()).throw(OSError('SUMMARY_IO_FAILED')) if Path(path)==owner.evidence/'summary.json' else real_write(path,payload))
    monkeypatch.setattr(runner,'_write_new_mesh_artifact',lambda *a,**kw:(_ for _ in ()).throw(OSError('OBSERVATION_IO_FAILED')))
    ticks=[]
    def yielded(delay):
        assert admission._RETAINED_OWNERS[id(owner)] is owner and owner.handles['compiler'] is child
        ticks.append(delay)
        if len(ticks)==1:assert child.poll() is None
        if len(ticks)==2:child.code=0;ps.group=''
    monkeypatch.setattr(admission.time,'sleep',yielded);clock.mono=2000.;clock.wall=1.
    summary={};runner._finalize_coupled_owned_lifecycle_before_cli_return(owner,summary,owner.evidence)
    assert len(ticks)==2 and summary['status']=='FAIL_OR_INCOMPLETE'
    assert summary['coupled_unit_budget_exceeded'] is True and owner.first_cause is first
    assert len(owner.diagnostic_errors)>=2 and id(owner) not in admission._RETAINED_OWNERS


@pytest.mark.parametrize('wall',[1.,100.,5000.])
def test_first_admission_reserve_rejects_by_original_monotonic_even_wall_rollback(owner4,wall):
    owner,clock,ps=owner4;original=owner.deadline_monotonic;clock.wall=wall;clock.mono=1300.
    with pytest.raises(TimeoutError):owner.before_effect('cure-next-model',600)
    assert owner.deadline_monotonic==original==1900. and owner.stop_reason


def test_retired_compiler_original_group_RSS_covers_reparented_descendants(owner4):
    owner,clock,ps=owner4;child=Process(201);ps.extra='201 1 201 1 compiler\n202 201 201 1 compiler-child\n';owner.bind_child(child,'compiler')
    assert 202 in {r['pid'] for r in owner.monitor.sample_once()['processes']}
    child.code=0;owner.monitor.retire_owned_child(child)
    assert owner.monitor.owned_children[201][0] is child and 201 in owner.monitor.owned_groups
    ps.extra=f'202 1 201 {7*1024*1024} reparented-compiler-child\n'
    with pytest.raises(RuntimeError,match='RSS'):owner.before_effect('cure-after-compiler',30)
    sample=owner.monitor.last_sample
    assert sample['combined_comsol_worker_rss_bytes']>=7*1024**3 and sample['status']=='RSS_THRESHOLD_REACHED'
    assert sample['processes'][-1]['original_owned_pgid']==201


def test_retired_compiler_low_RSS_group_remains_owned_until_fresh_complete_absence(owner4):
    owner,clock,ps=owner4;child=Process(201);ps.extra='201 1 201 1 compiler\n202 201 201 1 child\n';owner.bind_child(child,'compiler');child.code=0;owner.monitor.retire_owned_child(child)
    ps.extra='202 1 201 1 reparented-child\n';owner.before_effect('next-cure',30)
    assert 201 in owner.monitor.owned_groups and 202 in {r['pid'] for r in owner.monitor.last_sample['processes']}
    ps.extra='';owner.before_effect('after-group-absence',30)
    assert 201 not in owner.monitor.owned_groups and 201 not in owner.monitor.owned_children


def test_unknown_original_group_member_birth_stops_all_follow_on(owner4):
    owner,clock,ps=owner4;child=Process(201);ps.extra='201 1 201 1 compiler\n';owner.bind_child(child,'compiler');child.code=0;owner.monitor.retire_owned_child(child)
    ps.extra='202 1 201 1 reparented-child\n';ps.identity_error=True
    with pytest.raises(RuntimeError,match='RSS'):owner.before_effect('next-cure',30)
    assert owner.monitor.stop_reason=='RSS_MONITOR_FAILED'


def test_real_run_daemon_none_ledger_IO_cannot_bypass_outer_owner_finally(owner4,monkeypatch,tmp_path):
    """Real run and Controller/project/Store; prereadiness/compile and native OS boundaries synthetic.

    This specifically covers the post-birth daemon=None lifecycle, not freeze admission.
    It never executes COMSOL or reuses a future work path.
    """
    from uuid import uuid4
    from tools import run_native_resume_smoke as smoke
    owner_unused,clock,ps=owner4
    work=Path('/private/tmp')/('comsol-mcp-w24-cure-software-lifecycle-'+str(uuid4()))
    evidence=tmp_path/'real-run-artifacts';owners=[];births=[]
    monkeypatch.setenv(runner.TRUSTED_CODE_STARTUP_ENV,'1')
    monkeypatch.setattr(runner,'_verify_offline_candidate_freeze',lambda *a:{'setup_contract':{'typed':'SOFTWARE_LIFECYCLE_INPUT_ONLY'},'birth_budget_s':1800.,'cleanup_reserve_s':600.,'candidate_id':'W24-COUPLED-SETUP-04','runtime_environment':{}})
    monkeypatch.setattr(runner,'_runtime_environment_preflight',lambda *a:{'status':'SOFTWARE_PREREADINESS_FIXTURE_ONLY','python_executable':'fixture','python_version':'fixture','installed_distributions_sha256':'fixture','production_registry':{'entry_count':0}})
    monkeypatch.setattr(runner,'_process_inventory',lambda:{'quiescent':True})
    # Immutable Java classes are not recompiled by this lifecycle fault control.
    monkeypatch.setattr(runner,'_compile_offline',lambda *a,**kw:{'exit_code':0,'output_classes':['UNCHANGED_INHERITED_CLASS_FIXTURE'],'javac_version':'fixture','fixture_sha256':runner.sha256(runner.FIXTURE)})
    def shadow(server):server.project.mkdir();return {'scope':'synthetic Native filesystem boundary'}
    monkeypatch.setattr(smoke.NativeLoopbackServer,'prepare_shadow',shadow)
    new_owner=admission._new_coupled_admission
    def capture_owner(server,evidence):
        owner=new_owner(server,evidence);owners.append(owner);return owner
    monkeypatch.setattr(admission,'_new_coupled_admission',capture_owner)
    def birth_boundary(server,*,admission_owner):
        admission_owner.prepare_server_birth();server.proc=Process(101);births.append(server.proc)
        admission_owner.on_server_birth()
        raise RuntimeError('ORIGINAL_SDK_SERVER_STARTUP_FAILURE_BEFORE_DAEMON')
    monkeypatch.setattr(smoke.NativeLoopbackServer,'start_and_verify_listener',birth_boundary)
    original_write=runner.write_json
    def failed_ledger(path,data):
        if Path(path)==evidence/'worker_ledger_reconciliation.json':raise OSError('DAEMON_NONE_LEDGER_IO_FAILED')
        return original_write(path,data)
    monkeypatch.setattr(runner,'write_json',failed_ledger)
    ticks=[]
    def observed_only(delay):
        owner=owners[0];assert owner.handles['server'] is births[0] and births[0].poll() is None
        assert admission._RETAINED_OWNERS[id(owner)] is owner
        ticks.append(delay);births[0].code=0
    monkeypatch.setattr(admission.time,'sleep',observed_only)
    args=SimpleNamespace(work=str(work),evidence=str(evidence),candidate_freeze='software-prereadiness-fixture',expected_candidate_sha256='not-a-native-approval')
    result=runner.run(args)
    assert len(births)==len(owners)==len(ticks)==1
    owner=owners[0]
    assert result['status']=='FAIL_OR_INCOMPLETE' and result['engine_births']==1
    assert 'ORIGINAL_SDK_SERVER_STARTUP_FAILURE' in result['error']
    assert str(owner.first_cause)=='ORIGINAL_SDK_SERVER_STARTUP_FAILURE_BEFORE_DAEMON'
    assert any('DAEMON_NONE_LEDGER_IO_FAILED' in e['error'] for e in owner.diagnostic_errors)
    assert result['coupled_owner_final_observation']['status']=='TERMINAL_REAPED_AND_ORIGINAL_GROUPS_ABSENT'
    assert id(owner) not in admission._RETAINED_OWNERS
    assert (evidence/'summary.json').is_file()


def test_actual_cleanup_late_probe_stops_signals_but_postcap_same_owner_observation_can_close(owner4,monkeypatch):
    from tools import run_native_w23_te_managed_preflight as cleanup
    owner,clock,ps=owner4;clock.mono=1895.;clock.wall=1.;calls=[];signals=[]
    owner.server.port=56388;owner.server.worker=None
    owner.server.proc.terminate=lambda:signals.append('TERM')
    def late_probe(command,**kw):
        calls.append((command,kw['timeout']))
        assert kw['timeout']==5.0
        clock.mono=1901.
        return SimpleNamespace(returncode=0,stderr='',stdout='101 1 java /owned/mphserver\n')
    monkeypatch.setattr(cleanup.subprocess,'run',late_probe)
    reconciliation={'safe_for_owned_cleanup':True,'checks':{name:True for name in ['all_submissions_observed_once','all_observed_workers_terminal','no_orphan_observations','no_ambiguous_worker_ids','no_queued_or_running_jobs']}}
    with pytest.raises(TimeoutError):cleanup._exact_owned_cleanup(owner.server,server_identity={'pid':101,'birth':'original','command':'owned'},worker_identity=None,worker_port=None,process_snapshot=lambda *a,**kw:pytest.fail('late inventory must stop identity dispatch'),reconciliation=reconciliation,evidence=owner.evidence,admission_owner=owner)
    assert len(calls)==1 and signals==[] and owner.stop_reason=='exact_cleanup_original_deadline_failed'
    cause=owner.first_cause
    # Pure ownership reconciliation is a separate <=1s observation, never a
    # refreshed model/native/cleanup work budget or signal permission.
    monkeypatch.setattr(admission.subprocess,'run',lambda command,**kw:SimpleNamespace(returncode=0,stderr='',stdout=f'{os.getpid()} 1 99999 observer\n'))
    ticks=[]
    def observe_only(delay):
        assert owner.handles['server'] is owner.server.proc and owner.server.proc.poll() is None
        assert admission._RETAINED_OWNERS[id(owner)] is owner
        ticks.append(delay);owner.server.proc.code=0
    monkeypatch.setattr(admission.time,'sleep',observe_only)
    summary={'status':'NATIVE_SETUP_PREFLIGHT_PASS_NOT_SOLVED'}
    runner._finalize_coupled_owned_lifecycle_before_cli_return(owner,summary,owner.evidence)
    assert len(ticks)==1 and signals==[] and owner.first_cause is cause
    assert summary['status']=='FAIL_OR_INCOMPLETE' and summary['coupled_unit_budget_exceeded'] is True
    assert summary['coupled_owner_final_observation']['status']=='TERMINAL_REAPED_AND_ORIGINAL_GROUPS_ABSENT'
    with pytest.raises(RuntimeError):owner.before_effect('new-model-after-postcap-close',30)


def test_actual_native_identity_probe_receives_private_finite_cleanup_cap(owner4,monkeypatch):
    from comsol_mcp import _g2_isolation as isolation
    owner,clock,ps=owner4;clock.mono=1895.;seen=[]
    def sdk_os(command,**kw):
        seen.append(kw['timeout'])
        return SimpleNamespace(returncode=0,stderr='',stdout='101 1 Mon Oct 12 00:00:00 2026 /sdk/mphserver\n')
    monkeypatch.setattr(isolation.subprocess,'run',sdk_os)
    value=isolation._process_snapshot(101,timeout_s=owner.wait_cap(10,cleanup=True))
    assert seen==[5.] and value['pid']==101


def test_ordinary_identity_probe_default_keeps_existing_no_timeout_semantics(monkeypatch):
    from comsol_mcp import _g2_isolation as isolation
    seen=[]
    def sdk_os(command,**kw):
        seen.append(kw)
        return SimpleNamespace(returncode=0,stderr='',stdout='101 1 Mon Oct 12 00:00:00 2026 /sdk/mphserver\n')
    monkeypatch.setattr(isolation.subprocess,'run',sdk_os)
    assert isolation._process_snapshot(101)['pid']==101
    assert len(seen)==1 and 'timeout' not in seen[0]


def test_original_compiler_session_descendant_new_pgid_stays_sampled_and_retained(owner4):
    owner,clock,ps=owner4;child=Process(201);ps.extra='201 1 201 1 compiler\n';owner.bind_child(child,'compiler');child.code=0;owner.monitor.retire_owned_child(child)
    ps.extra=f'202 1 202 {7*1024*1024} original-session-new-process-group\n'
    with pytest.raises(RuntimeError,match='RSS'):owner.before_effect('cure-after-reparented-new-group',30)
    assert owner.monitor.last_sample['status']=='RSS_THRESHOLD_REACHED'
    assert any(r['pid']==202 and r['sid']==201 and r['pgid']==202 for r in owner.monitor.last_sample['processes'])
    owner.server.proc.code=0;ps.group='202 1 202 original-session-new-group\n'
    observation=owner.observe_owned_terminal_once()
    assert observation['status']=='UNKNOWN_RETAINED_OWNER_OBSERVE_ONLY'
    assert observation['remaining_owned_group_processes'][0]['sid']==201
