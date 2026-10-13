#!/usr/bin/env python3
"""Task-local exact offline Rust/OpenSSL/cryptography stages. Execution needs Root review."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import time
import tomllib
from acquire_exact_archives import MANIFEST_SHA, Refusal, identity, write_json
from resume_exact_archives_r1 import checked_regular
from cpython_universal2_stages import (SDK,CLANG,StageUnknown,TerminalCommandFailure,run,observe_unknown,
    diagnostic_write,fallback,file_record,read_record,task_environment,require_stage)
from intel_binary_evidence import inspect_static_archive,inspect_crypto_wheel
from intel_deps_prepare import SOURCE_INVENTORY_SHA,copy_exact_tree,install_wheel_exact,vendor_checksums,exclusive_json
from security_snapshot._host_scoped_provider import verify_private_path_security
HERE=Path(__file__).parent
MANIFEST=HERE.parent/'w25-native-delivery-preparation-r2/INTEL_X86_ARCHIVE_ACQUISITION_MANIFEST_R1.json'
CPYTHON_EXECUTION_SHA='2a2577b9359c07ad964e67e2d747bbf9d26aa682f28bedf1f50ef46cc8d750d5'
CPYTHON_SOURCE_SHA='735fe8009e23f74db836ea058fb45c8feade776a471afca69c16dc6347fe1947'
EXACT_PREFIX=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4')
STAGES=('prepare','rust','openssl','crypto')
RUST=(('rust-rustc-aarch64-apple-darwin','rustc-1.83.0-aarch64-apple-darwin','rustc'),
      ('rust-cargo-aarch64-apple-darwin','cargo-1.83.0-aarch64-apple-darwin','cargo'),
      ('rust-rust-std-aarch64-apple-darwin','rust-std-1.83.0-aarch64-apple-darwin','rust-std-aarch64-apple-darwin'),
      ('rust-rust-std-x86_64-apple-darwin','rust-std-1.83.0-x86_64-apple-darwin','rust-std-x86_64-apple-darwin'))

def layout(prefix):
    work=prefix/'intel-deps-r2'
    return {'work':work,'inputs':work/'inputs','vendor':work/'vendor','rust':work/'rust',
            'openssl':work/'openssl-x86_64','crypto':work/'inputs/cryptography-sdist-50.0.1/cryptography-50.0.1',
            'openssl_source':work/'inputs/openssl-source-4.0.2/openssl-4.0.2',
            'site':work/'host-site','scripts':work/'host-bin','logs':work/'build-logs',
            'wheelhouse':work/'wheelhouse','cargo_home':work/'cargo-home','cargo_target':work/'cargo-target'}

def env_for(prefix):
    x=layout(prefix);env=task_environment(prefix)
    env.update(PATH=str(x['rust']/'bin')+':'+str(x['scripts'])+':'+env['PATH'],PYTHONPATH=str(x['site']),
               SETUPTOOLS_USE_DISTUTILS='local',CARGO_HOME=str(x['cargo_home']),CARGO_TARGET_DIR=str(x['cargo_target']),
               CARGO_NET_OFFLINE='true',CARGO_BUILD_JOBS='2',RUSTUP_TOOLCHAIN='1.83.0',
               OPENSSL_DIR=str(x['openssl']),OPENSSL_INCLUDE_DIR=str(x['openssl']/'include'),OPENSSL_LIB_DIR=str(x['openssl']/'lib'),OPENSSL_STATIC='1',OPENSSL_NO_VENDOR='1',
               PKG_CONFIG_LIBDIR=str(x['openssl']/'lib/pkgconfig'),PKG_CONFIG_PATH=str(x['openssl']/'lib/pkgconfig'),PKG_CONFIG_ALLOW_CROSS='1',
               PYO3_PYTHON=str(prefix/'cpython-universal2/bin/python3.12'),PYO3_NO_PYTHON='1',PYO3_CROSS='1',
               PYO3_CROSS_PYTHON_VERSION='3.12',PYO3_CROSS_LIB_DIR=str(prefix/'cpython-universal2/lib/python3.12'),
               CC_x86_64_apple_darwin=CLANG,CFLAGS_x86_64_apple_darwin='-arch x86_64 -isysroot '+str(SDK)+' -mmacosx-version-min=11.0',
               CARGO_TARGET_X86_64_APPLE_DARWIN_LINKER=CLANG,
               CARGO_TARGET_X86_64_APPLE_DARWIN_RUSTFLAGS='-C link-arg=-isysroot -C link-arg='+str(SDK)+' -C link-arg=-mmacosx-version-min=11.0',
               COMSOL_TASK_DEPS_ROOT=str(x['work']))
    return env

def commands(stage,prefix):
    x=layout(prefix)
    if stage=='rust':
        return [(["/bin/sh",str(x['inputs']/artifact/root/'install.sh'),'--prefix='+str(x['rust']),
                  '--disable-ldconfig','--components='+component,'--sysconfdir='+str(x['rust']/'etc'),
                  '--bindir='+str(x['rust']/'bin'),'--libdir='+str(x['rust']/'lib'),'--datadir='+str(x['rust']/'share'),
                  '--mandir='+str(x['rust']/'share/man'),'--docdir='+str(x['rust']/'share/doc/rust')],x['inputs']/artifact/root) for artifact,root,component in RUST]
    if stage=='openssl':
        return [(['/usr/bin/perl',str(x['openssl_source']/'Configure'),'darwin64-x86_64-cc',
                  'no-shared','no-tests','no-apps','--prefix='+str(x['openssl']),'--openssldir='+str(x['openssl']/'ssl'),'--libdir=lib',
                  'CC='+CLANG,'CFLAGS=-O2 -arch x86_64 -isysroot '+str(SDK)+' -mmacosx-version-min=11.0'],x['openssl_source']),
                (['/usr/bin/make','-j2','build_libs'],x['openssl_source']),
                (['/usr/bin/make','install_dev'],x['openssl_source'])]
    if stage=='crypto':
        return [([str(prefix/'cpython-universal2/bin/python3.12'),'-B','-m','maturin','build','--release',
                  '--offline','--locked','--features','pyo3/abi3-py39','--target','x86_64-apple-darwin','--interpreter',str(prefix/'cpython-universal2/bin/python3.12'),
                  '--out',str(x['wheelhouse'])],x['crypto'])]
    return []

def tree_record(root):
    rows=[]
    for p in sorted(root.rglob('*')):
        st=p.lstat();r={'relative_path':p.relative_to(root).as_posix(),'identity':identity(st),'mode':stat.S_IMODE(st.st_mode)}
        if st.st_uid!=os.getuid():raise Refusal('task artifact ownership changed')
        if stat.S_ISREG(st.st_mode):
            if st.st_nlink!=1:raise Refusal('task artifact has shared hardlinks')
            r.update(kind='regular',bytes=st.st_size,sha256=file_record(p,maximum=1073741824)['sha256'])
        elif stat.S_ISDIR(st.st_mode):r['kind']='directory'
        elif stat.S_ISLNK(st.st_mode):
            if not p.resolve().is_relative_to(root):raise Refusal('installed link leaves owned subtree')
            r.update(kind='symlink',target=os.readlink(p))
        else:raise Refusal('installed special file rejected')
        rows.append(r)
    return rows

def validate_lock_closure(lockfile,crates):
    lock=tomllib.loads(lockfile.read_text())
    registry=[pkg for pkg in lock['package'] if 'source' in pkg]
    if any(pkg['source']!='registry+https://github.com/rust-lang/crates.io-index' for pkg in registry):raise Refusal('non-crates.io lock source refused')
    exact={(pkg['name'],pkg['version'],pkg['checksum']) for pkg in registry}
    expected={(row['filename'].removesuffix('.crate').rsplit('-',1)[0],row['version'],row['expected_sha256']) for row in crates}
    if len(registry)!=32 or len(exact)!=32 or exact!=expected:raise Refusal('manifest is not the exact original Cargo.lock closure')

def prepare(prefix,manifest):
    x=layout(prefix);raw=(prefix/'OFFLINE_SOURCE_TREE_INVENTORY_R3.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SOURCE_INVENTORY_SHA:raise Refusal('original unpack inventory changed')
    inventory=json.loads(raw);rows=inventory['rows']
    if inventory['source_root']!=str(prefix/'sources'):raise Refusal('inventory source root mismatch')
    for key in ('inputs','vendor','site','scripts','wheelhouse','cargo_home','cargo_target'):
        x[key].mkdir(mode=0o700)
    copied=[]
    def copy_subtree(relative,destination):
        subtree=[dict(r,relative_path=r['relative_path'][len(relative)+1:]) for r in rows if r['relative_path'].startswith(relative+'/')]
        roots=[r for r in rows if r['relative_path']==relative]
        if len(roots)!=1 or roots[0]['kind']!='directory':raise Refusal('exact source subtree root absent')
        return copy_exact_tree(prefix/'sources'/relative,subtree,destination,root_record=roots[0])
    exact_rust={(row['artifact_id'],row['filename']) for row in manifest['artifacts'] if row['artifact_id'].startswith('rust-')}
    if exact_rust!={(artifact,root+'.tar.xz') for artifact,root,component in RUST}:raise Refusal('fixed four Rust packages differ from exact manifest')
    for artifact,root,component in RUST:
        copied.append({'artifact_id':artifact,**copy_subtree(artifact,x['inputs']/artifact)})
    for artifact in ('openssl-source-4.0.2','cryptography-sdist-50.0.1'):
        copied.append({'artifact_id':artifact,**copy_subtree(artifact,x['inputs']/artifact)})
    crates=[row for row in manifest['artifacts'] if row['artifact_id'].startswith('cargo-')]
    if len(crates)!=32:raise Refusal('exact 32 vendored crates required')
    validate_lock_closure(x['crypto']/'Cargo.lock',crates)
    for row in crates:
        artifact=row['artifact_id'];crate=row['filename'].removesuffix('.crate')
        target=x['vendor']/crate
        info=copy_subtree(artifact+'/'+crate,target)
        checksum_files=vendor_checksums(target,row['expected_sha256'])
        copied.append({'artifact_id':artifact,'checksum_files':checksum_files,**info})
    config='[net]\noffline = true\n[source.crates-io]\nreplace-with = "reviewed-vendor"\n[source.reviewed-vendor]\ndirectory = '+json.dumps(str(x['vendor']))+'\n'
    fd=os.open(x['cargo_home']/'config.toml',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:os.write(fd,config.encode());os.fsync(fd)
    finally:os.close(fd)
    backend=[row for row in manifest['artifacts'] if row['artifact_id'].startswith('python-build-wheel-')]
    if len(backend)!=4:raise Refusal('exact four backend wheels required')
    wheels=[install_wheel_exact(prefix/'archives'/row['filename'],row,x['site'],x['scripts']) for row in backend]
    return {'copied_sources':copied,'backend_wheels':wheels,'offline_config':file_record(x['cargo_home']/'config.toml'),
            'cargo_lock':file_record(x['crypto']/'Cargo.lock'),'host_python':file_record(prefix/'cpython-universal2/bin/python3.12'),
            'target_headers':[file_record(prefix/'cpython-universal2/include/python3.12'/name) for name in ('Python.h','pyconfig.h')],
            'target_sysconfig':[file_record(p) for p in sorted((prefix/'cpython-universal2/lib/python3.12').glob('_sysconfigdata*.py'))]}

DEP_STAGE_RECEIPT_MAX=8*1048576
PREPARE_CHECKPOINT_SHA='3e465fd14d01679bff2301464fce54b1192da650e07dd45e50cd3c212af4f40a'
PREPARE_CHECKPOINT_BYTES=3719114
PREPARE_CHECKPOINT_IDENTITY={"device":16777231,"inode":17482489}

def read_dep_stage_record(path):
    # Full inventory receipts are larger than the CPython command receipts.
    # Keep a separate explicit bound; never alter the reviewed CPython reader.
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=checked_regular(fd)
        if st.st_size>DEP_STAGE_RECEIPT_MAX:raise Refusal('deps stage receipt exceeds eight MiB bound')
        raw=bytearray()
        while chunk:=os.read(fd,1048576):
            raw.extend(chunk)
            if len(raw)>DEP_STAGE_RECEIPT_MAX:raise Refusal('deps stage receipt grew beyond bound')
        if len(raw)!=st.st_size:raise Refusal('deps stage receipt size changed during read')
        return json.loads(raw)
    finally:os.close(fd)

def prior_stage(work,stage):
    path=work/(stage+'.PASS.json')
    if stage=='prepare':
        checkpoint=file_record(path,maximum=DEP_STAGE_RECEIPT_MAX)
        if checkpoint['identity']!=PREPARE_CHECKPOINT_IDENTITY or checkpoint['bytes']!=PREPARE_CHECKPOINT_BYTES or checkpoint['sha256']!=PREPARE_CHECKPOINT_SHA:raise Refusal('exact actual successful prepare checkpoint changed')
    prior=read_dep_stage_record(path)
    if prior.get('status')!='PASS' or prior.get('stage')!=stage or prior.get('manifest_sha256')!=MANIFEST_SHA:raise Refusal('prior stage not exact PASS')
    if stage=='prepare':
        prepared=prior['prepared']
        checks=[prepared['cargo_lock'],prepared['host_python'],prepared['offline_config'],*prepared['target_headers'],*prepared['target_sysconfig']]
        if len(prepared['target_sysconfig'])!=1:raise Refusal('one actual installed target sysconfig required')
        owned=work.parent
        expected_paths=[str(work/'inputs/cryptography-sdist-50.0.1/cryptography-50.0.1/Cargo.lock'),str(owned/'cpython-universal2/bin/python3.12'),str(work/'cargo-home/config.toml'),str(owned/'cpython-universal2/include/python3.12/Python.h'),str(owned/'cpython-universal2/include/python3.12/pyconfig.h')]
        if [record['path'] for record in checks[:5]]!=expected_paths:raise Refusal('prepared prerequisite path escapes exact owned files')
        config_path=Path(checks[5]['path'])
        if config_path.parent!=owned/'cpython-universal2/lib/python3.12' or not re.fullmatch(r'_sysconfigdata[A-Za-z0-9_.-]*\.py',config_path.name):raise Refusal('target sysconfig path escapes exact installed prefix')
        for record in checks:
            if file_record(Path(record['path']))!=record:raise Refusal('prepared lock/header/sysconfig/backend input changed')
        for key in ('vendor','site','scripts'):
            if tree_record(work/key if key=='vendor' else work/('host-site' if key=='site' else 'host-bin'))!=prior['artifacts'][key]:raise Refusal('prepared backend/vendor tree changed')
    if stage in ('rust','openssl'):
        key='rust' if stage=='rust' else 'openssl'
        root=work/'rust' if key=='rust' else work/'openssl-x86_64'
        if tree_record(root)!=prior['artifacts'][key]:raise Refusal('installed prerequisite artifact changed')
    if len(prior.get('commands',[]))!={'prepare':0,'rust':4,'openssl':3,'crypto':1}[stage]:raise Refusal('prior stage command count differs')
    for command in prior.get('commands',[]):
        if command.get('status')!='PASS' or command.get('rc')!=0 or command.get('owned_process_terminal') is not True or command.get('owned_group_absent') is not True:
            raise Refusal('prior stage command lacks owned terminal closure')
        name=command.get('result_name')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.result\.json',name):raise Refusal('prior command name escapes owned logs')
        raw=read_record(work/'build-logs'/name)
        if raw!={k:v for k,v in command.items() if k!='result_name'}:raise Refusal('prior stage raw result differs')
        token=command['result_name'].removesuffix('.result.json')
        birth=read_record(work/'build-logs'/(token+'.birth.json'));request=read_record(work/'build-logs'/(token+'.request.json'))
        if birth!=raw['birth'] or birth['status']!='OBSERVED' or request['argv']!=raw['argv']:raise Refusal('prior birth/request mismatch')
        for name in (raw['stdout'],raw['stderr']):
            if name!=token+('.stdout' if name==raw['stdout'] else '.stderr'):raise Refusal('prior raw log escapes token')
            file_record(work/'build-logs'/name,maximum=268435456)
    return prior

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prefix',type=Path,required=True)
    parser.add_argument('--stage',choices=STAGES,required=True);parser.add_argument('--execute-stage',action='store_true');a=parser.parse_args()
    prefix=a.prefix;x=layout(prefix)
    if not a.execute_stage:
        print(json.dumps({'status':'PREPARED_NOT_RUN','stage':a.stage,'commands':[{'argv':v,'cwd':str(c)} for v,c in commands(a.stage,prefix)],'environment':env_for(prefix)}));return
    if platform.system()!='Darwin' or platform.machine()!='arm64' or prefix!=EXACT_PREFIX or prefix!=prefix.resolve():raise Refusal('exact ARM Darwin acquired prefix required')
    if hashlib.sha256((HERE/'cpython_universal2_stages.py').read_bytes()).hexdigest()!=CPYTHON_SOURCE_SHA:raise Refusal('reviewed owned process runner changed')
    if hashlib.sha256((HERE/'CPYTHON_STAGES_EXECUTION_RESULT_R4_3.json').read_bytes()).hexdigest()!=CPYTHON_EXECUTION_SHA:raise Refusal('actual CPython dependency receipt changed')
    if hashlib.sha256(MANIFEST.read_bytes()).hexdigest()!=MANIFEST_SHA:raise Refusal('exact manifest changed')
    manifest=json.loads(MANIFEST.read_text());verify_private_path_security(prefix,directory=True)
    owner=read_record(prefix/'OWNER.json')
    if owner['security']['identity']!=identity(prefix.stat()) or owner['uid']!=os.getuid() or owner['manifest_sha256']!=MANIFEST_SHA:raise Refusal('original prefix owner mismatch')
    require_stage(prefix/'cpython-universal2-build','headers');require_stage(prefix/'cpython-universal2-build','install')
    if a.stage=='prepare':x['work'].mkdir(mode=0o700);x['logs'].mkdir(mode=0o700)
    verify_private_path_security(x['work'],directory=True);verify_private_path_security(x['logs'],directory=True)
    for stage in STAGES[:STAGES.index(a.stage)]:prior_stage(x['work'],stage)
    fd=os.open(x['work'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);lfd=os.open(x['logs'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    active=None;known=False;results=[];prepared=None
    try:
        try:os.stat(a.stage+'.GUARD.json',dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:pass
        else:raise Refusal('permanent stage guard forbids replay')
        active=os.open('ACTIVE_STAGE.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd);active_identity=identity(os.fstat(active))
        write_json(fd,a.stage+'.GUARD.json',{'stage':a.stage,'pid':os.getpid(),'uid':os.getuid(),'argv':sys.argv,'started_unix':time.time()})
        os.write(active,json.dumps({'stage':a.stage,'pid':os.getpid()}).encode());os.fsync(active)
        if a.stage=='prepare':prepared=prepare(prefix,manifest)
        if a.stage=='crypto':
            for directory in (x['crypto'],*x['crypto'].parents):
                for name in ('config','config.toml'):
                    path=directory/'.cargo'/name
                    if path.exists() or path.is_symlink():raise Refusal('ancestor Cargo configuration not admitted: '+str(path))
        for i,(argv,cwd) in enumerate(commands(a.stage,prefix)):
            results.append(run(lfd,a.stage+str(i),argv,cwd,env_for(prefix),timeout=1800))
        artifact_roots={'prepare':['inputs','vendor','site','scripts'],'rust':['rust'],'openssl':['openssl'],'crypto':['wheelhouse']}[a.stage]
        artifacts={key:tree_record(x[key]) for key in artifact_roots}
        target_evidence=None
        if a.stage=='openssl':
            target_evidence=[inspect_static_archive(x['openssl']/'lib'/name) for name in ('libssl.a','libcrypto.a')]
        if a.stage=='crypto':
            wheels=list(x['wheelhouse'].glob('*.whl'))
            if len(wheels)!=1:raise Refusal('exact one target crypto wheel required')
            target_evidence=inspect_crypto_wheel(wheels[0])
        write_json(fd,a.stage+'.PASS.json',{'stage':a.stage,'status':'PASS','manifest_sha256':MANIFEST_SHA,'commands':results,'prepared':prepared,'artifacts':artifacts,'target_binary_evidence':target_evidence,'pure_x86_runtime':'NOT_RUN','scientific_acceptance':'NOT_RUN'});known=True
    except StageUnknown as exc:
        if active is not None:
            diagnostic_write(fd,a.stage+'.UNKNOWN.json',{'stage':a.stage,'status':'UNKNOWN','active_guard_retained':True},exc.diagnostic_errors)
            if exc.process is not None:observe_unknown(lfd,exc)
        raise
    except BaseException as exc:
        if active is not None:
            diagnostic_write(fd,a.stage+'.FAIL.json',{'stage':a.stage,'error_type':type(exc).__name__,'error':str(exc),'active_guard_retained':True,'known_completed_commands':results},[])
        raise
    finally:
        if known:
            st=os.stat('ACTIVE_STAGE.json',dir_fd=fd,follow_symlinks=False)
            if identity(st)!=active_identity:raise Refusal('active guard identity differs')
            os.unlink('ACTIVE_STAGE.json',dir_fd=fd);os.fsync(fd)
        for handle in (active,lfd,fd):
            if handle is not None:
                try:os.close(handle)
                except BaseException as exc:fallback('deps stage close failure: '+repr(exc))
if __name__=='__main__':main()
