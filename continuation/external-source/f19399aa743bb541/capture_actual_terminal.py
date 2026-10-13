import os,sys,json,time,stat,subprocess
from pathlib import Path
W=Path(__file__).parent;REPO=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository');E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(E))
import openssl359_runtime_successor_r2 as actor
import openssl359_cpython_successor_r4 as prior
import intel_deps_stages as deps
from intel_crypto_successor_r1 import checkpoint_bytes
root=actor.ROOT
def row(path):
 r=prior.record(path,maximum=134217728);checkpoint_bytes(path,r);return r
def save(path,value):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  b=memoryview((json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
  while b:b=b[os.write(fd,b):]
  os.fsync(fd)
 finally:os.close(fd)
closed=json.loads((root/'CLOSED.json').read_bytes());assert closed['status']=='TLS_HASHLIB_SCOPED_ACTUAL_PASS' and closed['within_cap'] and closed['old_roots_unchanged'] and not closed['errors']
commands=[json.loads(f.read_bytes()) for f in (root/'logs').glob('*.result.json')];assert len(commands)==1
c=commands[0];assert c['rc']==0 and c['owned_group_absent'] and c['owned_process_terminal'] and not c['diagnostic_errors']
pids=[43875,43877,c['pid']];groups=[43877,c['group']['actual_pgid']]
ps=subprocess.run(['/bin/ps','-p',','.join(map(str,pids)),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10)
facts=[]
for pgid in groups:
 try:os.killpg(pgid,0);facts.append({'pgid':pgid,'absent':False})
 except ProcessLookupError:facts.append({'pgid':pgid,'absent':True,'actual_error':'ProcessLookupError'})
assert ps.returncode==1 and not ps.stdout.strip() and not ps.stderr.strip() and all(r['absent'] for r in facts)
absence={'argv':ps.args,'rc':ps.returncode,'stdout':ps.stdout,'stderr':ps.stderr,'groups':facts,'all_pids_absent':True,'all_groups_absent':True,'unix':time.time()};save(W/'OWNED_PID_GROUP_ABSENCE.json',absence)
actor.source_gate('ba426e936583648f83fece2ae6a584295e9c3c0ade893d05897b073aede8a0da','080e54e87de326ae7e127b926c685a12d547e3add10dd591e08224413c8ef00a')
inputs=actor.verify_inputs();save(W/'POST_EXECUTION_FULL_INPUT_PRESERVATION.json',inputs)
artifact=json.loads((root/'ACTUAL_RUNTIME_AND_INSTALL.json').read_bytes());profile_raw,_=checkpoint_bytes(root/'ARM_RUNTIME_PROFILE.json',artifact['actual_profile_first_record']);profile=json.loads(profile_raw)
assert profile==artifact['actual_arm_profile'] and profile['provider_info']==[3,5,0,9,0] and profile['provider_number']==0x30500090 and profile['provider']=='OpenSSL 3.5.9 29 Sep 2026'
stdout=(root/'logs'/c['stdout']).read_text();lines=stdout.splitlines();first=[json.loads(line)['ACTUAL_PROVIDER_FIRST_READ'] for line in lines if line.startswith('{"ACTUAL_PROVIDER_FIRST_READ"')];assert len(first)==1 and first[0]=={k:profile[k] for k in ('provider_info','provider_number','provider')}
save(W/'ACTUAL_FIRST_PROVIDER_AND_PROFILE_CONSUMPTION.json',{'original_first_raw_stdout':row(root/'logs'/c['stdout']),'first_actual_fields':first[0],'same_profile_first_record_before_after':artifact['actual_profile_first_record'],'profile_same_raw_sha':True,'actual_profile':profile,'native_scope':'ARM selected only; not Intel hardware; no socket/truststore claim'})
tree=deps.tree_record(root);save(W/'COMPLETE_RUNTIME_R2_TREE.json',tree);s=root.lstat();identity=dict(path=str(root),device=s.st_dev,inode=s.st_ino,uid=s.st_uid,mode=stat.S_IMODE(s.st_mode),records=len(tree),regular_bytes=sum(r.get('bytes',0) for r in tree));save(W/'COMPLETE_RUNTIME_R2_ROOT_IDENTITY.json',identity)
refs=[]
for folder in [W,root,root/'logs']:
 for f in folder.iterdir():
  if f.is_file() and not f.is_symlink():refs.append(row(f))
for name in ['OPENSSL359_RUNTIME_SOURCE_FREEZE_R2.json','ROOT_OPENSSL359_RUNTIME_SOURCE_REVIEW_R2.json','ROOT_OPENSSL359_RUNTIME_R2_ONCE_DISPATCH_BINDING.json','OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json','ROOT_OPENSSL359_CPYTHON_R4_ACTUAL_FAILURE_CHECK.json']:refs.append(row(E/name))
refs.append(row(REPO.parent/'local-preservation/openssl359-runtime-r2-independent-source-review/REVIEW_RECEIPT.json'))
packet={'schema':'comsol-mcp.openssl359-runtime-r2-actual/1','status':'TLS_HASHLIB_SCOPED_ACTUAL_PASS','actual_tool_session':66078,'wrapper_pid':43875,'actor_pid':43877,'wrapper_terminal':json.loads((W/'terminal.json').read_bytes()),'command':c,'closed':closed,'actual_profile':profile,'actual_profile_first_record':artifact['actual_profile_first_record'],'installed_binary_actual':artifact['binary'],'root_identity':identity,'complete_new_tree':row(W/'COMPLETE_RUNTIME_R2_TREE.json'),'old_input_preservation':inputs,'fresh_owner_absence':absence,'firstactual_provider_durable':row(W/'ACTUAL_FIRST_PROVIDER_AND_PROFILE_CONSUMPTION.json'),'effects':{'outer_make':1,'recipe_fixed_steps':2,'OpenSSL_lipo_CPbuild':0,'network':0,'system_install':0,'COMSOL':0,'pure_Intel_execution':0},'acceptance':{'ARM_selected_SSL_hashlib_knownvectors_defaultTLS_cert_MemoryBIO12and13':'PASS_ACTUAL','installed3dualCPU_min11_systemdylib_bytes':'PASS_ACTUAL_READONLY','full_immutable_R4_14215_and43_alloldtrees':'PASS_ACTUAL_UNCHANGED','_lzma':'REQUIRED_OPEN','Intel_hardware':'NOT_RUN','complete_runtime_three_arch_final_delivery':'OPEN'},'fences':'original R4 FAILED/ACTIVE/guards and this runtime guard/ACTIVE remain immutable; no replay','refs':refs}
save(E/'OPENSSL359_RUNTIME_EXECUTION_RESULT_R2.json',packet)
print(json.dumps({'receipt':row(E/'OPENSSL359_RUNTIME_EXECUTION_RESULT_R2.json'),'tree':row(W/'COMPLETE_RUNTIME_R2_TREE.json'),'root':identity,'provider':first[0],'pids':pids,'groups':groups,'status':packet['status']},indent=2))
