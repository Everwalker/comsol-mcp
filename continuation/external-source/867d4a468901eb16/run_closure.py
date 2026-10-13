from pathlib import Path
import os,json,subprocess,time,sys
O=Path(__file__).parent.resolve();B=O.parents[1];R=B/'repository';E=R/'docs/full_project_continuation_v2/evidence/intel-builder-execution-r1'
GUARD=Path('/Volumes/SSD/Comsol-MCP/execution-scratch/temp-loss-runtime-recovery-20261004-001/builder-preparation022/network-guard')
runtime='/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python'
log=O/'closure.guard.jsonl';fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
script=O/'verify_closure.py';code='from pathlib import Path; p=Path('+repr(str(script))+'); exec(compile(p.read_bytes(),str(p),"exec"),{"__file__":str(p),"__name__":"__main__"})'
argv=[runtime,'-B',str(GUARD/'guarded_entry.py'),'exec',code]
env=dict(os.environ,PYTHONPATH=f'{GUARD}:{E}:{R}',CODEX_NETWORK_GUARD_LOG=str(log),CODEX_NETWORK_GUARD_LABEL='ind-liblzma-r1-closure',CODEX_NETWORK_GUARD_RUN_ID='ind-liblzma-r1-closure',PYTHONDONTWRITEBYTECODE='1')
(O/'closure.argv.json').write_text(json.dumps({'argv':argv,'cwd':str(R)},indent=2)+'\n')
start=time.monotonic();result=subprocess.run(argv,cwd=R,env=env,capture_output=True)
(O/'closure.stdout.bin').write_bytes(result.stdout);(O/'closure.stderr.bin').write_bytes(result.stderr)
rows=[json.loads(line) for line in log.read_text().splitlines()]
receipt={'exit_code':result.returncode,'elapsed_s':time.monotonic()-start,'guard_active':sum(x['event']=='GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT' for x in rows),'guard_denied':sum('DENIED' in x['event'] for x in rows)}
(O/'closure.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(result.stdout.decode(errors='replace'));print(result.stderr.decode(errors='replace'));print(json.dumps(receipt));sys.exit(result.returncode)
