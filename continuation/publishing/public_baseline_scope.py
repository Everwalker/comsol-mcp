import json,subprocess
from pathlib import Path
O=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/github-main-sync-20261013-r1');S=O/'send-tree'
def git(*args):return subprocess.run(['git',*args],cwd=S,check=True,capture_output=True).stdout
public={}
for raw in git('ls-tree','-r','-z','origin/main').split(b'\0'):
 if raw:
  meta,name=raw.split(b'\t',1);mode,kind,oid=meta.decode().split();public[name.decode()]=oid
old=json.loads((O/'TRACKED_OBJECT_SCAN_R1.json').read_text());groups=[]
for r in old['banned']:
 groups.append(dict(r,classification='PUBLIC_PREEXISTING_UNCHANGED_NO_NEW_DISCLOSURE' if public.get(r['path'])==r['oid'] else 'LOCAL_OR_NEW_BANNED_EXCLUDE'))
(O/'BANNED_PUBLIC_BASELINE_COMPARISON.json').write_text(json.dumps({'root_scope_clarification':'preserve already-public unchanged scientific artifacts; no unrelated cleanup authorized','rows':groups,'already_public':sum(r['classification'].startswith('PUBLIC') for r in groups),'new_exclude':sum(r['classification'].startswith('LOCAL') for r in groups)},indent=2)+'\n')
git('read-tree','origin/main');git('add','-f','--pathspec-from-file='+str(O/'STAGE_ALLOWLIST.nul'),'--pathspec-file-nul')
print('public preserved',sum(r['classification'].startswith('PUBLIC') for r in groups),'newbanned excluded',sum(r['classification'].startswith('LOCAL') for r in groups))
