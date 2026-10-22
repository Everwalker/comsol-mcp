#!/usr/bin/env python3
"""Create a new universal-core continuation state, without replacing prior ledgers.
Imports actual action IDs only. Does not label any feature implemented or verified.
"""
from __future__ import annotations
import argparse,hashlib,json,os,tempfile
from pathlib import Path
from datetime import datetime,timezone
from bootstrap import ROOT,valid_path,safe_ancestors,reparse

def validate_contract(root):
    lock=json.loads((root/'CONTRACT_LOCK.json').read_text(encoding='utf-8'))
    for row in lock['files']:
        p=root.joinpath(*valid_path(row['path']));safe_ancestors(p)
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('frozen scope/DoD mismatch: '+row['path'])
    return lock

def initialize(repo:Path,pack:Path=ROOT):
    pin=json.loads((pack/'PIN.json').read_text(encoding='utf-8'));validate_contract(pack)
    repo=repo.absolute();safe_ancestors(repo)
    overlay=repo.joinpath(*valid_path(pin['handoff_directory']));safe_ancestors(overlay)
    receipt_path=overlay/'RESTORE_RECEIPT.json'
    receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt.get('commit')!=pin['commit'] or receipt.get('tree')!=pin['tree'] or receipt.get('status')!='PINNED_SOURCE_RESTORED':raise ValueError('missing/mismatched restore receipt')
    catalog_path=repo/'docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json';safe_ancestors(catalog_path)
    raw=catalog_path.read_bytes();catalog=json.loads(raw);ops=catalog.get('operations')
    if not isinstance(ops,list) or not ops:raise ValueError('missing original action operations')
    if catalog.get('operation_count')!=len(ops):raise ValueError('catalog header/list disagree; inspect rather than repair silently')
    ids=[r.get('operation_id') for r in ops]
    if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):raise ValueError('invalid/duplicate operation IDs')
    state=overlay/'state';safe_ancestors(state)
    if state.exists():
        existing=json.loads((state/'RESUME.json').read_text(encoding='utf-8'))
        if existing.get('baseline_commit')!=pin['commit']:raise ValueError('existing state belongs to different baseline; not modified')
        return {'status':'EXISTING_STATE_PRESERVED','path':str(state),'native_certified':False}
    specs=json.loads((pack/'config/UNIVERSAL_DOD.json').read_text(encoding='utf-8'))
    temp=Path(tempfile.mkdtemp(prefix='.state-init-',dir=overlay))
    def write(name,obj):(temp/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write('GENERAL_COVERAGE.json',{'schema_version':1,'original_catalog_sha256':hashlib.sha256(raw).hexdigest(),'operation_count':len(ops),'rows':[{'operation_id':r['operation_id'],'domain':r.get('domain'),'effect':r.get('effect'),'classification':'UNASSESSED','implementation_route':None,'implementation_status':'NOT_REVIEWED','evidence_status':'NOT_RUN','evidence_refs':[]} for r in ops],'test_assertion_mapping':'TO_BE_REVIEWED_FROM_ORIGINAL_03_ACCEPTANCE_NO_OLD_STATE_OVERWRITE'})
    write('DOD_STATUS.json',{'schema_version':1,'rows':[{'id':r['id'],'status':'NOT_RUN','targets':{},'evidence_refs':[]} for r in specs['groups']]})
    write('REVIEW.json',{'schema_version':1,'status':'NOT_RUN','main_session_id':None,'reviewer_session_id':None,'candidate_identity':None,'findings':[],'note':'Manual factual record; no script certifies independence or native execution.'})
    write('RESUME.json',{'schema_version':1,'baseline_commit':pin['commit'],'baseline_tree':pin['tree'],'state_revision':0,'phase':'M0_RECOVERED','status':'IN_PROGRESS','next_action':'Read latest canonical/candidate manifests, map core obligations once, and integrate only reusable fixes; then first public-MCP cold-start test.','active_job_refs':[],'blockers':[],'completed_groups':[],'scope_decision':'SCOPE_RESET.md','updated_utc':datetime.now(timezone.utc).isoformat(),'native_execution_claimed':False})
    if state.exists() or reparse(state):raise ValueError('state appeared; preserving existing state')
    temp.rename(state)
    return {'status':'NEW_STATE_INITIALIZED_NO_PRIOR_LEDGER_CHANGED','actions':len(ops),'groups':len(specs['groups']),'path':str(state),'native_certified':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);a=p.parse_args()
    print(json.dumps(initialize(a.repo),ensure_ascii=False,indent=2))
