from pathlib import Path
import os,sys,stat,hashlib,json,ast,struct,difflib,xml.etree.ElementTree as ET
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');C=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r15';O=Path(__file__).parent;sys.path.insert(0,str(C))
from tools import run_native_w23_saved_mph_diagnostic as runner
refs={}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def record(p):return dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p))
def collect(x):
 if isinstance(x,dict):
  if isinstance(x.get('path'),str) and Path(x['path']).is_absolute() and 'bytes' in x and 'sha256' in x:
   old=refs.get(x['path']);assert old is None or (old['bytes'],old['sha256'])==(x['bytes'],x['sha256']);refs[x['path']]=x
  for v in x.values():collect(v)
 elif isinstance(x,list):
  for v in x:collect(v)
def checked(row):
 p=Path(row['path']);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_nlink==1 and not stat.S_IMODE(s.st_mode)&0o022,str(p)
  h=hashlib.sha256();size=0
  while raw:=os.read(fd,1048576):h.update(raw);size+=len(raw)
  fields=lambda st:(st.st_dev,st.st_ino,st.st_uid,st.st_nlink,st.st_mode,st.st_size)
  assert fields(s)==fields(os.fstat(fd))==fields(p.lstat()) and size==row['bytes'] and h.hexdigest()==row['sha256'],str(p)
  return dict(path=str(p),bytes=size,sha256=h.hexdigest(),device=s.st_dev,inode=s.st_ino,uid=s.st_uid,nlink=s.st_nlink,mode=stat.S_IMODE(s.st_mode))
 finally:os.close(fd)
def save(name,obj):
 fd=os.open(O/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w') as out:out.write(json.dumps(obj,indent=2)+'\n')
bindPath=C/'root-review-packet/final3/FINAL_SOURCE_SOFTWARE_AND_FIXED_ENTRY_BINDING_ACTIVITY022_R15.json';assert sha(bindPath)=='d963166d639549a7102a34db624428171a2b9dff345da4ad84cfc9e1022855b0';binding=json.loads(bindPath.read_bytes());collect(binding);collect(record(bindPath))
packetPath=Path(binding['wholepacket']['path']);assert sha(packetPath)=='777cc9d4b2ed5febf012d93930655728666ac78c2e6a2c0b8d6c51166206af02';p=json.loads(packetPath.read_bytes());collect(p)
F=Path(binding['freeze']['path']);assert sha(F)=='dcc0165aee008150853700e58b3f27281dc067444f2f751dad3e9bc97087d67b';f=json.loads(F.read_bytes());collect(f)
assert len(f['source_files'])==426 and runner._source_files()==f['source_files']==p['current_source_files']
syntax=[k for k in f['source_files'] if k.endswith('.py')];assert len(syntax)==379
for rel in syntax:ast.parse(Path(f['source_files'][rel]['path']).read_bytes(),filename=rel)
R14=C.parent/'w23-saved-mph-science-readback-20261013-candidate20-r14';F13=R14/'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-022-saved-mph-zero-solve-candidate20-r14-final6/freeze.json';assert sha(F13)=='1bda0d14e6ce99ed7377c3cd51899b1f1268c80da3049c557057a8373784a92f';parent=json.loads(F13.read_bytes());collect(record(F13));collect(parent['source_files'])
same=[];modified=[];added=[]
for rel,row in f['source_files'].items():
 old=parent['source_files'].get(rel)
 if old is None:added.append(rel)
 elif (old['bytes'],old['sha256'])==(row['bytes'],row['sha256']):same.append(rel)
 else:modified.append(rel)
assert (len(same),len(modified),len(added))==(414,10,2) and set(parent['source_files'])<=set(f['source_files']) and sorted(added)==sorted(p['added_sources']) and sorted(modified)==sorted(r['relative'] for r in p['modified_sources'])
diffs=[]
for row in p['modified_sources']:
 rel=row['relative'];before=Path(row['before']['path']).read_text().splitlines(True);after=Path(row['after']['path']).read_text().splitlines(True)
 actual=''.join(difflib.unified_diff(before,after,fromfile=row['before']['path'],tofile=row['after']['path']));assert actual==Path(row['diff']['path']).read_text(),rel
 dest=O/(rel.replace('/','__')+'.independent.diff')
 if not dest.exists():
  fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as out:out.write(actual)
 else:assert dest.read_text()==actual
 diffs.append({'relative':rel,'exact':True,'independent':record(dest)})
protected=['comsol_mcp/_execution_service.py','comsol_mcp/_execution_contract.py','comsol_mcp/_state.py','comsol_mcp/_java_worker.py','comsol_mcp/_managed_backend.py','comsol_mcp/_control_daemon.py','comsol_mcp/_operation_store.py','comsol_mcp/_solution_binding.py','comsol_mcp/worker_java/PersistentComsolWorker.java','tools/java/NativeW23TEScienceFixture.java']
for rel in protected:assert (C/rel).read_bytes()==(R14/rel).read_bytes()
classPath=C/'root-review-packet/final3/CURRENT19_REAL_RECOMPILED_CLASSES_ALL_BYTEEXACT_R14.json';classification=json.loads(classPath.read_bytes());collect(record(classPath));collect(classification);classes=[]
for row in classification['classes']:
 a=Path(row['current']['path']).read_bytes();assert a[:4]==b'\xca\xfe\xba\xbe' and struct.unpack('>H',a[6:8])[0]==55 and row['byteexact_r13'] is True
 prior=R14/'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-021-saved-mph-zero-solve-candidate20-r14-final1/offline_gates/compile'/row['group']/'classes'/row['class'];collect(record(prior));assert a==prior.read_bytes()
 assert row['compiled_group_receipt']['source_sha256']==f['source_files'][str(Path(row['java_source']).relative_to(C))]['sha256']
 classes.append({'class':row['class'],'group':row['group'],'major':55,'exactR14':True,'sha256':hashlib.sha256(a).hexdigest()})
assert len(classes)==19
validated=runner._validate_freeze(F,sha(F));material=runner.science._check_offline_receipts(F,validated);assert sum(x['class_count'] for x in material['compiled_class_outputs'].values())==19
current=p['whole_current_software']['focused_tests'];raw=list(ET.parse(current['junit_path']).iter('testcase'));assert len(raw)==764 and current['returncode']==0 and not any(list(row.iter(tag)) for row in raw for tag in ('failure','error','skipped'))
currentguard=[json.loads(x) for x in Path(current['network_guard']['path']).read_text().splitlines()];assert sum(x['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for x in currentguard)==1 and not any('DENY' in x['event'] for x in currentguard)
assert len(p['ordinary_raw_material_refs'])==529 and not p['deliberate_negative_symlink_objects']
for rel in added:
 if rel.endswith('.json'):collect(json.loads((C/rel).read_bytes()))
metaPath=Path(binding['metadata']['path']);meta=json.loads(metaPath.read_bytes());collect(meta)
blue=json.loads(Path(meta['approval_blueprint']['path']).read_bytes());pre=json.loads(Path(meta['actual_preflight_reference']['path']).read_bytes());collect(blue);collect(pre)
script=Path(meta['script']['path']);prior=R14/'root-review-packet/final6/native-capture-supplement022-003/launch-activity022-r14.zsh';collect(record(prior));translated=prior.read_text().replace(str(F13.parent),str(F.parent)).replace(str(R14),str(C)).replace('20261013-022-candidate20-r14','20261013-022-candidate20-r15').replace(sha(F13),sha(F)).replace('ROOT_APPROVAL_ACTIVITY021','ROOT_APPROVAL_ACTIVITY022').replace('native-launch-capture-activity021-001','native-launch-capture-activity022-001');entry_translation_equal=translated==script.read_text()
argv=runner._native_argv(F,sha(F),Path(f['approval_path_expected']),runner.SOURCE_MODEL,Path(f['work_path']),Path(f['execution_evidence_path']));assert argv==meta['exact_native_invocation']['argv'] and [sha(F) if x=='<freeze-sha256>' else x for x in f['native_invocation_argv']]==argv
runner._validate_approval(blue['expected_approval_fields'],f,sha(F),argv);assert blue['status']=='REFERENCE_ONLY_NOT_ROOT_APPROVAL_NOT_NATIVE_ADMISSION' and meta['new_root_approval']=='NOT_CREATED' and len(meta['new_paths_verified_absent'])==6 and not any(os.path.lexists(x) for x in meta['new_paths_verified_absent'])
assert f['input']==parent['input'] and f['limits']['max_result_at_points_calls']==8 and f['limits']['max_seconds_from_server_birth']==1800 and f['limits']['cleanup_reserve_seconds']==120
assert len(f['read_only_call_plan']['ordered_point_slots'])==8
resume=B/'repository/docs/full_project_continuation_v2/state/RESUME.json';assert sha(resume)==binding['CAS']['sha256'] and json.loads(resume.read_bytes())['active_jobs']==[];collect(record(resume))
pins=[checked(row) for row in refs.values()]
for row in pins:checked(row)
report={'result':'PASS_MECHANICAL_HASH_CLOSURE_ONLY_SEMANTIC_AND_ENTRY_FINDINGS_SEPARATE','binding':record(bindPath),'packet':record(packetPath),'freeze':record(F),'candidate':str(C),'sources':426,'AST':379,'parent424_retained':True,'same':414,'modified':10,'added':2,'exact_diffs':diffs,'protected_originals':protected,'ordinary_raw_refs':529,'sameFD_unique_refs':len(refs),'prepost_exact':True,'current764raw':{'PASS':764,'FAIL':0,'ERROR':0,'SKIP':0,'guardACTIVE':1,'guardDENY':0,'reviewer_rerun':False},'classes19':classes,'genuine_relative_material':material,'fixed_entry':{'script':record(script),'metadata':record(metaPath),'effective':'022-004 only','old021_scheme_only_fixed022_substitutions':entry_translation_equal,'reference_only_METADATA_4arg_approval_validated':True,'actual_SCRIPT_argv_review_separate':True,'six_future_paths_absent':True,'current_CAS_unchanged':True,'CAS_sha256':sha(resume)},'source_review_native_COMSOL_compiler_processprobe_signal_CAS_Git_progress_effects':0,'first_sameFD_pins':pins}
save('CLOSURE_VERIFICATION.json',report)
print(json.dumps({k:report[k] for k in ('sources','AST','same','modified','added','sameFD_unique_refs','current764raw')}))
