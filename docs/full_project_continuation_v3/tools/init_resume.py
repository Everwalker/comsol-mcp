#!/usr/bin/env python3
"""Index existing progress without overwriting upstream state or marking acceptance.
Reads source/JSON only, never imports repository code or contacts COMSOL.
"""
from __future__ import annotations
import argparse,collections,hashlib,json,os,re,tempfile
from pathlib import Path
from datetime import datetime,timezone
from bootstrap import safe_ancestors,valid_path
ROOT=Path(__file__).resolve().parents[1]
STATE_NAMES=('RESUME.json','TASKS.json','ACTION_COVERAGE.json','ACCEPTANCE_MATRIX.json','ORIGINAL_INVENTORY.json','INITIALIZED.json','REVIEW_DECISION.json')
MAX_JSON_BYTES=64*1024*1024

def digest(data):return hashlib.sha256(data).hexdigest()
def blob(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def load(path):
    safe_ancestors(path)
    if not path.is_file():raise ValueError('Required file missing: '+str(path))
    if path.stat().st_size>MAX_JSON_BYTES:raise ValueError('JSON exceeds explicit read budget: '+path.name)
    return json.loads(path.read_text(encoding='utf-8'))

def atomic_json(path,data):
    path=Path(path);safe_ancestors(path.parent);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            json.dump(data,out,ensure_ascii=False,indent=2,allow_nan=False);out.write('\n');out.flush();os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def original_counts(repo,spec,frozen_contract_dir=None):
    data={};refs={}
    for r in spec['files']:
        path=repo.joinpath(*valid_path(spec['base_dir']),*valid_path(r['path']));safe_ancestors(path)
        raw=path.read_bytes();working_sha=digest(raw);source=path
        basis='WORKING_SOURCE_MATCHES_FROZEN_BLOB'
        if blob(raw)!=r['git_blob']:
            if frozen_contract_dir is None:raise ValueError('Original requirement bytes changed: '+r['path'])
            source=Path(frozen_contract_dir).joinpath(*valid_path(r['path']));safe_ancestors(source)
            source.relative_to(repo) # only an explicit, already restored copy inside this repository
            if not source.is_file():raise ValueError('Frozen requirement copy missing: '+r['path'])
            raw=source.read_bytes()
            if blob(raw)!=r['git_blob']:raise ValueError('Frozen requirement copy blob mismatch: '+r['path'])
            basis='EXACT_LOCKED_LOCAL_FROZEN_COPY'
        data[r['path']]=raw;refs[r['path']]={'git_blob':r['git_blob'],'sha256':digest(raw),'size':len(raw),
            'source_path':str(source.relative_to(repo)),'working_source_sha256':working_sha,'frozen_basis':basis}
    acts=json.loads(data['02_ACTION_CATALOG.json'])['operations'];test=json.loads(data['03_ACCEPTANCE.json'])
    tasks=re.findall(r'^\|\s*(W\d{2})\s',data['04_IMPLEMENTATION_PLAN.md'].decode(),flags=re.M)
    counts={'work_packages':len(tasks),'operations':len(acts),'tests':len(test['tests']),'platform_version_targets':len(test['matrix'])}
    if counts!=spec['expected_counts']:raise ValueError('Original ID counts differ: '+str(counts))
    if len(set(tasks))!=len(tasks) or len({a['operation_id'] for a in acts})!=len(acts):raise ValueError('Duplicate original IDs')
    return counts,refs,tasks,[a['operation_id'] for a in acts],[t['id'] for t in test['tests']],test['matrix']

def table(data,key,identifier):
    rows=data.get(key) if isinstance(data,dict) else None
    if not isinstance(rows,list) or any(not isinstance(x,dict) or identifier not in x for x in rows):
        raise ValueError('Current state schema not recognized: '+key+'; preserve it and review, do not initialize a replacement')
    return rows

def get_scope(repo,pin,snapshot,targets):
    path=repo/pin['upstream_execution_directory']/'USER_SCOPE_DECISIONS.json';data=load(path);raw=path.read_bytes()
    if blob(raw)!=snapshot['source_git_blob']:raise ValueError('Scope record differs from reviewed snapshot; reconcile actual user decisions explicitly')
    skip=[]
    for row in data.get('decisions',[]):
        if row.get('decision')=='USER_REQUESTED_SKIP':
            for target in row.get('targets',[]):
                if target not in targets:raise ValueError('Unknown waiver target')
                skip.append(target)
    expected={x for d in snapshot['decisions'] for x in d['targets']}
    if set(skip)!=expected:raise ValueError('Scope mismatch; no automatic expansion or erasure')
    return {'source':str(path.relative_to(repo)),'sha256':digest(raw),'user_requested_skip_native':sorted(set(skip)),
            'enabled_native_target_labels':[x for x in targets if x not in skip],
            'original_target_labels':targets,'waived_is_pass':False,'native_certification':False}

def prepare(repo,pin,spec,snapshot,frozen_contract_dir=None):
    repo=Path(repo).resolve();safe_ancestors(repo)
    overlay=repo.joinpath(*valid_path(pin['handoff_directory']));safe_ancestors(overlay)
    state=overlay/'state';marker=state/'INITIALIZED.json'
    if marker.exists():
        return {'status':'EXISTING_CONTINUATION_PRESERVED','marker':str(marker),'next':'Read state/RESUME.json. Do not overwrite prior progress.','native_execution_certified':False}
    if state.exists() and any(state.iterdir()):raise ValueError('Partial continuation state exists; reconcile it without overwriting')
    counts,contracts,work_ids,action_ids,test_ids,targets=original_counts(repo,spec,frozen_contract_dir)
    upstream=repo/pin['upstream_execution_directory'];loaded={};index={}
    for name in STATE_NAMES:
        path=upstream/'state'/name
        if path.exists():
            raw=path.read_bytes();loaded[name]=load(path);index[name]={'path':str(path.relative_to(repo)),'sha256':digest(raw),'bytes':len(raw),'mutation_by_this_tool':False}
        else:index[name]={'path':str(path.relative_to(repo)),'status':'MISSING_NOT_RECONSTRUCTED'}
    for name in ('RESUME.json','TASKS.json','ACTION_COVERAGE.json','ACCEPTANCE_MATRIX.json'):
        if name not in loaded:raise ValueError('Required upstream state missing: '+name+'; do not reset')
    tasks=table(loaded['TASKS.json'],'tasks','id');actions=table(loaded['ACTION_COVERAGE.json'],'actions','operation_id');tests=table(loaded['ACCEPTANCE_MATRIX.json'],'tests','id')
    for rows,key,want in ((tasks,'id',work_ids),(actions,'operation_id',action_ids),(tests,'id',test_ids)):
        ids=[x[key] for x in rows]
        if len(set(ids))!=len(ids) or set(ids)!=set(want):raise ValueError('Original IDs missing/added in current table: '+key+'; manual reconciliation required')
    scope=get_scope(repo,pin,snapshot,targets)
    # Latest source checkpoint is separate from the older full-project ledger.
    current_checkpoint = None
    checkpoint_meta = None
    if pin.get('checkpoint_path'):
        cp_path = repo.joinpath(*valid_path(pin['checkpoint_path']))
        current_checkpoint = load(cp_path)
        if not isinstance(current_checkpoint, dict) or not isinstance(current_checkpoint.get('current_status'),dict):
            raise ValueError('Latest checkpoint shape changed; preserve and reconcile explicitly')
        cp_raw = cp_path.read_bytes()
        checkpoint_meta = {'path':pin['checkpoint_path'],'sha256':digest(cp_raw),'bytes':len(cp_raw)}
    def tally(rows):
        cells=[c for r in rows for c in (r.get('targets',{}).values() if isinstance(r.get('targets'),dict) else [])]
        return dict(collections.Counter(str(c.get('status','NO_STATUS')) if isinstance(c,dict) else 'UNKNOWN_SCHEMA' for c in cells))
    summary={'schema_version':1,'reviewed_commit':pin['commit'],'counts':counts,'original_contract_hashes':contracts,
             'current_tasks':[{'id':r['id'],'title':r.get('title'),'reported_status':r.get('status','UNASSESSED'),
                               'next_action_excerpt':str(r.get('next_action',''))[:1200],
                               'blocker_count':len(r.get('blockers',[])) if isinstance(r.get('blockers'),list) else None} for r in tasks],
             'action_target_status_counts':tally(actions),'test_target_status_counts':tally(tests),
             'scope':scope,'upstream_state_files':index,'upstream_state_mutated':False,
             'observed_statuses_are_not_certification':True,'native_execution_certified':False,
             'latest_checkpoint':checkpoint_meta,
             'checkpoint_reported_status':current_checkpoint.get('current_status') if current_checkpoint else None,
             'checkpoint_active_jobs_is_historical_only':True}
    # All reads/validation above finish before any state write.
    state.mkdir(parents=True,exist_ok=True)
    atomic_json(state/'CURRENT_INDEX.json',summary)
    atomic_json(state/'RESUME.json',{'schema_version':1,'status':'RESUMED_FULL_COMPLETION_NOT_ACCEPTED','phase':'CANDIDATE_INTEGRATION_AND_RUNTIME_REPAIR','task':'P0.INTEGRATE','candidate_source':pin['commit'],
        'upstream_resume_path':index['RESUME.json']['path'],'upstream_resume_sha256_at_import':index['RESUME.json']['sha256'],
        'latest_checkpoint_path':checkpoint_meta['path'] if checkpoint_meta else None,
        'checkpoint_reported_state':current_checkpoint.get('current_status') if current_checkpoint else None,
        'historical_unknown_statement':current_checkpoint.get('old_unknown') if current_checkpoint else None,
        'upstream_state_is_preserved':True,'active_job_refs':None,'active_jobs_discovered':False,'historical_unknown_policy':'DO_NOT_REPLAY_OR_RECLASSIFY; inspect actual protected state before new native activity',
        'local_only_science_content':'NOT_RECOVERED_FROM_PUBLIC_SOURCE','native_execution_certified':False,
        'next_action':'Run candidate_inventory, reconcile root/windows-r19/w23-r15/w24/W26 changes without overlaying; fix runtime-ID domain mismatch; rebuild current safe runtime separately from historical UNKNOWN.',
        'scope_decisions':scope,'updated_at':datetime.now(timezone.utc).isoformat()})
    atomic_json(marker,{'schema_version':1,'status':'EXISTING_STATE_INDEXED_NOT_RESET','source_commit':pin['commit'],'index_sha256':digest((state/'CURRENT_INDEX.json').read_bytes()),'native_execution_certified':False})
    # Verify indexing did not mutate a source file.
    if checkpoint_meta and digest((repo/checkpoint_meta['path']).read_bytes())!=checkpoint_meta['sha256']:
        raise RuntimeError('Latest checkpoint changed during indexing; reconcile without resetting state')
    for name,meta in index.items():
        if 'sha256' in meta and digest((repo/meta['path']).read_bytes())!=meta['sha256']:raise RuntimeError('Upstream changed concurrently; stop and reconcile')
    return {'status':'EXISTING_STATE_INDEXED_NOT_RESET','counts':counts,'waived_native_targets':scope['user_requested_skip_native'],'overlay':str(overlay),
            'upstream_state_mutated':False,'native_execution_certified':False,'next':'Read short state then MASTER_GOAL; no repeated per-helper approval gates.'}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--repo',type=Path,required=True)
    ap.add_argument('--frozen-contract-dir',type=Path,help='Explicit restored directory of exact locked original contracts; production bytes remain unchanged')
    a=ap.parse_args();frozen=a.frozen_contract_dir.resolve() if a.frozen_contract_dir else None
    print(json.dumps(prepare(a.repo,load(ROOT/'PIN.json'),load(ROOT/'ORIGINAL_REQUIREMENTS.json'),load(ROOT/'SCOPE_DECISIONS_SNAPSHOT.json'),frozen),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
