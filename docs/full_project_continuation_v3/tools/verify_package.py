#!/usr/bin/env python3
"""Verify this workpack, not remote source behavior or native acceptance."""
import hashlib,json
from pathlib import Path
from bootstrap import valid_path,safe_ancestors

def verify(root):
    manifest=json.loads((root/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'));errors=[];seen=set()
    if not isinstance(manifest.get('files'),list) or not manifest['files']:
        return {'status':'FAIL','file_count':0,'errors':[{'error':'empty or invalid manifest'}],'native_certified':False}
    for row in manifest['files']:
        try:
            parts=valid_path(row['path'])
            if row['path'] in seen:raise ValueError('duplicate')
            seen.add(row['path']);p=root.joinpath(*parts);safe_ancestors(p)
            if not p.is_file() or p.stat().st_size!=row['bytes']:raise ValueError('missing or size mismatch')
            h=hashlib.sha256()
            with p.open('rb') as f:
                for b in iter(lambda:f.read(1048576),b''):h.update(b)
            if h.hexdigest()!=row['sha256']:raise ValueError('hash mismatch')
        except Exception as e:errors.append({'file':row.get('path'),'error':str(e)})
    return {'status':'WORKPACK_INTEGRITY_PASS' if not errors else 'FAIL','file_count':len(seen),'errors':errors,'native_certified':False}
if __name__=='__main__':
    r=verify(Path(__file__).resolve().parents[1]);print(json.dumps(r,ensure_ascii=False,indent=2));raise SystemExit(1 if r['errors'] else 0)
