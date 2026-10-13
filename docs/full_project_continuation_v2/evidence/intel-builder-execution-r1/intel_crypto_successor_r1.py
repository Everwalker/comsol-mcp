#!/usr/bin/env python3
"""Independent offline crypto successor; old failed crypto fence remains untouched."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import time
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
PREFIX=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4')
ROOT_ABI_SHA='96d56276c6405e1b81d6bd21907998b4314ece9dfa82800d11e1b936936a456f'
OLD_RESULT_SHA='bac965c71f6c22688a9c36551f3ae4446e0d772fc438aeb958efee1188f9806f'
OLD_CLOSE_SHA='c4b1305d92c68031384220fe8eb646dee284defb3a7dd4fa7f9b64c80f546ba1'
OLD_PIDS=(21020,21053,27313,40435,21055,22444,24428,25863,27315,27351,39197,40437)

def paths(prefix=PREFIX):
    root=prefix/'intel-crypto-r3'
    return {'root':root,'source':root/'cryptography-50.0.1','cargo_home':root/'cargo-home','target':root/'cargo-target','wheels':root/'wheelhouse','logs':root/'logs'}

def crypto_env(prefix,owned):
    import intel_deps_stages as deps
    env=deps.env_for(prefix)
    env.update(CARGO_HOME=str(owned['cargo_home']),CARGO_TARGET_DIR=str(owned['target']),COMSOL_TASK_DEPS_ROOT=str(owned['root']))
    return env

def crypto_argv(prefix,owned):
    return [str(prefix/'intel-deps-r2/host-bin/maturin'),'build','--release','--offline','--locked',
            '--features','pyo3/abi3-py39','--target','x86_64-apple-darwin','--interpreter',
            str(prefix/'cpython-universal2/bin/python3.12'),'--out',str(owned['wheels'])]

def checkpoint_bytes(path,expected):
    from resume_exact_archives_r1 import checked_regular
    from acquire_exact_archives import identity,Refusal
    import stat
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=checked_regular(fd)
        if (identity(st)!=expected['identity'] or st.st_size!=expected['bytes'] or stat.S_IMODE(st.st_mode)!=expected['mode']
                or expected['kind']!='regular' or st.st_size>134217728):raise Refusal('exact prerequisite file identity/size/mode differs')
        data=bytearray()
        while chunk:=os.read(fd,1048576):
            data.extend(chunk)
            if len(data)>134217728:raise Refusal('prerequisite raw bytes exceed bound')
        final=checked_regular(fd);raw=bytes(data)
        if identity(final)!=identity(st) or final.st_size!=st.st_size or len(raw)!=st.st_size or hashlib.sha256(raw).hexdigest()!=expected['sha256']:
            raise Refusal('exact prerequisite same-FD bytes changed')
        return raw,{'path':str(path),'identity':identity(st),'bytes':st.st_size,'mode':expected['mode'],'sha256':expected['sha256']}
    finally:os.close(fd)

class VerifiedFileBytes:
    """Immutable bytes from an actual checked FD for the existing production parser."""
    def __init__(self,path,raw):self.path,self.raw=path,raw
    def read_bytes(self):return self.raw
    def __str__(self):return str(self.path)

def inspect_host_tool(path,expected):
    from acquire_exact_archives import Refusal
    raw,record=checkpoint_bytes(path,expected)
    if expected['mode']!=0o700 or len(raw)<48 or struct.unpack_from('>II',raw)!=(0xcafebabe,2):raise Refusal('maturin is not exact executable universal2 tool')
    slices=[];ranges=[]
    for i in range(2):
        cpu,sub,offset,size,align=struct.unpack_from('>iiIII',raw,8+20*i)
        if align>30 or offset<48 or offset%(1<<align) or offset+size>len(raw) or size<32:raise Refusal('tool fat slice bounds/alignment invalid')
        header=struct.unpack_from('<IiiIIIII',raw,offset)
        if header[0]!=0xfeedfacf or header[1]!=cpu or header[3]!=2 or header[4]>4096 or 32+header[5]>size:raise Refusal('tool slice is not actual executable architecture')
        position=offset+32;end=position+header[5];versions=[]
        for _ in range(header[4]):
            if position+8>end:raise Refusal('tool load command truncated')
            command,length=struct.unpack_from('<II',raw,position)
            if length<8 or length%4 or position+length>end:raise Refusal('tool load command bound invalid')
            if command==0x32:
                if length<24:raise Refusal('tool build version short')
                _,_,system,minimum,sdk,tools=struct.unpack_from('<IIIIII',raw,position)
                if system!=1 or length!=24+tools*8:raise Refusal('tool build platform differs')
                versions.append(minimum)
            elif command==0x24:
                if length!=16:raise Refusal('tool minversion short')
                versions.append(struct.unpack_from('<IIII',raw,position)[2])
            position+=length
        minimum={0x100000c:0xb0000,0x1000007:0xa0c00}.get(cpu)
        if position!=end or minimum is None or versions!=[minimum]:raise Refusal('tool actual slice/minOS does not match fixed universal2 wheel')
        ranges.append((offset,offset+size));slices.append({'cpu':cpu,'architecture':{0x100000c:'arm64',0x1000007:'x86_64'}[cpu],'minimum_os':[minimum>>16,(minimum>>8)&255,minimum&255],'bytes':size,'offset':offset})
    if {s['architecture'] for s in slices}!={'arm64','x86_64'} or max(ranges[0][0],ranges[1][0])<min(ranges[0][1],ranges[1][1]):raise Refusal('tool universal2 CPU closure/overlap invalid')
    return {'file':record,'slices':slices,'launch_architecture':'arm64_native_host','x86_slice_execution':'NOT_RUN'}

def copy_crypto_checkpoint(old,owned,prepare):
    from intel_deps_prepare import copy_exact_tree
    from acquire_exact_archives import Refusal
    base='cryptography-sdist-50.0.1/cryptography-50.0.1';rows=prepare['artifacts']['inputs']
    def converted(row,name):
        result={'relative_path':name,'kind':row['kind'],'uid':os.getuid(),'device':row['identity']['device'],'inode':row['identity']['inode'],'mode':oct(row['mode'])}
        if row['kind']=='regular':result.update(bytes=row['bytes'],sha256=row['sha256'],nlink=1)
        return result
    roots=[r for r in rows if r['relative_path']==base]
    if len(roots)!=1:raise Refusal('exact original crypto source root absent')
    selected=[converted(r,r['relative_path'][len(base)+1:]) for r in rows if r['relative_path'].startswith(base+'/')]
    return copy_exact_tree(old/'inputs'/base,selected,owned['source'],root_record=converted(roots[0],''))

OLD_TREE_PATH=PREFIX.parent/'comsol-intel-deps-r41-continuation-i2dgudvn/TERMINAL_UNIT_TREE_INVENTORY.json'
OLD_TREE_SHA='d8db4e06b220f7f4647e53b4734dc3e655144d4448732b2db4a1bdd6b3b57d12'

def verify_old_unit_tree(old):
    from resume_exact_archives_r1 import checked_regular
    import intel_deps_stages as deps
    fd=os.open(OLD_TREE_PATH,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=checked_regular(fd)
        if st.st_size!=4242846:raise deps.Refusal('actual terminal unit tree snapshot size changed')
        raw=bytearray()
        while chunk:=os.read(fd,1048576):
            raw.extend(chunk)
            if len(raw)>8388608:raise deps.Refusal('unit tree snapshot exceeds bound')
        if len(raw)!=st.st_size or hashlib.sha256(raw).hexdigest()!=OLD_TREE_SHA:raise deps.Refusal('actual terminal tree snapshot bytes changed')
        snapshot=json.loads(bytes(raw))
    finally:os.close(fd)
    if deps.tree_record(old)!=snapshot:raise deps.Refusal('old complete failed unit tree/fences/raw/artifacts changed')
    return {'tree_records':len(snapshot),'snapshot_sha256':OLD_TREE_SHA,'old_complete_tree_unchanged':True}

def source_gate(freeze_sha,review_sha):
    pins={'INTEL_CRYPTO_SOURCE_FREEZE_R1.json':freeze_sha,'ROOT_INTEL_CRYPTO_SOURCE_REVIEW_R1.json':review_sha,
          'ROOT_INTEL_CRYPTO_ABI_CHOICE_R1.json':ROOT_ABI_SHA,'INTEL_DEPS_EXECUTION_RESULT_R4_1.json':OLD_RESULT_SHA,
          'INTEL_DEPS_TERMINAL_CLOSE_R4_1.json':OLD_CLOSE_SHA}
    verified={}
    for name,sha in pins.items():
        raw=(HERE/name).read_bytes()
        if not re.fullmatch('[0-9a-f]{64}',sha) or hashlib.sha256(raw).hexdigest()!=sha:raise RuntimeError('exact successor source/review/ABI/terminal binding differs: '+name)
        verified[name]=raw
    frozen=json.loads(verified['INTEL_CRYPTO_SOURCE_FREEZE_R1.json'])
    for row in frozen['source_files']:
        path=REPO/row['path']
        if not path.resolve().is_relative_to(REPO):raise RuntimeError('source reference escapes repository')
        raw=path.read_bytes()
        if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise RuntimeError('frozen source changed: '+str(path))
    return pins

def main():
    started=time.monotonic();parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-sha',required=True);parser.add_argument('--review-sha',required=True);a=parser.parse_args()
    pins=source_gate(a.freeze_sha,a.review_sha)
    import intel_deps_stages as deps
    from cpython_universal2_stages import run,StageUnknown,TerminalCommandFailure,DiagnosticErrors,diagnostic_write,diagnostic_print
    from run_intel_deps_continuation_r4_1 import retain_unknown_owner,verify_unchanged_inputs
    from intel_deps_prepare import exclusive_json
    from intel_binary_evidence import inspect_crypto_wheel
    from acquire_exact_archives import write_json
    from security_snapshot._host_scoped_provider import verify_private_path_security
    if platform.system()!='Darwin' or platform.machine()!='arm64':raise deps.Refusal('successor launcher requires actual ARM64 Darwin host')
    old=PREFIX/'intel-deps-r2';owned=paths()
    verify_private_path_security(PREFIX,directory=True);verify_private_path_security(old,directory=True)
    if owned['root'].exists() or owned['root'].is_symlink():raise deps.Refusal('new successor root must be absent')
    old_ps=subprocess.run(['/bin/ps','-p',','.join(map(str,OLD_PIDS)),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,env=dict(os.environ,LC_ALL='C'),timeout=10)
    if old_ps.returncode!=1 or old_ps.stdout.strip() or old_ps.stderr.strip():raise deps.Refusal('old exact owned PID absence unresolved')
    old_tree_evidence=verify_old_unit_tree(old)
    prepare=deps.prior_stage(old,'prepare');deps.prior_stage(old,'rust');openssl=deps.prior_stage(old,'openssl');verify_unchanged_inputs(old,prepare,after_openssl=True)
    from intel_binary_evidence import inspect_static_archive
    archive_evidence=[]
    for original in openssl['target_binary_evidence']:
        path=Path(original['path']);relative=path.relative_to(old/'openssl-x86_64').as_posix()
        expected=next(row for row in openssl['artifacts']['openssl'] if row['relative_path']==relative)
        raw,record=checkpoint_bytes(path,expected);parsed=inspect_static_archive(VerifiedFileBytes(path,raw))
        if parsed!=original:raise deps.Refusal('actual static object evidence differs from successful OpenSSL checkpoint')
        archive_evidence.append({'file':record,'object_count':parsed['object_count'],'all_objects_x86_macos11':True})
    for guard in ('ACTIVE_STAGE.json','crypto.GUARD.json','CONTINUATION_R4_1.GUARD.json'):
        deps.file_record(old/guard)
    script=inspect_host_tool(old/'host-bin/maturin',prepare['artifacts']['scripts'][0])
    if time.monotonic()-started>=600:raise deps.Refusal('successor preflight exceeded600s; no native command started')
    owned['root'].mkdir(mode=0o700)
    for key in ('cargo_home','target','wheels','logs'):owned[key].mkdir(mode=0o700)
    fd=os.open(owned['root'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);lfd=os.open(owned['logs'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    errors=DiagnosticErrors();status='UNKNOWN';command=None;artifact=None
    try:
        write_json(fd,'CRYPTO_SUCCESSOR_R1.GUARD.json',{'pid':os.getpid(),'uid':os.getuid(),'argv':sys.argv,'pins':pins,'started_unix':time.time()})
        write_json(fd,'ACTIVE_SUCCESSOR.json',{'pid':os.getpid(),'stage':'crypto','old_failed_fences_retained':True})
        write_json(fd,'OLD_OWNED_PID_ABSENCE.json',{'argv':old_ps.args,'rc':old_ps.returncode,'stdout':old_ps.stdout,'stderr':old_ps.stderr})
        copy_result=copy_crypto_checkpoint(old,owned,prepare)
        original=(old/'cargo-home/config.toml').read_bytes();config=owned['cargo_home']/'config.toml'
        out=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            view=memoryview(original)
            while view:view=view[os.write(out,view):]
            os.fsync(out)
        finally:os.close(out)
        if deps.file_record(config)['sha256']!=prepare['prepared']['offline_config']['sha256']:raise deps.Refusal('successor cargo offline config differs')
        if deps.file_record(owned['source']/'Cargo.lock')['sha256']!=prepare['prepared']['cargo_lock']['sha256']:raise deps.Refusal('successor exact Cargo.lock differs')
        for directory in (owned['source'],*owned['source'].parents):
            for name in ('config','config.toml'):
                path=directory/'.cargo'/name
                if path.exists() or path.is_symlink():raise deps.Refusal('unadmitted Cargo ancestor configuration')
        if time.monotonic()-started>=600:raise deps.Refusal('successor materialization exceeded600s; no native command started')
        source_gate(a.freeze_sha,a.review_sha);deps.prior_stage(old,'prepare');deps.prior_stage(old,'rust');deps.prior_stage(old,'openssl')
        write_json(fd,'PREPARED_EXACT.json',{'copy':copy_result,'host_tool':script,'source_lock':deps.file_record(owned['source']/'Cargo.lock'),'cargo_config':deps.file_record(config),'all_mutable_paths_in_successor':True,'verified_static_archives':archive_evidence,'old_complete_tree':old_tree_evidence})
        diagnostic_print({'root':str(owned['root']),'pid':os.getpid(),'status':'PREPARED_STARTING_DIRECT_HOST_MATURIN'},errors)
        remaining=1800-15-(time.monotonic()-started)
        if remaining<=0:raise deps.Refusal('unit budget exhausted before native launch')
        command=run(lfd,'crypto-successor',crypto_argv(PREFIX,owned),owned['source'],crypto_env(PREFIX,owned),timeout=remaining)
        wheels=list(owned['wheels'].glob('*.whl'))
        if len(wheels)!=1:raise deps.Refusal('one actual crypto target wheel required')
        artifact=inspect_crypto_wheel(wheels[0])
        if time.monotonic()-started>1800:raise deps.Refusal('unit acceptance/cleanup exceeded1800s; no target PASS')
        status='TARGET_WHEEL_ACTUAL_BUILT'
        write_json(fd,'CRYPTO_SUCCESSOR.PASS.json',{'status':status,'command':command,'artifact':artifact,'wheelhouse_tree':deps.tree_record(owned['wheels']),'Intel_runtime':'NOT_RUN','TLS_full_runtime':'NOT_ACCEPTED','final_delivery':'NOT_RUN'})
    except StageUnknown as exc:
        status='UNKNOWN';diagnostic_write(fd,'CRYPTO_SUCCESSOR.UNKNOWN.json',{'token':exc.token,'original_result':exc.original_result,'active_fence_retained':True},exc.diagnostic_errors)
        if exc.process is not None:retain_unknown_owner(lfd,exc)
    except TerminalCommandFailure as exc:
        status='TERMINAL_COMMAND_FAIL';command=exc.result;diagnostic_write(fd,'CRYPTO_SUCCESSOR.FAIL.json',{'status':status,'command':command,'active_fence_retained':True},errors)
    except BaseException as exc:
        status='VALIDATION_OR_PREPARATION_FAIL';diagnostic_write(fd,'CRYPTO_SUCCESSOR.FAIL.json',{'status':status,'error_type':type(exc).__name__,'error':str(exc),'command':command,'active_fence_retained':True},errors)
    finally:
        if status=='TARGET_WHEEL_ACTUAL_BUILT' and (errors or time.monotonic()-started>1800):status='UNKNOWN_ACCEPTANCE_OR_UNIT_CAP'
        result={'status':status,'root':str(owned['root']),'command':command,'artifact':artifact,'elapsed_s':time.monotonic()-started,'preparation_cap_s':600,'unit_cap_s':1800,'cleanup_reserve_s':15,'within_unit_cap':time.monotonic()-started<=1800,'diagnostic_errors':list(errors),'old_fences_retained':True,'successor_fence_retained':True,'Intel_runtime':'NOT_RUN','final_delivery':'NOT_RUN'}
        diagnostic_write(fd,'RESULT.json',result,errors);diagnostic_print({'status':status,'root':str(owned['root']),'elapsed_s':result['elapsed_s']},errors)
        for handle in (lfd,fd):
            try:os.close(handle)
            except BaseException as exc:deps.fallback('successor fd close failed: '+repr(exc))
    try:preservation=verify_old_unit_tree(old)
    except BaseException as exc:
        status='OLD_UNIT_PRESERVATION_FAIL';preservation={'error_type':type(exc).__name__,'error':str(exc)}
    closed={'status':status,'old_complete_tree':preservation,'elapsed_including_owned_fd_cleanup_s':time.monotonic()-started,'within_unit_cap':time.monotonic()-started<=1800,'same_unit_closed':True,'errors':list(errors),'all_fences_retained':True}
    try:exclusive_json(owned['root']/'CLOSED.json',closed)
    except BaseException as exc:deps.fallback('successor close receipt failed: '+repr(exc));return 1
    return 0 if status=='TARGET_WHEEL_ACTUAL_BUILT' and closed['within_unit_cap'] and time.monotonic()-started<=1800 and not errors else 1
if __name__=='__main__':sys.exit(main())
