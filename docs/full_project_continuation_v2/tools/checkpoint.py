#!/usr/bin/env python3
"""Update only compact continuation pointer, preserving jobs/blockers and old state."""
import argparse,json
from pathlib import Path
from datetime import datetime,timezone
from init_resume import load,atomic_json
ROOT=Path(__file__).resolve().parents[1]
def update(path,phase,task,next_action,candidate=None):
    data=load(path)
    data.update(phase=phase,task=task,next_action=next_action,updated_at=datetime.now(timezone.utc).isoformat())
    if candidate:data['candidate_source']=candidate
    atomic_json(path,data);return data
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--phase',required=True);p.add_argument('--task',required=True);p.add_argument('--next',dest='next_action',required=True);p.add_argument('--candidate');a=p.parse_args()
    pin=load(ROOT/'PIN.json');r=update(a.repo/pin['handoff_directory']/'state/RESUME.json',a.phase,a.task,a.next_action,a.candidate)
    print(json.dumps({'status':'CONTINUATION_POINTER_SAVED','phase':r['phase'],'task':r['task'],'does_not_certify_completion':True}))
