import errno,json,os,socket,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from tools import w26_runtime_actor as actor
from tools import w26_dependency_unit as unit
from tools import w26_dependency_stage as stage
from tools import full_project_release as release
from tools.w26_prepare_current_inputs import _helpers

@pytest.mark.parametrize('conflict',[None,'module','package'])
def test_real_runpy_cli_canonical_original_owner_identity_no_effects(conflict):
    code='''import runpy,sys,types,importlib.util
import tools
conflict=sys.argv[1]
if conflict=='module':
    different=types.ModuleType('different_actor');different.__spec__=importlib.util.find_spec('tools.w26_runtime_actor')
    sys.modules['tools.w26_runtime_actor']=different
if conflict=='package':tools.w26_runtime_actor=types.ModuleType('different_actor')
sys.argv=['tools.w26_runtime_actor','--help']
try:runpy.run_module('tools.w26_runtime_actor',run_name='__main__',alter_sys=True)
except RuntimeError as error:
    assert conflict!='None' and 'module identity differs' in str(error)
    print('REFUSED_BEFORE_DISPATCH');sys.exit(0)
except SystemExit as error:assert error.code==0
assert conflict=='None'
from tools import w26_runtime_actor as actor,w26_dependency_unit as unit
assert actor is unit.actor and actor._RETAINED=={}
class BeforeEffects(Exception):pass
def no_effects():raise BeforeEffects()
unit._helpers=no_effects
try:unit.execute_dependencies(None,None,None,None,began=0,admission_token=actor._OWNER_TOKEN)
except BeforeEffects:print('SAME_ORIGINAL_TOKEN_BEFORE_EFFECTS')
else:raise AssertionError('original internal token did not cross guarded path')
'''
    result=subprocess.run([sys.executable,'-c',code,str(conflict)],cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert ('SAME_ORIGINAL_TOKEN_BEFORE_EFFECTS' if conflict is None else 'REFUSED_BEFORE_DISPATCH') in result.stdout

def test_only_two_closed_activity_profiles_shared_epoch(monkeypatch,tmp_path):
    clock=SimpleNamespace(now=100.)
    monkeypatch.setattr(actor.time,'monotonic',lambda:clock.now)
    owner=actor.RuntimeOwner(tmp_path,{'activity_kind':actor.CURRENT43},token=actor._OWNER_TOKEN,began=50.)
    assert owner.deadline==1850. and owner.scope['cleanup_reserve_seconds']==300
    clock.now=1551.
    with pytest.raises(TimeoutError):owner.remaining(180)
    assert owner.deadline==1850.
    assert actor.activity_scope({})['budget_seconds']==7200
    for plan in ({'activity_kind':'generic'},{'activity_kind':actor.CURRENT43,'budget_seconds':7200},{'activity_kind':actor.CURRENT43,'cleanup_reserve_seconds':True}):
        with pytest.raises(RuntimeError):actor.activity_scope(plan)

def test_exact_eight_closed_argv_copies_hash_binary_no_cache(tmp_path):
    context={'path':str(tmp_path/'ctx.json'),'sha256':'a'*64};runtime={'base_python_record':{'path':'/fixed/current/bin/python3.12'}}
    commands=unit.dependency_plan(tmp_path,runtime,context)
    assert len(commands)==8 and [c['cap'] for c in commands]==[300]*4+[180]*4
    for c in commands[:2]:assert '--copies' in c['argv'] and c['argv'][1:3]==['-I','-B']
    for c in commands[2:4]:assert {'--no-index','--require-hashes','--only-binary=:all:','--no-cache-dir'}<=set(c['argv'])

@pytest.mark.parametrize('number',[errno.EPERM,errno.EACCES,errno.ECONNREFUSED])
def test_same_pid_numeric_deny_before_exec(tmp_path,monkeypatch,number):
    h=_helpers();(tmp_path/'evidence').mkdir(mode=0o700)
    context={'unit_root':str(tmp_path),'root_identity':h.secure_dir(tmp_path),'outside':str(tmp_path),
             'commands':[{'label':'fixture','argv':['fixture']}]*8,'environment':{'HOME':str(tmp_path)}}
    monkeypatch.setattr(stage,'read_context',lambda *a:(context,{'secure_dir':h.secure_dir,'publish':h.publish}))
    monkeypatch.setattr(stage.os,'chdir',lambda path:None)
    def denied(*a,**kw):raise OSError(number,'synthetic OS boundary')
    calls=[]
    if number in (errno.EPERM,errno.EACCES):
        stage.execute('ignored','ignored',0,54321,connector=denied,execve=lambda *a:calls.append((os.getpid(),a)))
        assert len(calls)==1 and calls[0][0]==os.getpid()
        assert json.loads((tmp_path/'evidence/EXEC_fixture.json').read_text())['same_original_pid']==os.getpid()
    else:
        with pytest.raises(RuntimeError,match='numeric'):stage.execute('ignored','ignored',0,54321,connector=denied,execve=lambda *a:calls.append(a))
        assert calls==[] and not (tmp_path/'evidence/EXEC_fixture.json').exists()

def test_wrong_release_argv_refused_before_metadata_or_popen(tmp_path,monkeypatch):
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('no Popen before exact admission'))
    plan={'commands':[{'request_argv':['exact']}]*8}
    launch=unit.DependencyLaunch(tmp_path,None,None,None,plan,None,None,None)
    with pytest.raises(RuntimeError,match='exact admitted'):launch.capture(['wrong'],cwd=None,capture_output=True,text=True)

def test_existing_release_launch_hook_reset_and_default_unchanged(monkeypatch):
    default=[];controlled=[]
    monkeypatch.setattr(release.subprocess,'run',lambda command,**kw:(default.append(command) or SimpleNamespace(returncode=0,stdout='default',stderr='')))
    hook=SimpleNamespace(capture=lambda command,**kw:(controlled.append(command) or SimpleNamespace(returncode=0,stdout='controlled',stderr='')))
    assert release.run_record(['before'])['stdout']=='default'
    with pytest.raises(ValueError):
        with release._controlled_launch_scope(hook):
            assert release.run_record(['inside'])['stdout']=='controlled';raise ValueError('retained failure')
    assert release.run_record(['after'])['stdout']=='default'
    assert default==[['before'],['after']] and controlled==[['inside']]

@pytest.mark.parametrize('name',['allsans.pem','badcert.pem','keycert.pem'])
def test_public_fixture_exception_exact_hash_name_path_only(name):
    raw=(Path(__file__).parent/'fixtures/w26_public_tls'/name).read_bytes();relative='tests/fixtures/w26_public_tls/'+name
    good=[];release._inspect_bytes(relative,raw,good);assert good==[]
    archive=[];release._inspect_bytes('capsule.zip!/'+relative,raw,archive);assert archive==[]
    for path,blob in [(name,raw),('tests/fixtures/other/'+name,raw),(relative,raw+b'tamper')]:
        findings=[];release._inspect_bytes(path,blob,findings)
        assert any(r['kind']=='SECRET_PATTERN' for r in findings)

def test_missing_new_scope_zero_popen(tmp_path,monkeypatch):
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('missing once must not create child'))
    with pytest.raises((RuntimeError,OSError)):actor.execute(tmp_path,tmp_path/'MAIN_ONCE_BINDING.json','a'*64)

def test_dependency_json_cannot_mint_original_owner(tmp_path):
    with pytest.raises(RuntimeError,match='cannot mint owner'):unit.execute_dependencies(tmp_path,{}, {},{},began=0.)

def test_capture_reads_actual_original_actor_log_and_rc_shape(tmp_path,monkeypatch):
    h=_helpers();(tmp_path/'logs').mkdir(mode=0o700)
    context=h.publish(tmp_path/'context.json',{'base_prefix':'/fixed/base','environment':unit.dependency_environment(tmp_path)})
    h.copy_blob(tmp_path/'logs/fixture.stdout',b'actual original stdout');h.copy_blob(tmp_path/'logs/fixture.stderr',b'')
    monkeypatch.setattr(actor,'verify_actor',lambda _:None);monkeypatch.setattr(unit,'verify_inputs',lambda *a:None)
    seen=[]
    def original_boundary(*a,**kw):
        seen.append(a[4]);return {'rc':0,'stdout':'fixture.stdout','stderr':'fixture.stderr','result_name':'fixture.result.json','status':'PASS'}
    monkeypatch.setattr(actor,'run_owned_stage',original_boundary)
    plan={'context':context,'commands':[{'request_argv':['exact'],'argv':['actual'],'label':'fixture','cap':180}]*8}
    launch=unit.DependencyLaunch(tmp_path,SimpleNamespace(remaining=lambda _:1),{}, {'nonce':'a'*16},plan,{'manifest':{},'runtime':{}},0,12345)
    result=launch.capture(['exact'],cwd=None,capture_output=True,text=True)
    assert result.returncode==0 and result.stdout=='actual original stdout' and launch.index==1
    assert len(seen)==1 and 'w26_dependency_stage.py' in ' '.join(seen[0])

def test_current43_refuses_app_wheel_before_staging_effect(tmp_path):
    launch=unit.DependencyLaunch(tmp_path,None,None,None,{},None,None,None)
    with release._controlled_launch_scope(launch):
        with pytest.raises(RuntimeError,match='excludes application'):release.build_wheel(tmp_path,tmp_path/'out',tmp_path/'python',tmp_path/'evidence')
    assert not (tmp_path/'out').exists() and not (tmp_path/'evidence').exists()
