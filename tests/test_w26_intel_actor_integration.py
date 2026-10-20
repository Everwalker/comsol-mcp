"""Closed Intel actor wiring only; no compiler/install/native stage dispatch."""
from pathlib import Path
import time
import pytest
from tools import w26_runtime_actor as a, w26_intel_derived as d
from tools.w26_prepare_current_inputs import _helpers

@pytest.mark.parametrize('key,value', [('budget_seconds',3601),('cleanup_reserve_seconds',179),('prepare_cap_seconds',601),('max_make_parallelism',3),('sampled_rss_stop_bytes',7*1024**3),('max_output_bytes',12*1024**3),('minimum_free_bytes',7*1024**3),('budget_seconds',True)])
def test_actual_actor_intel_closed_scope(key,value):
    assert a.activity_scope(d.SCOPE)==d.SCOPE
    with pytest.raises(RuntimeError,match='closed activity'):a.activity_scope({**d.SCOPE,key:value})

def test_actual_actor_source_closure_includes_intel_not_lifecycle():
    records=a.source_fingerprints(a.INTEL_DERIVED)
    names={Path(p).name for p in records}
    assert {'w26_intel_derived.py','w26_intel_derived_stage.py','w26_dependency_stage.py','full_project_release.py','w26_runtime_sources.lock.json'}<=names
    assert 'w26_application_lifecycle.py' not in names
    for p,row in records.items():_helpers().raw_file(Path(p),expected=row)
    assert 'w26_application_sources.lock.json' in {Path(p).name for p in a.source_fingerprints(a.APP_LIFECYCLE)}

@pytest.mark.parametrize('kind,limit',[(a.INTEL_DERIVED,8*1024**3),(a.CURRENT43,12*1024**3),(a.APP_LIFECYCLE,12*1024**3)])
def test_actual_owner_output_sampling_is_kind_scoped(tmp_path,kind,limit):
    with (tmp_path/'sparse-output').open('wb') as stream:stream.truncate(9*1024**3)
    owner=a.RuntimeOwner(tmp_path,{'activity_kind':kind},token=a._OWNER_TOKEN,began=time.monotonic())
    assert owner.scope['max_output_bytes']==limit
    if kind==a.INTEL_DERIVED:
        with pytest.raises(RuntimeError,match='SAMPLED_RESOURCE_STOP'):owner.sample()
        with pytest.raises(TimeoutError):owner.remaining(1)
    else:assert owner.sample()['output_bytes']==9*1024**3
    assert owner.process is None

@pytest.mark.parametrize('token',[None,{},'pretend-main',object()])
def test_intel_cannot_mint_owner_from_json(tmp_path,token):
    with pytest.raises(RuntimeError,match='internal owner'):a.RuntimeOwner(tmp_path,d.SCOPE,token=token)
    with pytest.raises(RuntimeError,match='internal source gate'):d.execute_intel(tmp_path,{},{},{},began=time.monotonic(),admission_token=token)

def synthetic_gate(tmp_path):
    h=_helpers();root=tmp_path/'synthetic-intel-gate';root.mkdir(mode=0o700)
    source=root/'fixture.py';source.write_text('# synthetic gate source, NOT native admission\n');source.chmod(0o600)
    rows={key:h.publish(root/(key+'.json'),{'software_gate_only':True}) for key in ('prepared','plan','toolchain')}
    rows['source']=h.publish(root/'source.json',{str(source):h.raw_file(source)[1]})
    candidate={**d.SCOPE,**rows,'root_identity':h.secure_dir(root)};rec=h.publish(root/'ACTOR_CANDIDATE.json',candidate)
    review=h.publish(root/'MAIN_SOURCE_REVIEW.json',{'status':'PASS_W26_INTEL_DERIVED_WHEEL_SOURCE_FOR_EXACT_CANDIDATE','candidate_sha256':rec['sha256']})
    binding={**d.SCOPE,'status':'MAIN_APPROVED_ONCE_W26_INTEL_DERIVED_WHEEL','candidate_sha256':rec['sha256'],'root':str(root),'root_identity':candidate['root_identity'],'GUI':False,'COMSOL':False,'nonce':'synthetic-fixture-not-real-authorization','review_sha256':review['sha256']}
    return root,binding

@pytest.mark.parametrize('damage',['status','kind','scope','source','review','consumed'])
def test_intel_gate_refuses_before_dispatch(tmp_path,monkeypatch,damage):
    root,b=synthetic_gate(tmp_path);h=_helpers()
    if damage=='status':b['status']='MAIN_APPROVED_ONCE_W26_APPLICATION_LIFECYCLE'
    elif damage=='kind':b['activity_kind']=a.APP_LIFECYCLE
    elif damage=='scope':b['max_output_bytes']=12*1024**3
    elif damage=='source':(root/'fixture.py').write_text('# drift\n')
    elif damage=='review':b['review_sha256']='0'*64
    else:h.publish(root/'UNIT_ONCE_GUARD.json',{'software_consumed':True})
    rec=h.publish(root/'MAIN_ONCE_BINDING.json',b)
    monkeypatch.setattr(a.subprocess,'Popen',lambda *args,**kw:pytest.fail('birth before refusal'))
    with pytest.raises(RuntimeError):a.execute(root,root/'MAIN_ONCE_BINDING.json',rec['sha256'])

def test_intel_dispatch_uses_original_private_owner_token(tmp_path,monkeypatch):
    root,b=synthetic_gate(tmp_path);h=_helpers();rec=h.publish(root/'MAIN_ONCE_BINDING.json',b);calls=[]
    def intercept(root,candidate,binding,binding_record,*,began,admission_token):
        calls.append((admission_token,began,candidate['activity_kind']));return 'SOFTWARE_DISPATCH_ONLY'
    monkeypatch.setattr(d,'execute_intel',intercept)
    monkeypatch.setattr(a.subprocess,'Popen',lambda *args,**kw:pytest.fail('software dispatch no native birth'))
    old=a.os.umask(0o077)
    try:assert a.execute(root,root/'MAIN_ONCE_BINDING.json',rec['sha256'])=='SOFTWARE_DISPATCH_ONLY'
    finally:a.os.umask(old)
    assert len(calls)==1 and calls[0][0] is a._OWNER_TOKEN and calls[0][2]==a.INTEL_DERIVED
