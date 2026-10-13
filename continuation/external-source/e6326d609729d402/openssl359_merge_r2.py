#!/usr/bin/env python3
"""Frozen make recipe helper: actual Apple lipo and read-only fat verification.

Children inherit the observed make group. Their actual wait is recorded, but
no live birth is fabricated for a child that has already exited.
"""
import argparse,hashlib,json,os,stat,subprocess,sys,time
from pathlib import Path
from intel_crypto_successor_r1 import checkpoint_bytes
from intel_deps_prepare import exclusive_json
from acquire_exact_archives import Refusal
from tls_binary_r1 import fat_archive

LIPO='/usr/bin/lipo'

def child(argv,cwd,logs,label,deadline,writer=exclusive_json):
    if time.monotonic()>=deadline:raise Refusal('merge deadline exhausted before child spawn')
    request={'argv':argv,'cwd':str(cwd),'parent_pid':os.getpid(),'inherited_group':os.getpgrp(),'start_unix':time.time()}
    writer(logs/(label+'.request.json'),request)
    handles=[];process=None;failures=[];rc=None
    try:
        for suffix in ('stdout','stderr'):
            fd=os.open(logs/(label+'.'+suffix),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            handles.append(fd)
        process=subprocess.Popen(argv,cwd=cwd,stdout=handles[0],stderr=handles[1],start_new_session=False)
        try:writer(logs/(label+'.spawn.json'),{'actual_pid':process.pid,'argv':argv,'group_policy':'inherits observed make group','individual_live_birth':'NOT_REQUIRED_NOT_CLAIMED'})
        except BaseException as exc:failures.append(('spawn_receipt',repr(exc)))
        # A failed diagnostic must never abandon an already-created child.
        while rc is None:
            try:rc=process.wait(timeout=max(.01,min(10,deadline-time.monotonic())))
            except subprocess.TimeoutExpired:
                if time.monotonic()>=deadline:
                    failures.append(('deadline','child still active; observing same handle without relaunch'))
                    deadline=float('inf')
            except BaseException as exc:
                failures.append(('same_handle_wait',repr(exc)))
                from cpython_universal2_stages import fallback
                fallback('TLS child observation failed; retaining same handle: '+repr(exc))
        if time.monotonic()>=deadline:failures.append(('deadline','actual child wait exceeded command cap'))
        returncode=rc
    finally:
        for fd in handles:
            for name,operation in (('fsync',os.fsync),('close',os.close)):
                try:operation(fd)
                except BaseException as exc:failures.append((name,repr(exc)))
        result={'request':request,'actual_pid':process.pid if process else None,'actual_wait_rc':rc,'diagnostic_errors':failures,'terminal_wait_observed':rc is not None}
        try:writer(logs/(label+'.result.json'),result)
        except BaseException as exc:
            failures.append(('terminal_receipt',repr(exc)))
            from cpython_universal2_stages import fallback
            fallback(repr(result))
    if failures or returncode!=0:raise Refusal('lipo child failed/diagnostic/deadline; actual child retained through wait')
    return result

def merge(root,checkpoint,deadline,checkpoint_sha):
    from intel_crypto_successor_r1 import PREFIX
    if root!=PREFIX/'intel-tls-openssl359-r2' or checkpoint!=root/'MERGE_INPUTS.json':raise Refusal('fixed official OpenSSL359 merge namespace required')
    from tls_successor_r1 import record
    expected=record(checkpoint);expected['sha256']=checkpoint_sha
    inputs=json.loads(checkpoint_bytes(checkpoint,expected)[0]);records=[]
    for name in ('libssl.a','libcrypto.a'):
        raw={};paths={}
        for arch in ('arm64','x86_64'):
            record=inputs['archives'][name][arch];paths[arch]=Path(record['path'])
            if paths[arch]!=root/('openssl-arm64' if arch=='arm64' else 'openssl-x86_64')/'lib'/name:raise Refusal('merge thin library not fixed fresh target')
            raw[arch],_=checkpoint_bytes(paths[arch],record)
        output=root/'fat/lib'/name
        if output.exists() or output.is_symlink():raise Refusal('fresh lipo output already exists')
        result=child([LIPO,'-create',str(paths['arm64']),str(paths['x86_64']),'-output',str(output)],root,root/'logs','lipo-'+name,deadline)
        from intel_deps_stages import file_record
        row=file_record(output,maximum=268435456)
        row.update(kind='regular',mode=stat.S_IMODE(output.lstat().st_mode))
        verified,_=checkpoint_bytes(output,row)
        records.append({'name':name,'actual_command':result,'output':row,'binary':fat_archive(verified,raw)})
    if time.monotonic()>deadline:raise Refusal('merge including verification exceeded allotted deadline')
    exclusive_json(root/'MERGE_OUTPUT_VERIFIED.json',{'archives':records,'individual_child_birth':'NOT_CLAIMED','closure_owner':'outer make original process group'})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--checkpoint-sha',required=True);p.add_argument('--deadline',type=float,required=True)
    a=p.parse_args();merge(a.root,a.checkpoint,a.deadline,a.checkpoint_sha)
