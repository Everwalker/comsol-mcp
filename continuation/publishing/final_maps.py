import json,hashlib,shutil
from pathlib import Path
O=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/github-main-sync-20261013-r1');S=O/'send-tree';F=O/'final-r1';maps={};aliases=[]
inputs=[O/'SEND_MANIFEST_R3.json',O/'delta-r1/DELTA_MANIFEST.json',O/'delta-entries-r1/ENTRY_SOURCE_DELTA.json',O/'delta-r15/R15_SOURCE_DELTA.json',O/'delta-r15/R14_SOURCE_MEMBERSHIP_SUPPLEMENT.json',F/'FINAL_DELTA_MANIFEST.json']
for p in inputs:
 for row in json.loads(p.read_text()):
  source=row.get('source',row.get('original_path'));dest=row.get('published_path',row.get('path'));r={'original_path':source,'published_path':dest,'bytes':row['bytes'],'sha256':row['sha256'],'authority':'included source bytes only; frozen host identity and once admission do not transfer'}
  maps[dest]=r;aliases.append(r)
# Drop provisional regular-file views overridden by original public symlink preservation.
for r in json.loads((O/'PUBLIC_TYPE_PRESERVATION.json').read_text()):maps.pop(r['path'],None)
current=sorted(maps.values(),key=lambda r:r['published_path'])
(S/'continuation/publishing/SOURCE_TO_SEND_RELATIVE_MAP.json').write_text(json.dumps(current,indent=2)+'\n')
(S/'continuation/publishing/HISTORICAL_SOURCE_ALIASES.json').write_text(json.dumps(aliases,indent=2)+'\n')
logical=[]
state=json.loads((S/'continuation/checkpoint/CURRENT_STATE.json').read_text())
for r in state['reviewed_materials']:
 candidates=[x for x in aliases if x['original_path']==r['path'] and x['sha256']==r['sha256'] and x['bytes']==r['bytes']];assert candidates
 logical.append(dict(r,published_path=candidates[-1]['published_path']))
(S/'continuation/checkpoint/REVIEWED_MATERIALS_LOGICAL_MAP.json').write_text(json.dumps(logical,indent=2)+'\n')
# Source-only reconstruction scripts and scoped selection proofs; original provisional remains immutable.
for p in O.glob('*.py'):shutil.copyfile(p,S/'continuation/publishing'/p.name)
for dirname in ['delta-r1','delta-entries-r1','delta-r15']:
 d=O/dirname
 for p in d.glob('*.json'):shutil.copyfile(p,S/'continuation/publishing'/p.name)
for p in F.glob('*.json'):shutil.copyfile(p,S/'continuation/publishing'/p.name)
# Packet source state belongs to immutable historical stages; final current scoped state is STATES.json.
print('current portable source paths',len(current),'historical aliases',len(aliases),'reviewed_materials',len(logical))
