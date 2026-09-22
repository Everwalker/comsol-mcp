from pathlib import Path
import datetime,json,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[4]
RUN=Path(__file__).resolve().parent
EXPECTED='a3f39b3022b417e16c3087b8b35f1daa06f30b8a'
groups=[('nodes',['--nodes-only']),('numeric',['--numeric-only']),('probe',['--probe-transient-only']),('m3',[]),('cutplane',['--cutplane-only']),('export',['--export-only','--export-transient-budget']),('control',['--control-only']),('studyfault',['--study-fault-only'])]
records=[]
for name,flags in groups:
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if head!=EXPECTED:raise RuntimeError('source HEAD changed')
    destination=ROOT/'evidence/phase4_3/runs'/f'final6_{name}_a3f39b3_20260922T0907Z'
    command=[sys.executable,'tools/g3_3_protocol_acceptance.py',*flags,'--run-dir',str(destination.relative_to(ROOT))]
    record={'name':name,'run_dir':str(destination.relative_to(ROOT)),'command':command,'cwd':str(ROOT),'interpreter':sys.executable,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'RUNNING'}
    records.append(record)
    (RUN/'batch_status.json').write_text(json.dumps({'source_commit':EXPECTED,'groups':records,'overall':False},indent=2)+'\n')
    print('START',name,flush=True)
    with (RUN/f'{name}.stdout_stderr.log').open('w') as log:
        result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    receipt=json.loads((destination/'run_status.json').read_text()) if (destination/'run_status.json').exists() else {}
    passed=result.returncode==0 and receipt.get('status')=='PASS' and receipt.get('changed_source_files')==[]
    record.update(exit_code=result.returncode,status='PASS' if passed else 'FAIL',finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),receipt=receipt)
    (RUN/'batch_status.json').write_text(json.dumps({'source_commit':EXPECTED,'groups':records,'overall':passed and len(records)==len(groups)},indent=2)+'\n')
    print(record['status'],name,flush=True)
    if not passed:sys.exit(1)
