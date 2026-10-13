"""Production fresh prebirth binding tests. No engine or GUI birth."""
import copy
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
from uuid import uuid4
import pytest
from tools import native_runtime_binding as binding
from tools import run_native_w24_static_shape_setup_campaign as w24
from tools import run_native_w23_saved_mph_diagnostic as w23

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
