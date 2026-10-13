from pathlib import Path
import os,json,subprocess,time
OUT=Path(__file__).parent;BASE=Path('/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2');REPO=BASE/'repository'
RUNTIME='/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python'
GUARD=Path('/Volumes/SSD/Comsol-MCP/execution-scratch/temp-loss-runtime-recovery-20261004-001/builder-preparation022/network-guard')
label='openssl359-runtime-r1-independent-closure001';log=OUT/(label+'.guard.jsonl')
fd=os.open(log,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
code='import runpy; runpy.run_path('+repr(str(OUT/'verify_closure.py'))+',run_name="__main__")'
argv=[RUNTIME,'-B',str(GUARD/'guarded_entry.py'),'exec',code]
env=dict(os.environ,PYTHONPATH=f'{GUARD}:{REPO}/docs/full_project_continuation_v2/evidence/intel-builder-execution-r1:{REPO}',CODEX_NETWORK_GUARD_LOG=str(log),CODEX_NETWORK_GUARD_LABEL=label,CODEX_NETWORK_GUARD_RUN_ID=label,PYTHONDONTWRITEBYTECODE='1')
(OUT/(label+'.argv.json')).write_text(json.dumps({'argv':argv,'cwd':str(REPO),'pythonpath':env['PYTHONPATH']},indent=2)+'\n')
start=time.monotonic();p=subprocess.run(argv,cwd=REPO,env=env,capture_output=True)
(OUT/(label+'.stdout.bin')).write_bytes(p.stdout);(OUT/(label+'.stderr.bin')).write_bytes(p.stderr)
rows=[json.loads(line) for line in log.read_text().splitlines()]
receipt={'rc':p.returncode,'elapsed_seconds':time.monotonic()-start,'guard_ACTIVE':sum(row['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for row in rows),'guard_DENY':sum('DENIED' in row['event'] for row in rows)}
(OUT/(label+'.receipt.json')).write_text(json.dumps(receipt,indent=2)+'\n')
print(p.stdout.decode(errors='replace'));print(p.stderr.decode(errors='replace'));print(json.dumps(receipt));raise SystemExit(p.returncode)
