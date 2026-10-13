#!/usr/bin/env python3
"""Show compact continuation state; no native acceptance inference."""
import argparse,json
from pathlib import Path
from init_resume import load
ROOT=Path(__file__).resolve().parents[1]
def status(repo):
    pin=load(ROOT/'PIN.json');root=Path(repo)/pin['handoff_directory']/'state'
    if not (root/'INITIALIZED.json').is_file():return {'status':'NOT_INDEXED','native_execution_certified':False,'next':'Run init_resume.py, not the original init_completion.py'}
    r=load(root/'RESUME.json');i=load(root/'CURRENT_INDEX.json')
    # Report live task statuses without rewriting either upstream or imported state.
    upstream=Path(repo)/pin['upstream_execution_directory']/'state/TASKS.json'
    live=load(upstream)
    task_states={x['id']:x.get('status','UNASSESSED') for x in live.get('tasks',[]) if isinstance(x,dict) and 'id' in x}

    return {'status':r.get('status'),'phase':r.get('phase'),'task':r.get('task'),'candidate_source':r.get('candidate_source'),
            'next_action':r.get('next_action'),'counts':i['counts'],'waived_native_targets':i['scope']['user_requested_skip_native'],
            'task_states':task_states,
            'active_jobs_discovered':r.get('active_jobs_discovered',False),'historical_unknown_policy':r.get('historical_unknown_policy'),
            'checkpoint_reported_status':r.get('checkpoint_reported_state'),
            'candidate_index_path':str(root/'CANDIDATE_INDEX.json'),
            'candidate_index_present':(root/'CANDIDATE_INDEX.json').is_file(),
            'notice':'Reported state and imported counts only; original source ledgers may now be newer. No automatic native or final acceptance.',
            'native_execution_certified':False}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);a=p.parse_args();print(json.dumps(status(a.repo),ensure_ascii=False,indent=2))
