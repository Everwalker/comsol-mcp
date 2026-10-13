import json,os,hashlib
from pathlib import Path
from strict_derived import read_regular
D=Path(__file__).parent;M=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w26-wheel048-metadata-r1-46ehevk6')
a=json.loads(read_regular(D/'AVAILABILITY_R2.json')[0]);m=json.loads(read_regular(M/'PARSED.json')[0]);wheel=next(q for q in m['files'] if q['filename']=='wheel-0.48.0-py3-none-any.whl')
assert wheel['yanked'] is False and wheel['size']==33320 and m['Requires-Dist']==['packaging>=24.0']
rows=[{'artifact_id':'runtime-'+q['name'],'filename':q['filename'],'version':q['version'],'url':q['source_url'],'origin_host':'files.pythonhosted.org','expected_bytes':q['bytes'],'expected_sha256':q['sha256'],'role':'ARM CPython3.12.13 base runtime exact current lock'} for q in a['missing_base_exact_acquisition']]
rows.append({'artifact_id':'backend-wheel-0.48.0','filename':wheel['filename'],'version':'0.48.0','url':wheel['url'],'origin_host':'files.pythonhosted.org','expected_bytes':wheel['size'],'expected_sha256':wheel['digests']['sha256'],'role':'separate fixed backend; packaging26.2 exact shared runtime input'})
assert len(rows)==32 and len({r['filename'] for r in rows})==32
p=D/'EXACT32_DOWNLOAD_MANIFEST.json';x={'schema':'comsol-w26-exact-wheel-acquisition/1','status':'SOURCE_ONLY_DOWNLOAD_NOT_ADMITTED','artifacts':rows,'count':len(rows),'total_bytes':sum(q['expected_bytes'] for q in rows),'original_available_reuse':'cffi2.1.1/pycparser3.0/setuptools80.9.0/eightdesktop original wheel only, each exact original file record in AVAILABILITY_R2','backend_closure':'setuptools80.9.0 + wheel0.48.0 + packaging26.2 (fixed runtime input in32); actual wheel metadata later rechecked before any install','metadata_refs':[read_regular(p)[1] for p in [M/'REQUEST.json',M/'HTTP.json',M/'metadata.raw',M/'PARSED.json',M/'RESULT.json',D/'AVAILABILITY_R2.json']]}
with p.open('xb') as f:f.write((json.dumps(x,indent=2,sort_keys=True)+'\n').encode());f.flush();os.fsync(f.fileno())
print(read_regular(p)[1]);print('32 aggregate bytes',x['total_bytes'])
# Explicit old/new marker decisions over all observed marker expressions.
old=json.loads((D.parent/'w26-helper-successor-r1/AVAILABILITY_FINAL_R1.json').read_bytes());new=a
for k in ['base_runtime','desktop_external_overlay','backend','counts','missing_base_exact_acquisition']:assert old[k]==new[k],k
import tomllib
from packaging.markers import Marker
root=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');lock=tomllib.loads(read_regular(root/'repository/uv.lock')[0].decode());marks=set()
def scan(q):
 if isinstance(q,dict):
  for k,v in q.items():
   if k=='marker' and isinstance(v,str):marks.add(v)
   elif k=='resolution-markers':marks.update(v)
   else:scan(v)
 elif isinstance(q,list):
  for v in q:scan(v)
scan(lock)
for r in old['desktop_external_overlay']:
 for t in r['Requires-Dist']:
  from packaging.requirements import Requirement
  req=Requirement(t)
  if req.marker:marks.add(str(req.marker))
checks=[{'marker':m,'old':Marker(m).evaluate(old['marker_environment']),'target':Marker(m).evaluate(new['marker_environment'])} for m in sorted(marks)]
assert all(r['old']==r['target'] for r in checks)
(D/'MARKER_CORRECTION.json').write_text(json.dumps({'old_implementation_version':old['marker_environment']['implementation_version'],'target_implementation_version':new['marker_environment']['implementation_version'],'complete_availability_rows_unchanged':True,'actual_marker_checks':checks,'scope':'finite current lock and bound desktop metadata'},indent=2,sort_keys=True)+'\n')
