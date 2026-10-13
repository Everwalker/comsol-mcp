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
    source=Path(__file__).parents[1];snapshot=tmp_path/'input';row=app.source_capture(source,snapshot)
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
