from __future__ import annotations
import hashlib, json, subprocess, zipfile
from pathlib import Path

run = Path(__file__).resolve().parent
src = run / 'fresh_extract' / 'repository'
inner = src / 'rebuild_runtime'
installed = inner / 'install_venv' / 'lib' / 'python3.14' / 'site-packages' / 'comsol_mcp'
wheel = inner / 'wheelhouse' / 'comsol_mcp-0.1.9-py3-none-any.whl'

def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
def sha_file(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def files(root: Path) -> dict[str,str]:
    out={}
    for p in root.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:
            out[p.relative_to(root).as_posix()] = sha_file(p)
    return out
source_files=files(src/'comsol_mcp')
installed_files=files(installed)
missing=sorted(set(source_files)-set(installed_files))
extra=sorted(set(installed_files)-set(source_files))
changed=sorted(k for k in set(source_files)&set(installed_files) if source_files[k]!=installed_files[k])
with zipfile.ZipFile(wheel) as z:
    wheel_files={n[len('comsol_mcp/'):]:sha_bytes(z.read(n)) for n in z.namelist() if n.startswith('comsol_mcp/') and not n.endswith('/')}
wm=sorted(set(source_files)-set(wheel_files)); we=sorted(set(wheel_files)-set(source_files)); wc=sorted(k for k in set(source_files)&set(wheel_files) if source_files[k]!=wheel_files[k])
# Validate packaged JSON schemas without emitting document values.
schemas=[]
for rel in sorted(k for k in source_files if k.startswith('data/') and k.endswith('.json')):
    p=src/'comsol_mcp'/rel
    try:
        doc=json.loads(p.read_text())
        schemas.append({'path':rel,'valid_json':True,'top_level_type':type(doc).__name__,'top_level_keys':sorted(doc) if isinstance(doc,dict) else []})
    except Exception as exc:
        schemas.append({'path':rel,'valid_json':False,'error_type':type(exc).__name__})
py=inner/'install_venv'/'bin'/'python'
proc=subprocess.run([str(py),'-m','pip','check'],cwd=str(run),capture_output=True,text=True)
pip_text=(proc.stdout+proc.stderr).strip()
archive=json.loads((run/'archive_result.json').read_text())
archive_manifest=json.loads((run/'fresh_extract'/'ARCHIVE_MANIFEST.json').read_text())
bridges=[]
for p in [Path('audit/publication_source_bridge.json'),Path('audit/publication_reference_closure.json'),Path('audit/publication_history_scan.json')]:
    if p.exists():
        try: status=json.loads(p.read_text()).get('status')
        except Exception: status=None
        bridges.append({'path':p.as_posix(),'sha256':sha_file(p),'status':status})
result={
 'schema':'comsol-mcp/fresh-extracted-source-rebuild/1',
 'status':'PASS' if not (missing or extra or changed or wm or we or wc or proc.returncode or not schemas or not all(x['valid_json'] for x in schemas)) else 'FAIL',
 'publication':{'commit':'84ec0962a6424cd54c523770e4898bf42b7af645','tree':'854d2e3095252913641085ed20584930cb5bbb9f','parent':'2cb4627924d1a3240818ea7cd00453d4bd2d2da8'},
 'archive':{'path':'comsol_mcp_g3_3_public_84ec096.zip','sha256':archive['archive_sha256'],'member_count':archive['member_count'],'manifest_commit':archive_manifest.get('commit'),'manifest_tree':archive_manifest.get('tree')},
 'fresh_extract':{'verified_member_count':archive['fresh_extraction']['archive_member_count'],'source_root':'fresh_extract/repository'},
 'wheel':{'path':'rebuild_runtime/wheelhouse/comsol_mcp-0.1.9-py3-none-any.whl','sha256':sha_file(wheel),'size':wheel.stat().st_size},
 'source_to_installed_resources':{'source_file_count':len(source_files),'installed_file_count':len(installed_files),'missing':missing,'extra':extra,'changed':changed,'source_manifest_sha256':sha_bytes(json.dumps(source_files,sort_keys=True).encode()),'installed_manifest_sha256':sha_bytes(json.dumps(installed_files,sort_keys=True).encode())},
 'wheel_resource_hash_compare':{'wheel_file_count':len(wheel_files),'missing':wm,'extra':we,'changed':wc,'wheel_manifest_sha256':sha_bytes(json.dumps(wheel_files,sort_keys=True).encode())},
 'schema_resources':schemas,
 'pip_check':{'exit_code':proc.returncode,'output':pip_text},
 'runtime_checks':json.loads((run/'rebuild_run_status.json').read_text()).get('step_exit_codes'),
 'source_bridge_receipts':bridges,
 'excluded_followup_refs':['evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/fresh_restore/RESTORE_RECEIPT.json','evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/fresh_restore/history_preservation_audit.json','evidence/phase4_3/runs/independent_acceptance_20260922T004753Z/current_history_vs_fresh_pin.json'],
}
(run/'archive_rebuild_checks.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps({'status':result['status'],'source_files':len(source_files),'installed_files':len(installed_files),'wheel_files':len(wheel_files),'schemas':len(schemas),'pip_check_exit':proc.returncode,'wheel_sha256':result['wheel']['sha256']},sort_keys=True))
