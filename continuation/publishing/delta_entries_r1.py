import os,stat,json,hashlib,re
from pathlib import Path
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');O=B/'local-preservation/github-main-sync-20261013-r1';S=O/'send-tree';D=O/'delta-entries-r1';D.mkdir(mode=0o700)
roots=[(B/'repository',''),(B/'local-preservation/windows_appcontainer_sol_r19_20261013','continuation/candidates/windows-r19'),(B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r14','continuation/candidates/w23-r14'),(B/'local-preservation/w24-coupled-science-20261013-candidate05','continuation/candidates/w24-coupled05')]
newtypes={'.zsh','.bash','.fish','.sql','.schema','.html','.css','.js','.ts'};records=[];checked=[];missing=[];paths=set()
for root,prefix in roots:
 for p in root.rglob('*'):
  if not p.is_file() or p.is_symlink() or any(x in p.parts for x in {'.git','node_modules','comsol-server-home','__pycache__'}):continue
  if p.suffix in newtypes:
   paths.add(p)
   if p.suffix in {'.zsh','.bash','.fish'}:paths.update(q for q in p.parent.iterdir() if q.is_file() and not q.is_symlink() and q.suffix=='.json')
packet=B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r14/root-review-packet/final6/ROOT_REVIEW_PACKET_ACTIVITY022_R14.json'
freeze=B/'local-preservation/w26-offline-venv-unit-r1/FREEZE_R1.json'
rootfreeze=B/'local-preservation/windows_appcontainer_sol_r19_20261013/SOURCE_FREEZE_R19.json'
queue=[packet,freeze,rootfreeze];seen=set();knownsuffix=newtypes|{'.py','.java','.ps1','.psm1','.sh','.sb','.toml','.ini','.lock'}
def values(v,key=''):
 if isinstance(v,dict):
  for k,a in v.items():yield from values(a,k)
 elif isinstance(v,list):
  for a in v:yield from values(a,key)
 elif isinstance(v,str) and v.startswith('/'):yield key,v
while queue:
 p=queue.pop()
 if p in seen:continue
 seen.add(p)
 try:d=json.loads(p.read_text())
 except (ValueError,FileNotFoundError,UnicodeError):continue
 for key,value in values(d):
  q=Path(value)
  if ' -m ' in value or '\n' in value:continue
  source=q.suffix in knownsuffix
  config=q.suffix=='.json' and any(x in key.lower() or x in q.name.lower() for x in ('freeze','metadata','config','schema','invocation','approval','blueprint','controller'))
  if not source and not config:continue
  if not value.startswith((str(B)+'/', '/private/var/folders/','/private/tmp/')):continue
  if '/site-packages/' in value or '/lib/python' in value:continue
  if not q.is_file() or q.is_symlink():missing.append({'reference':value,'key':key,'required':'HISTORICAL_OR_NONCREATED_REFERENCE_ONLY_REQUIRES_ROOT_CLASSIFICATION'});continue
  checked.append({'reference':value,'key':key,'kind':'entry/source/config'})
  paths.add(q)
  if config and 'freeze' in q.name.lower():queue.append(q)
for p in sorted(paths):
 raw=p.read_bytes();st=p.lstat();assert stat.S_ISREG(st.st_mode)
 dest=None
 for root,prefix in roots:
  if p.is_relative_to(root):dest=str(Path(prefix)/p.relative_to(root));break
 if dest is None:
  if p.is_relative_to(B/'local-preservation/w26-offline-venv-unit-r1'):dest='continuation/packets/w26-offline-venv-unit-r1/'+p.name
  else:dest='continuation/external-source/'+hashlib.sha256(raw).hexdigest()[:16]+'/'+p.name
 q=S/dest
 if q.exists() and not q.is_symlink() and q.read_bytes()==raw:continue
 q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(raw);q.chmod(0o755 if st.st_mode&0o111 else 0o644);assert q.read_bytes()==raw
 records.append({'source':str(p),'published_path':dest,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'source_relative':str(p.relative_to(B)) if p.is_relative_to(B) else None,'identity_not_migrated':True})
for name,data in [('ENTRY_SOURCE_DELTA.json',records),('EXPLICIT_POINTER_CHECK.json',checked),('REFERENCE_ONLY_OR_MISSING_POINTERS.json',missing)]: (D/name).write_text(json.dumps(data,indent=2)+'\n')
(D/'STAGE_DELTA.nul').write_bytes(b'\0'.join(r['published_path'].encode() for r in records)+b'\0')
patterns=[rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'AKIA[0-9A-Z]{16}',rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----$'];violations=[]
for r in records:
 raw=(S/r['published_path']).read_bytes()
 if any(re.search(p,raw) for p in patterns):violations.append(r['published_path'])
(D/'DELTA_SECRET_SCAN.json').write_text(json.dumps({'delta_files':len(records),'violations':violations,'native':0,'push':0},indent=2)+'\n');print('new delta',len(records),sum(r['bytes'] for r in records),'explicit refs',len(checked),'missing',len(missing),'violations',len(violations))
