import subprocess,json
from pathlib import Path
O=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/github-main-sync-20261013-r1');S=O/'send-tree';a=json.loads((O/'TRACKED_DIFF_NAME_STATUS_R2.json').read_text());rows=[]
for r in a:
 if r['status']!='T':continue
 path=r['path'];raw=subprocess.run(['git','ls-tree','origin/main','--',path],cwd=S,capture_output=True,check=True).stdout;meta,name=raw.split(b'\t',1);mode,kind,oid=meta.decode().split();assert mode=='120000';subprocess.run(['git','update-index','--add','--cacheinfo',mode+','+oid+','+path],cwd=S,check=True);rows.append({'path':path,'public_mode':mode,'public_oid':oid,'reason':'preserve untouched historical public symlink fixture; no unrelated type conversion authorized'})
(O/'PUBLIC_TYPE_PRESERVATION.json').write_text(json.dumps(rows,indent=2)+'\n');print('restored index public types',len(rows))
