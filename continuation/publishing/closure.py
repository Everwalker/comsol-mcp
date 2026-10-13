import json,hashlib,stat,re
from pathlib import Path
O=Path('local-preservation/github-main-sync-20261013-r1');S=O/'send-tree';rows=json.loads((O/'SEND_MANIFEST_R2.json').read_text()); known={r['source']:r['path'] for r in rows}; added=[]; missing=[]; queued=list(S.rglob('*.json')); seen=set()
while queued:
 p=queued.pop()
 if p in seen:continue
 seen.add(p)
 try: obj=json.loads(p.read_text())
 except (ValueError,UnicodeError):continue
 def walk(v):
  if isinstance(v,dict):
   for a in v.values():yield from walk(a)
  elif isinstance(v,list):
   for a in v:yield from walk(a)
  elif isinstance(v,str) and v.startswith('/'):yield v
 for name in walk(obj):
  q=Path(name)
  if q.suffix not in {'.py','.java','.ps1','.sh','.sb'} or name in known:continue
  if '/site-packages/' in name or '/lib/python' in name or '/sources/' in name:continue
  if not name.startswith(('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/','/private/tmp/','/private/var/folders/')):continue
  if not q.is_file() or q.is_symlink():missing.append(name);continue
  raw=q.read_bytes();sha=hashlib.sha256(raw).hexdigest();dst='continuation/external-source/'+sha[:16]+'/'+q.name
  target=S/dst;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
  r={'source':name,'path':dst,'bytes':len(raw),'sha256':sha,'mode':oct(stat.S_IMODE(q.stat().st_mode))};added.append(r);known[name]=dst
rows+=added
for n,v in [('SEND_MANIFEST_R3.json',rows),('EXTERNAL_SOURCE_MAP.json',added),('MISSING_SOURCE_REFERENCES.json',sorted(set(missing)))]: (O/n).write_text(json.dumps(v,indent=2)+'\n')
print('added',len(added),'bytes',sum(r['bytes'] for r in added),'missing',len(set(missing)))
# Content scan reports paths and types only; do not print candidate secret bytes.
patterns=[('github-token',rb'gh[pousr]_[A-Za-z0-9]{30,}'),('aws-access',rb'AKIA[0-9A-Z]{16}'),('credential-url',rb'https?://[^\s/"\']+:[^\s/@"\']+@'),('private-key-block',rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----$')]
findings=[]
for r in rows:
 raw=(S/r['path']).read_bytes()
 for label,pat in patterns:
  if re.search(pat,raw):findings.append({'path':r['path'],'type':label,'sha256':r['sha256']})
(O/'SECRET_PATTERN_SCAN.json').write_text(json.dumps({'files':len(rows),'findings':findings,'scope':'bounded strong credential signatures; not blanket secret-free proof'},indent=2)+'\n')
print('scan findings',len(findings))
