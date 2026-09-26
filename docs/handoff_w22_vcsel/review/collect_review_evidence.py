"""Collect only Reviewer-owned science/log evidence, excluding control secrets."""
import argparse,hashlib,json,zipfile
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('--names',nargs='+',required=True);ap.add_argument('--archive',required=True)
a=ap.parse_args();root=Path(r'C:\Temp\w22_20260926');files=[]
for name in a.names:
    assert name.replace('_','').isalnum()
    run=root/name
    status=json.loads((run/'summary.json').read_text())['status']
    assert status in ['NATIVE_SMOKE_COMPLETED','FRESH_WORKER_REOPEN_COMPLETED','FAILED']
    for base in ['summary.json','transcript.jsonl','package_origin.json','runner_source.py','runtime/isolation_receipt.json']:
        p=run/base
        if p.exists():files.append(p)
    files.extend(p for p in (run/'project').rglob('*') if p.is_file())
    for suffix in ['.log','_reviewer_dispatch.json']:
        p=root/(name+suffix)
        if p.exists():files.append(p)
manifest={str(p.relative_to(root)).replace('\\','/'):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files}
archive=root/a.archive
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
    for p in files:z.write(p,str(p.relative_to(root)))
    z.writestr('REVIEWER_EVIDENCE_MANIFEST.json',json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'archive':str(archive),'files':len(files),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size},indent=2))
