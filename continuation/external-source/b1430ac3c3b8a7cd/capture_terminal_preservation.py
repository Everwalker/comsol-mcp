import os,sys,json,hashlib,subprocess,time,stat
from pathlib import Path
D=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1');sys.path.insert(0,str(D))
import intel_deps_stages as deps
import openssl359_cpython_successor_r4 as actor
from intel_crypto_successor_r1 import verify_old_unit_tree
from openssl359_cpython_checkpoint_r4 import preservation_verified_summary
W=Path(__file__).parent;root=actor.layout()['root']
def save(name,value):
 fd=os.open(W/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  data=memoryview((json.dumps(value,indent=2,sort_keys=True)+'\n').encode())
  while data:data=data[os.write(fd,data):]
  os.fsync(fd)
 finally:os.close(fd)
actual=[json.loads(f.read_bytes()) for f in sorted((root/'logs').glob('*.result.json'))]
pids=[3509,3514]+[r['pid'] for r in actual];groups=[3514]+[r['group']['actual_pgid'] for r in actual]
ps=subprocess.run(['/bin/ps','-p',','.join(map(str,pids)),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10)
absence=[]
for group in groups:
 try:os.killpg(group,0);absence.append({'pgid':group,'absent':False})
 except ProcessLookupError:absence.append({'pgid':group,'absent':True,'actual_error':'ProcessLookupError'})
 except BaseException as e:absence.append({'pgid':group,'absent':False,'actual_error':repr(e)})
save('OWNED_PID_GROUP_ABSENCE.json',{'argv':ps.args,'rc':ps.returncode,'stdout':ps.stdout,'stderr':ps.stderr,'groups':absence,'all_pids_absent':ps.returncode==1 and not ps.stdout.strip() and not ps.stderr.strip(),'all_groups_absent':all(r['absent'] for r in absence),'observed_unix':time.time()})
actor.source_gate('ce38de67f4f2e0d07fbeef49357c7f3f1608bfc242288f369cae0d0296742e36','fca3aaeb51a2d60b516901af73ea92a6aec07db5af94d9bd31bf130ffec2ad5b')
prior=preservation_verified_summary();verify_old_unit_tree(actor.PREFIX/'intel-deps-r2')
prepared=json.loads((root/'PREPARED.json').read_bytes());originals=[]
for path,tree in prepared['original_trees'].items():
 p=Path(path);record=actor.original_root_record(p,prepared['original_root_records'][path])
 if deps.tree_record(p)!=tree:raise RuntimeError('old complete original tree changed')
 originals.append({'path':path,'records':len(tree),'same_root':record,'unchanged':True})
save('POST_EXECUTION_OLD_TREES_VERIFIED.json',{'prior_tls_r1_r2_r3_and_cache':prior,'originals':originals,'old_deps13588_unchanged':True})
tree=deps.tree_record(root);save('COMPLETE_OPENSSL359_CP_R4_TREE.json',tree)
st=root.lstat();save('COMPLETE_OPENSSL359_CP_R4_ROOT_IDENTITY.json',dict(path=str(root),device=st.st_dev,inode=st.st_ino,uid=st.st_uid,mode=stat.S_IMODE(st.st_mode),records=len(tree),regular_bytes=sum(r.get('bytes',0) for r in tree)))
print(json.dumps({'root':str(root),'records':len(tree),'all_pid_absent':ps.returncode==1 and not ps.stdout.strip() and not ps.stderr.strip(),'all_groups_absent':all(r['absent'] for r in absence),'pids':pids,'groups':groups,'old_complete_trees_verified':True}),flush=True)
