from pathlib import Path
import sys,os,stat,json,hashlib,ast,struct,xml.etree.ElementTree as ET,difflib
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');C=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r10';O=Path(__file__).parent
sys.path.insert(0,str(C))
from tools import run_native_w23_saved_mph_diagnostic as runner
P=C/'root-review-packet/ROOT_REVIEW_PACKET_ACTIVITY018_R10.json'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(P)=='9b8942f35b75df58274fc04b14641e22e7578351c7e856a67cb01c0677ec33ea'
p=json.loads(P.read_bytes());F=Path(p['freeze']['path']);f=json.loads(F.read_bytes());assert sha(F)=='76c4a936da5f0d90354ce9f7046fc55e55c44392cbfbf8aa3ffa8cc031a70d89'
refs={}
def collect(value):
 if isinstance(value,dict):
  if isinstance(value.get('path'),str) and Path(value['path']).is_absolute() and 'bytes' in value and 'sha256' in value:
   previous=refs.get(value['path']);assert previous is None or (previous['bytes'],previous['sha256'])==(value['bytes'],value['sha256']);refs[value['path']]=value
  for child in value.values():collect(child)
 elif isinstance(value,list):
  for child in value:collect(child)
collect(p)
def checked(row):
 path=Path(row['path'])
 if path.is_symlink():
  assert path.is_relative_to(C/'prebirth-input-tests') and path.name in ('approval-symlink.json','freeze-symlink.json','source-symlink.mph')
  literal=os.readlink(path);target=Path(literal)
  expected={'approval-symlink.json':path.parent/'reference-only-native-sentinel-approval.json','freeze-symlink.json':path.parent/'freeze.json','source-symlink.mph':runner.SOURCE_MODEL}[path.name]
  assert target==expected and target.is_absolute() and not target.is_symlink()
  result=checked(dict(row,path=str(target)))
  return dict(result,path=str(path),kind='preserved_negative_symlink_not_accepted_material',literal_target=literal,target_bytes_verified_sameFD=True)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  st=os.fstat(fd);assert stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and st.st_nlink==1 and not stat.S_IMODE(st.st_mode)&0o022
  h=hashlib.sha256();size=0
  while raw:=os.read(fd,1048576):h.update(raw);size+=len(raw)
  final=os.fstat(fd);named=path.lstat();fields=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_nlink,s.st_mode,s.st_size)
  assert fields(st)==fields(final)==fields(named) and size==row['bytes'] and h.hexdigest()==row['sha256']
  return dict(path=str(path),bytes=size,sha256=h.hexdigest(),device=st.st_dev,inode=st.st_ino,mode=stat.S_IMODE(st.st_mode),uid=st.st_uid)
 finally:os.close(fd)
checked_refs=[checked(row) for row in refs.values()]
assert len(p['whole_source'])==len(f['source_files'])==404
assert runner._source_files()==f['source_files']==p['whole_source']
syntax=[]
for name,row in f['source_files'].items():
 if name.endswith('.py'):ast.parse(Path(row['path']).read_bytes(),filename=name);syntax.append(name)
assert len(syntax)==374
prep=json.loads(Path(p['builder_and_prepare_receipt'][1]['path']).read_bytes());parent_path=Path(prep['parent_failed_freeze']['path']);assert sha(parent_path)==prep['parent_failed_freeze']['sha256'];parent=json.loads(parent_path.read_bytes())
assert len(parent['source_files'])==403
same=[];mod=[];new=[]
for name,row in f['source_files'].items():
 old=parent['source_files'].get(name)
 if old is None:new.append(name)
 elif (old['bytes'],old['sha256'])==(row['bytes'],row['sha256']):same.append(name)
 else:mod.append(name)
assert sorted(same)==p['unchanged401_original_sources'] and len(same)==401 and len(mod)==2 and len(new)==1
delta_results=[]
for delta in p['delta']:
 name=delta['relative_path'];after=Path(delta['candidate']['path']).read_text().splitlines(True);before=Path(delta['parent']['path']).read_text().splitlines(True) if delta.get('parent') else []
 actual=''.join(difflib.unified_diff(before,after,fromfile='activity017-r8/'+name,tofile='activity018-r10/'+name))
 assert actual==Path(delta['diff']['path']).read_text()
 dest=O/(name.replace('/','__')+'.independent.diff');dest.write_text(actual);delta_results.append({'source':name,'exact_frozen_diff':True,'independent_diff':str(dest),'sha256':sha(dest)})
classes=[]
for item in p['actual19classes']:
 old=Path(item['original']['path']).read_bytes();newraw=Path(item['new']['path']).read_bytes();assert old==newraw and newraw[:4]==b'\xca\xfe\xba\xbe' and struct.unpack('>H',newraw[6:8])[0]==55
 classes.append({'label':item['label'],'relative':item['relative'],'bytes':len(newraw),'sha256':hashlib.sha256(newraw).hexdigest(),'major':55,'original_byte_exact':True})
assert len(classes)==19
validated=runner._validate_freeze(F,sha(F));material=runner.science._check_offline_receipts(F,validated)
assert sum(row['class_count'] for row in material['compiled_class_outputs'].values())==19 and all(Path(row['class_dir']).is_relative_to(F.parent) for row in material['compiled_class_outputs'].values())
current=f['offline_preflight']['affected_controls'];raw=list(ET.parse(current['junit']['path']).iter('testcase'));assert len(raw)==33 and not any(list(c.iter(t)) for c in raw for t in ('failure','error','skipped'))
names=[c.attrib['name'] for c in raw];assert sum(name.startswith(('test_production_execute_prebirth','test_production_cli')) for name in names)==31
guard=[json.loads(line) for line in Path(current['guard']['path']).read_text().splitlines()];active=sum(row['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for row in guard);deny=sum('DENY' in row['event'] for row in guard);assert active==1 and deny==0 and current['returncode']==0
baselines=[]
for row in p['actual31_real_material_baseline_receipts']:
 d=json.loads(Path(row['path']).read_bytes());assert d['status']=='PASS_REAL_PRODUCTION_FREEZE_AND_MATERIAL_BEFORE_TARGET_MUTATION' and d['activity_id']==runner.ACTIVITY_ID and d['revision']==runner.CANDIDATE_REVISION and d['material_gate_mocked'] is False and sum(r['class_count'] for r in d['material']['compiled_class_outputs'].values())==19
 baselines.append({'path':row['path'],'actual_material19':True,'mocked':False})
assert len(baselines)==31
negative=[]
for row in p['actual28_target_reason_negative_receipts']:
 d=json.loads(Path(row['path']).read_bytes());assert d['status']=='PASS_TARGETED_REFUSAL_AFTER_REAL_MATERIAL_GATE' and d['native_handoffs']==0 and d['material_gate_mocked'] is False and d['expected_reason'] in d['actual_reason'] and 'offline gates' not in d['actual_reason'] and 'compiler output' not in d['actual_reason']
 if d['mutation'] in {'old012','old013','old014','old015','wrong_activity','wrong_revision'}:assert d['work']!=d['expected_work']
 assert (Path(row['path']).parent/'VALID_MATERIAL_PRE_MUTATION.json').is_file()
 negative.append({k:d[k] for k in ('mutation','actual_type','expected_reason','actual_reason','native_handoffs')})
assert len(negative)==28 and len({r['mutation'] for r in negative})==28
inherit=json.loads(Path(p['original435_unaffected_narrow_inheritance']['path']).read_bytes());old_cases=list(ET.parse(inherit['original_junit']['path']).iter('testcase'));assert len(old_cases)==466 and not any(list(c.iter(t)) for c in old_cases for t in ('failure','error','skipped'))
unaffected=[c.attrib for c in old_cases if not c.attrib['name'].startswith(('test_production_execute_prebirth','test_production_cli'))]
assert unaffected==inherit['unaffected_case_identities'] and len(unaffected)==435
whole_test_bindings=0;prefix_bindings=0
for pair in inherit['test_source_bindings']:
 old=Path(pair['original']['path']).read_bytes();newraw=Path(pair['current']['path']).read_bytes()
 if 'unaffected_region_sha256' in pair:
  a=old.split(b'def _actual_execute_prebirth_fixture')[0];b=newraw.split(b'def _actual_execute_prebirth_fixture')[0];assert a==b and hashlib.sha256(a).hexdigest()==pair['unaffected_region_sha256'];prefix_bindings+=1
 else:assert old==newraw;whole_test_bindings+=1
assert whole_test_bindings==5 and prefix_bindings==1
old_guard_path=Path(parent['offline_preflight']['focused_tests']['network_guard']['path']);old_guard=[json.loads(line) for line in old_guard_path.read_text().splitlines()];assert any(r['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for r in old_guard) and not any('DENY' in r['event'] for r in old_guard)
collect(inherit);collect(prep)
for value in refs.values():checked(value)
# Scientific and native budget/UNKNOWN behavior outside the two preflight insertions
# is byte-conserved by the exact runner diff and the 401 unchanged source hashes.
assert f['limits']==parent['limits'] and f['read_only_call_plan']==parent['read_only_call_plan'] and f['input']==parent['input']
assert not Path(f['work_path']).exists() and not Path(f['execution_evidence_path']).exists() and not Path(f['approval_path_expected']).exists()
report={'result':'PASS_WHOLE_FROZEN_SOURCE_MATERIAL_AND_RAW_CONTROLS_PENDING_FINAL_RECEIPT','packet_sha256':sha(P),'freeze_sha256':sha(F),'source_count':404,'AST':374,'parent403_401_exact_2mod_1new':True,'sameFD_unique_packet_and_embedded_refs':len(refs),'actual_preflight':material,'all19_new_classes_original_bytes_major55':classes,'all3_exact_diffs':delta_results,'affected_raw':{'tests':33,'failures':0,'errors':0,'skips':0,'guard_ACTIVE':active,'guard_DENY':deny,'22_DESELECT_stdout_preserved':Path(current['stdout']['path']).read_text()},'all31_actual_material_baselines':baselines,'all28_actual_target_reason_negative':negative,'unaffected435':{'original466_raw_pass':True,'precise_case_ids_exact':True,'whole_file5_byte_exact':True,'modified_test22_prefix_byte_exact':True,'not_rerun':True},'science_input_call_plan_limits_parent_exact':True,'old017_and_r9_failure_refs_preserved':True,'work_evidence_and_approval_absent':True,'new_native_or_compiler_dispatch':0,'verified_sameFD_refs':checked_refs}
(O/'CLOSURE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:report[k] for k in ('source_count','AST','parent403_401_exact_2mod_1new','sameFD_unique_packet_and_embedded_refs','work_evidence_and_approval_absent','new_native_or_compiler_dispatch')}))
