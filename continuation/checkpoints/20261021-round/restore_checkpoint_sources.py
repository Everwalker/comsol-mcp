#!/usr/bin/env python3
"""Restore the byte-exact source roster into one new, caller-selected directory.

This tool never executes restored source. Existing destinations and symlinks are rejected.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, stat
from pathlib import Path, PurePosixPath

MANIFEST_NAME='CHECKPOINT_MANIFEST_R2.json'
def sha(b): return hashlib.sha256(b).hexdigest()
def safe_rel(s):
    if not isinstance(s,str) or not s or '\\' in s or ':' in s: raise ValueError('unsafe path syntax')
    if any(part in ('', '.', '..') for part in s.split('/')): raise ValueError('unsafe path components')
    p=PurePosixPath(s)
    if p.is_absolute() or any(x in ('','.','..') for x in p.parts): raise ValueError('unsafe path components')
    return p

def no_symlink_components(base:Path, rel:PurePosixPath):
    cur=base
    for part in rel.parts:
        cur=cur/part
        if os.path.lexists(cur) and cur.is_symlink(): raise ValueError('symlink destination component rejected')
    return cur

def reject_existing_symlink_ancestors(path:Path):
    cur=Path(path.anchor)
    for part in path.parts[1:]:
        cur=cur/part
        if os.path.lexists(cur) and cur.is_symlink(): raise ValueError('symlink output ancestor rejected')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',required=True,help='new non-existing restore directory')
    args=ap.parse_args()
    package=Path(__file__).resolve().parent
    manifest_path=package/MANIFEST_NAME
    m=json.loads(manifest_path.read_text(encoding='utf-8'))
    if m.get('schema')!='COMSOL_MCP_FULL_PROJECT_PORTABLE_SOURCE_CHECKPOINT_R2': raise SystemExit('unexpected manifest schema')
    toolrec=m.get('tools',{}).get('restore_checkpoint_sources.py',{})
    own=Path(__file__).read_bytes()
    if sha(own)!=toolrec.get('sha256'): raise SystemExit('restore tool hash differs from manifest')
    map_path=package/'SOURCE_MAP_R2.json'
    maprec=m.get('tools',{}).get('SOURCE_MAP_R2.json',{})
    if map_path.is_symlink() or not map_path.is_file() or sha(map_path.read_bytes())!=maprec.get('sha256'):
        raise SystemExit('source map hash differs from manifest')
    dest=Path(args.output).expanduser().absolute()
    if dest.exists() or dest.is_symlink(): raise SystemExit('restore destination must not exist')
    reject_existing_symlink_ancestors(dest.parent)
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.parent.is_symlink(): raise SystemExit('restore parent may not be a symlink')
    dest.mkdir(mode=0o700)
    restored=[]
    for rec in m['files']:
        rel=safe_rel(rec['path'])
        objrel=safe_rel(rec['object'])
        obj=no_symlink_components(package,objrel)
        if obj.is_symlink() or not obj.is_file(): raise SystemExit('missing or symlinked CAS object: '+str(objrel))
        raw=obj.read_bytes()
        if len(raw)!=rec['bytes'] or sha(raw)!=rec['sha256'] or rec['sha256']!=Path(objrel).name: raise SystemExit('CAS bytes fail manifest: '+rec['path'])
        target=no_symlink_components(dest,rel)
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() or target.is_symlink(): raise SystemExit('restore target collision: '+rec['path'])
        with target.open('xb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        os.chmod(target,0o644)
        check=target.read_bytes()
        if len(check)!=rec['bytes'] or sha(check)!=rec['sha256']: raise SystemExit('restored bytes fail manifest: '+rec['path'])
        restored.append(rec)
    counts={}
    for rec in restored: counts[rec['lane']]=counts.get(rec['lane'],0)+1
    report={'schema':'COMSOL_MCP_FULL_PROJECT_PORTABLE_SOURCE_RESTORE_READBACK_R2','status':'PASS_EXACT_BYTES_SOURCE_ONLY','manifest_sha256':sha(manifest_path.read_bytes()),'restored_files':len(restored),'unique_objects_verified':len(m['objects']),'lane_counts':dict(sorted(counts.items())),'destination':str(dest),'executed_source':False,'authority_restored':False}
    out=dest/'RESTORE_READBACK_R2.json'
    with out.open('xb') as f: f.write((json.dumps(report,sort_keys=True,indent=2)+'\n').encode()); f.flush(); os.fsync(f.fileno())
    print(json.dumps(report,sort_keys=True))
if __name__=='__main__': main()
