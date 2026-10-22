#!/usr/bin/env python3
"""Restore pinned public CAS source candidates to a NEW directory; never execute.
No engine, credentials, runtime ownership, active PID or approvals are restored.
"""
from __future__ import annotations
import argparse,hashlib,json,os,tempfile,stat
from pathlib import Path
from datetime import datetime,timezone
from bootstrap import ROOT,valid_path,validate_paths,safe_ancestors,reparse

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def source_file(base,name):
    p=base.joinpath(*valid_path(name));safe_ancestors(p)
    if not p.is_file() or not stat.S_ISREG(p.lstat().st_mode):raise ValueError('missing/nonregular source '+name)
    return p

def restore(repo:Path,target:Path,pin:dict)->dict:
    repo=repo.absolute();target=target.absolute();safe_ancestors(repo);safe_ancestors(target)
    if target.exists():raise ValueError('candidate target exists; choose a new directory')
    cfg=pin['portable_candidates'];base=repo.joinpath(*valid_path(cfg['path']))
    manifest_path=source_file(base,cfg['manifest'])
    if sha(manifest_path)!=cfg['manifest_sha256']:raise ValueError('pinned candidate manifest mismatch')
    m=json.loads(manifest_path.read_text(encoding='utf-8'))
    if m.get('schema')!=cfg['schema']:raise ValueError('unexpected CAS schema')
    rows=m.get('files');
    if not isinstance(rows,list) or len(rows)!=cfg['expected_files']:raise ValueError('candidate file count mismatch')
    validate_paths(rows)
    if any(row['path']=='CANDIDATE_RECOVERY_RECEIPT.json' for row in rows):raise ValueError('reserved receipt path')
    total=0;objects={}
    for row in rows:
        size=row.get('bytes');digest=row.get('sha256')
        if type(size) is not int or not 0<=size<=1024**3:raise ValueError('invalid candidate size')
        total+=size
        if total>8*1024**3:raise ValueError('expanded candidate budget exceeded')
        p=source_file(base,row['object'])
        if p.name!=digest:raise ValueError('object name/digest mismatch')
        if p.stat().st_size!=size:raise ValueError('object size mismatch')
        if digest not in objects:
            if sha(p)!=digest:raise ValueError('candidate object hash mismatch')
            objects[digest]=p
    if len(objects)!=cfg['expected_unique_objects']:raise ValueError('unique object count mismatch')
    # Check the original map and restore program as data, do not import or execute.
    for name,record in m.get('tools',{}).items():
        if sha(source_file(base,name))!=record.get('sha256'):raise ValueError('bound candidate helper mismatch')
    target.parent.mkdir(parents=True,exist_ok=True);safe_ancestors(target.parent)
    stage=Path(tempfile.mkdtemp(prefix='.candidate-restore-',dir=target.parent));counts={}
    try:
        for row in rows:
            p=stage.joinpath(*valid_path(row['path']));p.parent.mkdir(parents=True,exist_ok=True)
            with objects[row['sha256']].open('rb') as src,p.open('xb') as dst:
                for b in iter(lambda:src.read(1048576),b''):dst.write(b)
            if p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('candidate readback mismatch')
            if os.name!='nt':p.chmod(0o644)
            lane=row.get('lane','unspecified');counts[lane]=counts.get(lane,0)+1
        result={'status':'EXACT_BYTES_SOURCE_ONLY','source_commit':pin['commit'],
                'manifest_sha256':cfg['manifest_sha256'],'file_count':len(rows),
                'unique_objects':len(objects),'lane_counts':counts,'bytes':total,
                'target':str(target),'created_utc':datetime.now(timezone.utc).isoformat(),
                'source_executed':False,'runtime_authority_restored':False,'merged_into_production':False}
        (stage/'CANDIDATE_RECOVERY_RECEIPT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        safe_ancestors(target)
        if target.exists() or reparse(target):raise ValueError('target appeared during restore')
        stage.rename(target);return result
    except Exception:
        print('Partial candidate staging preserved (NOT ready): '+str(stage));raise

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);p.add_argument('--target',type=Path);a=p.parse_args()
    pin=json.loads((ROOT/'PIN.json').read_text(encoding='utf-8'))
    result=restore(a.repo,a.target or a.repo.absolute().parent/'candidate_sources',pin)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
