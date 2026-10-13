import json,hashlib,shutil,stat
from pathlib import Path
O=Path('local-preservation/github-main-sync-20261013-r1');S=O/'send-tree';a=json.loads((O/'SEND_MANIFEST.json').read_text());local=json.loads((O/'LOCAL_ONLY_MANIFEST.json').read_text());kept=[]
code={'.py','.java','.ps1','.psm1','.cs','.c','.h','.sh','.bat','.cmd','.toml','.lock','.ini','.yaml','.yml','.patch','.diff','.sb'}
for r in a:
 old=r['path'];p=Path(old);suffix=p.suffix;name=p.name.lower()
 rawzone=('/evidence/' in old or '/audit/' in old or '/offline-' in old or '/root-review-packet/' in old)
 useful=any(x in name for x in ('freeze','receipt','result','check','status','manifest','decision','finding','binding','scope','fail','unknown','mismatch','diagnos','review','plan','disposition','primary','checkpoint','closure','guard','preserv','runtime','budget'))
 drop=rawzone and suffix not in code|{'.md','.rst'} and (suffix in {'.txt','.csv','.xml','.jsonl'} or not useful)
 if drop:
  (S/old).unlink();local.append(dict(r,reason='raw-local-compact-public-index'));continue
 new=old[11:] if old.startswith('repository/') else old
 if new!=old:
  d=S/new;d.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(S/old),str(d));r=dict(r,path=new)
 kept.append(r)
# Exact synthetic private-key-marker fixture, not private key material. Preserve full required test source.
B=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2')
for source,dest in [(B/'repository/tests/test_full_project_release.py',S/'tests/test_full_project_release.py'),(B/'local-preservation/windows_appcontainer_sol_r19_20261013/candidate/tests/test_full_project_release.py',S/'continuation/candidates/windows-r19/candidate/tests/test_full_project_release.py'),(B/'local-preservation/w23-saved-mph-science-readback-20261013-candidate20-r14/tests/test_full_project_release.py',S/'continuation/candidates/w23-r14/tests/test_full_project_release.py'),(B/'repository/LICENSE',S/'LICENSE')]:
 raw=source.read_bytes();dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);kept.append({'source':str(source),'path':str(dest.relative_to(S)),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'mode':oct(stat.S_IMODE(source.stat().st_mode))})
for name,data in [('SEND_MANIFEST_R2.json',kept),('LOCAL_ONLY_MANIFEST_R2.json',local)]: (O/name).write_text(json.dumps(data,indent=2)+'\n')
print(len(kept),sum(r['bytes'] for r in kept),len(local));
