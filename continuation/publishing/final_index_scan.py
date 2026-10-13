import json,subprocess,hashlib
from pathlib import Path
O=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/github-main-sync-20261013-r1');S=O/'send-tree'
def git(*args):return subprocess.run(['git',*args],cwd=S,capture_output=True,check=True).stdout
public={}
for row in git('ls-tree','-r','-z','origin/main').split(b'\0'):
 if row:
  meta,name=row.split(b'\t',1);mode,kind,oid=meta.decode().split();public[name.decode()]=(mode,oid)
rows=[]
for raw in git('ls-files','--stage','-z').split(b'\0'):
 if raw:
  meta,name=raw.split(b'\t',1);mode,oid,stage=meta.decode().split();path=name.decode();rows.append({'path':path,'mode':mode,'oid':oid,'public_unchanged':public.get(path)==(mode,oid)})
ids=sorted({r['oid'] for r in rows});raw=subprocess.run(['git','cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)'],cwd=S,input=('\n'.join(ids)+'\n').encode(),capture_output=True,check=True).stdout.decode();size={}
for line in raw.splitlines():
 oid,kind,n=line.split();assert kind=='blob';size[oid]=int(n)
for r in rows:r['bytes']=size[r['oid']]
banned={'.mph','.jar','.whl','.dylib','.so','.a','.class','.pyc','.sqlite','.sqlite3','.db','.dll','.exe'};new=[r for r in rows if not r['public_unchanged']];violations=[r for r in new if Path(r['path']).suffix.lower() in banned or r['mode'] not in {'100644','100755'}]
newids={r['oid'] for r in new};receipt={'status':'PASS_NO_NEW_BANNED_OBJECTS' if not violations else 'FAIL','tree':git('write-tree').decode().strip(),'expected_parent':git('rev-parse','HEAD').decode().strip(),'public_base':git('rev-parse','origin/main').decode().strip(),'indexed_files':len(rows),'public_unchanged_files':len(rows)-len(new),'new_or_changed_files':len(new),'new_or_changed_unique_blob_count':len(newids),'new_or_changed_unique_blob_bytes':sum(size[x] for x in newids),'largest_new_30':sorted(new,key=lambda r:r['bytes'],reverse=True)[:30],'new_banned_violations':violations,'preexisting_public_banned_count':sum(Path(r['path']).suffix.lower() in banned for r in rows if r['public_unchanged']),'deletions':0,'source_scan':'completed exact bytes scan reused; metadata/source delta separately checked','push':0,'original_git_mutations':0,'final':False,'pending':'R15 and canonical Root pages'}
(O/'CURRENT_INDEX_MANIFEST.json').write_text(json.dumps(rows,indent=2)+'\n');(O/'CURRENT_TRACKED_OBJECT_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({k:v for k,v in receipt.items() if k not in {'largest_new_30','new_banned_violations'}},indent=2))
