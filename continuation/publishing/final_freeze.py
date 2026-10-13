import subprocess,json,hashlib,collections
from pathlib import Path
O=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/github-main-sync-20261013-r1');S=O/'send-tree';F=O/'final-r1'
def git(*args):return subprocess.run(['git',*args],cwd=S,capture_output=True,check=True).stdout
public={}
for raw in git('ls-tree','-r','-z','origin/main').split(b'\0'):
 if raw:
  meta,name=raw.split(b'\t',1);mode,kind,oid=meta.decode().split();public[name.decode()]=(mode,oid)
rows=[]
for raw in git('ls-files','--stage','-z').split(b'\0'):
 if raw:
  meta,name=raw.split(b'\t',1);mode,oid,stage=meta.decode().split();path=name.decode();rows.append({'path':path,'mode':mode,'oid':oid,'public_unchanged':public.get(path)==(mode,oid)})
ids=sorted({r['oid'] for r in rows});raw=subprocess.run(['git','cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)'],cwd=S,input=('\n'.join(ids)+'\n').encode(),capture_output=True,check=True).stdout.decode();sizes={}
for line in raw.splitlines():
 oid,kind,n=line.split();assert kind=='blob';sizes[oid]=int(n)
for r in rows:r['bytes']=sizes[r['oid']]
banned={'.mph','.jar','.whl','.dylib','.so','.a','.class','.pyc','.sqlite','.sqlite3','.db','.dll','.exe'};new=[r for r in rows if not r['public_unchanged']];bad=[r for r in new if Path(r['path']).suffix.lower() in banned or r['mode'] not in {'100644','100755'}];assert not bad,bad
parent=git('rev-parse','HEAD').decode().strip();remote=git('rev-parse','origin/main').decode().strip();assert parent=='799bf7a0fa95313e7659f44d8ff40ff1678314d5';assert remote=='60464c85b429e649f4f80484473999e5365ad96a';subprocess.run(['git','merge-base','--is-ancestor',remote,parent],cwd=S,check=True)
commits=git('rev-list','--all','--not','origin/main').decode().splitlines();assert set(commits)=={parent},commits
for r in rows:
 if r['path'] in public and (Path(r['path']).suffix.lower() in banned or public[r['path']][0]=='120000'):assert (r['mode'],r['oid'])==public[r['path']],r
existing_missing=sorted(set(public)-{r['path'] for r in rows});assert not existing_missing,existing_missing
prev=json.loads((O/'provisional-r1/CURRENT_TRACKED_OBJECT_RECEIPT.json').read_text())['tree'];tree=git('write-tree').decode().strip();stats={}
for label,base in [('parent',parent),('public_main',remote),('provisional',prev)]:
 raw=git('diff','--cached','--name-status','--no-renames','-z',base);parts=raw.split(b'\0');changes=[{'status':parts[i].decode(),'path':parts[i+1].decode()} for i in range(0,len(parts)-1,2)];assert not [r for r in changes if r['status'] in {'D','T'}],label
 (F/(label.upper()+'_DIFF_MANIFEST.json')).write_text(json.dumps(changes,indent=2)+'\n');(F/(label.upper()+'_DIFF_STAT.raw')).write_bytes(git('diff','--cached','--stat',base));stats[label]=dict(collections.Counter(r['status'] for r in changes))
newids={r['oid'] for r in new};receipt={'status':'PASS_FINAL_INDEX_NO_NEW_BANNED_OR_UNRELATED_DELETION','tree':tree,'expected_parent':parent,'fresh_public_main':remote,'prior_provisional_tree':prev,'tracked_files':len(rows),'public_unchanged_files':len(rows)-len(new),'new_or_changed_files':len(new),'unique_new_or_changed_blobs':len(newids),'unique_new_or_changed_bytes':sum(sizes[x] for x in newids),'largest_new_30':sorted(new,key=lambda r:r['bytes'],reverse=True)[:30],'new_banned':bad,'preexisting_public_banned_sameblob':sum(Path(r['path']).suffix.lower() in banned for r in rows if r['public_unchanged']),'unchanged_public_symlinks':sum(r['mode']=='120000' for r in rows if r['public_unchanged']),'git_stats':stats,'incoming_private_commits':commits,'original_git_mutations':0,'native_effects':0,'commit':0,'push':0,'source_check':'Reuse completed exact source scans; R15/final metadata deltas checked separately; no old suite/source rescan','scope':'source checkpoint; target/native/full-project not accepted; sync then user-requested pause'}
(F/'FINAL_INDEX_MANIFEST.json').write_text(json.dumps(rows,indent=2)+'\n');(F/'FINAL_TRACKED_OBJECT_SCAN.json').write_text(json.dumps(receipt,indent=2)+'\n');refs=[]
for p in sorted(F.iterdir()):
 if p.is_file() and p.suffix in {'.json','.raw'}:
  raw=p.read_bytes();refs.append({'path':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
freeze={'schema':'github-main-source-checkpoint-final-freeze/1','status':'FROZEN_NOT_COMMITTED_NOT_PUSHED','tree':tree,'expected_parent':parent,'public_base':remote,'private_git_directory':str(S/'.git'),'references':refs,'user_boundary':'normal main sync after exact review then PAUSE; do not mark goal complete or dispatch another stage','provisional_unchanged':True}
(F/'FINAL_SEND_FREEZE.json').write_text(json.dumps(freeze,indent=2)+'\n');print(json.dumps({k:v for k,v in receipt.items() if k not in {'largest_new_30','new_banned'}},indent=2));p=F/'FINAL_SEND_FREEZE.json';print('freeze',p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest())
