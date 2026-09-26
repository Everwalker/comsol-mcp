"""Export completed task-owned W22 evidence without credentials/runtime files."""
import argparse, hashlib, json, sqlite3, zipfile
from pathlib import Path


def main(base, output):
    base=Path(base).resolve(); output=Path(output)
    assert not output.exists(), 'Append-only export: choose a new output'
    manifest=[]
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED) as archive:
        def add(name, raw):
            archive.writestr(name,raw)
            manifest.append({'path':name,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        for run in sorted(base.iterdir()):
            if not run.is_dir() or not (run/'summary.json').is_file(): continue
            if json.loads((run/'summary.json').read_text()).get('status')=='RUNNING': continue
            files=[run/'summary.json',run/'transcript.jsonl',run/'package_origin.json',run/'runner_source.py',
                   run/'runtime'/'private_runtime.json',run/'runtime'/'isolation_receipt.json',
                   base/(run.name+'.log')]
            if (run/'project').is_dir():
                files += [p for p in (run/'project').rglob('*') if p.is_file()
                          and p.suffix.lower() in {'.mph','.java','.json','.txt','.csv','.xml','.png'}]
            for path in files:
                if path.is_file(): add(str(path.relative_to(base)).replace('\\','/'),path.read_bytes())
            candidates=list((run/'control').rglob('operations.sqlite3')) if (run/'control').exists() else []
            assert len(candidates)<=1, 'Ambiguous operation store'
            if candidates:
                db_path=candidates[0]
                # Export only operation/result and artifact evidence. Runtime,
                # session and endpoint tables/files are deliberately excluded.
                db=sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row
                ledger={}
                for table in ('operations','jobs','job_events','artifacts'):
                    ledger[table]=[dict(row) for row in db.execute('SELECT * FROM '+table)]
                db.close()
                add(run.name+'/ledger_evidence.json',json.dumps(ledger,ensure_ascii=False).encode())
        add('manifest.json',json.dumps(manifest,indent=2).encode())
    print(json.dumps({'archive':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(manifest)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('base');parser.add_argument('output')
    a=parser.parse_args();main(a.base,a.output)
