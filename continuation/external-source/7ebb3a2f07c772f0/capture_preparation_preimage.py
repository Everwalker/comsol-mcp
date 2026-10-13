import os,sys,json,hashlib,stat,time,subprocess,tarfile
from pathlib import Path
C=Path(__file__).parent;W=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-liblzma-r1-execution-_k151oo6');REPO=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository');E=REPO/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';sys.path.insert(0,str(E))
import liblzma_successor_r1 as actor
import liblzma_inputs_r1 as fixed
from liblzma_prepare_r1 import verify_tls_runtime
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
terminal=json.loads((W/'terminal.json').read_bytes());closed=json.loads((root/'CLOSED.json').read_bytes());result=json.loads((root/'RESULT.json').read_bytes());failure=json.loads((root/'FAIL.json').read_bytes())
assert terminal['actual_wait_rc']==1 and not terminal['diagnostic_errors'] and result['commands']==[] and closed['status']=='VALIDATION_FAIL' and closed['old_roots_unchanged'] and closed['accepted_TLS_runtime_unchanged'] and closed['within_cap'] and not closed['errors']
ps=subprocess.run(['/bin/ps','-p','98585,98592','-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10);groups=[]
for pgid in (98592,):
 try:os.killpg(pgid,0);groups.append({'pgid':pgid,'absent':False})
 except ProcessLookupError:groups.append({'pgid':pgid,'absent':True,'actual_error':'ProcessLookupError'})
assert ps.returncode==1 and not ps.stdout.strip() and not ps.stderr.strip() and all(g['absent'] for g in groups)
absence={'argv':ps.args,'rc':ps.returncode,'stdout':ps.stdout,'stderr':ps.stderr,'groups':groups,'all_pids_absent':True,'all_groups_absent':True,'unix':time.time()};save(C/'OWNED_PID_GROUP_ABSENCE.json',absence)
actor.source_gate('2e66519156b4764ec4488bb5b2f680a09c28c202484ea2096c232a382354a43b','92017cb767a151459de49c807c31f330388c1101438010d1be8d93f87dfd74f0');preserved=verify_tls_runtime();inputs=fixed.verify_inputs_and_scan();save(C/'POST_EXECUTION_OLD_AND_AUTHENTICATED_INPUTS_VERIFIED.json',{'old':preserved,'input_records':inputs['inputs']})
actual=actor.deps.tree_record(root/'xz-input');projected=[{k:r[k] for k in ('relative_path','kind','mode','bytes','sha256') if k in r} for r in actual];expected=inputs['inventory']['rows'];am={r['relative_path']:r for r in projected};em={r['relative_path']:r for r in expected};assert len(am)==len(projected)==704 and len(em)==len(expected)==704
differences=[{'path':p,'expected':em.get(p),'actual':am.get(p)} for p in sorted(am.keys()|em.keys()) if am.get(p)!=em.get(p)];order=[{'index':i,'expected_path':e['relative_path'],'actual_path':a['relative_path']} for i,(e,a) in enumerate(zip(expected,projected)) if e!=a]
assert differences==[] and order and sorted(projected,key=lambda r:r['relative_path'])==expected
raw_modes=[];fd=os.open(fixed.INPUT_ROOT/'xz-5.8.4.tar.gz',os.O_RDONLY|os.O_NOFOLLOW)
try:
 fixed.checked_input(fd,fixed.INPUTS['xz-5.8.4.tar.gz'])
 with os.fdopen(os.dup(fd),'rb') as stream,tarfile.open(fileobj=stream,mode='r:*') as archive:
  for member in archive:raw_modes.append({'relative_path':member.name.rstrip('/'),'raw_archive_mode':member.mode,'raw_size':member.size,'is_directory':member.isdir(),'is_regular':member.isfile()})
 fixed.checked_input(fd,fixed.INPUTS['xz-5.8.4.tar.gz'])
finally:os.close(fd)
save(C/'ORIGINAL_XZ_RAW_MEMBER_MODES.json',raw_modes);save(C/'ORIGINAL_XZ_EXPECTED_SAMEFD_COMPLETE_CONTENT.json',expected);save(C/'ACTUAL_XZ_EXTRACTED_COMPLETE_TREE.json',actual)
source=actor.record(E/'prepare_offline_builder.py');diagnosis={'actual_entries':704,'expected_entries':704,'unique_membership_equal':set(am)==set(em),'mapping_full_kind_mode_size_sha_differences':differences,'full_semantic_difference_count':0,'raw_list_order_difference_count':len(order),'first_raw_list_order_difference':order[0],'all_raw_list_order_differences':order,'canonical_complete_list_equal':True,'expected_sort':'whole relative_path string','actual_sort':'sorted Path objects, component ordering','mode_policy':'Original safeextract and scanner both unchanged700 directories/executable,600 ordinaryfiles. No mode changes or bytes/member subset waiver.','raw_tar_modes_retained':row(C/'ORIGINAL_XZ_RAW_MEMBER_MODES.json'),'reviewed_extractor':source,'native_outer_commands':0,'actual_official_XZ_safe_extractions':1,'actual_official_CP_safe_extraction_in_same_prepare':1,'source_extraction_fact':'CP prerequisite extraction and exactcert/TLS copying completed before one XZ safeextract, then projection comparison failed before any arch source copy/configure/build; no archive program executed'};save(C/'WHOLE_704_ORDER_ONLY_FIRST_FAILURE_DIAGNOSIS.json',diagnosis)
from liblzma_cpython_checkpoint_r1 import sources
cp_inventory=sources.inventory(actor.PREFIX);cp_expected=[dict(r,relative_path=r['relative_path'][len(sources.CP_RELATIVE)+1:]) for r in cp_inventory if r['relative_path'].startswith(sources.CP_RELATIVE+'/')]
cp_actual=actor.deps.tree_record(root/'cpython-input/Python-3.12.13');save(C/'ACTUAL_CP_PRISTINE_SOURCE_COMPLETE_TREE.json',cp_actual)
# Compare complete raw content, not old compiled/cache source trees.
cp_exp={r['relative_path']:{k:r[k] for k in ('kind','bytes','sha256') if k in r} for r in cp_expected};cp_now={r['relative_path']:{k:r[k] for k in ('kind','bytes','sha256') if k in r} for r in cp_actual};assert cp_exp==cp_now
save(C/'CP_PRISTINE_FULL_CONTENT_COMPARISON.json',{'actual_records':len(cp_actual),'expected_records':len(cp_expected),'complete_content_equal':True,'official_zero_patch_SSL_source_sha':actor.deps.file_record(root/'cpython-input/Python-3.12.13/Modules/_ssl.c')['sha256'],'no_native_compile_or_cache':True})
tree=actor.deps.tree_record(root);save(C/'COMPLETE_FAILED_LIBLZMA_R1_TREE.json',tree);s=root.lstat();identity={'path':str(root),'device':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'mode':stat.S_IMODE(s.st_mode),'records':len(tree),'regular_bytes':sum(r.get('bytes',0) for r in tree)};save(C/'COMPLETE_FAILED_ROOT_IDENTITY.json',identity)
refs=[]
for folder in [C,W,root,root/'logs']:
 for p in folder.iterdir():
  if p.is_file() and not p.is_symlink():refs.append(row(p))
for name in ['LIBLZMA_SOURCE_FREEZE_R1.json','ROOT_LIBLZMA_SOURCE_REVIEW_R1.json','ROOT_LIBLZMA_R1_ONCE_DISPATCH_BINDING.json']:refs.append(row(E/name))
refs.append(row(REPO.parent/'local-preservation/liblzma-r1-independent-source-review/REVIEW_RECEIPT.json'))
packet={'schema':'comsol-mcp.liblzma-r1-actual-first-failure/1','status':'VALIDATION_FAIL_COMPLETE_LIST_ORDER_ONLY','actual_tool_session':30679,'wrapper_pid':98585,'actor_pid':98592,'wrapper_terminal':terminal,'closed':closed,'original_first_failure':failure,'commands':[],'native_outer_commands':0,'actual_XZ_safe_unpack':1,'actual_CP_prerequisite_safe_unpack':1,'OpenSSL_lipo_rebuild':0,'pure_Intel_runtime':0,'COMSOL':0,'network':0,'systeminstall':0,'root_identity':identity,'full_new_failed_tree':row(C/'COMPLETE_FAILED_LIBLZMA_R1_TREE.json'),'original43_auth_and_allold_TLS_Runtime_R4_preservation':preserved,'whole704_first_failure_diagnosis':row(C/'WHOLE_704_ORDER_ONLY_FIRST_FAILURE_DIAGNOSIS.json'),'fresh_owner_absence':absence,'fences':'all original FAILED/ACTIVE/GUARD/root/raw retained; no replay or nextbuild','scope':{'previous_ARM_TLS_runtime':'unchanged prior scopedPASS','new_lzma_build_runtime':'NOT_RUN','_lzma':'REQUIRED_OPEN','Intel_hardware':'NOT_RUN','full_three_arch_delivery':'OPEN'},'refs':refs};save(E/'LIBLZMA_EXECUTION_RESULT_R1.json',packet)
print(json.dumps({'capture':str(C),'receipt':row(E/'LIBLZMA_EXECUTION_RESULT_R1.json'),'failedtree':row(C/'COMPLETE_FAILED_LIBLZMA_R1_TREE.json'),'root':identity,'semantic_diff_count':0,'order_diff_count':len(order),'first_order_diff':order[0],'native_commands':0,'XZ_safeunpack':1,'CP_safeunpack':1,'owners_absent':True},indent=2))
