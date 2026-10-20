#!/usr/bin/env python3
"""Fixed science host entry: validate Main's fresh binding, consume once, exec.

No native launcher, caller JSON permissions, SDK calls or cleanup are added.
The existing public client and original controller owner retain every handle.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

BINDING_SCHEMA = 'science-native-fixed-binding/1'
CURRENT_BINDING_SCHEMA = 'science-native-fixed-binding/2'
APPROVAL_SCHEMA = 'science-native-main-approval/1'
BINDING_KEYS = {'schema','activity_id','authority','source_root','source_files','source_sha256',
                'source_freeze','source_freeze_sha256','plan','plan_sha256','runtime_namespace',
                'argv','environment','budget','app4_production_sha256','sdk_receipt_sha256'}
CURRENT_BINDING_KEYS = (BINDING_KEYS - {'app4_production_sha256'}) | {'production_sha256'}
APPROVAL_KEYS = {'schema','status','issuer','activity_id','binding_sha256','entry_sha256',
                 'nonce','expires_at_utc','budget','approval_scope'}
SHA = re.compile(r'[0-9a-f]{64}')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def signature(st):
    return (st.st_dev,st.st_ino,st.st_mode,st.st_uid,st.st_nlink,st.st_size,st.st_mtime_ns,st.st_ctime_ns)


def pairs(rows):
    result={}
    for k,v in rows:
        if k in result:raise ValueError('duplicate key')
        result[k]=v
    return result


def read_file(path,*,private=False):
    path=Path(path)
    if not path.is_absolute() or path.resolve(strict=True)!=path:raise ValueError('canonical no-symlink path required')
    before=path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or before.st_nlink!=1
            or (private and stat.S_IMODE(before.st_mode)!=0o600)):
        raise ValueError('ordinary owner single-link file required')
    if before.st_size>64*1024*1024 or (private and before.st_size>2*1024*1024):raise ValueError('bounded input record required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        opened=os.fstat(fd);raw=b''
        while chunk:=os.read(fd,1048576):raw+=chunk
        after=os.fstat(fd)
    finally:os.close(fd)
    if any(signature(before)!=signature(st) for st in (opened,after,path.lstat())):raise ValueError('sameFD input changed')
    return raw,signature(before)


def read_json(path,sha):
    if not isinstance(sha,str) or not SHA.fullmatch(sha):raise ValueError('exact SHA required')
    raw,sig=read_file(path,private=True)
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('record SHA differs')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))
    return value,sig


def namespace_absent(path):
    path=Path(path)
    if path.parent!=Path('/private/tmp') or path.parent.resolve()!=path.parent or path.exists() or path.is_symlink():
        raise ValueError('fresh direct private native namespace absent required')


def source_identity(binding):
    source=Path(binding['source_root'])
    if source.resolve(strict=True)!=source or source.lstat().st_uid!=os.getuid() or source.lstat().st_mode&0o222:
        raise ValueError('canonical owner readonly source copy required')
    for member in source.rglob('*'):
        if member.is_symlink() or (member.is_dir() and (member.lstat().st_uid!=os.getuid() or member.lstat().st_mode&0o222)):
            raise ValueError('source symlink or writable directory forbidden')
    files=binding['source_files']
    if not isinstance(files,dict) or not files:raise ValueError('closed nonempty source inventory required')
    actual={p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual!=set(files):raise ValueError('source file set differs')
    for rel,sha in files.items():
        if not isinstance(sha,str) or not SHA.fullmatch(sha):raise ValueError('source SHA required')
        p=source/rel;raw,_=read_file(p)
        if not p.is_relative_to(source) or p.lstat().st_mode&0o222 or hashlib.sha256(raw).hexdigest()!=sha:
            raise ValueError('readonly source SHA differs')
    if hashlib.sha256(canonical(files)).hexdigest()!=binding['source_sha256']:raise ValueError('source closure differs')
    if Path(__file__).resolve()!=source/'tools/science_native_once_entry.py':raise ValueError('fixed actual entry import origin differs')


def validate_production_identity(binding, appfiles):
    if binding['schema']==BINDING_SCHEMA:
        count, key = 174, 'app4_production_sha256'
    elif binding['schema']==CURRENT_BINDING_SCHEMA:
        count, key = 175, 'production_sha256'
        package={p for p in appfiles if p.startswith('comsol_mcp/')}
        if len(package)!=172 or 'comsol_mcp/_windows_state_restore.py' not in package:
            raise ValueError('exact current172 package membership differs')
    else:
        raise ValueError('unknown production binding schema')
    if len(appfiles)!=count or hashlib.sha256(canonical(appfiles)).hexdigest()!=binding[key]:
        raise ValueError('exact schema-bound production source differs')


def validate(binding_path,binding_sha,approval_path,approval_sha,nonce):
    binding,bsig=read_json(binding_path,binding_sha);approval,asig=read_json(approval_path,approval_sha)
    keys = BINDING_KEYS if binding.get('schema')==BINDING_SCHEMA else CURRENT_BINDING_KEYS if binding.get('schema')==CURRENT_BINDING_SCHEMA else None
    if keys is None or set(binding)!=keys:raise ValueError('closed binding required')
    if set(approval)!=APPROVAL_KEYS or approval['schema']!=APPROVAL_SCHEMA:raise ValueError('closed Main approval required')
    authority=Path(binding['authority']);before=authority.lstat()
    if (authority.resolve()!=authority or authority.parent!=Path('/private/tmp') or not stat.S_ISDIR(before.st_mode)
            or before.st_uid!=os.getuid() or stat.S_IMODE(before.st_mode)!=0o700
            or Path(binding_path)!=authority/'BINDING.json' or Path(approval_path)!=authority/'MAIN_APPROVAL.json'):
        raise ValueError('private canonical fixed authority paths required')
    if (approval['status']!='APPROVED_BY_MAIN_FRESH_SCOPED' or approval['issuer']!='Main'
            or approval['approval_scope']!='SCIENCE76_REAL_PUBLIC_RECIPE_UNSOLVED_ONLY'
            or approval['binding_sha256']!=binding_sha or approval['activity_id']!=binding['activity_id']
            or not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{64}',nonce) or approval['nonce']!=nonce):
        raise ValueError('Main exact fresh approval/nonce differs')
    expires=datetime.fromisoformat(approval['expires_at_utc'].replace('Z','+00:00'))
    if expires.tzinfo is None or expires<=datetime.now(timezone.utc):raise ValueError('Main approval expired')
    namespace_absent(binding['runtime_namespace']);source_identity(binding)
    if binding['activity_id']!=Path(binding['runtime_namespace']).name:raise ValueError('activity/namespace differs')
    raw,_=read_file(Path(__file__).resolve())
    if hashlib.sha256(raw).hexdigest()!=approval['entry_sha256']:raise ValueError('actual entry differs')
    source=Path(binding['source_root']);sys.path.insert(0,str(source/'tools'))
    import science_recipe_cycle as science
    if Path(science.__file__).resolve()!=source/'tools/science_recipe_cycle.py':raise ValueError('fixed helper origin differs')
    plan=science.validate_plan(binding['plan'],binding['plan_sha256'],source=source,runtime=binding['runtime_namespace'],
         installation=binding['argv'][binding['argv'].index('--installation')+1],java_home=binding['argv'][binding['argv'].index('--java-home')+1])
    freeze,_=read_json(binding['source_freeze'],binding['source_freeze_sha256'])
    if freeze['files']!={p:sha for p,sha in binding['source_files'].items() if p.startswith('comsol_mcp/')}:
        raise ValueError('production freeze differs from closed source inventory')
    appfiles={p:sha for p,sha in binding['source_files'].items() if p.startswith('comsol_mcp/') or p in {'LICENSE','README.md','pyproject.toml'}}
    validate_production_identity(binding, appfiles)
    if not isinstance(binding['sdk_receipt_sha256'],str) or not SHA.fullmatch(binding['sdk_receipt_sha256']):raise ValueError('SDK receipt SHA required')
    if binding['budget']!=plan['budget'] or approval['budget']!=plan['budget'] or plan['budget']['public_calls']!=76:
        raise ValueError('exact 76-call science budget differs')
    expected=[sys.executable,str(source/'tools/run_v3_public_mcp_smoke.py'),'--source',str(source),'--freeze',binding['source_freeze'],
      '--runtime',binding['runtime_namespace'],'--installation',plan['runtime_identity']['comsol_root'],'--java-home',plan['runtime_identity']['java_home'],
      '--science-recipes','--science-plan',binding['plan'],'--science-plan-sha256',binding['plan_sha256']]
    if binding['argv']!=expected:raise ValueError('fixed literal executable argv differs')
    env={'HOME':str(Path.home()),'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LANG':'C','LC_ALL':'C',
         'PYTHONUTF8':'1','PYTHONPATH':str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    if binding['environment']!=env:raise ValueError('fixed host environment differs')
    # Recheck all pinned records/authority after source/runtime reads.
    if (read_file(binding_path,private=True)[1]!=bsig or read_file(approval_path,private=True)[1]!=asig
            or signature(authority.lstat())!=signature(before)):
        raise ValueError('authority/approval changed during prebirth verification')
    return binding,before


def ensure_quiescent():
    import psutil
    for process in psutil.process_iter(['pid','name','create_time']):
        name=process.info.get('name') or ''
        if 'comsol' in name.lower() or name.lower() in {'java','java.exe'}:
            raise ValueError('existing native/JDK process; no new birth authorized')


def consume_and_exec(binding,authority_before,binding_sha,approval_sha,nonce):
    authority=Path(binding['authority']);fd=os.open(authority,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if signature(os.fstat(fd))!=signature(authority_before):raise ValueError('authority directory changed')
        ensure_quiescent();namespace_absent(binding['runtime_namespace'])
        oncefd=os.open('ONCE_CONSUMED.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            data=canonical({'schema':'science-native-once-consumed/1','binding_sha256':binding_sha,
                 'approval_sha256':approval_sha,'nonce_sha256':hashlib.sha256(nonce.encode()).hexdigest(),
                 'activity_id':binding['activity_id'],'state':'CONSUMED_BEFORE_EXEC_NO_AUTOMATIC_RETRY'})
            with os.fdopen(oncefd,'wb',closefd=False) as stream:stream.write(data);stream.flush();os.fsync(oncefd)
        finally:os.close(oncefd)
        os.fsync(fd)
        namespace_absent(binding['runtime_namespace']);source_identity(binding)
        current=authority.lstat()
        if (current.st_dev,current.st_ino,current.st_uid,current.st_mode)!=(authority_before.st_dev,authority_before.st_ino,authority_before.st_uid,authority_before.st_mode):
            raise ValueError('authority original directory lost after once')
        read_json(authority/'BINDING.json',binding_sha);read_json(authority/'MAIN_APPROVAL.json',approval_sha)
        os.chdir(binding['source_root'])
        os.execve(binding['argv'][0],binding['argv'],binding['environment'])
    finally:os.close(fd)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['binding','binding-sha256','approval','approval-sha256','nonce']:parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    binding,before=validate(Path(args.binding),args.binding_sha256,Path(args.approval),args.approval_sha256,args.nonce)
    consume_and_exec(binding,before,args.binding_sha256,args.approval_sha256,args.nonce)
    return 1

if __name__=='__main__':raise SystemExit(main())
