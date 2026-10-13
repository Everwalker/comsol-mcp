import os,ast,json,hashlib
from pathlib import Path
R=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');D=Path(__file__).parent;A=D.parent/'w26-helper-successor-r2';E=R/'repository/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1';W=Path((D/'W_PATH.txt').read_text().strip())
import sys;sys.path.insert(0,str(A));from strict_derived import read_regular
# Immutable local dependency import graph for the reused runner/provider modules.
seen={};pending=['cpython_universal2_stages','intel_deps_stages','acquire_exact_archives']
while pending:
 name=pending.pop()
 if name in seen:continue
 p=E/Path(name.replace('.','/')).with_suffix('.py')
 if not p.exists():
  p=E/name.replace('.','/')/'__init__.py'
 if not p.exists():continue
 seen[name]=read_regular(p)[1];tree=ast.parse(p.read_bytes())
 for node in ast.walk(tree):
  if isinstance(node,ast.Import):pending += [a.name for a in node.names]
  elif isinstance(node,ast.ImportFrom) and node.module:
   if node.level:
    parent=name.rsplit('.',1)[0];pending.append(parent+'.'+node.module)
   else:pending.append(node.module)
(W/'actor.py').write_bytes((D/'actor_r3.py').read_bytes())
manifest=read_regular(A/'EXACT32_DOWNLOAD_MANIFEST.json')[1];old=json.loads(read_regular(E/'ACQUISITION_ATTEMPT004_SUMMARY.json')[0]);archive_refs=[]
for row in old['archive_inventory']:
 p=Path(row['path']);raw,rec=read_regular(p)
 assert rec['sha256']==row['sha256'] and rec['bytes']==row['bytes'] and rec['inode']==row['inode'] and rec['dev']==row['device']
 archive_refs.append(rec)
lzma=json.loads(read_regular(E/'LIBLZMA_EXECUTION_RESULT_R2.json')[0]);snapshot=lzma['complete_new_tree'];raw,srec=read_regular(Path(snapshot['path']))
assert srec['sha256']==snapshot['sha256'];source_refs=list(seen.values())
source_refs += [read_regular(p)[1] for p in [A/'strict_derived.py',A/'native_parser.py',A/'FREEZE_R2.json',A/'ORIGIN_BINDING.json',A/'ACCEPTED_CONTENT.json',E/'ROOT_LIBLZMA_R2_ACTUAL_CHECK.json',E/'LIBLZMA_EXECUTION_RESULT_R2.json',E/'ACQUISITION_ATTEMPT004_SUMMARY.json',W/'actor.py',A/'EXACT32_DOWNLOAD_MANIFEST.json']]
s=W.stat();env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C','HOME':os.environ['HOME'],'TMPDIR':str(W.parent),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
argv=['/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3','-B','-u',str(W/'actor.py'),'--execute-download','--approved-manifest-sha256',manifest['sha256']]
meta={'schema':'comsol-w26-exact32-wrapper-metadata/1','status':'SOURCE_ONLY_NO_SPAWN_NO_GUARDS_NO32GET','work_identity':{'device':s.st_dev,'inode':s.st_ino},'work_directory':str(W),'actor_sha256':read_regular(W/'actor.py')[1]['sha256'],'actor_argv':argv,'environment':env,'source_refs':source_refs,'manifest_sha256':manifest['sha256'],'manifest_record':manifest,'network_URLs':[q['url'] for q in json.loads(read_regular(A/'EXACT32_DOWNLOAD_MANIFEST.json')[0])['artifacts']],'old_archive_root':str(Path(archive_refs[0]['path']).parent),'archive_refs':archive_refs,'runtime_root':lzma['root_identity'],'runtime_snapshot':srec,'capture_paths':{'logs':str(W/'logs'),'request':str(W/'REQUEST.json'),'wrapper_birth':str(W/'WRAPPER_BIRTH.json'),'wrapper_guard':str(W/'WRAPPER_ONCE_GUARD.json'),'actor_guard':str(W/'EXACT32_ONCE_GUARD.json'),'prefix_checkpoint':str(W/'PREFIX_CREATED.json'),'newtree':str(W/'COMPLETE_ACQUISITION_TREE.json'),'terminal':str(W/'TERMINAL.json'),'original_cp_run':'<actual token>.request/spawn/group/birth/stdout/stderr/result and UNKNOWN observations within exact logs directory'},'deadline':{'seconds':600,'cleanup_reserve':15,'epoch':'before outer actor Popen, conservatively wrapper main entry includes preflight; inherited exact monotonic in actor before source loading; no reset'},'binding_expectations':{'path':str(E/'ROOT_W26_EXACT32_ONCE_DISPATCH_BINDING_R3.json'),'review_path':str(E/'ROOT_W26_EXACT32_SOURCE_REVIEW_R3.json'),'binding_schema':'comsol-w26-exact32-once-binding/1','binding_status':'GRANTED_ONCE_DOWNLOAD_ONLY','review_status':'PASS_SOURCE_EXACT32_WRAPPER_ONLY'}}
with (W/'METADATA.json').open('xb') as f:f.write((json.dumps(meta,sort_keys=True,indent=2)+'\n').encode());f.flush();os.fsync(f.fileno())
sha=read_regular(W/'METADATA.json')[1]['sha256'];p=D/'wrapper.py';code=p.read_text().replace('__METADATA_SHA__',sha).replace('__W_PATH__',str(W));p.write_text(code);(W/'wrapper.py').write_bytes(p.read_bytes())
print('metadata',read_regular(W/'METADATA.json')[1]);print('wrapper',read_regular(W/'wrapper.py')[1]);print('source graph',len(seen))
