from pathlib import Path
import os, json, subprocess, sys, hashlib, time
OUT=Path(__file__).resolve().parent / "evidence"
CANDIDATE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/windows_appcontainer_sol_r19_20261013/candidate')
RUNTIME='/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python'
GUARD=Path('/Volumes/SSD/Comsol-MCP/execution-scratch/temp-loss-runtime-recovery-20261004-001/builder-preparation022/network-guard')
label=sys.argv[1]
base=json.loads((CANDIDATE.parent.parent/'windows_service_sid_sol_r17_20261013/SOURCE_FREEZE_R17.json').read_text())['source_files']
files=set(base)|{str(p.relative_to(CANDIDATE)) for p in CANDIDATE.rglob('*.py') if '.pytest_cache' not in p.parts}
blobs=OUT/'source-blobs';blobs.mkdir(exist_ok=True)
snapshot={}
for name in sorted(files):
    raw=(CANDIDATE/name).read_bytes();digest=hashlib.sha256(raw).hexdigest();snapshot[name]=digest
    if not (blobs/digest).exists():(blobs/digest).write_bytes(raw)
(OUT/f'{label}.source-before.json').write_text(json.dumps(snapshot,indent=2,sort_keys=True)+'\n')
log=OUT/f'{label}.guard.jsonl';fd=os.open(log,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
args=['-q','--disable-warnings','--junitxml='+str(OUT/f'{label}.junit.xml'),'--basetemp=/private/tmp/'+label, sys.argv[2],*sys.argv[3:]]
code='import pytest; raise SystemExit(pytest.main('+repr(args)+'))'
argv=[RUNTIME,'-B',str(GUARD/'guarded_entry.py'),'exec',code]
env=dict(os.environ,PYTHONPATH=f'{GUARD}:{CANDIDATE}',CODEX_NETWORK_GUARD_LOG=str(log),CODEX_NETWORK_GUARD_LABEL=label,CODEX_NETWORK_GUARD_RUN_ID=label,TMPDIR='/private/tmp',PYTHONDONTWRITEBYTECODE='1')
(OUT/f'{label}.argv.json').write_text(json.dumps({'argv':argv,'cwd':str(CANDIDATE),'pythonpath':env['PYTHONPATH']},indent=2)+'\n')
start=time.monotonic();p=subprocess.run(argv,cwd=CANDIDATE,env=env,capture_output=True)
(OUT/f'{label}.stdout.bin').write_bytes(p.stdout);(OUT/f'{label}.stderr.bin').write_bytes(p.stderr)
rows=[json.loads(x) for x in log.read_text().splitlines()]
r={'exit_code':p.returncode,'elapsed_seconds':time.monotonic()-start,'guard_active':sum(x['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for x in rows),'guard_denied':sum('DENIED' in x['event'] for x in rows),'test_args':args}
(OUT/f'{label}.receipt.json').write_text(json.dumps(r,indent=2)+'\n')
assert all(hashlib.sha256((CANDIDATE/name).read_bytes()).hexdigest()==value for name,value in snapshot.items()), 'source changed during test'
print(p.stdout.decode(errors='replace'));print(p.stderr.decode(errors='replace'));print(json.dumps(r));sys.exit(p.returncode)
