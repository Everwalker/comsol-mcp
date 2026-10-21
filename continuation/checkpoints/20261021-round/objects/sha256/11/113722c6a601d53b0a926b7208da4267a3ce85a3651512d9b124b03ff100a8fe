"""Synthetic portable interface controls; never current build/native acceptance."""
import base64,csv,hashlib,io,json,uuid,zipfile
from pathlib import Path
import pytest
from tools import w26_sealed_data as data,w26_runtime_actor as actor,w26_sealed_data_r6 as data_r6
from tools.w26_prepare_current_inputs import _helpers

@pytest.fixture(params=['current175-r4','current175-r5','current175-r6'])
def current_product(monkeypatch,request):
    source_profile=request.param
    h=_helpers();root=Path('/private/tmp')/('cvw26-application-'+uuid.uuid4().hex[:8]);root.mkdir(mode=0o700)
    def put(path,value):
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        return h.publish(path,value)
    put(root/'FIRST_RECORD.json',{'root_identity':{'device':root.stat().st_dev,'inode':root.stat().st_ino}})
    snapshot=root/'source-input';snapshot.mkdir(mode=0o700)
    files={'README.md':b'synthetic interface fixture','LICENSE':b'synthetic fixture','pyproject.toml':b'[project]\nname="comsol-mcp"\nversion="0.1.10"\n','comsol_mcp/_server.py':b'VERSION="0.1.10"\n','comsol_mcp/_state_backup.py':b'# synthetic backup source, never executed\n'}
    files.update({'comsol_mcp/fixture_%03d.py'%i:b'# synthetic no code\n' for i in range(170)})
    rows={}
    for rel,raw in files.items():
        path=snapshot/rel;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);h.copy_blob(path,raw);rows[rel]=h.raw_file(path)[1]
    lock=root/'fixture.lock.json';lock_record=put(lock,{'schema':'w26-application-source-closure/1','profile':source_profile,'member_count':175,'package_count':172,'root_count':3,'members':{rel:{'bytes':r['bytes'],'sha256':r['sha256']} for rel,r in rows.items()}})
    source_records=actor.source_fingerprints(actor.APP_LIFECYCLE)
    for path in [Path(data.__file__).with_name('w26_target_inputs.py'),data.CURRENT_APPLICATION_LOCK,data.CURRENT_APPLICATION_R5_LOCK,data.CURRENT_APPLICATION_R6_LOCK]:
        record=h.raw_file(path)[1];source_records[record['path']]=record
    loader_source_closure=data.current_application_loader_closure(source_records)
    lock_attribute={'current175-r4':'CURRENT_APPLICATION_LOCK','current175-r5':'CURRENT_APPLICATION_R5_LOCK','current175-r6':'CURRENT_APPLICATION_R6_LOCK'}[source_profile]
    monkeypatch.setattr(data,lock_attribute,lock) # Only this explicitly synthetic exact fixture SHA/path.
    prepared=put(root/'APP_INPUT_BINDING_app6c.json',{'source':{'source_profile':source_profile,'original_source':str(snapshot),'original_members':rows,'snapshot_source':str(snapshot),'snapshot_members':rows,'source_lock':lock_record}})
    candidate=put(root/'ACTOR_CANDIDATE_app6c.json',{'prepared':prepared})
    review=put(root/'MAIN_SOURCE_REVIEW.json',{'kind':'SYNTHETIC_SOFTWARE_NOT_ROOT_APPROVAL'})
    binding=put(root/'MAIN_ONCE_BINDING.json',{'status':'MAIN_APPROVED_ONCE_W26_APPLICATION_BUNDLE','root':str(root),'candidate_filename':Path(candidate['path']).name,'candidate_sha256':candidate['sha256'],'nonce':'synthetic-not-native','review_sha256':review['sha256']})
    once=put(root/'UNIT_ONCE_GUARD.json',{'activity_kind':actor.APP_BUNDLE,'nonce':'synthetic-not-native','binding_first_record':binding})
    payload={rel:raw for rel,raw in files.items() if rel.startswith('comsol_mcp/')};prefix='comsol_mcp-0.1.10.dist-info/'
    payload[prefix+'METADATA']=b'Metadata-Version: 2.4\nName: comsol-mcp\nVersion: 0.1.10\nRequires-Python: >=3.12\n'
    payload[prefix+'WHEEL']=b'Wheel-Version: 1.0\nGenerator: synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    stream=io.StringIO();writer=csv.writer(stream)
    for name,raw in payload.items():writer.writerow([name,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode(),str(len(raw))])
    writer.writerow([prefix+'RECORD','','']);payload[prefix+'RECORD']=stream.getvalue().encode();zbytes=io.BytesIO()
    with zipfile.ZipFile(zbytes,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in payload.items():archive.writestr(name,raw)
    wheel=root/'appwheel/comsol_mcp-0.1.10-py3-none-any.whl';wheel.parent.mkdir(mode=0o700);h.copy_blob(wheel,zbytes.getvalue());wheel_record=h.raw_file(wheel)[1]
    commands=[]
    for i,label in enumerate(['builder-check','application-build']):
        row={'status':'PASS','rc':0,'owned_group_absent':True,'owned_process_terminal':True,'pid':100+i,'birth':{'status':'OBSERVED','pid':100+i},'result_name':label+'-synthetic.result.json'}
        put(root/'logs'/row['result_name'],{k:v for k,v in row.items() if k!='result_name'});commands.append(row)
        probe=put(root/'evidence'/('PROBE_'+label+'.json'),{'status':'PASS_ACTUAL_EPERM','errno':1})
        put(root/'evidence'/('EXEC_'+label+'.json'),{'same_original_pid':100+i,'inherited_group':100+i,'extra_processes':0,'network_probe_first_record':probe})
    result=put(root/'APPLICATION_BUNDLE_RESULT.json',{'status':'PASS_CURRENT_APPLICATION_BUNDLE_SCOPED_NOT_RELEASE','version':'0.1.10','target':'macos_arm64','build':{'source_file_count':175,'sha256':wheel_record['sha256'],'bytes':wheel_record['bytes']},'commands':commands,'outputs':[wheel_record],'COMSOL_JVM_GUI':'NOT_RUN'})
    profile={'schema':'w26-current-installed-application/1','source_profile':source_profile,'root':str(root),'result':result,'candidate':candidate,'once':once,'loader_source_closure':loader_source_closure}
    monkeypatch.setattr(actor.subprocess,'Popen',lambda *a,**kw:pytest.fail('input validation must have zero children'))
    return root,profile

def _rewrite_bound_prepared(root,profile,mutate):
    """Rebind a synthetic producer chain after a prepared-input mutation."""
    h=_helpers();candidate_path=root/'ACTOR_CANDIDATE_app6c.json';candidate=json.loads(h.raw_file(candidate_path,expected=profile['candidate'])[0])
    prepared_path=Path(candidate['prepared']['path']);prepared=json.loads(h.raw_file(prepared_path,expected=candidate['prepared'])[0]);mutate(prepared['source'])
    prepared_path.write_text(json.dumps(prepared,sort_keys=True,separators=(',',':')));candidate['prepared']=h.raw_file(prepared_path)[1]
    candidate_path.write_text(json.dumps(candidate,sort_keys=True,separators=(',',':')));profile['candidate']=h.raw_file(candidate_path)[1]
    binding_path=root/'MAIN_ONCE_BINDING.json';binding=json.loads(h.raw_file(binding_path)[0]);binding['candidate_sha256']=profile['candidate']['sha256']
    binding_path.write_text(json.dumps(binding,sort_keys=True,separators=(',',':')));binding_record=h.raw_file(binding_path)[1]
    once_path=root/'UNIT_ONCE_GUARD.json';once=json.loads(h.raw_file(once_path)[0]);once['binding_first_record']=binding_record
    once_path.write_text(json.dumps(once,sort_keys=True,separators=(',',':')));profile['once']=h.raw_file(once_path)[1]

def test_current_profile_full_payload_and_backup_binding(current_product):
    root,profile=current_product;observed=data.current_application_profile(profile)
    assert observed['wheel']['path']==str(root/'appwheel/comsol_mcp-0.1.10-py3-none-any.whl')
    assert observed['backup_source_sha256']==hashlib.sha256((root/'source-input/comsol_mcp/_state_backup.py').read_bytes()).hexdigest()
    assert data.backup_producer_sha({})==data.BACKUP_PRODUCER_SHA
    assert data.backup_producer_sha({'current_application_profile':profile})==observed['backup_source_sha256']

@pytest.mark.parametrize('field,value',[('source_profile','current175-r3'),('schema','caller-claimed'),('root','/private/tmp/cvw26-application-not-owned')])
def test_current_profile_closed_selection(current_product,field,value):
    _,profile=current_product;profile[field]=value
    with pytest.raises(RuntimeError):data.current_application_profile(profile)

def test_current_profile_exact_source_lock_path(current_product):
    root,profile=current_product
    _rewrite_bound_prepared(root,profile,lambda source:source['source_lock'].__setitem__('path',str(root/'caller-selected.lock.json')))
    with pytest.raises(RuntimeError,match='exact source lock path'):data.current_application_profile(profile)

def test_current_profile_exact_source_lock_profile(current_product):
    root,profile=current_product;h=_helpers();lock_path=Path(profile['root'])/'fixture.lock.json';lock=json.loads(lock_path.read_text())
    lock['profile']='current175-r4' if profile['source_profile']=='current175-r5' else 'current175-r5'
    lock_path.write_text(json.dumps(lock,sort_keys=True,separators=(',',':')));lock_record=h.raw_file(lock_path)[1]
    _rewrite_bound_prepared(root,profile,lambda source:source.__setitem__('source_lock',lock_record))
    with pytest.raises(RuntimeError,match='complete175 membership'):data.current_application_profile(profile)

@pytest.mark.parametrize('case',['failed','bad-version','live-group','wrong-once','wrong-pid','changed-payload','wrong-record-root'])
def test_current_profile_actual_producer_tamper_refused(current_product,case):
    root,profile=current_product;h=_helpers()
    if case=='changed-payload':(root/'source-input/comsol_mcp/fixture_000.py').write_bytes(b'tamper')
    elif case=='wrong-record-root':profile['result']['path']=str(root/'foreign.json')
    else:
        path=root/('UNIT_ONCE_GUARD.json' if case=='wrong-once' else 'evidence/EXEC_builder-check.json' if case=='wrong-pid' else 'APPLICATION_BUNDLE_RESULT.json');value=json.loads(path.read_bytes())
        if case=='failed':value['status']='TERMINAL_FAIL'
        elif case=='bad-version':value['version']='0.1.9'
        elif case=='live-group':value['commands'][0]['owned_group_absent']=False
        elif case=='wrong-once':value['nonce']='foreign'
        elif case=='wrong-pid':value['same_original_pid']=999
        path.write_text(json.dumps(value));record=h.raw_file(path)[1]
        if case!='wrong-pid':profile['once' if case=='wrong-once' else 'result']=record
    with pytest.raises(RuntimeError):data.current_application_profile(profile)


@pytest.mark.parametrize("current_product", ["current175-r6"], indirect=True)
def test_r6_profile_uses_main_bound_app_candidate_filename(current_product):
    root,profile=current_product;h=_helpers()
    first=json.loads(h.raw_file(root/'FIRST_RECORD.json')[0])
    old_candidate=Path(profile['candidate']['path'])
    candidate=json.loads(h.raw_file(old_candidate,expected=profile['candidate'])[0])
    candidate.update(status='READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED',
                     root_identity=first['root_identity'],native_Popen=0)
    target=root/'ACTOR_CANDIDATE_app6e.json'
    target.write_text(json.dumps(candidate,sort_keys=True,separators=(',',':')))
    old_candidate.unlink()
    candidate_record=h.raw_file(target)[1]
    review_path=root/'MAIN_SOURCE_REVIEW.json'
    review_path.write_text(json.dumps({'status':'PASS_W26_APPLICATION_BUNDLE_SOURCE_FOR_EXACT_CANDIDATE',
                                      'candidate_sha256':candidate_record['sha256']},sort_keys=True,separators=(',',':')))
    review_record=h.raw_file(review_path)[1]
    binding_path=root/'MAIN_ONCE_BINDING.json';binding=json.loads(h.raw_file(binding_path)[0])
    binding.update(candidate_filename=target.name,candidate_sha256=candidate_record['sha256'],
                   root_identity=first['root_identity'],review_sha256=review_record['sha256'])
    binding_path.write_text(json.dumps(binding,sort_keys=True,separators=(',',':')))
    binding_record=h.raw_file(binding_path)[1]
    once_path=root/'UNIT_ONCE_GUARD.json';once=json.loads(h.raw_file(once_path)[0]);once['binding_first_record']=binding_record
    once_path.write_text(json.dumps(once,sort_keys=True,separators=(',',':')))
    profile['candidate']=candidate_record;profile['once']=h.raw_file(once_path)[1]
    verified=data_r6.current_application_profile(profile,data)
    assert verified['wheel']['path']==str(root/'appwheel/comsol_mcp-0.1.10-py3-none-any.whl')
    assert verified['candidate']['path']==str(target)


@pytest.mark.parametrize("current_product", ["current175-r6"], indirect=True)
def test_r6_profile_rejects_unbound_candidate_filename(current_product):
    root,profile=current_product;h=_helpers()
    first=json.loads(h.raw_file(root/'FIRST_RECORD.json')[0])
    target=root/'ACTOR_CANDIDATE_app6e.json'
    candidate=json.loads(h.raw_file(Path(profile['candidate']['path']),expected=profile['candidate'])[0])
    candidate.update(status='READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED',
                     root_identity=first['root_identity'],native_Popen=0)
    target.write_text(json.dumps(candidate,sort_keys=True,separators=(',',':')))
    old_candidate=Path(profile['candidate']['path']);old_candidate.unlink()
    profile['candidate']=h.raw_file(target)[1]
    binding_path=root/'MAIN_ONCE_BINDING.json';binding=json.loads(h.raw_file(binding_path)[0])
    binding['candidate_filename']='ACTOR_CANDIDATE_app6c.json'
    binding_path.write_text(json.dumps(binding,sort_keys=True,separators=(',',':')))
    binding_record=h.raw_file(binding_path)[1]
    once_path=root/'UNIT_ONCE_GUARD.json';once=json.loads(h.raw_file(once_path)[0]);once['binding_first_record']=binding_record
    once_path.write_text(json.dumps(once,sort_keys=True,separators=(',',':')));profile['once']=h.raw_file(once_path)[1]
    with pytest.raises((RuntimeError,FileNotFoundError)):
        data_r6.current_application_profile(profile,data)
