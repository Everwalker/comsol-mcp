import subprocess,json,collections,hashlib
from pathlib import Path
O=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/github-main-sync-20261013-r1');S=O/'send-tree'
def git(*args):return subprocess.run(['git',*args],cwd=S,check=True,capture_output=True).stdout
parent=git('rev-parse','HEAD').decode().strip();remote=git('rev-parse','origin/main').decode().strip();tree=git('write-tree').decode().strip()
assert parent=='799bf7a0fa95313e7659f44d8ff40ff1678314d5';assert remote=='60464c85b429e649f4f80484473999e5365ad96a';subprocess.run(['git','merge-base','--is-ancestor',remote,parent],cwd=S,check=True)
raw=git('diff','--cached','--name-status','--no-renames','-z');chunks=raw.split(b'\0');changes=[]
for i in range(0,len(chunks)-1,2):
 status=chunks[i].decode();path=chunks[i+1].decode();classification='included-source-or-compact-evidence'
 if status=='D':classification='ROOT_REVIEW_DELETE_OLD_PUBLISHED_RAW_OR_STALE_PATH'
 changes.append({'status':status,'path':path,'classification':classification})
(O/'TRACKED_DIFF_NAME_STATUS_R2.json').write_text(json.dumps(changes,indent=2)+'\n');(O/'TRACKED_DIFF_STAT.raw').write_bytes(git('diff','--cached','--stat'));(O/'PUBLIC_HISTORY_REFS.raw').write_bytes(git('show-ref'));(O/'EXPECTED_PARENT_LOG.raw').write_bytes(git('log','--oneline','--decorate','-5','HEAD'))
sourceext={'.py','.java','.ps1','.sh','.toml','.lock','.yaml','.yml','.md','.rst','.c','.h'};source_deleted=[r for r in changes if r['status']=='D' and Path(r['path']).suffix in sourceext]
receipt={'status':'PREPARED_INDEX_NOT_COMMITTED_NOT_PUSHED','tree':tree,'expected_commit_parent':parent,'fresh_remote_main':remote,'non_force_ancestor':True,'counts':dict(collections.Counter(r['status'] for r in changes)),'source_like_deleted_requiring_review':source_deleted,'original_git_mutations':0,'next':'R15/Root pages delta then Root exact tree review','historical_incoming_objects':'public main history plus sole audited799bf checkpoint; no private refs imported','old_banned_history':'original public ancestor may contain historical MPH; no history rewrite; new index banned count0'}
(O/'CURRENT_GIT_REVIEW_PACKET_R2.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'tree':tree,'counts':receipt['counts'],'source_like_deleted':len(source_deleted)},indent=2))
