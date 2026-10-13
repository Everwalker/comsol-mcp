import datetime,hashlib,json,os,stat,sys
from pathlib import Path
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');C=R/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r10';D=C/'root-review-packet/native-capture-supplement018-001';O=R/'repository/docs/full_project_continuation_v2/evidence/w23_saved_mph_candidate20_r10';v={}
def ck(p,h=None):
 p=Path(p);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_uid==os.getuid() and a.st_nlink==1 and not(stat.S_IMODE(a.st_mode)&0o022);parts=[]
  while b:=os.read(fd,1048576):parts.append(b)
  raw=b''.join(parts);z=os.fstat(fd);assert (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)==(z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns,z.st_ctime_ns)
 finally:os.close(fd)
 hh=hashlib.sha256(raw).hexdigest();assert h is None or hh==h,str(p);v[str(p)]={'path':str(p),'sha256':hh,'bytes':len(raw)};return raw
def refs(obj):
 if isinstance(obj,dict):
  if isinstance(obj.get('path'),str) and len(str(obj.get('sha256','')))==64:ck(obj['path'],obj['sha256'])
  for x in obj.values():refs(x)
 elif isinstance(obj,list):
  for x in obj:refs(x)
mp=D/'NATIVE_CAPTURE_INVOCATION_ACTIVITY018_R10.json';meta=json.loads(ck(mp,'d2025606374172b305a0633522cb3d94ee94d331d3399b48224b982933e08cac'));refs(meta)
ip=R/'local-preservation/w23-activity018-r10-independent-source-review/REVIEW_RECEIPT.json';ind=json.loads(ck(ip,'7f886f99607ca28c226a6644efd50e7cba955265f8c0db32d0cbd1b82a8e3599'));assert ind['result']=='PASS_SCOPED_SOURCE_SOFTWARE' and ind['findings']==[] and ind['exact_freeze']['sha256']==meta['freeze']['sha256'];refs(ind['private_evidence'])
rootp=O/'ROOT_SOURCE_REVIEW_ACTIVITY018_R10.json';root=json.loads(ck(rootp,'fb69b3ea55495f65b612561f4f1a44a9fbcb2f7b8f399e8fbf6424d59e1bbe7d'));assert root['findings']==[] and root['status']=='PASS_SCOPED_SOURCE_SDK_SOFTWARE_PENDING_INDEPENDENT'
binding=D/'INPUT_AND_FIXED_CAPTURE_BINDING_REFERENCE_ONLY.json';refs(json.loads(ck(binding,'b1d1c71a9fa019dccee17ed3a31f7cd933b6a151c34cf38e182c573ab871124b')))
script=ck(meta['script']['path'],meta['script']['sha256']).decode();oldC=R/'local-preservation/w23-saved-mph-science-readback-20261012-candidate20-r8-complete1';oldE=oldC/'docs/full_project_execution/w23_overlap/evidence/native-run-20261012-017-saved-mph-zero-solve-candidate20-r8-final2';newE=Path(meta['freeze']['path']).parent
old=ck(oldC/'root-review-packet-final2/native-capture-supplement017-001/launch-activity017-r8.zsh','44d827cac5e0a18b1b7668fa8559acc066d063df97e7e283f0d64fdda9461001').decode();translated=old.replace(str(oldE),str(newE)).replace(str(oldC),str(C)).replace('20261012-017-candidate20-r8','20261013-018-candidate20-r10').replace('activity017','activity018').replace('ACTIVITY017','ACTIVITY018').replace('bc73dcb4484ded268ecc24f2ccee187611c8e1f2602b526cae935591a290369f',meta['freeze']['sha256']);assert translated==script
sys.path.insert(0,str(C));from tools import run_native_w23_saved_mph_diagnostic as runner
fp=Path(meta['freeze']['path']);f=runner._validate_freeze(fp,meta['freeze']['sha256']);material=runner.science._check_offline_receipts(fp,f);blueprint=json.loads(ck(meta['approval_blueprint']['path'],meta['approval_blueprint']['sha256']));runner._validate_approval(blueprint,f,meta['freeze']['sha256'],meta['exact_native_invocation']['argv'])
assert meta['limits']==f['limits'] and meta['read_only_call_plan']==f['read_only_call_plan'] and meta['input']==f['input'] and meta['exact_native_invocation']['argv']==runner._native_argv(fp,meta['freeze']['sha256'],Path(f['approval_path_expected']),runner.SOURCE_MODEL,Path(f['work_path']),Path(f['execution_evidence_path']))
for row in meta['new_paths_verified_absent']:
 p=Path(row['path']);assert not p.exists() and not p.is_symlink()
sp=R/'repository/docs/full_project_continuation_v2/state/RESUME.json';s=json.loads(ck(sp,'c06de4050f17ad282510bc382471721016381c2db0dabfd19e15d1beced10022'));assert s['active_jobs']==[]
payload={'schema':'ROOT_ACTIVITY018_R10_FIXED_ENTRYPOINT_REVIEW/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'PASS_SOURCE_SDK_SOFTWARE_AND_EXACT_SCRIPT_PREPARED_USER_AUTHORIZATION_PENDING','freeze':meta['freeze'],'Root':v[str(rootp)],'Independent':v[str(ip)],'metadata':v[str(mp)],'script':meta['script'],'actual_material':material,'verified_sameFD_refs':list(v.values()),'exact_old017_scheme_only_fixed018_paths_identity_hash_changes':True,'actual_four_argument_reference_approval_validator':'PASS_REFERENCE_ONLY_NOT_NEW_NATIVE_APPROVAL','limits':f['limits'],'input':f['input'],'work':f['work_path'],'execution_evidence':f['execution_evidence_path'],'fresh_paths_absent':meta['new_paths_verified_absent'],'native_authorization':'NOT_ISSUED_017_ACTUAL_ONCE_CONSUMED_018_DIRECT_HUMAN_REPLY_PENDING','fresh_process_inventory':'REQUIRED_AT_NEW_ADMISSION_NOT_CLAIMED_FROM_SOURCE_CHECK','W24':'PREVIOUS_DIRECT_AUTH_RETAINED_FIRSTFAIL_HOLD_NO_AUTOSTART','Root_initial_readonly_metadata_audit_error':'tool f9aaee passed PosixPath as fourth approval validator argument rather than exactargv, JSON TypeError before approval/source/write/native; corrected actual signature and explicit argv here, no candidate source change','Root_RESUME_writes':0,'Goal':'ACTIVE_INCOMPLETE'}
p=O/'ROOT_EXACT_ENTRYPOINT_REVIEW_ACTIVITY018_R10.json';raw=(json.dumps(payload,indent=2,sort_keys=True)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
 view=memoryview(raw)
 while view:view=view[os.write(fd,view):]
 os.fsync(fd)
finally:os.close(fd)
print(json.dumps({'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'refs':len(v)}))
