import ast,datetime,difflib,hashlib,json,os,pathlib,shlex,stat,struct,sys,xml.etree.ElementTree as ET
B=pathlib.Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');C=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r14';O=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r13';v={}
def ck(p,h=None,n=None):
 p=pathlib.Path(p);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_uid==os.getuid() and a.st_nlink==1 and not a.st_mode&0o022,str(p)
  parts=[]
  while x:=os.read(fd,1048576):parts.append(x)
  raw=b''.join(parts);z=os.fstat(fd);named=p.lstat();key=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  assert key(a)==key(z)==key(named),str(p)
 finally:os.close(fd)
 h0=hashlib.sha256(raw).hexdigest();assert h is None or h0==h,(str(p),h0,h);assert n is None or len(raw)==n,str(p)
 v[str(p)]={'path':str(p),'bytes':len(raw),'sha256':h0};return raw
def j(p,h=None,n=None):return json.loads(ck(p,h,n))
def refs(x):
 if isinstance(x,dict):
  if isinstance(x.get('path'),str) and len(str(x.get('sha256','')))==64:ck(x['path'],x['sha256'],x.get('bytes'))
  for z in x.values():refs(z)
 elif isinstance(x,list):
  for z in x:refs(z)
P=C/'root-review-packet/final6';bp=P/'FINAL_SOURCE_SOFTWARE_AND_FIXED_ENTRY_BINDING_ACTIVITY022_R14.json';b=j(bp,'57f431fd618abbcdd36a3d705583f2211616c9e319039ea149b8de34276ca600',2759);refs(b)
q=j(P/'ROOT_REVIEW_PACKET_ACTIVITY022_R14.json','204632ffecf51c5077687477f4cdbd2547d954ec9cb4432be17f5e4270b2967a',708696);refs(q);f=j(b['freeze']['path']);prior=j(O/'docs/full_project_execution/w23_overlap/evidence/native-run-20261013-021-saved-mph-zero-solve-candidate20-r13-final1/freeze.json','d8babde3ce5ff94f7f026c9f86271ac5f52074a17a484d9d3e94dbdc55a729fa')
assert len(f['source_files'])==424 and len(prior['source_files'])==411 and q['current_source_files']==f['source_files']
mods={k for k,r in prior['source_files'].items() if r['sha256']!=f['source_files'][k]['sha256']};adds=set(f['source_files'])-set(prior['source_files']);assert len(mods)==12 and len(adds)==13
assert mods=={r['relative'] for r in q['modified_sources']} and adds==set(q['added_sources'])
AST=0
for r in f['source_files'].values():
 raw=ck(r['path'],r['sha256'],r['bytes'])
 if pathlib.Path(r['path']).suffix=='.py':ast.parse(raw,filename=r['path']);AST+=1
assert AST==378
for r in prior['source_files'].values():ck(r['path'],r['sha256'],r['bytes'])
for r in q['modified_sources']:
 old=ck(r['before']['path']).decode();new=ck(r['after']['path']).decode();diff=ck(r['diff']['path']).decode();lines=diff.splitlines(True)
 assert diff==''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=lines[0][4:].strip(),tofile=lines[1][4:].strip()))
t=q['whole_current_software']['focused_tests'];cases=list(ET.fromstring(ck(t['junit_path'],t['junit_sha256'])).iter('testcase'))
assert len(cases)==752 and not any(x.find(k)is not None for x in cases for k in ['failure','error','skipped'])
guard=[json.loads(x) for x in ck(t['network_guard']['path'],t['network_guard']['sha256']).splitlines()];assert len(guard)==1 and guard[0]['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT'
fp=pathlib.Path(b['freeze']['path']);nclasses=0
for role,r in q['whole_current_software']['compile']['compiled_sources'].items():
 for relative,rec in r['classes'].items():
  raw=ck(fp.parent/'offline_gates/compile'/role/'classes'/relative,rec['sha256'],rec['bytes']);assert struct.unpack('>H',raw[6:8])[0]==55;nclasses+=1
assert nclasses==19
assert f['input']==prior['input'] and f['target']==prior['target']
limits=f['limits'];assert limits['max_gui_processes']==limits['max_solver_run_calls']==limits['max_study_run_calls']==0
assert limits['max_owned_server_processes']==limits['max_worker_sessions']==1 and limits['max_seconds_from_server_birth']==1800 and limits['cleanup_reserve_seconds']==120 and limits['max_result_at_points_calls']==8
plans=f['read_only_call_plan']['literal_field_plans'];assert len(plans)==2
for r in plans:
 port=r['port'];assert len(r['expressions'])==len(r['units'])==14 and r['expressions']==[f'down(emw.{kind}{axis})' for kind in ['E','H'] for axis in 'xyz']+[f'emw.{kind}{axis}_{port}' for kind in ['Emode','Hmode'] for axis in 'xyz']+['emw.nx','emw.ny']
 assert r['expected_entities']==([1,3,5] if port=='1' else [8,10,12]) and [g['count'] for g in r['grids']]==[321,641]
S=P/'native-capture-supplement022-003';m=j(S/'NATIVE_CAPTURE_INVOCATION_ACTIVITY022_R14.json','1fdb2fb99f4bb326e2db27269f643e32cb2525dcd37c2807db07d2ca08dceac2',199530)
launch=ck(S/'launch-activity022-r14.zsh','a2e0bb52a5e2e34342125f5ada460cb98339030163ecacb3cfcfd8f4b8a78864',4614).decode();argv=shlex.split(next(x for x in launch.splitlines() if x.startswith('exec '))[5:].split(' > ',1)[0]);assert argv==m['exact_native_invocation']['argv']
sys.path.insert(0,str(C));from tools import run_native_w23_saved_mph_diagnostic as runner
validated=runner._validate_freeze(fp,b['freeze']['sha256']);material=runner.science._check_offline_receipts(fp,validated)
blueprint=j(S/'ROOT_APPROVAL_EXPECTED_FIELDS_REFERENCE_ONLY.json');runner._validate_approval(blueprint['expected_approval_fields'],f,b['freeze']['sha256'],argv)
for p in m['new_paths_verified_absent']:assert not os.path.lexists(p)
resume=j(B/'repository/docs/full_project_continuation_v2/state/RESUME.json','17a7b0b347b19a0b7aaee2f2fd06ec5d188b03f67238c4c275295dfe655cfd11');assert resume['active_jobs']==[]
out={'schema':'ROOT_ACTIVITY022_R14_SOURCE_MATERIAL_REVIEW/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'CHANGES_REQUIRED_SHARED_MEASURE_ALL_COMPATIBILITY','binding':v[str(bp)],'freeze':b['freeze'],'material':'424source378AST/399same12modified13added/12exactdiffs; original752PASS+1guard0DENY;19currentmajor55classes; real4argentry/material validated SOURCE_ONLY','concentrated_review':'Point SelectionSpec is named/explicit only and original transient Interp child provenance/handle/generation/parent create-before-remove, strict before/after selection and unit arrays, privateconsume,14native expressions perplane and original science thresholds retained. Actual021definitions justify scope andside but are not numeric evidence.','required_issue':{'file':'comsol_mcp/_g3_common.py','function':'bind_strict_feature_selection','description':'Shared helper is also used by original strict measure _apply_measure_selection. MeasureSpec admits named/explicit/all and prior strict path called selection.all() for all. Current helper raises INVALID_SELECTION for all. Preserve original measure all without widening point/Ticket kinds. Independent actual producer comparison pending.'},'actual022':'NOT_RUN_NOT_ADMITTED','W24':'HOLD','runtime_identity_observation':'UNFIXED_ACTUAL021_UNVERIFIED_RETAINED','current_round':'Source checkpoint GitHub main then USER_REQUESTED_PAUSE; no new native this round','Root_effects':{'native':0,'JVM':0,'OS_inventory':0,'CAS':0,'approval':0,'tests_rerun':0},'actual_source_material':material,'sameFD_references':list(v.values())}
E=B/'local-preservation/root-activity022-r14-source-review';E.mkdir(mode=0o700,exist_ok=False);p=E/'REVIEW_RECEIPT.json';raw=(json.dumps(out,indent=2,sort_keys=True)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
 view=memoryview(raw)
 while view:view=view[os.write(fd,view):]
 os.fsync(fd)
finally:os.close(fd)
print(json.dumps({'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'refs':len(v),'status':out['status']}))
