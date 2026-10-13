import os,sys,json,stat,time,subprocess
from pathlib import Path
C=Path(__file__).parent;W=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-liblzma-r2-execution-3wacz70a');REPO=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository');E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(E))
import liblzma_successor_r2 as actor
import liblzma_prepare_r2 as source
from intel_crypto_successor_r1 import checkpoint_bytes
root=actor.layout()['root']
def save(p,value):
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  b=memoryview((json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
  while b:b=b[os.write(fd,b):]
  os.fsync(fd)
 finally:os.close(fd)
def row(p):
 r=actor.record(p,maximum=134217728);checkpoint_bytes(p,r);return r
terminal=json.loads((W/'terminal.json').read_bytes());closed=json.loads((root/'CLOSED.json').read_bytes());result=json.loads((root/'RESULT.json').read_bytes())
commands=[];children=[]
for path in sorted((root/'logs').glob('*.result.json')):
 r=json.loads(path.read_bytes())
 if 'pid' in r:commands.append(r)
 elif 'actual_pid' in r:children.append(r)
 else:raise RuntimeError('unexpected native terminal schema')
commands.sort(key=lambda r:r['finished_unix'])
assert all(r['owned_process_terminal'] and r['owned_group_absent'] for r in commands)
pids=[5908,5946]+[r['pid'] for r in commands];groups=[5946]+[r['group']['actual_pgid'] for r in commands]
for r in children:
 pids.append(r['actual_pid']);pids.append(r['request']['parent_pid'])
pids=sorted(set(pids));groups=sorted(set(groups))
ps=subprocess.run(['/bin/ps','-p',','.join(map(str,pids)),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10);gf=[]
for pgid in groups:
 try:os.killpg(pgid,0);gf.append({'pgid':pgid,'absent':False})
 except ProcessLookupError:gf.append({'pgid':pgid,'absent':True,'actual_error':'ProcessLookupError'})
assert ps.returncode==1 and not ps.stdout.strip() and not ps.stderr.strip() and all(g['absent'] for g in gf)
absence={'argv':ps.args,'rc':ps.returncode,'stdout':ps.stdout,'stderr':ps.stderr,'groups':gf,'pids':pids,'all_pids_absent':True,'all_groups_absent':True,'unix':time.time()};save(C/'OWNED_PID_GROUP_ABSENCE.json',absence)
actor.source_gate('3d6cf9b36dad862d4a125b337dee62405db1021d4aa14ca33fbd348550cdf9ea','7f970289dc01372283d6874c2d4f13e2db64149045c377836995d87a4426cab8');oldrows=source.verify_failed_r1();tls=source.verify_tls_runtime();auth=source.fixed.verify_inputs_and_scan();save(C/'ALL_ORIGINAL_INPUTS_AFTER_VERIFIED.json',{'failed_R1_records':len(oldrows),'failed_R1_snapshot_sha':source.FAILED_PIN['sha256'],'TLS_Runtime_R4_allolder':tls,'authenticated_input_records':auth['inputs']})
profile=None;artifact=None;library=None
if (root/'ARM_RUNTIME_PROFILE.json').exists():
 artifact=json.loads((root/'ACTUAL_RUNTIME_AND_INSTALL.json').read_bytes());profile=json.loads(checkpoint_bytes(root/'ARM_RUNTIME_PROFILE.json',artifact['actual_profile_first_record'])[0]);assert profile==artifact['actual_arm_profile']
if (root/'MERGE_OUTPUT_VERIFIED.json').exists():library=json.loads((root/'MERGE_OUTPUT_VERIFIED.json').read_bytes())
if closed['status']=='LZMA_TLS_SCOPED_ACTUAL_PASS':
 assert len(commands)==13 and all(r['rc']==0 for r in commands) and len(children)==1 and children[0]['actual_wait_rc']==0 and not children[0]['diagnostic_errors']
 assert profile['provider_info']==[3,5,0,9,0] and profile['provider_number']==0x30500090 and profile['actual_lzma_roundtrip'] and profile['actual_tar_xz_product_roundtrip']
 assert closed['within_cap'] and closed['old_roots_unchanged'] and closed['accepted_TLS_runtime_unchanged'] and not closed['errors'] and terminal['actual_wait_rc']==0 and not terminal['diagnostic_errors']
tree=actor.deps.tree_record(root);save(C/'COMPLETE_LIBLZMA_R2_TREE.json',tree);s=root.lstat();identity={'path':str(root),'device':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'mode':stat.S_IMODE(s.st_mode),'records':len(tree),'regular_bytes':sum(r.get('bytes',0) for r in tree)};save(C/'COMPLETE_LIBLZMA_R2_ROOT_IDENTITY.json',identity)
refs=[]
for folder in [C,W,root,root/'logs']:
 for p in folder.iterdir():
  if p.is_file() and not p.is_symlink():refs.append(row(p))
for name in ['LIBLZMA_SOURCE_FREEZE_R2.json','ROOT_LIBLZMA_SOURCE_REVIEW_R2.json','ROOT_LIBLZMA_R2_ONCE_DISPATCH_BINDING.json','LIBLZMA_EXECUTION_RESULT_R1.json','ROOT_LIBLZMA_R1_ACTUAL_FAILURE_CHECK.json']:refs.append(row(E/name))
refs.append(row(REPO.parent/'local-preservation/liblzma-r2-independent-source-review/REVIEW_RECEIPT.json'))
packet={'schema':'comsol-mcp.liblzma-r2-actual-terminal/1','status':closed['status'],'actual_tool_session':24981,'wrapper_pid':5908,'actor_pid':5946,'wrapper_terminal':terminal,'closed':closed,'commands':commands,'lipo_children':children,'native_outer_commands':len(commands),'actual_original_XZ_reuse':'strict old704 firstpin copier; no repeated XZsafeunpack','new_CP_official_safeextract':1,'root_identity':identity,'complete_new_tree':row(C/'COMPLETE_LIBLZMA_R2_TREE.json'),'old5799_Runtime17_R414215_and43_auth3':'PASS_ACTUAL_UNCHANGED_READONLY','original_fulltree_after':row(C/'ALL_ORIGINAL_INPUTS_AFTER_VERIFIED.json'),'fresh_owner_absence':absence,'actual_profile':profile,'actual_library':library,'actual_final_binary':artifact['binary'] if artifact else None,'actual_profile_first_record':artifact['actual_profile_first_record'] if artifact else None,'effects':{'network':0,'system_install':0,'COMSOL':0,'pure_Intel_runtime':0,'old_OpenSSL_CP_Runtime_rebuild':0},'acceptance':{'ARM_lzma_tarXZ_TLS_hashlib_runtime':closed['status'],'four_target_binaries_twoCPU_min11_systemdylib':'actual bytes in artifact if available','Intel_hardware':'NOT_RUN','full_three_arch_delivery':'OPEN'},'fences':'all new/original ACTIVE/GUARD/failed/raw preserved; no replay','refs':refs};save(E/'LIBLZMA_EXECUTION_RESULT_R2.json',packet)
print(json.dumps({'receipt':row(E/'LIBLZMA_EXECUTION_RESULT_R2.json'),'tree':row(C/'COMPLETE_LIBLZMA_R2_TREE.json'),'root':identity,'status':closed['status'],'commands_rc':[r['rc'] for r in commands],'children':len(children),'pids':pids,'groups':groups,'wrapper_elapsed':terminal['elapsed_including_wait_and_flush_s']},indent=2))
