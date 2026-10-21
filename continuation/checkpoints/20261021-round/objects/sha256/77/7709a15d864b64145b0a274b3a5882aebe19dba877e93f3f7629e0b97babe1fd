"""Actual fixed entry guards with synthetic approvals; zero native/exec births."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import os
from uuid import uuid4
import pytest
from tools import science_native_once_entry as entry

ROOT=Path(__file__).resolve().parents[1]
PREP=ROOT.parent/'_state/science-public-recipes/current-r4/BINDING_PROPOSAL.json'

def _approved_from(proposal_path, monkeypatch):
    proposal=json.loads(proposal_path.read_text());base=json.loads(Path(proposal['binding']).read_text())
    authority=Path('/private/tmp')/('cvscience-entry-software-'+uuid4().hex);authority.mkdir(mode=0o700)
    runtime=Path('/private/tmp')/('cvscience-native-software-'+uuid4().hex)
    base['authority']=str(authority);base['runtime_namespace']=str(runtime);base['activity_id']=runtime.name
    plan=json.loads(Path(base['plan']).read_text());plan['runtime_namespace']=str(runtime)
    plan['literal_base_calls'][2]['arguments']['label']=runtime.name
    def write(name,value):
        p=authority/name;p.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');p.chmod(0o600)
        return p,hashlib.sha256(p.read_bytes()).hexdigest()
    base['plan'],base['plan_sha256']=write('LITERAL_PLAN.json',plan);base['plan']=str(base['plan'])
    freeze=json.loads(Path(base['source_freeze']).read_text());base['source_freeze'],base['source_freeze_sha256']=write('SOURCE_FREEZE.json',freeze);base['source_freeze']=str(base['source_freeze'])
    for option,value in [('--runtime',str(runtime)),('--freeze',base['source_freeze']),('--science-plan',base['plan']),('--science-plan-sha256',base['plan_sha256'])]:base['argv'][base['argv'].index(option)+1]=value
    bp,bsha=write('BINDING.json',base);nonce='7'*64
    approval={'schema':entry.APPROVAL_SCHEMA,'status':'APPROVED_BY_MAIN_FRESH_SCOPED','issuer':'Main','activity_id':runtime.name,'binding_sha256':bsha,'entry_sha256':proposal['entry_sha256'],'nonce':nonce,'expires_at_utc':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),'budget':base['budget'],'approval_scope':'SCIENCE76_REAL_PUBLIC_RECIPE_UNSOLVED_ONLY'}
    ap,asha=write('MAIN_APPROVAL.json',approval)
    monkeypatch.setattr(entry,'__file__',str(Path(base['source_root'])/'tools/science_native_once_entry.py'))
    return base,approval,bp,bsha,ap,asha,nonce,write


@pytest.fixture
def approved(monkeypatch):
    return _approved_from(PREP, monkeypatch)


@pytest.fixture
def approved_v2(monkeypatch):
    proposal=ROOT.parent/'_state/science-public-recipes/current-r5/BINDING_PROPOSAL.json'
    return _approved_from(proposal, monkeypatch)


def validate(row):
    _,_,bp,bsha,ap,asha,nonce,_=row
    return entry.validate(bp,bsha,ap,asha,nonce)


def test_real_samefd_source_runtime_and_synthetic_approval_validate_no_effect(approved):
    binding,_=validate(approved)
    assert binding['budget']['public_calls']==76 and binding['budget']['task_models_max']==8
    assert not Path(binding['runtime_namespace']).exists()
    assert not (Path(binding['authority'])/'ONCE_CONSUMED.json').exists()

@pytest.mark.parametrize('change',['nonce','binding_sha','expiry','scope','old_status','dropfield'])
def test_bad_main_record_never_consumes_once(approved,change):
    b,a,bp,bsha,ap,asha,nonce,write=approved
    if change=='nonce':a['nonce']='8'*64
    elif change=='binding_sha':a['binding_sha256']='0'*64
    elif change=='expiry':a['expires_at_utc']='2000-01-01T00:00:00Z'
    elif change=='scope':a['approval_scope']='fullscience'
    elif change=='old_status':a['status']='APPROVED_ACTIVITY020'
    else:a.pop('budget')
    ap,asha=write('MAIN_APPROVAL.json',a)
    with pytest.raises(ValueError):entry.validate(bp,bsha,ap,asha,nonce)
    assert not (Path(b['authority'])/'ONCE_CONSUMED.json').exists()

@pytest.mark.parametrize('change',['argv','env','budget','app4','source_sha','namespace','sourceoutside'])
def test_binding_tamper_rehashed_with_matching_synthetic_main_sha_fails(approved,change):
    b,a,bp,bsha,ap,asha,nonce,write=approved
    if change=='argv':b['argv'].append('--parameter-io')
    elif change=='env':b['environment']['COMSOL_MCP_TRUSTED_CODE']='1'
    elif change=='budget':b['budget']['solver_calls']=1
    elif change=='app4':b['app4_production_sha256']='0'*64
    elif change=='source_sha':b['source_sha256']='0'*64
    elif change=='namespace':Path(b['runtime_namespace']).mkdir()
    else:b['source_files']['../foreign.py']='0'*64
    bp,bsha=write('BINDING.json',b);a['binding_sha256']=bsha;ap,asha=write('MAIN_APPROVAL.json',a)
    with pytest.raises(ValueError):entry.validate(bp,bsha,ap,asha,nonce)
    assert not (Path(b['authority'])/'ONCE_CONSUMED.json').exists()


def test_actual_exclusive_once_spent_before_exact_exec_and_never_replay(approved,monkeypatch):
    binding,before=validate(approved);_,_,_,bsha,_,asha,nonce,_=approved
    monkeypatch.setattr(entry,'ensure_quiescent',lambda:None)
    original=Path.cwd();calls=[]
    class ExecSentinel(Exception):pass
    def execve(executable,argv,env):
        calls.append((executable,argv,env));raise ExecSentinel('no actual exec')
    monkeypatch.setattr(entry.os,'execve',execve)
    try:
        with pytest.raises(ExecSentinel):entry.consume_and_exec(binding,before,bsha,asha,nonce)
        assert calls==[(binding['argv'][0],binding['argv'],binding['environment'])]
        assert (Path(binding['authority'])/'ONCE_CONSUMED.json').is_file()
        with pytest.raises(FileExistsError):entry.consume_and_exec(binding,Path(binding['authority']).lstat(),bsha,asha,nonce)
        assert len(calls)==1 and not Path(binding['runtime_namespace']).exists()
    finally:os.chdir(original)


def test_existing_once_symlink_and_busy_native_refuse_no_exec(approved,monkeypatch):
    binding,before=validate(approved);_,_,_,bsha,_,asha,nonce,_=approved
    monkeypatch.setattr(entry.os,'execve',lambda *a:pytest.fail('native exec guard escaped'))
    monkeypatch.setattr(entry,'ensure_quiescent',lambda:(_ for _ in ()).throw(ValueError('native busy')))
    with pytest.raises(ValueError,match='busy'):entry.consume_and_exec(binding,before,bsha,asha,nonce)
    once=Path(binding['authority'])/'ONCE_CONSUMED.json';assert not once.exists()
    once.symlink_to('/private/tmp/never-write-this-target')
    monkeypatch.setattr(entry,'ensure_quiescent',lambda:None)
    with pytest.raises(FileExistsError):entry.consume_and_exec(binding,Path(binding['authority']).lstat(),bsha,asha,nonce)


def test_actual_source_directory_symlink_rejected_before_import(approved,monkeypatch):
    import shutil
    binding=copy.deepcopy(approved[0]);source=Path('/private/tmp')/('cvscience-source-symlink-test-'+uuid4().hex)
    shutil.copytree(binding['source_root'],source)
    source.chmod(0o700);(source/'foreign-directory').symlink_to('/private/tmp',target_is_directory=True);source.chmod(0o500)
    binding['source_root']=str(source)
    with pytest.raises(ValueError,match='symlink'):entry.source_identity(binding)


def test_real_private_hardlink_and_symlink_records_refused(tmp_path):
    file=tmp_path/'ordinary';file.write_text('{}');file.chmod(0o600)
    link=tmp_path/'link';link.symlink_to(file)
    with pytest.raises(ValueError):entry.read_file(link,private=True)
    second=tmp_path/'hardlink';os.link(file,second)
    with pytest.raises(ValueError,match='single-link'):entry.read_file(file,private=True)



def test_v2_actual_current172_package_175_production_prebirth_no_effect(approved_v2):
    binding,_=validate(approved_v2)
    package={p:h for p,h in binding['source_files'].items() if p.startswith('comsol_mcp/')}
    assert len(package)==172 and 'comsol_mcp/_windows_state_restore.py' in package
    assert binding['schema']==entry.CURRENT_BINDING_SCHEMA and binding['budget']['public_calls']==76
    assert binding['budget']['study_submissions']==binding['budget']['solver_calls']==0
    assert not Path(binding['runtime_namespace']).exists()
    assert not (Path(binding['authority'])/'ONCE_CONSUMED.json').exists()


@pytest.mark.parametrize('change',['unknown_schema','old_schema','old_field','mixed_field','extra','dropfield','production_hash','membership','source_hash'])
def test_v2_closed_current_binding_rejection_without_birth(approved_v2,change):
    b,a,bp,bsha,ap,asha,nonce,write=approved_v2
    if change=='unknown_schema':b['schema']='science-native-fixed-binding/3'
    elif change=='old_schema':b['schema']=entry.BINDING_SCHEMA
    elif change=='old_field':b['app4_production_sha256']=b.pop('production_sha256')
    elif change=='mixed_field':b['app4_production_sha256']=b['production_sha256']
    elif change=='extra':b['trusted_code']=True
    elif change=='dropfield':b.pop('production_sha256')
    elif change=='production_hash':b['production_sha256']='0'*64
    elif change=='membership':b['source_files'].pop('comsol_mcp/_windows_state_restore.py')
    else:b['source_files']['comsol_mcp/_g3_managed_models.py']='0'*64
    bp,bsha=write('BINDING.json',b);a['binding_sha256']=bsha;ap,asha=write('MAIN_APPROVAL.json',a)
    with pytest.raises(ValueError):entry.validate(bp,bsha,ap,asha,nonce)
    assert not Path(b['runtime_namespace']).exists() and not (Path(b['authority'])/'ONCE_CONSUMED.json').exists()


@pytest.mark.parametrize('mode',['v1_new175','v2_old174','v2_wrongmember'])
def test_v2_exact_count_membership_and_v1_historical_rule(approved_v2,mode):
    b=approved_v2[0]
    app={p:h for p,h in b['source_files'].items() if p.startswith('comsol_mcp/') or p in {'LICENSE','README.md','pyproject.toml'}}
    assert len(app)==175
    if mode=='v1_new175':
        b['schema']=entry.BINDING_SCHEMA;b['app4_production_sha256']=hashlib.sha256(entry.canonical(app)).hexdigest()
    elif mode=='v2_old174':app.pop('comsol_mcp/_windows_state_restore.py');b['production_sha256']=hashlib.sha256(entry.canonical(app)).hexdigest()
    else:
        app['comsol_mcp/foreign.py']=app.pop('comsol_mcp/_windows_state_restore.py');b['production_sha256']=hashlib.sha256(entry.canonical(app)).hexdigest()
    with pytest.raises(ValueError):entry.validate_production_identity(b,app)
