#!/usr/bin/env python3
"""Read-only local Git metadata inventory. Not a backup or permission to delete.
No file content, credentials, running-process command lines or global paths read.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess
from pathlib import Path
from bootstrap import safe_ancestors

def collect(repo):
    repo=Path(repo).resolve();safe_ancestors(repo)
    if not repo.is_dir():raise ValueError('Repository directory is missing')
    env=os.environ.copy();env['GIT_OPTIONAL_LOCKS']='0';env['GIT_TERMINAL_PROMPT']='0'
    def git(args):
        r=subprocess.run(['git','--no-optional-locks','-c','core.fsmonitor=false',*args],cwd=repo,env=env,capture_output=True,timeout=60)
        if r.returncode:raise ValueError('Git inventory command failed: '+args[0]+'; no deletion or backup performed')
        return r.stdout
    head=git(['rev-parse','HEAD']).decode().strip()
    modified=git(['diff','--name-only','-z','HEAD']).split(b'\0')
    untracked=git(['ls-files','--others','--exclude-standard','--directory','-z']).split(b'\0')
    ignored=git(['ls-files','--others','--ignored','--exclude-standard','--directory','-z']).split(b'\0')
    def rows(xs):return [x.decode('utf-8','replace') for x in xs if x]
    usage=shutil.disk_usage(repo)
    return {'schema_version':1,'scope':'LOCAL_METADATA_ONLY_NOT_BACKUP','head':head,'modified_or_staged_paths':rows(modified),
            'untracked_paths_or_directories':rows(untracked),'ignored_paths_or_directories':rows(ignored),
            'disk_free_bytes':usage.free,'file_contents_read':False,'backup_created':False,'safe_to_delete':False,
            'notice':'Keep this local: paths may be private. Back up selected science/source with independent byte verification before manual cleanup. Ignored directories are not enumerated recursively.'}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('Output already exists; no overwrite')
    safe_ancestors(a.output);data=collect(a.repo);a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'status':'LOCAL_METADATA_RECORDED','safe_to_delete':False,'backup_created':False,'output':str(a.output)},ensure_ascii=False))
