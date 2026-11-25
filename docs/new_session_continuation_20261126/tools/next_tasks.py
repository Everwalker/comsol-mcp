#!/usr/bin/env python3
"""Read-only task navigation. Never runs work, edits state or certifies acceptance."""
from __future__ import annotations
import argparse,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATUSES={'TODO','RUNNING','IMPLEMENTED','DONE','BLOCKED','NOT_APPLICABLE'}
FINISHED={'DONE','NOT_APPLICABLE'}

def validate_plan(plan):
    tasks=plan.get('tasks',[])
    ids=[t['id'] for t in tasks]
    if not ids or len(ids)!=len(set(ids)):raise ValueError('empty or duplicate task ids')
    byid={t['id']:t for t in tasks}
    visiting=set();seen=set()
    def visit(tid):
        if tid in visiting:raise ValueError('dependency cycle at '+tid)
        if tid in seen:return
        if tid not in byid:raise ValueError('unknown dependency '+tid)
        visiting.add(tid)
        for dep in (byid[tid].get('depends_on',[])+byid[tid].get('attempt_dependencies',[])
                    +byid[tid].get('finished_dependencies',[])+byid[tid].get('children',[])):visit(dep)
        visiting.remove(tid);seen.add(tid)
    for tid in ids:
        visit(tid)
        children=byid[tid].get('children',[])
        if len(children)!=len(set(children)) or any(c not in byid or c==tid for c in children):
            raise ValueError('invalid children for '+tid)
        if any(byid[c].get('parent_id')!=tid for c in children):
            raise ValueError('child parent mismatch for '+tid)
    return byid

def load_state(plan,state):
    byid=validate_plan(plan)
    records=state.get('tasks',{})
    if set(records)!=set(byid):raise ValueError('state task ids must match the plan; do not silently reset progress')
    checks=state.get('target_checks',{})
    for key,record in [*records.items(),*checks.items()]:
        if not isinstance(record,dict) or record.get('status') not in STATUSES:
            raise ValueError('invalid status for '+key)
        if 'implementation_done' in record and type(record['implementation_done']) is not bool:
            raise ValueError('implementation_done must be boolean for '+key)
        if 'execution_complete' in record and type(record['execution_complete']) is not bool:
            raise ValueError('execution_complete must be boolean for '+key)
        if record.get('execution_complete') and not record.get('evidence_refs'):
            raise ValueError('actual attempt requires evidence: '+key)
        if record['status'] in FINISHED and not record.get('evidence_refs'):
            raise ValueError('finished record must cite evidence or applicability decision: '+key)
    return records,checks

def implemented(record):
    return record['status'] in FINISHED|{'IMPLEMENTED'} or record.get('implementation_done',False)

def navigate(plan,state):
    records,checks=load_state(plan,state)
    ready=[];blocked=[];waiting=[]
    for task in sorted(plan['tasks'],key=lambda t:(t.get('priority_wave',''), t['id'])):
        tid=task['id'];record=records[tid]
        if record['status'] in FINISHED:continue
        if record['status']=='BLOCKED':
            blocked.append({'id':tid,'implementation_done':implemented(record),'blocker':record.get('blocker')})
            continue
        children=[c for c in task.get('children',[]) if records[c]['status'] not in FINISHED]
        if children:
            waiting.append({'id':tid,'open_children':children,
                'note':'Work on the concrete children first; parent acceptance remains required.'});continue
        missing=[dep for dep in task.get('depends_on',[]) if not implemented(records[dep])]
        if missing:
            waiting.append({'id':tid,'implementation_dependencies':missing});continue
        unfinished=[dep for dep in task.get('finished_dependencies',[]) if records[dep]['status'] not in FINISHED]
        if unfinished:
            waiting.append({'id':tid,'finished_dependencies':unfinished});continue
        missing_attempts=[dep for dep in task.get('attempt_dependencies',[])
                          if not (records[dep].get('execution_complete',False) or records[dep]['status'] in FINISHED)]
        if missing_attempts:
            waiting.append({'id':tid,'actual_attempts_needed':missing_attempts});continue
        required_checks=task.get('validation_check_dependencies',[])
        missing_checks=[key for key in required_checks if checks.get(key,{}).get('status')!='DONE']
        mode='VALIDATION_OR_INTEGRATION' if implemented(record) else 'IMPLEMENT_OR_PREPARE'
        if missing_checks:
            waiting.append({'id':tid,'target_checks':missing_checks,
                'note':'Preparation may continue; do not freeze/execute this gate until these checks are actually DONE.'})
            continue
        ready.append({'id':tid,'group':task['group'],'mode':mode,'task':task['task'],
            'native_validation_may_still_need_environment':True,
            'target_checks':task.get('target_checks',[])})
    return {'scope':'read-only navigation, not execution or acceptance',
            'product_acceptance':'NOT_ESTABLISHED_BY_THIS_TOOL',
            'ready':ready,'blocked':blocked,'waiting':waiting,
            'finished_execution_steps':sum(r['status'] in FINISHED for r in records.values()),
            'total_steps':len(records)}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path)
    parser.add_argument('--limit',type=int,default=12)
    parser.add_argument('--show-waiting',action='store_true',
                        help='include detailed dependency lists; default output stays focused on actionable work')
    args=parser.parse_args(argv)
    if not 1<=args.limit<=1000:parser.error('--limit must be 1..1000')
    try:
        plan=json.loads((ROOT/'TASKS.json').read_text(encoding='utf-8'))
        path=args.state if args.state is not None else (
            ROOT/'state/CURRENT.json' if (ROOT/'state/CURRENT.json').is_file()
            else ROOT/'state/PROGRESS_TEMPLATE.json')
        state=json.loads(path.read_text(encoding='utf-8'))
        result=navigate(plan,state)
        result['state_source']=str(path)
        result['using_initial_seed']=path==ROOT/'state/PROGRESS_TEMPLATE.json'
        result['session_bootstrap']=state.get('session_bootstrap',{'status':'NOT_RECORDED'})
        result['total_ready']=len(result['ready'])
        result['total_waiting']=len(result['waiting'])
        result['total_blocked']=len(result['blocked'])
        result['ready']=result['ready'][:args.limit]
        if not args.show_waiting:
            result.pop('waiting')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'status':'INVALID_PLAN_OR_STATE','message':str(exc)},ensure_ascii=False))
        return 2

if __name__=='__main__':raise SystemExit(main())
