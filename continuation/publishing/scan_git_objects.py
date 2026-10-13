import subprocess,json,collections,hashlib
from pathlib import Path
O=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/github-main-sync-20261013-r1');S=O/'send-tree'
def git(*args):return subprocess.run(['git',*args],cwd=S,check=True,capture_output=True).stdout
entries=[]
for row in git('ls-files','--stage','-z').split(b'\0'):
 if not row:continue
 meta,name=row.split(b'\t',1);mode,oid,stage=meta.decode().split();entries.append({'path':name.decode(),'mode':mode,'oid':oid})
ids=sorted({r['oid'] for r in entries});result=subprocess.run(['git','cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)'],cwd=S,input=('\n'.join(ids)+'\n').encode(),check=True,capture_output=True).stdout.decode().splitlines();sizes={}
for row in result:
 oid,kind,size=row.split();assert kind=='blob';sizes[oid]=int(size)
banned=[]
for r in entries:
 r['bytes']=sizes[r['oid']]
 if Path(r['path']).suffix.lower() in {'.mph','.jar','.whl','.dylib','.so','.a','.class','.pyc','.sqlite','.sqlite3','.db','.dll','.exe'} or r['mode']!='100644' and r['mode']!='100755':banned.append(r)
largest=sorted(entries,key=lambda r:r['bytes'],reverse=True)[:30]
receipt={'status':'PASS_BOUNDED_INDEX_OBJECT_TYPES_NO_COMMERCIAL_RUNTIME_DB' if not banned else 'FAIL','indexed_files':len(entries),'unique_blob_objects':len(ids),'unique_blob_bytes':sum(sizes.values()),'indexed_logical_bytes':sum(r['bytes'] for r in entries),'max_blob':largest[0] if largest else None,'largest_30':largest,'banned':banned,'source_byte_scan':'reuse CONTENT_VERIFICATION; no duplicate source byte scan','push':0,'original_git_mutations':0,'pending':['R15 source delta','Root canonical progress delta','final Root tree review']}
(O/'TRACKED_OBJECT_SCAN_R1.json').write_text(json.dumps(receipt,indent=2)+'\n');(O/'INDEX_MANIFEST_R1.json').write_text(json.dumps(entries,indent=2)+'\n');print(json.dumps({k:v for k,v in receipt.items() if k not in {'largest_30','banned'}},indent=2));print('banned',len(banned))
