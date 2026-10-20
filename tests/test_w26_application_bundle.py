import json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import w26_runtime_actor as actor,w26_application_bundle as app,full_project_release as release

def test_application_gate_rejects_dependency_once_before_child(tmp_path,monkeypatch):
    h=actor._helpers();identity={'device':tmp_path.stat().st_dev,'inode':tmp_path.stat().st_ino}
    records={name:h.publish(tmp_path/(name+'.json'),{}) for name in ['prepared','toolchain','plan','source']}
    candidate=h.publish(tmp_path/'ACTOR_CANDIDATE.json',{**actor.activity_scope({'activity_kind':actor.APP_BUNDLE}),'root_identity':identity,**records})
    binding=h.publish(tmp_path/'MAIN_ONCE_BINDING.json',{'status':'MAIN_APPROVED_ONCE_W26_CURRENT43_DEPENDENCIES','candidate_sha256':candidate['sha256'],'nonce':'x'*32})
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('wrong activity approval must not create child'))
    with pytest.raises(RuntimeError,match='scope/source/root'):actor.execute(tmp_path,tmp_path/'MAIN_ONCE_BINDING.json',binding['sha256'])
    assert not (tmp_path/'UNIT_ONCE_GUARD.json').exists()

def test_app_closed_scope_preserves_existing_two_profiles():
    scope=actor.activity_scope({'activity_kind':actor.APP_BUNDLE})
    assert scope['budget_seconds']==1800 and scope['cleanup_reserve_seconds']==300
    assert scope['prepare_cap_seconds']==600 and scope['build_cap_seconds']==600 and scope['check_cap_seconds']==180
    assert actor.activity_scope({})['budget_seconds']==7200
    assert actor.activity_scope({'activity_kind':actor.CURRENT43})['profile_cap_seconds']==180
    for key,value in [('budget_seconds',7200),('build_cap_seconds',True),('cleanup_reserve_seconds',0)]:
        with pytest.raises(RuntimeError):actor.activity_scope({'activity_kind':actor.APP_BUNDLE,key:value})
    assert app.LATEST_SOURCE_PROFILE==app.CURRENT175_R9_SOURCE_PROFILE
    assert app.LATEST_SOURCE_LOCK==app.CURRENT175_R9_SOURCE_LOCK
    assert app.current175_source_lock(app.CURRENT175_R4_SOURCE_PROFILE)==app.CURRENT175_R4_SOURCE_LOCK
    assert app.current175_source_lock(app.CURRENT175_R5_SOURCE_PROFILE)==app.CURRENT175_R5_SOURCE_LOCK
    assert app.current175_source_lock(app.CURRENT175_R6_SOURCE_PROFILE)==app.CURRENT175_R6_SOURCE_LOCK
    assert app.current175_source_lock(app.CURRENT175_R7_SOURCE_PROFILE)==app.CURRENT175_R7_SOURCE_LOCK
    assert app.current175_source_lock(app.CURRENT175_R8_SOURCE_PROFILE)==app.CURRENT175_R8_SOURCE_LOCK
    assert app.current175_source_lock(app.CURRENT175_R9_SOURCE_PROFILE)==app.CURRENT175_R9_SOURCE_LOCK

def test_current175_actor_source_closure_pins_all_immutable_profile_locks():
    records={};app.add_current175_lock_sources(records,app.CURRENT175_R6_SOURCE_PROFILE)
    expected={str(app.current175_source_lock(profile)) for profile in app.CURRENT175_PROFILES}
    assert set(records)==expected and len(records)==9
    assert all(records[path]['sha256']==app._helpers().raw_file(Path(path))[1]['sha256'] for path in expected)
    app.add_current175_lock_sources(records,app.CURRENT_SOURCE_PROFILE)
    assert set(records)==expected

def test_two_original_build_captures_only_closed_flags(tmp_path):
    rows=app.commands(tmp_path,'/actual/builder/bin/python')
    assert len(rows)==2 and [r['cap'] for r in rows]==[180,600]
    assert rows[1]['request_argv'][1:4]==['-m','pip','wheel']
    assert rows[1]['argv'][1:6]==['-I','-B','-m','pip','wheel']
    assert {'--no-index','--no-cache-dir','--no-deps','--no-build-isolation'}<=set(rows[1]['argv'])
    assert rows[1]['argv'][-1]==str(tmp_path/'tmp/build-source/source')
    assert '--requirement' not in rows[1]['argv']

def test_app_json_cannot_mint_original_owner_no_effects(tmp_path,monkeypatch):
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('no child before admission'))
    with pytest.raises(RuntimeError,match='cannot mint'):app.execute_bundle(tmp_path,{}, {},{},began=0,admission_token={})
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('project,server',[('0.1.9','0.1.10'),('0.1.10','0.1.9'),('0.1.10','0.1.10\nVERSION = "0.1.10"')])
def test_declared_and_package_version_refuse_before_source_effects(tmp_path,project,server):
    (tmp_path/'comsol_mcp').mkdir();(tmp_path/'pyproject.toml').write_text('[project]\nversion="'+project+'"\n')
    (tmp_path/'comsol_mcp/_server.py').write_text('VERSION = "'+server+'"\n' if '\n' not in server else 'VERSION = "0.1.10"\nVERSION = "0.1.10"\n')
    with pytest.raises(RuntimeError,match='version'):app.verify_version(tmp_path)

def test_actual_current_source_snapshot_tamper_and_membership_refuse(tmp_path):
    # Retired app3 had a frozen153+3 contract; newer live source can add recipes.
    # Portable synthetic source checks the real capture/tamper guards only.
    source=tmp_path/'source';(source/'comsol_mcp').mkdir(parents=True,mode=0o700)
    (source/'pyproject.toml').write_text('[project]\nversion="0.1.10"\n')
    (source/'README.md').write_text('synthetic source fixture; not app build evidence\n')
    (source/'LICENSE').write_text('synthetic test\n')
    (source/'comsol_mcp/_server.py').write_text('VERSION="0.1.10"\n')
    for index in range(152):(source/'comsol_mcp'/('fixture_'+str(index)+'.py')).write_text('# synthetic fixture\n')
    snapshot=tmp_path/'input';row=app.source_capture(source,snapshot)
    assert len(row['snapshot_members'])==156;app.verify_source_input(row)
    p=snapshot/'comsol_mcp/_server.py';p.write_bytes(p.read_bytes()+b'\n# tamper\n')
    with pytest.raises(Exception):app.verify_source_input(row)
    row=app.source_capture(source,tmp_path/'input2');(tmp_path/'input2/comsol_mcp/added.py').write_text('x=1\n')
    with pytest.raises(RuntimeError,match='membership'):app.verify_source_input(row)

def test_private_temp_hook_and_wrong_api_path_no_effects(tmp_path,monkeypatch):
    owner=actor.RuntimeOwner(tmp_path,{'activity_kind':actor.APP_BUNDLE},token=actor._OWNER_TOKEN)
    (tmp_path/'tmp').mkdir();launch=app.ApplicationLaunch(tmp_path,owner,{}, {},{}, {'source':{'snapshot_source':str(tmp_path/'source')},'builder':{'path':'/actual/python'}},None,None)
    with launch.temporary_source_directory() as temp:
        assert Path(temp)==tmp_path/'tmp/build-source' and Path(temp).stat().st_mode&0o777==0o700
    with pytest.raises(FileExistsError):
        with launch.temporary_source_directory():pass
    with release._controlled_launch_scope(launch):
        with pytest.raises(RuntimeError,match='exact build API paths'):release.build_wheel(tmp_path/'wrong',tmp_path/'out',Path('/actual/python'),tmp_path/'evidence')
    assert not (tmp_path/'out').exists() and not (tmp_path/'evidence').exists()

def test_wrong_capture_no_popen(tmp_path,monkeypatch):
    launch=app.ApplicationLaunch(tmp_path,None,{}, {},{'commands':app.commands(tmp_path,'/actual/python')},{},None,None)
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('wrong command must not dispatch'))
    with pytest.raises(RuntimeError,match='exact application captures'):launch.capture(['/bin/sh','-c','anything'],capture_output=True,text=True)


def test_closed_current174_lock_rejects_same_count_wrong_path_or_bytes(tmp_path,monkeypatch):
    # Synthetic portable software data exercises the fixed new contract, not a build.
    source=tmp_path/'current';(source/'comsol_mcp').mkdir(parents=True)
    for name in release.SOURCE_ROOT_FILES:(source/name).write_text('[project]\nversion="0.1.10"\n' if name=='pyproject.toml' else 'synthetic\n')
    (source/'comsol_mcp/_server.py').write_text('VERSION="0.1.10"\n')
    for n in range(170):(source/'comsol_mcp'/('fixture_'+str(n)+'.py')).write_text('# synthetic\n')
    import hashlib
    members=app.source_members(source)
    lock=tmp_path/'lock.json';lock.write_text(json.dumps({'schema':'w26-application-source-closure/1','profile':app.CURRENT_SOURCE_PROFILE,'member_count':174,'members':{rel:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for rel,p in members.items()}}))
    monkeypatch.setattr(app,'SOURCE_LOCK',lock)
    captured=app.source_capture(source,tmp_path/'snapshot',profile=app.CURRENT_SOURCE_PROFILE);app.verify_source_input(captured)
    with pytest.raises(RuntimeError,match='153'):app.source_capture(source,tmp_path/'old-default')
    (source/'comsol_mcp/fixture_0.py').rename(source/'comsol_mcp/wrong.py')
    with pytest.raises(RuntimeError,match='membership'):app.source_capture(source,tmp_path/'wrong-path',profile=app.CURRENT_SOURCE_PROFILE)
    assert not (tmp_path/'wrong-path').exists()
    (source/'comsol_mcp/wrong.py').rename(source/'comsol_mcp/fixture_0.py');(source/'comsol_mcp/fixture_0.py').write_text('# different\n')
    with pytest.raises(RuntimeError,match='bytes'):app.source_capture(source,tmp_path/'wrong-bytes',profile=app.CURRENT_SOURCE_PROFILE)
    assert not (tmp_path/'wrong-bytes').exists()


@pytest.mark.parametrize('profile,lock_attribute',[(app.CURRENT175_SOURCE_PROFILE,'CURRENT175_SOURCE_LOCK'),(app.CURRENT175_R2_SOURCE_PROFILE,'CURRENT175_R2_SOURCE_LOCK'),(app.CURRENT175_R3_SOURCE_PROFILE,'CURRENT175_R3_SOURCE_LOCK'),(app.CURRENT175_R4_SOURCE_PROFILE,'CURRENT175_R4_SOURCE_LOCK'),(app.CURRENT175_R5_SOURCE_PROFILE,'CURRENT175_R5_SOURCE_LOCK'),(app.CURRENT175_R6_SOURCE_PROFILE,'CURRENT175_R6_SOURCE_LOCK'),(app.CURRENT175_R7_SOURCE_PROFILE,'CURRENT175_R7_SOURCE_LOCK'),(app.CURRENT175_R8_SOURCE_PROFILE,'CURRENT175_R8_SOURCE_LOCK'),(app.CURRENT175_R9_SOURCE_PROFILE,'CURRENT175_R9_SOURCE_LOCK')])
def test_current175_exact_locked_snapshot_rejects_stale174_and_source_drift(tmp_path,monkeypatch,profile,lock_attribute):
    import hashlib
    source=tmp_path/'source';package=source/'comsol_mcp';package.mkdir(parents=True)
    for name in release.SOURCE_ROOT_FILES:(source/name).write_text('[project]\nversion="0.1.10"\n' if name=='pyproject.toml' else 'synthetic software source\n')
    (package/'_server.py').write_text('VERSION="0.1.10"\n')
    for n in range(171):(package/('fixture_'+str(n)+'.py')).write_text('# portable synthetic\n')
    members=app.source_members(source);assert len(members)==175
    lock=tmp_path/'current175.json';lock.write_text(json.dumps({'schema':'w26-application-source-closure/1','profile':profile,'member_count':175,'package_count':172,'root_count':3,'members':{rel:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for rel,p in members.items()}}))
    monkeypatch.setattr(app,lock_attribute,lock)
    captured=app.source_capture(source,tmp_path/'snapshot',profile=profile);app.verify_source_input(captured)
    with pytest.raises(RuntimeError,match='153'):app.source_capture(source,tmp_path/'old-default')
    wrong=json.loads(lock.read_bytes());wrong['package_count']=171;lock.write_text(json.dumps(wrong))
    with pytest.raises(RuntimeError,match='package172'):app.source_capture(source,tmp_path/'wrong-count',profile=profile)
    assert not (tmp_path/'wrong-count').exists()
    wrong['package_count']=172;lock.write_text(json.dumps(wrong));(package/'fixture_0.py').write_text('# source drift\n')
    with pytest.raises(RuntimeError,match='bytes'):app.source_capture(source,tmp_path/'drift',profile=profile)
    assert not (tmp_path/'drift').exists()

@pytest.mark.parametrize('fault',['target','count','extra-key','wrong-source'])
def test_closed_delivery_scope_refuses_before_input_reads_or_effects(monkeypatch,fault):
    delivery={'target_inputs':{},'primary_notices':{},'targets':['macos_arm64','macos_x86_64','win_amd64'],'runtime_counts':{'macos_arm64':41,'macos_x86_64':41,'win_amd64':34},'package_counts':{'macos_arm64':42,'macos_x86_64':42,'win_amd64':35}}
    source={'source_profile':app.CURRENT175_SOURCE_PROFILE}
    if fault=='target':delivery['targets'][2]='other'
    elif fault=='count':delivery['runtime_counts']['win_amd64']=35
    elif fault=='extra-key':delivery['arbitrary_argv']=['/bin/sh']
    else:source['source_profile']=app.CURRENT175_R4_SOURCE_PROFILE
    monkeypatch.setattr(app,'_helpers',lambda:pytest.fail('invalid delivery performed input read'))
    with pytest.raises(RuntimeError,match='scope differs'):app.verify_delivery(delivery,source)

@pytest.mark.parametrize('delivery',[None,{'closed':'fixture-only'}])
def test_copy_application_extras_preserves_genuine_lock_basename_only_for_delivery(tmp_path,delivery):
    h=app._helpers();source=tmp_path/'source';source.mkdir();(source/'uv.lock').write_text('version=1\n');(source/'RELEASE_OPERATIONS.md').write_text('synthetic operations\n');out=tmp_path/'new-root';out.mkdir()
    extras={'uv_lock':h.raw_file(source/'uv.lock')[1],'operations':h.raw_file(source/'RELEASE_OPERATIONS.md')[1]}
    result=app.copy_application_extras(out,extras,delivery=delivery)
    assert Path(result['uv_lock']['path']).name==('uv.lock' if delivery else 'INPUT_uv_lock')
    assert Path(result['operations']['path']).name=='INPUT_operations'
    assert Path(result['uv_lock']['path']).read_bytes()==(source/'uv.lock').read_bytes()
    with pytest.raises(FileExistsError):app.copy_application_extras(out,extras,delivery=delivery)


def test_copy_application_extras_refuses_unknown_member_before_output(tmp_path):
    with pytest.raises(RuntimeError,match='membership'):app.copy_application_extras(tmp_path,{'arbitrary':{}},delivery={})
    assert list(tmp_path.iterdir())==[]
