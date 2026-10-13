from pathlib import Path
import os, subprocess, sys, json, hashlib
ROOT=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/windows_policy_owner_sol_r10_20261012/candidate')
OUT=Path(__file__).resolve().parent
RUNTIME='/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python'
GUARD=Path('/Volumes/SSD/Comsol-MCP/execution-scratch/temp-loss-runtime-recovery-20261004-001/builder-preparation022/network-guard')
label=sys.argv[1]
nodes=sys.argv[2:]
log=OUT/f'{label}.guard.jsonl'
fd=os.open(log,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd)
env=dict(os.environ, PYTHONPATH=f'{GUARD}:{ROOT}', CODEX_NETWORK_GUARD_LOG=str(log), CODEX_NETWORK_GUARD_LABEL=label,CODEX_NETWORK_GUARD_RUN_ID=label, TMPDIR='/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T',PYTHONDONTWRITEBYTECODE='1')
if label.startswith('sample'):
 code="from comsol_mcp._w26_memory_resources import _DarwinMemoryApi; import json; print(json.dumps(_DarwinMemoryApi().read(),sort_keys=True))"
else:
 args=['-q','--disable-warnings','--maxfail=1','--junitxml='+str(OUT/f'{label}.junit.xml'),*nodes]
 code='import pytest; raise SystemExit(pytest.main('+repr(args)+'))'
p=subprocess.run([RUNTIME,'-B',str(GUARD/'guarded_entry.py'),'exec',code],env=env,cwd=ROOT,capture_output=True,text=True)
(OUT/f'{label}.stdout.txt').write_text(p.stdout);(OUT/f'{label}.stderr.txt').write_text(p.stderr)
rows=[json.loads(x) for x in log.read_text().splitlines()]
receipt={'exit_code':p.returncode,'nodes':nodes,'guard_active':sum(x['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for x in rows),'guard_denied':sum('DENIED' in x['event'] for x in rows)}
(OUT/f'{label}.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(p.stdout);print(p.stderr);print(json.dumps(receipt));sys.exit(p.returncode)
