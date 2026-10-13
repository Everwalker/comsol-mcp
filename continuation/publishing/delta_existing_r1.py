import os,stat,json,hashlib
from pathlib import Path
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');O=B/'local-preservation/github-main-sync-20261013-r1';S=O/'send-tree';D=O/'delta-r1';D.mkdir(mode=0o700);records=[]
def copy(source,dest,expected=None):
 p=Path(source);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_uid==os.getuid() and a.st_nlink==1
  with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read()
  b=os.fstat(fd);assert (a.st_dev,a.st_ino,a.st_size,a.st_mode,a.st_mtime_ns)==(b.st_dev,b.st_ino,b.st_size,b.st_mode,b.st_mtime_ns)
 finally:os.close(fd)
 sha=hashlib.sha256(raw).hexdigest()
 if expected:
  assert len(raw)==expected['bytes'] and sha==expected['sha256'];assert a.st_dev==expected['dev'] and a.st_ino==expected['inode'] and a.st_uid==expected['uid'] and a.st_nlink==expected['nlink'] and stat.S_IMODE(a.st_mode)==expected['mode']
 q=S/dest;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(raw);q.chmod(0o755 if a.st_mode&0o111 else 0o644);assert q.read_bytes()==raw
 records.append({'original_path':str(p),'published_path':str(dest),'bytes':len(raw),'sha256':sha,'original_identity':{'dev':a.st_dev,'inode':a.st_ino,'uid':a.st_uid,'nlink':a.st_nlink,'mode':stat.S_IMODE(a.st_mode)},'meaning':'source/config bytes only; original host identity/admission not transferred'})
f=json.loads((B/'local-preservation/w26-offline-venv-unit-r1/FREEZE_R1.json').read_text())
assert len(f['work_files'])==9
for row in f['work_files']:copy(row['path'],'continuation/packets/w26-offline-venv-unit-r1/frozen-work/'+Path(row['path']).name,row)
for row in f['files']:copy(row['path'],'continuation/packets/w26-offline-venv-unit-r1/'+Path(row['path']).name,row)
for number in (3,4,5):
 p=B/f'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r14/root-review-packet/final{number}-exact-frozen-source-preservation/tests/test_full_project_release.py'
 assert p.read_bytes()==(B/'repository/tests/test_full_project_release.py').read_bytes()
 copy(p,f'continuation/candidates/w23-r14/root-review-packet/final{number}-exact-frozen-source-preservation/tests/test_full_project_release.py')
for name in ['w26-offline-venv-r1-independent-source-review','w23-activity022-r14-independent-source-review','root-activity022-r14-source-review','root-activity022-r14-first-audit-failure']:
 root=B/'local-preservation'/name
 for p in sorted(root.iterdir()):
  if p.is_file() and not p.is_symlink() and p.suffix in {'.json','.py','.md','.jsonl','.xml','.bin'} and p.stat().st_size<=2*1024*1024:copy(p,'continuation/packets/'+name+'/'+p.name)
for rootsha,name in [('9059248dee70ece3390b415d8c2283adc59486ef138b62071b184cd965d0df4c','w26-offline-venv-r1-independent-source-review'),('048dfa05fb324e53f5a6c0b68ab1b6779cbab13e7f8190ab7cd348b393009731','w23-activity022-r14-independent-source-review')]:assert hashlib.sha256((S/'continuation/packets'/name/'REVIEW_RECEIPT.json').read_bytes()).hexdigest()==rootsha
(S/'continuation/packets/w26-offline-venv-unit-r1/FROZEN_WORK_AND_SOURCE_ALIASES.json').write_text(json.dumps(records[:41],indent=2)+'\n')
(D/'DELTA_MANIFEST.json').write_text(json.dumps(records,indent=2)+'\n');(D/'DELTA_STATUS.json').write_text(json.dumps({'status':'PASS_EXACT_DELTA_BYTES','source_records':len(records),'work_files':9,'synthetic_old_tests':3,'target_native':0,'commit':0,'push':0,'pending':'R15 and final Root canonical pages'},indent=2)+'\n')
(D/'STAGE_DELTA.nul').write_bytes(b'\0'.join(r['published_path'].encode() for r in records)+b'\0continuation/packets/w26-offline-venv-unit-r1/FROZEN_WORK_AND_SOURCE_ALIASES.json\0')
print('delta',len(records),'bytes',sum(r['bytes'] for r in records),'work9 verified')
