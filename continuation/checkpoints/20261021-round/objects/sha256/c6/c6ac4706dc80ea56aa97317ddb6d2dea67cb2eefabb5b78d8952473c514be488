import argparse,copy,importlib,json,os,shutil,socket,sqlite3,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import w26_runtime_actor as actor,w26_application_lifecycle as unit,w26_sealed_data as data,full_project_release as release,release_transition_lifecycle as lifecycle

@pytest.fixture
def actual_closure(tmp_path):
    supplied=os.environ.get('W26_LIFECYCLE_TEST_SEAL')
    if supplied:return data.verify_seal(supplied,os.environ['W26_LIFECYCLE_TEST_SEAL_SHA'])
    # Portable synthetic software fixture; never a native R6 acceptance claim.
    path=tmp_path/'portable-fixture.sqlite3';db=sqlite3.connect(path)
    tables=['artifacts','checkpoints','host_control_admissions','job_events','jobs','operations','projects','resume_claims','revisions','runtimes','schema_meta','sessions','stage_attempts']
    for table in tables:
        if table=='job_events':db.execute('CREATE TABLE job_events(id INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT)');db.execute("INSERT INTO job_events(event) VALUES('synthetic')")
        elif table=='jobs':db.execute('CREATE TABLE jobs(job_id TEXT PRIMARY KEY,status TEXT)');db.execute("INSERT INTO jobs VALUES('synthetic-job','SUCCEEDED')")
        else:db.execute('CREATE TABLE '+table+'(key TEXT PRIMARY KEY,value TEXT)')
    db.commit();db.close();observed=data.database_observation(path)
    files=[]
    for name,raw in [('parameters.json',b'{}'),('v3-native-smoke.mph',b'synthetic opaque software fixture; not an MPH model')]:
        p=tmp_path/name;p.write_bytes(raw);files.append({'project_id':'synthetic-project','registered_blob':False,'relative_path':name,'copy':data.raw_file(p)[1]})
    return {'evidence_kind':'SYNTHETIC_PORTABLE_NOT_NATIVE_R6','whole_database':{'copy':data.raw_file(path)[1]},'schema_columns':observed['schema_columns'],'table_counts':observed['table_counts'],'table_row_digests':observed['table_row_digests'],'durable_history_sha256_same_algorithm_as_production':observed['durable_history_sha256'],'projects':[{'project_id':'synthetic-project'}],'project_files':files}

def test_lifecycle_closed_budget_and_old_profiles_unchanged():
    scope=actor.activity_scope({'activity_kind':actor.APP_LIFECYCLE})
    assert scope['budget_seconds']==3600 and scope['cleanup_reserve_seconds']==600 and scope['install_cap_seconds']==600 and scope['readback_cap_seconds']==180 and scope['restore_cap_seconds']==600
    assert actor.activity_scope({})['budget_seconds']==7200
    assert actor.activity_scope({'activity_kind':actor.APP_BUNDLE})['build_cap_seconds']==600
    assert actor.activity_scope({'activity_kind':actor.CURRENT43})['profile_cap_seconds']==180
    for key,value in [('budget_seconds',1800),('restore_cap_seconds',True),('cleanup_reserve_seconds',0)]:
        with pytest.raises(RuntimeError):actor.activity_scope({'activity_kind':actor.APP_LIFECYCLE,key:value})
    assert unit.environment(Path('/private/tmp/cvw26-fixture'))['PIP_NO_CACHE_DIR']=='1'

def current_loader_source_records():
    rows=actor.source_fingerprints(actor.APP_LIFECYCLE)
    for path in [Path(data.__file__).with_name('w26_target_inputs.py'),data.CURRENT_APPLICATION_LOCK,data.CURRENT_APPLICATION_R5_LOCK,data.CURRENT_APPLICATION_R6_LOCK]:
        record=actor._helpers().raw_file(path)[1];rows[record['path']]=record
    return rows

def test_current_application_isolated_loader_closure_matches_exact_lifecycle_sources():
    sources=current_loader_source_records();closure=data.current_application_loader_closure(sources)
    assert closure['schema']==data.CURRENT_APPLICATION_LOADER_SCHEMA
    assert set(closure['members'])==data.CURRENT_APPLICATION_LOADER_NAMES
    assert len(closure['members'])==24
    assert data.verify_current_application_loader_closure(closure)==data.CURRENT_APPLICATION_TOOLS_ROOT
    assert {str(Path(key)) for key in sources}=={str(data.CURRENT_APPLICATION_TOOLS_ROOT/name) for name in closure['members']}

@pytest.mark.parametrize('damage,match',[('root','source closure differs'),('member','member path differs'),('hash','first record differs'),('extra','source closure differs')])
def test_current_application_isolated_loader_rejects_path_or_closure_tamper_before_import(damage,match):
    import copy
    closure=data.current_application_loader_closure(current_loader_source_records())
    damaged=copy.deepcopy(closure)
    if damage=='root':damaged['root']='/private/tmp/untrusted-tools'
    elif damage=='member':damaged['members']['w26_runtime_actor.py']['path']='/private/tmp/w26_runtime_actor.py'
    elif damage=='hash':damaged['members']['w26_runtime_actor.py']['sha256']='0'*64
    else:damaged['members']['unlisted.py']={'path':'/private/tmp/unlisted.py'}
    with pytest.raises(RuntimeError,match=match):data.verify_current_application_loader_closure(damaged)

def test_current_application_isolated_loader_does_not_add_project_to_sys_path():
    before=list(sys.path);sources=current_loader_source_records();closure=data.current_application_loader_closure(sources)
    data.verify_current_application_loader_closure(closure)
    assert sys.path==before

def test_current_application_loader_rejects_foreign_cached_app_module_before_import(tmp_path,monkeypatch):
    import importlib.util,types
    root=data.CURRENT_APPLICATION_TOOLS_ROOT;closure=data.current_application_loader_closure(current_loader_source_records())
    package=types.ModuleType('tools');package.__path__=[str(root)];package.__spec__=importlib.machinery.ModuleSpec('tools',loader=None,is_package=True);package.__spec__.submodule_search_locations=[str(root)]
    foreign_path=tmp_path/'foreign_w26_application_bundle.py';foreign_path.write_text('')
    foreign_spec=importlib.util.spec_from_file_location('tools.w26_application_bundle',foreign_path)
    foreign=types.ModuleType('tools.w26_application_bundle');foreign.__file__=str(foreign_path);foreign.__spec__=foreign_spec
    monkeypatch.setitem(sys.modules,'tools',package);monkeypatch.setitem(sys.modules,'tools.w26_application_bundle',foreign)
    monkeypatch.setattr(data.importlib,'import_module',lambda name:pytest.fail('foreign cached module must be rejected before import_module'))
    with pytest.raises(RuntimeError,match='cached current application tools module provenance differs: tools.w26_application_bundle'):
        data._install_current_application_tools_namespace(root,closure)

def test_current_application_loader_rejects_unknown_tools_module_loaded_from_closure(tmp_path,monkeypatch):
    import types
    root=data.CURRENT_APPLICATION_TOOLS_ROOT;closure=data.current_application_loader_closure(current_loader_source_records())
    real_app=sys.modules['tools.w26_application_bundle'];real_actor=sys.modules['tools.w26_runtime_actor']
    package=types.ModuleType('tools');package.__path__=[str(root)];package.__spec__=importlib.machinery.ModuleSpec('tools',loader=None,is_package=True);package.__spec__.submodule_search_locations=[str(root)]
    monkeypatch.setitem(sys.modules,'tools',package);monkeypatch.delitem(sys.modules,'tools.w26_application_bundle',raising=False);monkeypatch.delitem(sys.modules,'tools.w26_runtime_actor',raising=False)
    def unexpected(name):
        module=types.ModuleType('tools.unexpected');module.__file__=str(tmp_path/'unexpected.py');sys.modules['tools.unexpected']=module
        return real_app if name.endswith('w26_application_bundle') else real_actor
    monkeypatch.setattr(data.importlib,'import_module',unexpected)
    with pytest.raises(RuntimeError,match='imported an unknown tools module'):
        data._install_current_application_tools_namespace(root,closure)

@pytest.mark.skipif(not os.environ.get('W26_APP_PROFILE_PRODUCER_ROOT'),reason='actual App6D producer root not supplied')
def test_current_application_profile_real_producer_under_isolated_python(tmp_path):
    root=Path(os.environ['W26_APP_PROFILE_PRODUCER_ROOT'])
    records={key:actor._helpers().raw_file(root/name)[1] for key,name in [('result','APPLICATION_BUNDLE_RESULT.json'),('candidate','ACTOR_CANDIDATE_app6c.json'),('once','UNIT_ONCE_GUARD.json')]}
    profile={'schema':'w26-current-installed-application/1','source_profile':'current175-r4','root':str(root),**records,'loader_source_closure':data.current_application_loader_closure(current_loader_source_records())}
    helper=str(Path(data.__file__).resolve())
    code="import json,runpy,sys; before=list(sys.path); n=runpy.run_path(sys.argv[2]); result=n['current_application_profile'](json.loads(sys.argv[1])); assert sys.path==before; print(json.dumps(result,sort_keys=True))"
    def run(value):
        return subprocess.run([sys.executable,'-I','-B','-c',code,json.dumps(value),helper],cwd=tmp_path,capture_output=True,text=True,timeout=60)
    positive=run(profile)
    assert positive.returncode==0,positive.stderr
    observed=json.loads(positive.stdout)
    expected_wheel=actor._helpers().raw_file(root/'appwheel/comsol_mcp-0.1.10-py3-none-any.whl')[1]
    assert observed['wheel']['sha256']==expected_wheel['sha256'] and observed['wheel']['bytes']==expected_wheel['bytes']
    assert observed['result']['sha256']==records['result']['sha256'] and len(observed['backup_source_sha256'])==64
    wrong_path=copy.deepcopy(profile);wrong_path['loader_source_closure']['members']['w26_runtime_actor.py']['path']=str(tmp_path/'w26_runtime_actor.py')
    negative_path=run(wrong_path)
    assert negative_path.returncode!=0 and 'member path differs' in negative_path.stderr
    wrong_closure=copy.deepcopy(profile);del wrong_closure['loader_source_closure']['members']['_w26_original_process.py']
    negative_closure=run(wrong_closure)
    assert negative_closure.returncode!=0 and 'source closure differs' in negative_closure.stderr
    cached_code="from pathlib import Path; import importlib.machinery,importlib.util,json,runpy,sys,types; p=json.loads(sys.argv[1]); h=sys.argv[2]; root=str(Path(h).resolve().parent); pkg=types.ModuleType('tools'); pkg.__path__=[root]; pkg.__spec__=importlib.machinery.ModuleSpec('tools',loader=None,is_package=True); pkg.__spec__.submodule_search_locations=[root]; sys.modules['tools']=pkg; name='tools.w26_application_bundle'; foreign=str(Path(h).resolve()); spec=importlib.machinery.ModuleSpec(name,importlib.machinery.SourceFileLoader(name,foreign)); spec.origin=foreign; cached=types.ModuleType(name); cached.__file__=foreign; cached.__spec__=spec; sys.modules[name]=cached; n=runpy.run_path(h); n['current_application_profile'](p)"
    foreign_cached=subprocess.run([sys.executable,'-I','-B','-c',cached_code,json.dumps(profile),helper],cwd=tmp_path,capture_output=True,text=True,timeout=60)
    assert foreign_cached.returncode!=0 and 'cached current application tools module provenance differs: tools.w26_application_bundle' in foreign_cached.stderr

def test_desktop_status_exact_current_caller_script_has_no_ui_or_prompt_calls(tmp_path):
    rows=unit.inventory(tmp_path,'/product/runtime',{'path':'/descriptor','sha256':'a'*64},{'members':[]},{'members':[]})
    row=next(r for r in rows if r['label']=='current-caller-desktop-metadata-status')
    assert row['max_calls']==1 and row['cap']==180 and row['request_argv'][-1]==unit.DESKTOP_STATUS_CODE
    assert 'create_native_metadata_adapter().status()' in unit.DESKTOP_STATUS_CODE
    assert 'AXIsProcessTrustedWithOptions' not in unit.DESKTOP_STATUS_CODE and 'CGRequestScreenCaptureAccess' not in unit.DESKTOP_STATUS_CODE
    assert 'enumerate_windows(' not in unit.DESKTOP_STATUS_CODE and 'capture(' not in unit.DESKTOP_STATUS_CODE

def test_bad_seal_no_popen(tmp_path,monkeypatch):
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('bad seal must not start process'))
    path=tmp_path/'SEAL.json';path.write_text('{}')
    with pytest.raises(RuntimeError,match='seal SHA'):data.verify_seal(path,'0'*64)

@pytest.mark.parametrize('schema,synthetic',[('wrong',False),('w26-sealed-r6-lifecycle-descriptor/1',True)])
def test_external_descriptor_mode_rejects_before_seal_or_child(tmp_path,monkeypatch,schema,synthetic):
    value={'schema':schema,'synthetic_data':synthetic,'old_historical_dependencies':'UNVERIFIED_CURRENT43_NEW_TEST_BASELINE','unit_root':str(tmp_path)}
    path=tmp_path/'descriptor.json';path.write_text(json.dumps(value));raw,record=data.raw_file(path)
    monkeypatch.setattr(data,'verify_seal',lambda *a:pytest.fail('wrong mode must not reach external input'))
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('wrong mode must not start child'))
    with pytest.raises(RuntimeError,match='descriptor mode'):data.descriptor(path,record['sha256'])

def test_lifecycle_json_cannot_mint_owner_no_effect(tmp_path,monkeypatch):
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('JSON cannot create original owner'))
    with pytest.raises(RuntimeError,match='cannot mint'):unit.execute_lifecycle(tmp_path,{}, {},{},began=0,admission_token={})
    assert list(tmp_path.iterdir())==[]

def test_actual_complete14_rows_and_data_dependent_tamper(tmp_path,actual_closure):
    path=tmp_path/'database.sqlite3';shutil.copyfile(actual_closure['whole_database']['copy']['path'],path)
    observed=data.verify_database(path,actual_closure,restored=False)
    assert len(observed['table_counts'])==14 and observed['table_counts']==actual_closure['table_counts']
    db=sqlite3.connect(path);db.execute("UPDATE jobs SET status='FAILED' WHERE job_id=(SELECT job_id FROM jobs LIMIT 1)");db.commit();db.close()
    with pytest.raises(RuntimeError,match='invariant|rows'):data.verify_database(path,actual_closure,restored=False)

def test_actual_supplemental_no_clobber_and_hash_negative(tmp_path,actual_closure):
    workspace=tmp_path/'workspace';workspace.mkdir(mode=0o700);project=workspace/'project';project.mkdir(mode=0o700);mapping={actual_closure['projects'][0]['project_id']:str(project)}
    rows=data.supplemental_copy(actual_closure,mapping,workspace)
    assert len(rows)==2 and {p.name for p in project.iterdir()}=={'parameters.json','v3-native-smoke.mph'}
    before={p.name:p.read_bytes() for p in project.iterdir()}
    with pytest.raises(FileExistsError):data.supplemental_copy(actual_closure,mapping,workspace)
    assert before=={p.name:p.read_bytes() for p in project.iterdir()}
    wrong=copy.deepcopy(actual_closure);next(r for r in wrong['project_files'] if not r['registered_blob'])['copy']['sha256']='0'*64
    another=workspace/'other';another.mkdir(mode=0o700)
    with pytest.raises(RuntimeError,match='first record'):data.supplemental_copy(wrong,{actual_closure['projects'][0]['project_id']:str(another)},workspace)
    assert list(another.iterdir())==[]

def test_external_mode_refuses_synthetic_before_any_child(tmp_path,monkeypatch):
    args=SimpleNamespace(external_sealed_descriptor='/anything',external_sealed_descriptor_sha='0'*64,synthetic_data=True)
    monkeypatch.setattr(release,'run_record',lambda *a,**kw:pytest.fail('mutually exclusive modes must not run child'))
    with pytest.raises(release.ReleaseError,match='mutually exclusive'):lifecycle.Lifecycle(args,release,tmp_path,None,None,'/usr/bin/sandbox-exec')

def test_wrong_lifecycle_command_cannot_spawn(tmp_path,monkeypatch):
    launch=unit.LifecycleLaunch(tmp_path,None,{}, {},{'inventory':[]},{},None,1)
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('wrong command cannot spawn'))
    with pytest.raises(RuntimeError,match='closed inventory'):launch.capture(['/bin/sh','-c','unsafe'],capture_output=True,text=True)

def test_original_listener_object_only_dynamic_port(tmp_path):
    descriptor={'path':'/descriptor','sha256':'a'*64};item={'members':[]};rows=unit.inventory(tmp_path,'/actual/python',descriptor,item,item)
    launch=unit.LifecycleLaunch(tmp_path,None,{}, {},{'inventory':rows},{},None,1)
    args=argparse.Namespace(python=Path('/actual/python'),requirements=tmp_path/'requirements-new.lock',wheelhouse=tmp_path/'new-wheelhouse',venv=tmp_path/'current-venv')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(1)
        launch.before_offline_install(args,Path(release.__file__),listener,tmp_path/'current-install-evidence/network-deny.sb')
        row=launch.offline[1];request=list(row['request_argv']);request[-1]=str(listener.getsockname()[1])
        assert launch.admit(request,{'capture_output':True,'text':True}) is row
        request[-1]=str(int(request[-1])+1)
        with pytest.raises(RuntimeError,match='closed inventory'):launch.admit(request,{'capture_output':True,'text':True})
    with pytest.raises(RuntimeError,match='lost'):launch.admit(list(row['request_argv'])[:-1]+[str(launch.offline[2])],{'capture_output':True,'text':True})

def test_unknown_fence_keeps_original_exception_and_no_rollback_spawn(tmp_path,monkeypatch):
    error=actor.original.StageUnknown('retained',process=object(),token='native')
    launch=unit.LifecycleLaunch(tmp_path,None,{}, {},{'inventory':[]},{},None,1);launch.pending_unknown=error
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('UNKNOWN cannot spawn rollback'))
    with pytest.raises(actor.original.StageUnknown) as caught:launch.capture(['/actual/rollback'],capture_output=True,text=True)
    assert caught.value is error and caught.value.process is error.process


def test_restore_producer_two_name_only_does_not_weaken_general_sealed_gate(tmp_path):
    home=tmp_path/'home';home.mkdir();recovery=home/'state-full-recovery-20261014T000000Z-0123456789';recovery.mkdir();live=home/'operations.sqlite3';c=sqlite3.connect(live);c.execute('create table x(a)');c.close();alias=recovery/'operations.sqlite3';alias.hardlink_to(live)
    import hashlib
    receipt={'success':True,'operation':'full_restore','recovery_directory':str(recovery),'database_sha256':hashlib.sha256(live.read_bytes()).hexdigest(),'worker_or_model_bindings_revived':False,'operation_replay_performed':False}
    with pytest.raises(RuntimeError,match='link'):data.raw_file(live)
    binding=data.restored_database_binding(receipt,home);assert data.database_observation(live,restore_binding=binding)['table_counts']=={'x':0}
    with pytest.raises(RuntimeError):data.restored_database_binding({**receipt,'database_sha256':'0'*64},home)
    third=tmp_path/'third-link';third.hardlink_to(live)
    with pytest.raises(RuntimeError,match='link'):data.database_observation(live,restore_binding=binding)


def test_quiescent_database_observation_refuses_nonempty_wal(tmp_path):
    p=tmp_path/'db';c=sqlite3.connect(p);c.execute('create table x(a)');c.close();Path(str(p)+'-wal').write_bytes(b'uncheckpointed frames')
    with pytest.raises(RuntimeError,match='uncheckpointed'):data.database_observation(p)


def test_json_cannot_mint_original_archive_observer_or_fd(tmp_path):
    with pytest.raises(RuntimeError,match='JSON'):data._actual_backup_archive_raw(tmp_path/'archive',{'schema':'w26-observed-original-fullbackup-archive/1'})
    with pytest.raises(RuntimeError,match='JSON'):data._ObservedBackupArchive(None,tmp_path,tmp_path/'archive',{})


def test_real_publisher_observer_keeps_original_failure_and_restores_function(tmp_path,monkeypatch):
    import types,hashlib
    source=tmp_path/'synthetic_producer.py';source.write_text('def original(stage,target):\n raise marker\ndef create(home,target):\n module._publish_archive_no_clobber(home,target)\n')
    marker=RuntimeError('original synthetic publisher first failure');module=types.ModuleType('synthetic_backup');module.__file__=str(source);module.__dict__.update(marker=marker,module=module);exec(compile(source.read_text(),str(source),'exec'),module.__dict__);module._publish_archive_no_clobber=module.original;module.create_full_backup=module.create
    monkeypatch.setattr(data,'BACKUP_PRODUCER_SHA',hashlib.sha256(source.read_bytes()).hexdigest())
    evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700);scratch=evidence/'.comsol-full-backup-12345678';scratch.mkdir(mode=0o700);(scratch/'operations.sqlite3').write_bytes(b'synthetic db');stage=scratch/'full-state.tar';stage.write_bytes(b'synthetic stage')
    # Preexisting scratch must not be mistaken for a new producer even same SHA.
    with pytest.raises(RuntimeError,match='source root'):data.observed_full_backup(module,stage,evidence/'archive',evidence)
    assert module._publish_archive_no_clobber is module.original


def test_publisher_original_exception_and_observer_close_error_both_retained(tmp_path,monkeypatch):
    import types,hashlib
    source=tmp_path/'synthetic_producer2.py';source.write_text("def original(stage,target):\n raise marker\ndef create(home,target):\n scratch=home/'.comsol-full-backup-abcdefgh'\n scratch.mkdir(mode=0o700)\n (scratch/'operations.sqlite3').write_bytes(b'synthetic')\n stage=scratch/'full-state.tar'\n stage.write_bytes(b'synthetic')\n module._publish_archive_no_clobber(stage,target)\n")
    marker=RuntimeError('original publisher failed');cleanup=RuntimeError('original FD cleanup failed');module=types.ModuleType('synthetic_backup2');module.__file__=str(source);module.__dict__.update(marker=marker,module=module);exec(compile(source.read_text(),str(source),'exec'),module.__dict__);module._publish_archive_no_clobber=module.original;module.create_full_backup=module.create
    monkeypatch.setattr(data,'BACKUP_PRODUCER_SHA',hashlib.sha256(source.read_bytes()).hexdigest());evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
    original_close=data._ObservedBackupArchive.close;retained=[]
    def close(value):retained.append(value);raise cleanup
    monkeypatch.setattr(data._ObservedBackupArchive,'close',close)
    with pytest.raises(RuntimeError) as error:data.observed_full_backup(module,evidence,evidence/'archive',evidence)
    assert error.value.original_cause is marker and error.value.cleanup_failure is cleanup and module._publish_archive_no_clobber is module.original
    for value in retained:original_close(value)

@pytest.mark.parametrize('failure_point',['counts','migration','success-close'])
def test_candidate_phase_all_postproducer_failures_close_once_and_keep_causes(tmp_path,monkeypatch,failure_point):
    from importlib import metadata
    import jpype
    original=RuntimeError('migration first cause');cleanup=RuntimeError('FD close unknown')
    closure={'projects':[{}],'registered_blob_count':2,'durable_history_sha256_same_algorithm_as_production':'history'}
    receipt={'project_count':0 if failure_point=='counts' else 1,'file_count':2,'durable_history_sha256':'history'}
    calls=[]
    class Observation:
        record={}
        def close(self):
            calls.append('close')
            if failure_point!='counts':raise cleanup
    observation=Observation()
    monkeypatch.setattr(jpype,'isJVMStarted',lambda:False);monkeypatch.setattr(metadata,'version',lambda name:'0.1.10')
    monkeypatch.setattr(data,'descriptor',lambda *args:({'seal':'fixture','seal_sha256':'fixture'},closure))
    monkeypatch.setattr(data,'data_paths',lambda value:{'home':tmp_path,'evidence':tmp_path})
    monkeypatch.setattr(data,'verify_database',lambda *args,**kwargs:None)
    monkeypatch.setattr(data,'observed_full_backup',lambda *args:(receipt,observation))
    def migration(*args,**kwargs):
        if failure_point=='migration':raise original
        return {'path':'fixture'}
    monkeypatch.setattr(data,'migration_bundle',migration);monkeypatch.setattr(data,'verify_migration',lambda *args:None);monkeypatch.setattr(data,'verify_seal',lambda *args:None)
    with pytest.raises(RuntimeError) as caught:data.run_phase('fixture','fixture','candidate-backup')
    assert calls==['close']
    if failure_point=='counts':assert 'counts/history' in str(caught.value)
    else:
        assert caught.value.cleanup_failure is cleanup and caught.value.archive_observation is observation
        assert caught.value.original_cause is (original if failure_point=='migration' else None)
        with pytest.raises(RuntimeError,match='no retry'):data._close_archive_observation(observation)
        assert calls==['close']

@pytest.mark.parametrize('name',['registered-full-state.tar','../before-candidate-open.full-state.tar','/before-candidate-open.full-state.tar'])
def test_restore_original_manifest_filename_refused_before_output(tmp_path,name):
    inputs=tmp_path/'inputs'
    with pytest.raises(RuntimeError,match='filename'):data.restore_archive_inputs({'archive':b'synthetic','manifest':json.dumps({'archive_name':name}).encode()},inputs)
    assert not inputs.exists()

def test_restore_original_manifest_filename_retained_without_rewriting(tmp_path):
    raw=json.dumps({'archive_name':'before-candidate-open.full-state.tar','synthetic':True}).encode();inputs=tmp_path/'inputs'
    archive,manifest=data.restore_archive_inputs({'archive':b'synthetic archive','manifest':raw},inputs)
    assert archive.name=='before-candidate-open.full-state.tar' and archive.read_bytes()==b'synthetic archive' and manifest.read_bytes()==raw
    with pytest.raises(FileExistsError):data.restore_archive_inputs({'archive':b'new','manifest':raw},inputs)
    assert archive.read_bytes()==b'synthetic archive'

@pytest.fixture
def actual_original_capture_fixture(tmp_path,monkeypatch):
    # Synthetic software scope with a real retained harmless child. No Main approval minted.
    import subprocess
    h=unit._helpers();root=tmp_path;logs=root/'logs';logs.mkdir(mode=0o700)
    binding={'nonce':'synthetic-software-control'};binding_record=h.publish(root/'BINDING.json',binding)
    once=h.publish(root/'UNIT_ONCE_GUARD.json',{'nonce':binding['nonce'],'activity_kind':actor.APP_LIFECYCLE,'binding_first_record':binding_record})
    source=h.publish(root/'SOURCE.json',{'synthetic':True});candidate={'source':source};row={'label':'data-candidate-backup'}
    owner=SimpleNamespace();launch=unit.LifecycleLaunch(root,owner,candidate,binding,{'inventory':[row]}, {},None,1)
    monkeypatch.setitem(actor._RETAINED,str(root),owner)
    monkeypatch.setattr(actor,'verify_actor',lambda candidate:h.raw_file(Path(candidate['source']['path']),expected=candidate['source']))
    retained=[];original_module=actor.original.subprocess
    def original_popen(*args,**kwargs):
        process=subprocess.Popen(*args,**kwargs);retained.append(process);return process
    monkeypatch.setattr(actor.original,'subprocess',SimpleNamespace(Popen=original_popen,run=subprocess.run,TimeoutExpired=subprocess.TimeoutExpired))
    logfd=os.open(logs,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        result=actor.original.run(logfd,'data-candidate-backup',[sys.executable,'-I','-c',"import time,json;time.sleep(.1);print(json.dumps({'success':True,'jpype_started_before_after':[False,False]}))"],root,dict(os.environ),timeout=10)
    finally:os.close(logfd)
    process=retained[0];assert process.returncode==0 and result['birth']['status']=='OBSERVED' and result['owned_group_absent'] is True
    output=h.raw_file(logs/result['stdout'])[1];result_record=h.raw_file(logs/result['result_name'])[1];launch.completed.append(result)
    launch.protected_captures[row['label']]={'process':process,'result':result,'request':row,'stdout':output,'result_record':result_record,'source_record':source,'once_record':once}
    return launch

def test_original_capture_parent_protection_positive_actual_child(actual_original_capture_fixture):
    launch=actual_original_capture_fixture
    assert unit._original_protection_capture(launch,'data-candidate-backup')['success'] is True

@pytest.mark.parametrize('tamper',['wronglabel','status','stdout','source','once','JSONprocess','result_name'])
def test_original_capture_protection_rejects_tamper_before_pair_read(actual_original_capture_fixture,tamper):
    launch=actual_original_capture_fixture;observed=launch.protected_captures['data-candidate-backup']
    if tamper=='wronglabel':observed['request']['label']='old-data'
    elif tamper=='status':observed['result']['status']='FAIL'
    elif tamper=='JSONprocess':observed['process']={'pid':observed['result']['pid']}
    elif tamper=='result_name':observed['result']['result_name']='foreign.result.json'
    else:
        key={'source':'source_record','once':'once_record','stdout':'stdout'}[tamper];Path(observed[key]['path']).write_bytes(b'{}')
    with pytest.raises(Exception):unit._original_protection_capture(launch,'data-candidate-backup')

@pytest.mark.parametrize('tamper',['thirdalias','replace','wrongpair','foreigndir'])
def test_parent_owned_readonly_pair_fd_refuses_mutations(tmp_path,tamper):
    root=tmp_path;folder=root/'transition-work';folder.mkdir(mode=0o700);one=folder/'one';one.write_bytes(b'synthetic producer output');one.chmod(0o600);two=folder/'two';os.link(one,two)
    a=data._raw_regular(one,link_count=2)[1];b=data._raw_regular(two,link_count=2)[1]
    proof=unit._ProtectionPairs.__new__(unit._ProtectionPairs);proof.members={};proof.fds=[];proof.close_attempted=False;proof.close_failure=None
    try:
        if tamper=='wrongpair':
            b={**b,'path':str(folder/'unknown')}
            with pytest.raises(Exception):proof.add_pair(a,b,root)
        elif tamper=='foreigndir':
            with pytest.raises(RuntimeError,match='path'):proof.add_pair(a,b,root/'other-root')
        else:
            proof.add_pair(a,b,root);assert proof.read(one)==a
            if tamper=='thirdalias':os.link(one,folder/'third')
            else:one.rename(folder/'retained');one.write_bytes(b'foreign same-name output');one.chmod(0o600)
            with pytest.raises(RuntimeError):proof.read(one)
        with pytest.raises(RuntimeError,match='link'):data.raw_file(two)
    finally:proof.close()

@pytest.mark.parametrize('tamper',['label','lane','cap','max_calls','argv','operand'])
def test_system_reader_only_exact_fixed_ps_lane_before_any_child(tmp_path,tamper,monkeypatch):
    row={'label':'uninstall-process-guard','lane':'SYSTEM_READER','cap':15,'max_calls':2,'request_argv':['ps','-axo','pid=,ppid=,command=']};command=list(unit.SYSTEM_READER_ARGV)
    if tamper=='label':row['label']='data-restore'
    elif tamper=='lane':row['lane']='PRODUCT'
    elif tamper=='cap':row['cap']=16
    elif tamper=='max_calls':row['max_calls']=3
    elif tamper=='argv':command[0]='/bin/sh'
    else:command[-1]='pid=,command='
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('bad lane cannot spawn'))
    with pytest.raises(RuntimeError,match='lane'):unit.system_reader_lane(row,command)

def test_system_reader_plan_max_two_and_network_not_counted(tmp_path):
    rows=unit.inventory(tmp_path,'/fixture/python',{'path':'/fixture','sha256':'a'*64},{'members':[]},{'members':[]});readers=[r for r in rows if r.get('lane')=='SYSTEM_READER']
    assert len(readers)==1 and readers[0]['max_calls']==2 and readers[0]['cap']==15
    assert readers[0]['label']=='uninstall-process-guard'

def test_system_reader_actual_original_birth_go_fixed_ps_no_product_dispatch(tmp_path,monkeypatch):
    import subprocess,time
    h=unit._helpers()
    for name in ('logs','evidence','outside','empty-home','tmp'):(tmp_path/name).mkdir(mode=0o700)
    row={'label':'uninstall-process-guard','lane':'SYSTEM_READER','cap':15,'max_calls':2,'request_argv':['ps','-axo','pid=,ppid=,command=']};plan={**actor.activity_scope({'activity_kind':actor.APP_LIFECYCLE}),'inventory':[row],'administrative_python':{'path':sys.executable}}
    owner=actor.RuntimeOwner(tmp_path,plan,token=actor._OWNER_TOKEN,began=time.monotonic());logfd=os.open(tmp_path/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    launch=unit.LifecycleLaunch(tmp_path,owner,{}, {'nonce':'synthetic-software-reader-control'},plan,{'system_reader_signature':{'synthetic':True}},logfd,1)
    # Only signature fixture is synthetic; original Popen/birth/GO/exec/ps result is real.
    monkeypatch.setattr(unit,'verify_system_reader_signature',lambda record:{'system_ps':actor.system_record('/bin/ps')})
    monkeypatch.setattr(actor.original,'subprocess',SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=subprocess.TimeoutExpired))
    try:result=unit.run_system_reader(launch,0,'uninstall-process-guard-0',list(unit.SYSTEM_READER_ARGV))
    finally:os.close(logfd)
    assert result['rc']==0 and result['owned_group_absent'] is True and result['birth']['status']=='OBSERVED'
    go=json.loads((tmp_path/'logs/GO_0.json').read_bytes());assert go['pid']==result['pid'] and go['nonce']==launch.binding['nonce']
    evidence=json.loads((tmp_path/'evidence/SYSTEM_READER_0.json').read_bytes());assert evidence['network_proof']=='NETWORK_NOT_APPLICABLE_READONLY_SYSTEM_INVENTORY' and evidence['argv']==unit.SYSTEM_READER_ARGV
    inventory=(tmp_path/'logs'/result['stdout']).read_text().splitlines();assert any(line.split()[:1]==[str(os.getpid())] for line in inventory) and len(inventory)>5
