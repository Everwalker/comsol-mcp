"""Production fresh prebirth binding tests. No engine or GUI birth."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest
from tools import native_runtime_binding as binding
from tools import run_native_w24_static_shape_setup_campaign as w24
from tools import run_native_w23_saved_mph_diagnostic as w23


def test_v2_runtime_launcher_capture_requires_exact_no_site_module(monkeypatch):
    repo=Path(__file__).resolve().parents[1]
    module=repo/'tools/native_runtime_binding.py'
    monkeypatch.setattr(binding.sys,'flags',SimpleNamespace(no_site=True))
    monkeypatch.setattr(binding.sys,'argv',[str(module),'--phase','w24-setup'])
    monkeypatch.setattr(binding.sys,'orig_argv',[sys.executable,'-S','-m','tools.native_runtime_binding','--phase','w24-setup'])
    captured=binding._capture_runtime_argv0(repo=repo,expected_module='tools.native_runtime_binding')
    path=Path(sys.executable).resolve(strict=True)
    assert captured=={'source':'sys.orig_argv[0]','path':str(path),
                      'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(binding.sys,'orig_argv',[sys.executable,'-m','tools.native_runtime_binding'])
    with pytest.raises(binding.BindingError,match='explicit no-site -S'):
        binding._capture_runtime_argv0(repo=repo,expected_module='tools.native_runtime_binding')
    monkeypatch.setattr(binding.sys,'orig_argv',[sys.executable,'-S','-m','tests._native_runtime_binding_probe'])
    with pytest.raises(binding.BindingError,match='exact no-site'):
        binding._capture_runtime_argv0(repo=repo,expected_module='tools.native_runtime_binding')
    external=repo/'tests/_native_runtime_binding_probe.py'
    monkeypatch.setattr(binding.sys,'argv',[str(external),'--ready','unused'])
    monkeypatch.setattr(binding.sys,'orig_argv',[sys.executable,'-S',str(external),'--ready','unused'])
    captured=binding._capture_runtime_argv0(repo=repo,expected_module=None)
    path=Path(sys.executable).resolve(strict=True)
    assert captured=={'source':'sys.orig_argv[0]','path':str(path),
                      'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    wrong_runtime=repo/'pyproject.toml'
    monkeypatch.setattr(binding.sys,'orig_argv',[str(wrong_runtime),'-S',str(external)])
    mismatched=binding._capture_runtime_argv0(repo=repo,expected_module=None)
    assert mismatched['path']==str(wrong_runtime.resolve(strict=True))
    assert mismatched['sha256']==hashlib.sha256(wrong_runtime.read_bytes()).hexdigest()


def test_v2_binding_is_opt_in_closed_and_detects_runtime_argv0_drift(case,monkeypatch):
    root,repo,_,_,observed=case
    launcher=Path(sys.executable).resolve(strict=True)
    runtime={'source':'sys.orig_argv[0]','path':str(launcher),
             'sha256':hashlib.sha256(launcher.read_bytes()).hexdigest()}
    monkeypatch.setattr(binding,'observe',lambda *a,**k:{**copy.deepcopy(observed),'runtime_argv0':copy.deepcopy(runtime)})
    authority=root/'authority-v2.json'
    packet=binding.create(repo=repo,authority=authority,activity_id=str(uuid4()),
        python=Path('/fresh/python'),packages=Path('/fresh/site-packages'),install=Path('/fresh/comsol'),
        jdk=Path('/fresh/jdk'),budget=w24.SETUP_BUDGET,schema=binding.SCHEMA_V2)
    assert packet['schema']==binding.SCHEMA_V2 and set(packet)==binding.KEYS_V2
    assert packet['runtime_argv0']==runtime
    assert binding.validate(packet,repo=repo,budget=w24.SETUP_BUDGET)==packet
    bad=copy.deepcopy(packet);bad['runtime_argv0']['sha256']='0'*64
    bad['binding_sha256']=binding.digest({k:v for k,v in bad.items() if k!='binding_sha256'})
    with pytest.raises(binding.BindingError,match='identity differs'):
        binding.validate(bad,repo=repo,budget=w24.SETUP_BUDGET)
    bad=copy.deepcopy(packet)
    wrong_path=repo/'pyproject.toml'
    bad['runtime_argv0']['path']=str(wrong_path)
    bad['runtime_argv0']['sha256']=hashlib.sha256(wrong_path.read_bytes()).hexdigest()
    bad['binding_sha256']=binding.digest({k:v for k,v in bad.items() if k!='binding_sha256'})
    with pytest.raises(binding.BindingError,match='identity differs'):
        binding.validate(bad,repo=repo,budget=w24.SETUP_BUDGET)
    bad=copy.deepcopy(packet);bad['unexpected']=True
    bad['binding_sha256']=binding.digest({k:v for k,v in bad.items() if k!='binding_sha256'})
    with pytest.raises(binding.BindingError,match='schema is closed'):
        binding.validate(bad,repo=repo,budget=w24.SETUP_BUDGET)


def test_cli_phase_selects_v2_only_for_w24(monkeypatch,tmp_path,capsys):
    observed=[]
    monkeypatch.setattr(binding,'create',lambda **kwargs:observed.append(kwargs) or {
        'schema':kwargs['schema'],'binding_sha256':'a'*64})
    common=['--repo',str(tmp_path),'--authority',str(tmp_path/'runtime.json'),
        '--activity-id',str(uuid4()),'--python','/fresh/python','--site-packages',
        '/fresh/site-packages','--comsol-root','/fresh/comsol','--jdk11','/fresh/jdk']
    assert binding.main(common+['--phase','w24-setup'])==0
    assert observed[-1]['schema']==binding.SCHEMA_V2
    assert binding.main(common+['--phase','w23-readback'])==0
    assert observed[-1]['schema']==binding.SCHEMA_V1
    assert 'FRESH_NATIVE_RUNTIME_SOURCE_V2' in capsys.readouterr().out


def test_real_no_site_module_long_argv_matches_only_pinned_runtime_paths(tmp_path):
    repo=Path(__file__).resolve().parents[1]
    ready=tmp_path/'ready.json'
    long_arg='x'*4096
    argv=[sys.executable,'-S','-m','tests._native_runtime_binding_probe',
          '--ready',str(ready),'--hold-ms','4000','--long-arg',long_arg]
    proc=subprocess.Popen(argv,cwd=repo,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+8
        while not ready.is_file() and time.monotonic()<deadline and proc.poll() is None:
            time.sleep(0.02)
        if not ready.is_file():
            stdout,stderr=proc.communicate(timeout=3)
            pytest.fail(f'harmless no-site module probe failed: {stdout} {stderr}')
        payload=json.loads(ready.read_text())
        actual=payload['orig_argv']
        assert payload['no_site'] is True and actual[1:]==argv[1:]
        runtime_path=Path(actual[0]).resolve(strict=True)
        runtime={'source':'sys.orig_argv[0]','path':str(runtime_path),
                 'sha256':hashlib.sha256(runtime_path.read_bytes()).hexdigest()}
        allowed=[argv[0],runtime['path']]
        assert w24._same_controller_argv(actual,argv,allowed_argv0=allowed)
        foreign=list(actual);foreign[0]=str(tmp_path/'foreign-python')
        assert not w24._same_controller_argv(foreign,argv,allowed_argv0=allowed)
        changed_tail=list(actual);changed_tail[-1]+='-tampered'
        assert not w24._same_controller_argv(changed_tail,argv,allowed_argv0=allowed)
    finally:
        try:proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate();proc.communicate(timeout=8)


def test_v2_cli_captures_real_runtime_argv0_without_engine_birth(tmp_path):
    repo=Path(__file__).resolve().parents[1]
    frontend=Path(sys.executable)
    python=frontend.resolve(strict=True)
    packages=frontend.parent.parent/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    install=w24.INSTALL_ROOT;jdk=w24.JAVA11
    if not packages.is_dir() or not (install/'readme.txt').is_file() or not jdk.is_dir():
        pytest.skip('exact Python site-packages, COMSOL readme, or JDK11 is unavailable')
    root=Path(tempfile.mkdtemp(prefix='cv3-runtime-v2-binding-',dir='/private/tmp')).resolve()
    root.chmod(0o700)
    authority=root/'RUNTIME_BINDING.json'
    command=[str(frontend),'-S','-m','tools.native_runtime_binding',
        '--repo',str(repo),'--authority',str(authority),'--activity-id',str(uuid4()),
        '--python',str(frontend),'--site-packages',str(packages),
        '--comsol-root',str(install),'--jdk11',str(jdk),'--phase','w24-setup']
    env={k:v for k,v in os.environ.items() if k not in {
        'PYTHONPATH','PYTHONHOME','CODEX_NETWORK_GUARD_LOG','CODEX_NETWORK_GUARD_LABEL',
        'CODEX_NETWORK_GUARD_RUN_ID'}}
    env.update({'TMPDIR':'/private/tmp','PYTHONDONTWRITEBYTECODE':'1'})
    try:
        result=subprocess.run(command,cwd=repo,env=env,text=True,capture_output=True,timeout=90)
        assert result.returncode==0,result.stdout+result.stderr
        packet=json.loads(authority.read_text())
        assert packet['schema']==binding.SCHEMA_V2
        assert packet['runtime_argv0']['source']=='sys.orig_argv[0]'
        launcher=Path(packet['runtime_argv0']['path'])
        assert launcher.is_absolute() and launcher.resolve(strict=True)==launcher
        assert hashlib.sha256(launcher.read_bytes()).hexdigest()==packet['runtime_argv0']['sha256']
        assert packet['python']['entry']==str(frontend)
        assert 'FRESH_NATIVE_RUNTIME_SOURCE_V2' in result.stdout
    finally:
        shutil.rmtree(root)


def test_external_no_site_execute_once_check_only_uses_original_v2_verifier():
    """A real -S outer script verifies a V2 candidate without consuming once or starting native work."""
    repo=Path(__file__).resolve().parents[1]
    frontend=Path(sys.executable)
    packages=frontend.parent.parent/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    install=w24.INSTALL_ROOT;jdk=w24.JAVA11
    if (sys.version_info[:2]!=(3,12) or not packages.is_dir()
            or not (install/'readme.txt').is_file() or not jdk.is_dir()):
        pytest.skip('exact Python 3.12 site-packages, COMSOL readme, or JDK11 is unavailable')
    root=Path(tempfile.mkdtemp(prefix='cv3-w24-external-checkonly-',dir='/private/tmp')).resolve()
    root.chmod(0o700)
    temp_root=root/'tmp';home=root/'empty-home'
    temp_root.mkdir(mode=0o700);home.mkdir(mode=0o700)
    authority=root/'RUNTIME_BINDING.json';evidence=root/'evidence';work=root/'work'
    entry=root/'execute_once.py'
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(home),'TMPDIR':str(temp_root),
         'LC_ALL':'C','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
    create=[str(frontend),'-S','-m','tools.native_runtime_binding',
        '--repo',str(repo),'--authority',str(authority),'--activity-id',str(uuid4()),
        '--python',str(frontend),'--site-packages',str(packages),
        '--comsol-root',str(install),'--jdk11',str(jdk),'--phase','w24-setup']
    created=subprocess.run(create,cwd=repo,env=env,text=True,capture_output=True,timeout=120)
    assert created.returncode==0,created.stdout+'\n'+created.stderr
    packet=json.loads(authority.read_text())
    assert packet['schema']==binding.SCHEMA_V2
    prepare=[str(frontend),'-S','-m','tools.run_native_w24_static_shape_setup_campaign','prepare',
        '--evidence',str(evidence),'--runtime-authority',str(authority),
        '--comsol-root',str(install),'--jdk11',str(jdk)]
    prepared=subprocess.run(prepare,cwd=repo,env=env,text=True,capture_output=True,timeout=240)
    assert prepared.returncode==0,prepared.stdout+'\n'+prepared.stderr
    freeze=json.loads((evidence/'candidate_freeze.json').read_text())
    assert freeze['fresh_runtime_binding']==packet
    assert not work.exists() and not (evidence/'native-setup-run').exists()
    assert not (root/'W24_ONCE.json').exists()

    bad_evidence={}
    for variant in ('path','hash'):
        bad_dir=root/f'negative-{variant}';bad_dir.mkdir(mode=0o700)
        bad=copy.deepcopy(freeze)
        bad['candidate_evidence_dir']=str(bad_dir)
        bad_binding=bad['fresh_runtime_binding']
        if variant=='path':
            wrong_path=repo/'pyproject.toml'
            bad_binding['runtime_argv0']['path']=str(wrong_path)
            bad_binding['runtime_argv0']['sha256']=hashlib.sha256(wrong_path.read_bytes()).hexdigest()
        else:
            bad_binding['runtime_argv0']['sha256']='0'*64
        bad_binding['binding_sha256']=binding.digest(
            {k:v for k,v in bad_binding.items() if k!='binding_sha256'})
        bad['candidate_sha256']=w24._json_hash(
            {k:v for k,v in bad.items() if k!='candidate_sha256'})
        (bad_dir/'candidate_freeze.json').write_text(
            json.dumps(bad,sort_keys=True,indent=2,allow_nan=False))
        bad_evidence[variant]=(bad_dir,bad['candidate_sha256'])

    entry.write_text('''import argparse,json,sys
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--repo',required=True,type=Path)
parser.add_argument('--packages',required=True,type=Path)
parser.add_argument('--evidence',required=True,type=Path)
parser.add_argument('--work',required=True,type=Path)
parser.add_argument('--reviewed-sha',required=True)
parser.add_argument('--mode',choices=('check-only','negative-path','negative-hash'),required=True)
args=parser.parse_args()
if (not sys.flags.no_site or len(sys.orig_argv)<3 or sys.orig_argv[1]!='-S'
        or Path(sys.orig_argv[2]).resolve(strict=True)!=Path(__file__).resolve(strict=True)):
    raise RuntimeError('external no-site script entry differs')
sys.path[:0]=[str(args.repo),str(args.packages)]
from tools import native_runtime_binding as binding
from tools import run_native_w24_static_shape_setup_campaign as campaign
if args.mode=='check-only':
    freeze=campaign.verify_candidate(repo=args.repo,evidence=args.evidence,
                                     reviewed_sha256=args.reviewed_sha)
    plan=campaign.fresh_exact_execute_plan(repo=args.repo,evidence=args.evidence,
        work=args.work,reviewed_sha256=args.reviewed_sha)
    if (plan.get('native')!='NOT_RUN' or plan.get('require_new_external_root_approval_and_once') is not True
            or freeze.get('fresh_runtime_binding',{}).get('schema')!=binding.SCHEMA_V2
            or args.work.exists() or (args.evidence/'native-setup-run').exists()):
        raise RuntimeError('check-only verifier produced a native effect')
    print(json.dumps({'status':'EXTERNAL_CHECK_ONLY_PASS_ONCE_ABSENT_NATIVE_NOT_RUN',
        'caller':'EXTERNAL_NO_SITE_SCRIPT','runtime_schema':binding.SCHEMA_V2,
        'candidate_sha256':args.reviewed_sha,'native':'NOT_RUN'}))
else:
    try:
        campaign.verify_candidate(repo=args.repo,evidence=args.evidence,
                                  reviewed_sha256=args.reviewed_sha)
    except binding.BindingError as exc:
        if 'identity differs from immutable binding' not in str(exc):
            raise
        print(json.dumps({'status':'EXPECTED_WRONG_RUNTIME_REJECTED',
                          'mode':args.mode,'error_type':type(exc).__name__,
                          'native':'NOT_RUN'}))
    else:
        raise RuntimeError('wrong runtime path/hash was accepted')
''')
    results={}
    for mode,evidence_path,reviewed_sha in [
        ('check-only',evidence,freeze['candidate_sha256']),
        ('negative-path',bad_evidence['path'][0],bad_evidence['path'][1]),
        ('negative-hash',bad_evidence['hash'][0],bad_evidence['hash'][1])]:
        result=subprocess.run([str(frontend),'-S',str(entry),'--repo',str(repo),
            '--packages',str(packages),'--evidence',str(evidence_path),'--work',str(work),
            '--reviewed-sha',reviewed_sha,'--mode',mode],cwd=repo,env=env,
            text=True,capture_output=True,timeout=180)
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        results[mode]=json.loads(result.stdout)
    assert results['check-only']['status']=='EXTERNAL_CHECK_ONLY_PASS_ONCE_ABSENT_NATIVE_NOT_RUN'
    assert results['negative-path']['status']==results['negative-hash']['status']=='EXPECTED_WRONG_RUNTIME_REJECTED'
    assert not work.exists() and not (root/'W24_ONCE.json').exists()
    assert not (evidence/'native-setup-run').exists()
    # Preserve the exact non-native seam evidence under /private/tmp for Main's review.
    print(json.dumps({'test_root':str(root),'candidate_sha256':freeze['candidate_sha256'],
        'runtime_binding_sha256':packet['binding_sha256'],'external_check_only':results['check-only'],
        'wrong_runtime_path':results['negative-path'],'wrong_runtime_hash':results['negative-hash'],
        'once':'ABSENT','native':'NOT_RUN'},sort_keys=True))

@pytest.fixture
def case(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix='cv3-bind-', dir='/private/tmp')).resolve()
    root.chmod(0o700)
    repo = root/'repo'; repo.mkdir()
    for directory in ('tools','tests','comsol_mcp'):
        (repo/directory).mkdir()
    (repo/'tools/x.py').write_text('pass\n'); (repo/'pyproject.toml').write_text('[project]\n')
    # Only installation observation is synthetic; ownership, hashes, schema,
    # source closure, paths and production prepare/execute gates run for real.
    observed = {"python":{"entry":"/fresh/python","site_packages":"/fresh/site-packages","no_site":False},
                "comsol":{"root":"/fresh/comsol","version":"6.4.0.293"}, "jdk":{"home":"/fresh/jdk","version":"11"}}
    monkeypatch.setattr(binding,'observe',lambda *a,**k:copy.deepcopy(observed))
    authority = root/'authority.json'
    packet=binding.create(repo=repo,authority=authority,activity_id=str(uuid4()),python=Path('/fresh/python'),
                          packages=Path('/fresh/site-packages'),install=Path('/fresh/comsol'),jdk=Path('/fresh/jdk'),budget=w24.SETUP_BUDGET)
    yield root,repo,authority,packet,observed
    shutil.rmtree(root)

@pytest.mark.parametrize('fault',['python','install','jdk','source','budget','namespace','dropfield','activity','oldmode','extra','hash'])
def test_binding_rejects_drift_before_popen(case,monkeypatch,fault):
    root,repo,authority,packet,observed=case
    births=[];monkeypatch.setattr(w24.subprocess,'Popen',lambda *a,**k:births.append(a))
    if fault in {'python','install','jdk'}:
        key={'python':'python','install':'comsol','jdk':'jdk'}[fault]; observed[key]['version']='wrong'
    elif fault=='source':(repo/'tools/x.py').write_text('changed\n')
    else:
        packet=copy.deepcopy(packet)
        if fault=='budget':packet['budget']={}
        elif fault=='namespace':packet['namespace']='/private/tmp'
        elif fault=='dropfield':packet.pop('python')
        elif fault=='activity':packet['activity_id']=w23.ACTIVITY_ID
        elif fault=='oldmode':packet['schema']='W24_CURRENT11_INPUT_ATTACHMENT_V1'
        elif fault=='extra':packet['caller_trusted_code']=True
        elif fault=='hash':packet['binding_sha256']='0'*64
        if fault not in {'dropfield','extra','hash'}:packet['binding_sha256']=binding.digest({k:v for k,v in packet.items() if k!='binding_sha256'})
        authority.write_text(json.dumps(packet))
    with pytest.raises((binding.BindingError,ValueError,KeyError)):
        w24.prepare_candidate(repo=repo,evidence=root/'evidence',runtime_authority=authority)
    assert not births and not (root/'evidence').exists()

def test_closed_json_and_authority_symlink(case):
    root,repo,authority,packet,_=case
    authority.write_text('{"schema":"x","schema":"y"}')
    with pytest.raises(binding.BindingError,match='duplicate'):
        binding.load(authority,repo=repo,budget=w24.SETUP_BUDGET)
    link=root/'alias';link.symlink_to(authority)
    with pytest.raises(binding.BindingError):binding.read_owned(link)

def test_fresh_prepare_verify_runtime_and_exact_work(case,monkeypatch):
    root,repo,authority,packet,_=case
    monkeypatch.setattr(w24,'configure_archive_python',lambda *a,**k:{'site_processing_disabled':True})
    monkeypatch.setattr(w24,'preflight_campaign_dependencies',lambda *a,**k:{'status':'TEST_ONLY'})
    monkeypatch.setattr(w24,'_compile_sources',lambda *a,**k:{'status':'COMPILE_PASS','comsol_version':'6.4.0.293','jdk_version':'11','classpath_manifest_sha256':'a','classpath_jar_count':1,'classpath_jar_content_fingerprint_sha256':'b'})
    evidence=root/'evidence'
    freeze=w24.prepare_candidate(repo=repo,evidence=evidence,runtime_authority=authority)
    assert freeze['fresh_runtime_binding']==packet and freeze['scientific_status']=='NOT_RUN'
    assert w24.verify_candidate(repo=repo,evidence=evidence,reviewed_sha256=freeze['candidate_sha256'])==freeze
    with pytest.raises(binding.BindingError):binding.scoped_path(packet,root/'elsewhere'/'work',absent=True)
    (repo/'tools/x.py').write_text('drift')
    births=[];monkeypatch.setattr(w24.subprocess,'Popen',lambda *a,**k:births.append(a))
    with pytest.raises(binding.BindingError):w24.execute_candidate(repo=repo,evidence=evidence,work=root/'work',reviewed_sha256=freeze['candidate_sha256'])
    assert not births and not (root/'work').exists()

def test_w23_fresh_argv_approval_and_literal_identity(case):
    root,repo,authority,packet,_=case
    packet=copy.deepcopy(packet); packet['budget']=w23.fresh_budget()
    approval_path=root/'ROOT_APPROVAL_ACTIVITY_FRESH.json'
    freeze={'fresh_runtime_binding':packet,'source_manifest_sha256':'s','read_only_call_plan':{},'approval_path_expected':str(approval_path)}
    argv=w23._native_argv(root/'freeze.json','f',approval_path,w23.SOURCE_MODEL,root/'work',root/'native',packet)
    assert '/fresh/python' in argv and str(w23.EXPECTED_PYTHON) not in argv
    approval={'status':'ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE','activity_id':w23._activity(freeze),'candidate_id':w23.CANDIDATE_ID,'candidate_revision':w23.CANDIDATE_REVISION,'freeze_sha256':'f','source_manifest_sha256':'s','native_argv_sha256':w23.sha256_json(argv),'runtime_binding_sha256':packet['binding_sha256'],'max_owned_server_processes':1,'max_worker_sessions':1,'max_gui_processes':0,'max_study_run_calls':0,'max_solver_run_calls':0,'max_seconds_from_server_birth':1800,'cleanup_reserve_seconds':120,'retry_unknown_or_nonterminal_worker_request':False,'read_only_call_plan_sha256':w23.sha256_json({}),'exact_native_invocation':{'argv':argv,'cwd':str(w23.REPO),'environment':w23._native_environment(packet)}}
    w23._validate_approval(approval,freeze,'f',argv)
    script=root/'fresh-literal.zsh'
    header='\n'.join(['#!/bin/zsh','test -f '+shlex.quote(str(approval_path)),
                       'test ! -L '+shlex.quote(str(approval_path)),'cd '+shlex.quote(str(w23.REPO))])+'\n'
    script.write_text(header+'exec '+shlex.join(argv)+' > '+shlex.quote(str(root/'stdout'))+' 2> '+shlex.quote(str(root/'stderr'))+'\n')
    metadata={'exact_native_invocation':approval['exact_native_invocation']}
    assert w23._validate_literal_capture_entry(script,metadata,approval,freeze,'f')['native']=='NOT_RUN'
    bad=copy.deepcopy(approval)
    bad['exact_native_invocation']['environment']['COMSOL_SERVER_MCP_HOME']=str(w23.REPO/'comsol-server-home')
    with pytest.raises(RuntimeError,match='exact_native_invocation.environment'):
        w23._validate_approval(bad,freeze,'f',argv)
    wrong_argv=[('COMSOL_SERVER_MCP_HOME='+str(w23.REPO/'comsol-server-home')) if token.startswith('COMSOL_SERVER_MCP_HOME=') else token for token in argv]
    script.write_text(header+'exec '+shlex.join(wrong_argv)+' > '+shlex.quote(str(root/'stdout'))+' 2> '+shlex.quote(str(root/'stderr'))+'\n')
    with pytest.raises(RuntimeError,match='literal script argv differs'):
        w23._validate_literal_capture_entry(script,metadata,approval,freeze,'f')
    for key in ('activity_id','runtime_binding_sha256','native_argv_sha256'):
        bad=dict(approval);bad[key]='old'
        with pytest.raises(RuntimeError):w23._validate_approval(bad,freeze,'f',argv)


def test_w23_fresh_real_execute_prebirth_reaches_only_sentinel(monkeypatch):
    """Actual SDK/source/MPH validators; synthetic approval never starts engine."""
    seed=os.environ.get('W23_OFFLINE_CLASS_TEST_SEED')
    if not seed:
        pytest.skip('actual SDK seed required; no fabricated class receipt')
    root=Path(tempfile.mkdtemp(prefix='cv3-w23-fresh-test-',dir='/private/tmp')).resolve();root.chmod(0o700)
    try:
        python=Path(os.sys.executable)
        packet=binding.create(repo=w23.REPO,authority=root/'runtime.json',activity_id=str(uuid4()),python=python,
                              packages=python.parent.parent/'lib/python3.12/site-packages',install=w23.science.INSTALL_ROOT,
                              jdk=w23.science.JAVA11,budget=w23.fresh_budget())
        candidate=root/'prepare';candidate.mkdir()
        shutil.copytree(seed,candidate/'offline_gates/compile')
        compile_receipt=json.loads(Path(os.environ['W23_OFFLINE_CLASS_TEST_RECEIPT']).read_text())
        source=w23._source_files();manifest=w23.science._write_source_manifest_artifact(candidate,source)
        ap=candidate/'SYNTHETIC_APPROVAL_ZERO_NATIVE_SENTINEL.json';fp=candidate/'freeze.json';work=root/'work';evidence=root/'native'
        freeze={'status':'FROZEN_AWAITING_ROOT_APPROVAL','activity_id':'w23-saved-mph-zero-solve-readback-'+packet['activity_id'],
                'candidate_id':w23.CANDIDATE_ID,'candidate_revision':w23.CANDIDATE_REVISION,'fresh_runtime_binding':packet,
                'source_files':source,'source_manifest_sha256':w23._source_digest(source),'source_manifest_artifact':manifest,
                'work_path':str(work),'execution_evidence_path':str(evidence),'approval_path_expected':str(ap),'read_only_call_plan':{},
                'offline_preflight':{'status':'PASS_ALL_OFFLINE_GATES','compile':compile_receipt}}
        w23.write_json(fp,freeze);sha=w23.sha256_file(fp);argv=w23._native_argv(fp,sha,ap,w23.SOURCE_MODEL,work,evidence,packet)
        approval={'status':'ROOT_APPROVED_FOR_EXACT_FROZEN_CANDIDATE','activity_id':freeze['activity_id'],'candidate_id':w23.CANDIDATE_ID,
                  'candidate_revision':w23.CANDIDATE_REVISION,'freeze_sha256':sha,'source_manifest_sha256':freeze['source_manifest_sha256'],
                  'native_argv_sha256':w23.sha256_json(argv),'runtime_binding_sha256':packet['binding_sha256'],
                  'max_owned_server_processes':1,'max_worker_sessions':1,'max_gui_processes':0,'max_study_run_calls':0,
                  'max_solver_run_calls':0,'max_seconds_from_server_birth':1800,'cleanup_reserve_seconds':120,
                  'retry_unknown_or_nonterminal_worker_request':False,'read_only_call_plan_sha256':w23.sha256_json({}),
                  'exact_native_invocation':{'argv':argv,'cwd':str(w23.REPO),'environment':w23._native_environment(packet)},
                  'issued_by':'SYNTHETIC_UNIT_TEST_ONLY_NATIVE_BOUNDARY_REPLACED'}
        w23.write_json(ap,approval)
        # A genuine fresh CLI process binds home before any production imports.
        # In-process rebinding would hide the exact late-import defect.
        env=dict(os.environ, TMPDIR='/private/tmp', PYTHONPATH=str(w23.REPO),
                 COMSOL_SERVER_MCP_HOME=w23._native_environment(packet)['COMSOL_SERVER_MCP_HOME'],
                 PYTHONDONTWRITEBYTECODE='1')
        for key in ('CODEX_NETWORK_GUARD_LOG','CODEX_NETWORK_GUARD_LABEL','CODEX_NETWORK_GUARD_RUN_ID'):
            env.pop(key,None)
        program = """
import json,subprocess,sys
from pathlib import Path
from tools import run_native_w23_saved_mph_diagnostic as w23
fp,ap,work,evidence=map(Path,sys.argv[1:5]);sha=sys.argv[5]
from comsol_mcp import _server
w23.science._load_helper_refs()
handoffs=[];births=[]
original_popen=subprocess.Popen
def fenced_popen(command,*args,**kwargs):
    if command == ['/sbin/mount']:
        return original_popen(command,*args,**kwargs)
    births.append(command)
    raise AssertionError('native birth blocked by software sentinel')
subprocess.Popen=fenced_popen
w23._execute_native_campaign=lambda *args:handoffs.append(args) or {'native':'NOT_RUN'}
assert w23.execute(fp,sha,ap,w23.SOURCE_MODEL,work,evidence)=={'native':'NOT_RUN'}
assert len(handoffs)==1 and not births and not work.exists() and not evidence.exists()
approval=json.loads(ap.read_text());approval['runtime_binding_sha256']='0'*64;w23.write_json(ap,approval)
try:
    w23.execute(fp,sha,ap,w23.SOURCE_MODEL,work,evidence)
except RuntimeError as exc:
    assert 'approval mismatch' in str(exc)
else:
    raise AssertionError('mismatched Root approval accepted')
assert len(handoffs)==1 and not births
print(json.dumps({'handoffs':len(handoffs),'births':births,'native':'NOT_RUN'}))
"""
        result=subprocess.run([sys.executable,'-c',program,str(fp),str(ap),str(work),str(evidence),sha],
                              cwd=w23.REPO,env=env,text=True,capture_output=True,timeout=30)
        assert result.returncode==0,result.stdout+result.stderr
        assert json.loads(result.stdout)=={'handoffs':1,'births':[],'native':'NOT_RUN'}
    finally:
        shutil.rmtree(root)
