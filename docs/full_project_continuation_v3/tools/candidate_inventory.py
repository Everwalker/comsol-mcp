#!/usr/bin/env python3
"""Read/hash candidate sources and public checkpoint maps; never merge or run code.
No private source paths from maps are opened. Output is a planning index, not approval.
"""
from __future__ import annotations
import argparse,collections,hashlib,json,os
from pathlib import Path
from datetime import datetime,timezone
from bootstrap import safe_ancestors,valid_path,reparse
from init_resume import load,atomic_json
ROOT=Path(__file__).resolve().parents[1]
SOURCE_EXTS={'.py','.java','.toml','.ps1','.sh','.bash','.bat','.cmd','.c','.h','.cpp','.ini','.cfg','.yaml','.yml'}
SOURCE_ROOTS={'comsol_mcp','tests','tools'}
SPECIALS={'pyproject.toml','setup.py','setup.cfg','MANIFEST.in','requirements.txt'}
PUBLISHED_SOURCE_LAYOUTS={'windows-r19':'candidate','w24-coupled05':'repository'}
MAP_NAMES=('README.md','STATES.json','INTEGRATION_CONFLICTS.json','EXTERNAL_SOURCE_MAP.json','LOCAL_ONLY_MANIFEST_R2.json','MISSING_SOURCE_REFERENCES.json','checkpoint/CURRENT_STATE.json','checkpoint/REVIEWED_MATERIALS_LOGICAL_MAP.json')
MAX_FILES=60000
MAX_SOURCE_BYTES=32*1024**2

def sha(path):
    safe_ancestors(path)
    if not path.is_file():raise ValueError('missing/nonregular file: '+str(path))
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def all_files(root):
    safe_ancestors(root);count=0
    for base,dirs,files in os.walk(root,followlinks=False):
        for d in dirs:
            if reparse(Path(base)/d):raise ValueError('candidate directory reparse point: '+d)
        dirs[:]=sorted(d for d in dirs if d not in {'.git','__pycache__','.pytest_cache','.venv','venv'})
        for n in sorted(files):
            p=Path(base)/n;safe_ancestors(p);count+=1
            if count>MAX_FILES:raise ValueError('candidate file-count budget exceeded')
            yield p

def source_like(rel):
    return (len(rel.parts)>1 and rel.parts[0] in SOURCE_ROOTS and rel.suffix in SOURCE_EXTS) or rel.as_posix() in SPECIALS

def inspect(repo,pin):
    repo=Path(repo).absolute();safe_ancestors(repo)
    cont=repo/'continuation';safe_ancestors(cont)
    if not cont.is_dir():raise ValueError('current continuation tree missing; no fallback to old release')
    manifests={}
    for name in MAP_NAMES:
        p=cont.joinpath(*valid_path(name));safe_ancestors(p)
        manifests[name]={'path':p.relative_to(repo).as_posix(),'status':'AVAILABLE','bytes':p.stat().st_size,'sha256':sha(p)} if p.is_file() else {'status':'MISSING_NOT_RECONSTRUCTED'}
    conflicts=load(cont/'INTEGRATION_CONFLICTS.json')
    if not isinstance(conflicts,list):raise ValueError('Unknown conflict-map schema; do not guess merge decisions')
    counts=collections.Counter()
    declared=[]
    for row in conflicts:
        if not isinstance(row,dict) or not isinstance(row.get('candidate'),str) or not isinstance(row.get('path'),str):
            raise ValueError('Invalid conflict row; preserve and inspect')
        valid_path(row['path']);counts[row['candidate']]+=1
        declared.append({k:row.get(k) for k in ('candidate','path','base_sha256','candidate_sha256','integration')})
    candroot=cont/'candidates';safe_ancestors(candroot)
    if not candroot.is_dir():raise ValueError('candidate directory missing')
    comparisons=[];candidate_summaries=[]
    for candidate in sorted(candroot.iterdir()):
        safe_ancestors(candidate)
        if not candidate.is_dir():continue
        source_root=candidate/PUBLISHED_SOURCE_LAYOUTS.get(candidate.name,'')
        safe_ancestors(source_root)
        if not source_root.is_dir():raise ValueError('Published candidate source root missing: '+candidate.name)
        ctr=collections.Counter();file_total=0
        for path in all_files(candidate):
            file_total+=1
            if not path.is_relative_to(source_root):continue
            rel=path.relative_to(source_root)
            if not source_like(rel):continue
            if path.stat().st_size>MAX_SOURCE_BYTES:raise ValueError('Source exceeds comparison budget; review explicitly')
            current=repo.joinpath(*valid_path(rel.as_posix()));safe_ancestors(current)
            cd=sha(path);rd=sha(current) if current.is_file() else None
            category='SAME_BYTES' if cd==rd else 'DIFFERENT_BYTES' if rd else 'CANDIDATE_ONLY'
            ctr[category]+=1
            comparisons.append({'candidate':candidate.name,'relative_path':rel.as_posix(),'included_path':path.relative_to(repo).as_posix(),'root_path':current.relative_to(repo).as_posix(),'candidate_sha256':cd,'root_sha256':rd,'observation':category,'integration_status':'NOT_DECIDED_BY_TOOL'})
        candidate_summaries.append({'candidate':candidate.name,'source_root':source_root.relative_to(repo).as_posix(),'source_comparisons':dict(ctr),'files_walked':file_total,'not_inferred':'revision acceptance, native status, completeness or merge priority'})
    conflicting_variants=[];by_path=collections.defaultdict(list)
    for row in comparisons:by_path[row['relative_path']].append(row)
    for path,rows in by_path.items():
        if len({r['candidate_sha256'] for r in rows})>1:
            conflicting_variants.append({'path':path,'candidate_variants':[{'candidate':r['candidate'],'sha256':r['candidate_sha256']} for r in rows]})
    cp=load(cont/'checkpoint/CURRENT_STATE.json')
    return {'schema':'full-project-v3-candidate-index/1','generated_at':datetime.now(timezone.utc).isoformat(),'reviewed_commit':pin['commit'],'reviewed_tree':pin['tree'],
       'upstream_map_files':manifests,'declared_conflict_count':len(declared),'declared_conflicts_by_candidate':dict(counts),
       'declared_conflicts':declared,'candidate_summaries':candidate_summaries,'actual_source_comparisons':comparisons,
       'cross_candidate_different_bytes':conflicting_variants,
       'latest_checkpoint_reported_status':cp.get('current_status'),
       'historical_unknown_statement':cp.get('old_unknown'),'current_live_processes':'NOT_OBSERVED',
       'maps_only_are_not_file_backups':True,'private_paths_followed':False,'code_executed':False,'source_modified':False,
       'native_execution_certified':False,'automatic_merge_performed':False,
       'limits':['Source-like selected files compared; data/JSON receipts and build external sources require their manifest-guided review.','Old r14 conflict map does not automatically cover r15; actual byte differences are listed, not accepted.','LOCAL_ONLY manifest hash is not verification or retrieval of its referenced files.']}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--output',type=Path);a=ap.parse_args()
    pin=load(ROOT/'PIN.json');out=a.output or a.repo/pin['handoff_directory']/'state/CANDIDATE_INDEX.json'
    safe_ancestors(out)
    if out.exists():
        raise ValueError('Candidate index exists. Read it or use a NEW --output for a later source snapshot; no overwrite.')
    result=inspect(a.repo,pin)
    atomic_json(out,result)
    print(json.dumps({'status':'CANDIDATE_BYTES_INDEXED_NOT_MERGED','candidates':len(result['candidate_summaries']),'declared_conflicts':result['declared_conflict_count'],'actual_cross_candidate_differences':len(result['cross_candidate_different_bytes']),'output':str(out),'native_execution_certified':False},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
