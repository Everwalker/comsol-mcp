import json,os,stat,hashlib,pathlib,ast,sys,datetime,importlib.util,difflib
B=pathlib.Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');D=B/'local-preservation/w26-exact32-wrapper-r3';E=B/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';refs={}
def read(p,h=None,n=None):
 p=pathlib.Path(p);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);assert stat.S_ISREG(a.st_mode);v=bytearray()
  while True:
   z=os.read(fd,1048576)
   if not z:break
   v.extend(z)
  c=os.fstat(fd);assert(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)==(c.st_dev,c.st_ino,c.st_size,c.st_mtime_ns,c.st_ctime_ns)
 finally:os.close(fd)
 raw=bytes(v);r={'path':str(p),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'dev':a.st_dev,'inode':a.st_ino,'uid':a.st_uid,'mode':a.st_mode&0o777,'nlink':a.st_nlink};assert h is None or r['sha256']==h;(None if n is None else None);assert n is None or len(raw)==n;refs[str(p)]=r;return raw
def j(p,h=None,n=None):return json.loads(read(p,h,n))
def verify(x):
 if isinstance(x,dict):
  if isinstance(x.get('path'),str) and isinstance(x.get('sha256'),str):
   read(x['path'],x['sha256'],x.get('bytes'));r=refs[x['path']]
   for k in ['dev','inode','uid','mode','nlink']:
    if k in x:assert r[k]==x[k],(x['path'],k)
  for v in x.values():verify(v)
 elif isinstance(x,list):
  for v in x:verify(v)
def write(p,obj):
 raw=(json.dumps(obj,indent=2,sort_keys=True)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  view=memoryview(raw)
  while view:view=view[os.write(fd,view):]
  os.fsync(fd)
 finally:os.close(fd)
 read(p);return refs[str(p)]
F=j(D/'FREEZE_R3.json','601343c31a9ff80b126ef85c568a6e4800f9a0c3023763b80c3b312e179f2788',30439);verify(F)
F2=j(B/'local-preservation/w26-helper-successor-r2/FREEZE_R2.json','aab6a18ca1c450ff4c625ce30b334d27e5257f1089afde87cab5f9aaf8283b60',32531);verify(F2)
assert F['original_R2_files_preserved']==F2['files']
R2=j(B/'local-preservation/root-w26-helper-successor-r2-review/REVIEW_AND_NEXT_ADMISSION_DECISION.json','b71cfad4af425925927f9df68b0e57abadfd4ac671e22e1011a84512ea9d3b9a',26825)
assert R2['status']=='PASS_SCOPED_PRIVATE_HELPER_SOFTWARE_AND_EXACT32_INPUT_PLAN_NOT_DOWNLOAD_ADMISSION'
W=pathlib.Path(F['work_directory']);m=j(F['metadata']['path'],F['metadata']['sha256']);verify(m)
assert set(p.name for p in W.iterdir())=={'wrapper.py','actor.py','METADATA.json','logs'} and not list((W/'logs').iterdir())
assert W.lstat().st_uid==os.getuid() and W.lstat().st_mode&0o777==0o700
for p in [D/'wrapper.py',D/'actor_r3.py',D/'test_prepare.py',W/'wrapper.py',W/'actor.py']:ast.parse(read(p))
assert read(W/'wrapper.py')==read(D/'wrapper.py') and read(W/'actor.py')==read(D/'actor_r3.py')
assert F['controls']['count']==5 and 'Ran 5 tests' in read(F['controls']['raw']['path']).decode() and '\nOK' in read(F['controls']['raw']['path']).decode()
oldactor=read(B/'local-preservation/w26-helper-successor-r2/acquire_exact32.py').decode();newactor=read(W/'actor.py').decode()
assert ''.join(difflib.unified_diff(oldactor.splitlines(True),newactor.splitlines(True),fromfile='immutable R2 actor',tofile='new R3 actor'))==read(D/'ACTOR_R2_TO_R3.diff').decode()
def load(name,p):
 s=importlib.util.spec_from_file_location(name,p);v=importlib.util.module_from_spec(s);s.loader.exec_module(v);return v
w=load('root_prepared_exact32_wrapper',W/'wrapper.py');m2,cp,deps=w.load_sources();assert m2==m
assert w.verify_old(m,deps)['records']==16870
actor=load('root_prepared_exact32_actor',W/'actor.py');original,rows=actor.source_and_rows();assert len(rows)==32 and sum(x['expected_bytes'] for x in rows)==21090946
assert [r['url']for r in rows]==m['network_URLs']
assert m['actor_argv']==[F['next_invocation_prefix'][0],'-B','-u',str(W/'actor.py'),'--execute-download','--approved-manifest-sha256',m['manifest_sha256']]
assert m['deadline']['seconds']==600 and m['deadline']['cleanup_reserve']==15
assert m['environment']=={'HOME':'/Users/everwalker','LC_ALL':'C','PATH':'/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','TMPDIR':str(W.parent)}
for p in [E/'ROOT_W26_EXACT32_SOURCE_REVIEW_R3.json',E/'ROOT_W26_EXACT32_ONCE_DISPATCH_BINDING_R3.json']:assert not p.exists()
review={'schema':'ROOT_EXACT32_WHOLE_SOURCE_AND_ACTUAL_ENTRY_REVIEW/3','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'PASS_SOURCE_EXACT32_WRAPPER_ONLY','freeze':refs[str(D/'FREEZE_R3.json')],'whole_R2_preserved':len(F['original_R2_files_preserved']),'current_R3_files':len(F['files']),'current_work_files':3,'current_controls':5,'Root_fresh_production_readonly_controls':['actual wrapper.load_sources with permanent guards absent','original complete runtime16870 and46 archive membership whole SHA verification','actual actor.source_and_rows exact32/21090946 official fixed URLs, zero fetch'],'actual_entry':{'work_directory':str(W),'work_identity':F['work_identity'],'wrapper':F['wrapper'],'actor':F['actor'],'metadata':F['metadata'],'actor_argv':m['actor_argv'],'wrapper_prefix':F['next_invocation_prefix'],'environment':m['environment']},'source_disposition':'Original cp.run exact Popen/birth/group/wait/log owner and StageUnknown same-handle observation reused; no replay or signals. Inherited outer monotonic includes all actor source loading/network; 585-second no-next-dispatch deadline with15 reserve. Original observer may retain same handle past cap on ambiguous terminal state; such overrun is UNKNOWN and cannot PASS. Actor first exception leaves original request/HTTP/partial/result, all acquired prefix bytes retained. No target wheel execution, pip, install, build or unpack.','remaining_external_acceptance':'Actual wrapper tool wait/rc/stdout, complete elapsed and32 hashes each HTTP receipt plus source/runtime preservation required. No full dependency closure/install/runtime/three-platform delivery acceptance yet.','effects':{'network':0,'spawn':0,'native_inventory':0,'COMSOL':0,'Windows':0,'pip_build_unpack':0,'guards_created':0,'CAS_PROGRESS_Git':0},'Goal':'ACTIVE_INCOMPLETE','references':list(refs.values())}
rr=write(E/'ROOT_W26_EXACT32_SOURCE_REVIEW_R3.json',review)
b={'schema':'comsol-w26-exact32-once-binding/1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'GRANTED_ONCE_DOWNLOAD_ONLY','authorization_basis':'Human MASTER_GOAL authorizes required ordinary public dependency acquisition; Root exact candidate/source/once entry reviewed. No installer/system configuration or private data transmission.','wrapper_sha256':F['wrapper']['sha256'],'metadata_sha256':F['metadata']['sha256'],'actor_sha256':m['actor_sha256'],'manifest_sha256':m['manifest_sha256'],'work_directory':str(W),'work_identity':m['work_identity'],'actor_argv':m['actor_argv'],'budget_seconds':600,'cleanup_reserve_seconds':15,'network_files':32,'network_bytes':21090946,'root_source_review':{k:rr[k]for k in ('path','sha256','bytes')},'first_failure':'STOP_NEXT_DISPATCH_NO_RETRY; preserve same original handle/partial/cause. Overrun or unknown never PASS.','scope':'Only fixed32 unauthenticated official files.pythonhosted.org whole inputs; zero pip/install/build/unpack/target interpreter/native/COMSOL/Windows/CAS/progress/Git. Never repeat already successful acquisition or metadata GET.'}
br=write(E/'ROOT_W26_EXACT32_ONCE_DISPATCH_BINDING_R3.json',b)
assert w.binding(m,br['sha256'])['binding']==br
print(json.dumps({'review':rr,'binding':br,'actual_wrapper_argv':F['next_invocation_prefix']+[br['sha256']],'network':0,'guards':0,'refs':len(refs)}))
