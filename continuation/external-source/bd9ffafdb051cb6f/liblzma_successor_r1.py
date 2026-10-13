#!/usr/bin/env python3
"""Private fixed-input TLS/hashlib universal2 successor. Root review required."""
import argparse,hashlib,json,os,platform,stat,subprocess,sys,time
from pathlib import Path
from acquire_exact_archives import Refusal,write_json
from intel_crypto_successor_r1 import PREFIX,HERE,REPO,checkpoint_bytes,verify_old_unit_tree
import intel_deps_stages as deps
import cpython_universal2_stages as cp
from intel_deps_prepare import copy_exact_tree,exclusive_json,SOURCE_INVENTORY_SHA
from tls_binary_r1 import static_archive,fat_binary
from run_intel_deps_continuation_r4_1 import retain_unknown_owner,verify_unchanged_inputs

HOST_PYTHON='/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3'
UNIT_CAP=1800;RESERVE=300
CAPS={'xz-arm64-configure':120,'xz-arm64-build':300,'xz-arm64-install':120,'xz-x86_64-configure':120,'xz-x86_64-build':300,'xz-x86_64-install':120,'prepare':600,'arm-configure':120,'arm-build':600,'arm-install':120,'headers-compare':120,'merge':180,'cp-configure':120,'header-arm64':60,'header-x86_64':60,'cp-build':600,'cp-install':600,'runtime':180}
ANCHOR_ID={'device':16777231,'inode':17174941}
ORIGINAL_NAMES=('cpython-universal2','cpython-universal2-build','intel-crypto-r3')

def layout(prefix=PREFIX):
    root=prefix/'intel-liblzma-r1'
    return dict(root=root,arm_source=root/'openssl-source',arm=root/'openssl-arm64',fat=root/'fat',cp_source=root/'cpython-input/Python-3.12.13',build=root/'cpython-build',install=root/'cpython',logs=root/'logs',xz_source_arm64=root/'xz-arm64-source',xz_source_x86_64=root/'xz-x86_64-source',xz_build_arm64=root/'xz-arm64-build',xz_build_x86_64=root/'xz-x86_64-build',lzma_arm64=root/'lzma-arm64',lzma_x86_64=root/'lzma-x86_64')

class Budget:
    def __init__(self,clock=time.monotonic):self.clock=clock;self.start=clock()
    def remaining(self,stage):
        value=min(CAPS[stage],UNIT_CAP-RESERVE-(self.clock()-self.start))
        if value<=0:raise Refusal('unit budget exhausted; no next command')
        return value
    def completed(self,start,stage):
        if self.clock()-start>CAPS[stage] or self.clock()-self.start>UNIT_CAP-RESERVE:raise Refusal('stage/acceptance wall budget exceeded')
    def wait_remaining(self,stage,start):
        remaining=min(self.remaining(stage),CAPS[stage]-(self.clock()-start))
        if remaining<=0:raise Refusal('stage preparation exhausted native wait budget')
        return remaining

def frozen_digest(path):
    """Same-FD source hash; one fixed Apple primary document may be root owned."""
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd);allowed_uid=0 if str(path)=='/Library/Developer/CommandLineTools/usr/share/man/man1/lipo.1' else os.getuid()
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=allowed_uid or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)&0o022 or st.st_size>1073741824:raise Refusal('frozen source/document ownership/mode/link/bound mismatch')
        digest=hashlib.sha256();size=0
        while data:=os.read(fd,1048576):
            size+=len(data)
            if size>1073741824:raise Refusal('frozen source/document grew beyond bound')
            digest.update(data)
        final=os.fstat(fd)
        if (final.st_dev,final.st_ino,final.st_size,final.st_uid,final.st_nlink,final.st_mode)!=(st.st_dev,st.st_ino,st.st_size,st.st_uid,st.st_nlink,st.st_mode) or size!=st.st_size:raise Refusal('frozen source/document changed during same-FD read')
        return {'bytes':size,'sha256':digest.hexdigest()}
    finally:os.close(fd)

def source_gate(freeze_sha,review_sha):
    raw=(HERE/'LIBLZMA_SOURCE_FREEZE_R1.json').read_bytes();review=(HERE/'ROOT_LIBLZMA_SOURCE_REVIEW_R1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=freeze_sha or hashlib.sha256(review).hexdigest()!=review_sha:raise Refusal('TLS freeze/Root review mismatch')
    freeze=json.loads(raw)
    verify_original_archives(freeze)
    for row in freeze['source_files']+freeze['primary_refs']:
        path=Path(row['path']);path=path if path.is_absolute() else REPO/path
        actual=frozen_digest(path)
        if actual['bytes']!=row['bytes'] or actual['sha256']!=row['sha256']:raise Refusal('frozen TLS source or primary evidence changed: '+str(path))
    return {'freeze_sha256':freeze_sha,'review_sha256':review_sha}

def verify_original_archives(freeze):
    from acquire_exact_archives import manifest_rows
    rows=freeze['original_archive_inputs'];manifest=manifest_rows()
    if len(rows)!=43 or {row['path'] for row in rows}!={str(PREFIX/'archives'/item['filename']) for item in manifest}:raise Refusal('original43 archive path closure differs')
    by_path={row['path']:row for row in rows}
    for item in manifest:
        row=by_path[str(PREFIX/'archives'/item['filename'])]
        if row['bytes']!=item['expected_bytes'] or row['sha256']!=item['expected_sha256']:raise Refusal('original43 archive metadata differs from exact manifest')
        checkpoint_bytes(Path(row['path']),row)
    return {'original43_archive_owner_identity_mode_bytes_unchanged':True,'archives':43}

def record(path,maximum=134217728):
    row=cp.file_record(path,maximum);row.update(kind='regular',mode=stat.S_IMODE(path.lstat().st_mode));return row

def exclusive_text(path,text):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        view=memoryview(text.encode())
        while view:view=view[os.write(fd,view):]
        os.fsync(fd)
    finally:os.close(fd)

def prepare_sources(x,prefix=PREFIX):
    from liblzma_prepare_r1 import prepare
    if prefix!=PREFIX:raise Refusal('fixed LZMA prefix required')
    return prepare(x)


def compare_headers(arm,x86):
    trees=[deps.tree_record(p) for p in (arm,x86)];normalized=[]
    for tree in trees:
        if any(r['kind'] not in ('directory','regular') for r in tree):raise Refusal('header tree links/specials rejected')
        normalized.append([{k:v for k,v in r.items() if k not in ('identity','mode')} for r in tree])
    if normalized[0]!=normalized[1]:
        a={r['relative_path']:r for r in normalized[0]};b={r['relative_path']:r for r in normalized[1]}
        changed=[p for p in sorted(a.keys()|b.keys()) if a.get(p)!=b.get(p)]
        raise Refusal('actual ARM/x86 installed headers differ: '+json.dumps(changed))
    for a,b in zip(trees[0],trees[1]):
        if a['kind']=='regular':
            ra=dict(a,path=str(arm/a['relative_path']));rb=dict(b,path=str(x86/b['relative_path']))
            if checkpoint_bytes(Path(ra['path']),ra)[0]!=checkpoint_bytes(Path(rb['path']),rb)[0]:raise Refusal('actual same-FD header bytes differ')
    return {'exact_bytes_equal':True,'arm':trees[0],'x86_64':trees[1]}

def copy_headers(source,destination,tree):
    rows=[]
    for r in tree:
        row=dict(relative_path=r['relative_path'],kind=r['kind'],uid=os.getuid(),device=r['identity']['device'],inode=r['identity']['inode'],mode=oct(r['mode']))
        if r['kind']=='regular':row.update(bytes=r['bytes'],sha256=r['sha256'],nlink=1)
        rows.append(row)
    st=source.lstat();root={'relative_path':'','kind':'directory','uid':st.st_uid,'device':st.st_dev,'inode':st.st_ino,'mode':oct(stat.S_IMODE(st.st_mode))}
    return copy_exact_tree(source,rows,destination,root_record=root)

def command_plan(x):
    env=cp.task_environment(PREFIX);env.update(COMSOL_TASK_BUILD_PREFIX=str(x['install']),COMSOL_TASK_BUILD_LOGS=str(x['logs']))
    env['PKG_CONFIG_LIBDIR']=str(x['fat']/'lib/pkgconfig');env['PKG_CONFIG_PATH']=str(x['fat']/'lib/pkgconfig')
    env['LIBLZMA_CFLAGS']='-I'+str(x['fat']/'include');env['LIBLZMA_LIBS']=str(x['fat']/'lib/liblzma.a')
    # No inherited compiler/pkg-config/Homebrew/OpenSSL override is carried.
    plan=[]
    for arch in ('arm64','x86_64'):
        source=x['xz_source_'+arch];build=x['xz_build_'+arch];install=x['lzma_'+arch]
        host='aarch64-apple-darwin' if arch=='arm64' else 'x86_64-apple-darwin'
        args=['/bin/sh',str(source/'configure'),'--prefix='+str(install),'--build=aarch64-apple-darwin','--host='+host,'--disable-shared','--enable-static','--enable-threads=posix','--disable-xz','--disable-xzdec','--disable-lzmadec','--disable-lzmainfo','--disable-lzma-links','--disable-scripts','--disable-doc','--disable-doxygen','--disable-nls','--disable-dependency-tracking']
        plan.extend([('xz-'+arch+'-configure',args,build),('xz-'+arch+'-build',['/usr/bin/make','-C','src/liblzma','-j2'],build),('xz-'+arch+'-install',['/usr/bin/make','-C','src/liblzma','install'],build)])
    plan.append(('merge',['/usr/bin/make','-f',str(x['root']/'Merge.mk'),'merge'],x['root']))
    argv=cp.stage_commands('configure',x['cp_source'],x['build'],x['install'])[0]+['--with-openssl='+str(x['fat']),'--with-openssl-rpath=no']
    plan.append(('cp-configure',argv,x['build']))
    for arch,argv in zip(('arm64','x86_64'),cp.stage_commands('headers',x['cp_source'],x['build'],x['install'])):plan.append(('header-'+arch,argv,x['build']))
    plan.extend([('cp-build',['/usr/bin/make','-j2'],x['build']),('cp-install',['/usr/bin/make','altinstall'],x['build']),('runtime',['/usr/bin/make','-f',str(x['root']/'Runtime.mk'),'runtime'],x['root'])])
    return plan,env

def stage_environment(label,base):
    env=dict(base)
    if label.startswith('xz-'):
        arch='arm64' if label.startswith('xz-arm64-') else 'x86_64'
        env.update(CC=cp.CLANG+' -arch '+arch+' -isysroot '+str(cp.SDK)+' -mmacosx-version-min=11.0',CFLAGS='-O2 -g0',CPPFLAGS='',LDFLAGS='-arch '+arch+' -isysroot '+str(cp.SDK)+' -mmacosx-version-min=11.0',AR='/usr/bin/ar',RANLIB='/usr/bin/ranlib',NM='/usr/bin/nm',STRIP='/usr/bin/strip')
    return env

def actual_binaries(x):
    result={'python':fat_binary(checkpoint_bytes(x['install']/'bin/python3.12',record(x['install']/'bin/python3.12'))[0],(2,))}
    for name in ('_ssl','_hashlib','_lzma'):
        matches=list((x['install']/'lib/python3.12/lib-dynload').glob(name+'.*.so'))
        if len(matches)!=1:raise Refusal('required exact extension missing/ambiguous: '+name)
        row=record(matches[0]);result[name]={'file':row,'slices':fat_binary(checkpoint_bytes(matches[0],row)[0],(6,8))}
    for facts in result['_lzma']['slices'].values():
        if any('lzma' in Path(p).name.lower() for p in facts['linked_dylibs']):raise Refusal('required target LZMA linked dynamic system/foreign fallback')
    return result

def original_root_record(root,expected=None):
    """Fixed read-only child directory beneath the original strict private anchor."""
    from security_snapshot._host_scoped_provider import verify_private_path_security
    if root not in {PREFIX/name for name in ORIGINAL_NAMES}:raise Refusal('not one of three fixed original roots')
    verify_private_path_security(PREFIX,directory=True)
    anchor=os.open(PREFIX,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    child=None
    try:
        a=os.fstat(anchor)
        if {'device':a.st_dev,'inode':a.st_ino}!=ANCHOR_ID or a.st_uid!=os.getuid() or stat.S_IMODE(a.st_mode)!=0o700:raise Refusal('original private anchor identity/owner/mode changed')
        child=os.open(root.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=anchor)
        st=os.fstat(child)
        if not stat.S_ISDIR(st.st_mode) or st.st_dev!=a.st_dev or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)&0o022:raise Refusal('original child directory owner/type/mount/write permissions changed')
        row={'path':str(root),'device':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':stat.S_IMODE(st.st_mode),'anchor_identity':dict(ANCHOR_ID)}
        if expected is not None and row!=expected:raise Refusal('original child directory swapped or identity/mode changed')
        return row
    finally:
        if child is not None:os.close(child)
        os.close(anchor)

def preservation_outcome(old,original_roots,original_trees,status,errors,budget,original_root_records=None):
    """Actual terminal verification facts remain independent of status rewrites."""
    verified=False;failure=None;final_root_records={};prior_tls_source=None
    try:
        from liblzma_cpython_checkpoint_r1 import preservation_verified_summary
        prior_tls_source=preservation_verified_summary()
        from liblzma_prepare_r1 import verify_tls_runtime
        verify_tls_runtime()
        verify_old_unit_tree(old)
        for root in original_roots:
            if original_root_records is not None:original_root_record(root,original_root_records[str(root)])
            if deps.tree_record(root)!=original_trees[str(root)]:raise Refusal('original CPython/crypto tree changed: '+str(root))
            if original_root_records is not None:final_root_records[str(root)]=original_root_record(root,original_root_records[str(root)])
        verified=True
    except BaseException as exc:
        failure={'phase':'preservation','error_type':type(exc).__name__,'error':str(exc),'repr':repr(exc)}
        status='PRESERVATION_FAIL';errors.append(failure)
        if hasattr(errors,'causes'):errors.causes.append(exc)
    if time.monotonic()-budget.start>UNIT_CAP or errors:status='UNKNOWN_CLEANUP_OR_CAP'
    return status,{'old_roots_unchanged':verified,'preservation_failure':failure,'final_original_root_records':final_root_records,'prior_failed_TLS_and_original_source':prior_tls_source}

def stage_done_record_name(label):
    if label not in ('xz-arm64-configure','xz-arm64-build','xz-arm64-install','xz-x86_64-configure','xz-x86_64-build','xz-x86_64-install','merge','cp-configure','header-arm64','header-x86_64','cp-build','cp-install','runtime'):raise Refusal('unknown fixed LZMA stage label')
    return 'STAGE_'+label+'_DONE.json'

def run_install_with_original_acceptance(x,accepted,lfd,argv,cwd,env,budget,stage_start):
    from liblzma_build_gate_r1 import verify_preinstall_checkpoint
    if argv!=['/usr/bin/make','altinstall'] or cwd!=x['build']:raise Refusal('fixed original install invocation required')
    verify_preinstall_checkpoint(x,accepted)
    remaining=budget.wait_remaining('cp-install',stage_start)
    return cp.run(lfd,'cp-install',argv,cwd,env,timeout=remaining)

def main():
    budget=Budget();p=argparse.ArgumentParser();p.add_argument('--freeze-sha',required=True);p.add_argument('--review-sha',required=True);a=p.parse_args()
    pins=source_gate(a.freeze_sha,a.review_sha);x=layout();old=PREFIX/'intel-deps-r2'
    from security_snapshot._host_scoped_provider import verify_private_path_security
    if platform.system()!='Darwin' or platform.machine()!='arm64':raise Refusal('native ARM Darwin host required')
    verify_private_path_security(PREFIX,directory=True)
    if x['root'].exists() or x['root'].is_symlink():raise Refusal('fresh TLS namespace must be absent; no replay')
    from liblzma_cpython_checkpoint_r1 import preservation_verified_summary
    prior_tls_source=preservation_verified_summary()
    from liblzma_prepare_r1 import verify_tls_runtime
    verify_tls_runtime()
    preservation=verify_old_unit_tree(old);prepare=deps.prior_stage(old,'prepare');deps.prior_stage(old,'rust');openssl=deps.prior_stage(old,'openssl');verify_unchanged_inputs(old,prepare,after_openssl=True)
    # Preserve all prior installed/source/failure data including successful crypto.
    original_roots=[PREFIX/'cpython-universal2',PREFIX/'cpython-universal2-build',PREFIX/'intel-crypto-r3']
    original_root_records={str(root):original_root_record(root) for root in original_roots}
    original_trees={str(root):deps.tree_record(root) for root in original_roots}
    for root in original_roots:original_root_record(root,original_root_records[str(root)])
    ps=subprocess.run(['/bin/ps','-p','59819,59907,42746,42748,43190,43086,52171,70948,63053,70935,70943,63042,53404,53380,61829,63047,63046,63044','-o','pid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10)
    if ps.returncode!=1 or ps.stdout.strip() or ps.stderr.strip():raise Refusal('prior crypto ownership not proven absent')
    runtime_ps=subprocess.run(['/bin/ps','-p','43875,43877,43972','-o','pid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10)
    if runtime_ps.returncode!=1 or runtime_ps.stdout.strip() or runtime_ps.stderr.strip() or not all(cp.group_absent(p) for p in (43877,43972)):raise Refusal('accepted runtime actual owners/groups not absent')
    x['root'].mkdir(mode=0o700)
    for key in ('logs','fat','build','xz_build_arm64','xz_build_x86_64'):x[key].mkdir(mode=0o700)
    (x['fat']/'lib').mkdir(mode=0o700)
    fd=os.open(x['root'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);lfd=os.open(x['logs'],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    errors=cp.DiagnosticErrors();status='UNKNOWN';results=[];artifacts=None;accepted_build=None;merged=None;merge_input=None;preservation_facts={'old_roots_unchanged':False,'preservation_failure':None}
    try:
        write_json(fd,'LIBLZMA_R1_ONCE_GUARD.json',{'actual_pid':os.getpid(),'argv':sys.argv,'pins':pins,'unit_cap_s':UNIT_CAP,'reserve_s':RESERVE})
        write_json(fd,'ACTIVE.json',{'actual_pid':os.getpid(),'stage':'prepare'})
        copied=prepare_sources(x);budget.completed(budget.start,'prepare')
        exclusive_json(x['root']/'PREPARED.json',{'copies':copied,'old_complete_tree':preservation,'original_trees':original_trees,'original_root_records':original_root_records,'prior_failed_TLS_and_original_source':prior_tls_source,'prior_crypto_absence':{'argv':ps.args,'rc':ps.returncode,'stdout':ps.stdout,'stderr':ps.stderr}})
        exclusive_text(x['build']/'header_probe.c',cp.PROBE)
        plan,env=command_plan(x)
        for label,argv,cwd in plan:
            source_gate(a.freeze_sha,a.review_sha)
            stage_start=time.monotonic();remaining=budget.remaining(label)
            from liblzma_prepare_r1 import verify_prepared
            verify_prepared(x,copied)
            if label=='merge':
                from liblzma_acceptance_r1 import prepare_merge
                merge_input=prepare_merge(x,budget,stage_start)
            if label.startswith('cp-') or label.startswith('header-') or label=='runtime':
                from liblzma_acceptance_r1 import verify_merged
                verify_merged(x,merged)
            if label=='runtime':
                exclusive_text(x['root']/'Runtime.mk','runtime:\n\t'+str(x['install']/'bin/python3.12')+' -I -B '+str(HERE/'liblzma_runtime_profile_r1.py')+' '+str(x['root']/'ARM_RUNTIME_PROFILE.json')+'\n\t'+HOST_PYTHON+' -B '+str(HERE/'liblzma_verify_r1.py')+' --root '+str(x['root'])+'\n')
            remaining=budget.wait_remaining(label,stage_start)
            write_json(fd,'STAGE_'+label+'_GUARD.json',{'argv':argv,'cwd':str(cwd),'pins':pins,'remaining_wait_cap_s':remaining,'unit_elapsed_s':time.monotonic()-budget.start})
            remaining=budget.wait_remaining(label,stage_start)
            if label=='cp-install':result=run_install_with_original_acceptance(x,accepted_build,lfd,argv,cwd,stage_environment(label,env),budget,stage_start)
            else:result=cp.run(lfd,label,argv,cwd,stage_environment(label,env),timeout=remaining)
            results.append(result)
            if label=='merge':
                from liblzma_acceptance_r1 import accept_merged
                merged=accept_merged(x,merge_input)
            if label=='cp-build':
                from liblzma_build_gate_r1 import required_build_outputs,capture_build_checkpoint
                accepted_build=capture_build_checkpoint(x['root']/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json',required_build_outputs(x,result))
            if label.startswith('header-'):cp.read_probe(x['build']/('header_probe-'+label[7:]),label[7:])
            budget.completed(stage_start,label)
            write_json(fd,stage_done_record_name(label),{'command':result,'elapsed_s':time.monotonic()-stage_start})
        artifacts=actual_binaries(x);status='LZMA_TLS_SCOPED_ACTUAL_PASS'
    except cp.StageUnknown as exc:
        status='UNKNOWN';cp.diagnostic_write(fd,'UNKNOWN.json',{'token':exc.token,'original_result':exc.original_result,'original_cause':repr(exc.original_cause)},exc.diagnostic_errors)
        if exc.process is not None:retain_unknown_owner(lfd,exc)
    except cp.TerminalCommandFailure as exc:
        status='TERMINAL_COMMAND_FAIL';cp.diagnostic_write(fd,'FAIL.json',{'status':status,'command':exc.result},errors)
    except BaseException as exc:
        status='VALIDATION_FAIL';cp.diagnostic_write(fd,'FAIL.json',{'type':type(exc).__name__,'cause':str(exc),'commands':results},errors)
    finally:
        accepted_TLS_unchanged=False;accepted_TLS_failure=None
        try:verify_tls_runtime();accepted_TLS_unchanged=True
        except BaseException as exc:accepted_TLS_failure=repr(exc);errors.append({'phase':'accepted_TLS_preservation','error':accepted_TLS_failure})
        status,preservation_facts=preservation_outcome(old,original_roots,original_trees,status,errors,budget,original_root_records)
        preservation_facts['accepted_TLS_runtime_unchanged']=accepted_TLS_unchanged
        if not accepted_TLS_unchanged:
            preservation_facts['old_roots_unchanged']=False
            preservation_facts['accepted_TLS_preservation_failure']=accepted_TLS_failure
        if preservation_facts['preservation_failure'] is not None:cp.diagnostic_write(fd,'PRESERVATION_FAILURE.json',preservation_facts,errors)
        cp.diagnostic_write(fd,'RESULT.json',{'status':status,'commands':results,'artifacts':artifacts,'elapsed_s':time.monotonic()-budget.start,'unit_cap_s':UNIT_CAP,**preservation_facts,'diagnostic_errors':list(errors),'_lzma':'PASS_ACTUAL_SCOPED' if status=='LZMA_TLS_SCOPED_ACTUAL_PASS' else 'REQUIRED_OPEN','Intel_runtime':'NOT_RUN','final_delivery':'OPEN','fences_retained':True},errors)
        for handle in (lfd,fd):
            try:os.close(handle)
            except BaseException as exc:errors.append({'phase':'close','error':repr(exc)});deps.fallback(repr(exc))
    if errors or time.monotonic()-budget.start>UNIT_CAP:status='UNKNOWN_CLEANUP_OR_CAP'
    closed={'status':status,**preservation_facts,'elapsed_including_cleanup_s':time.monotonic()-budget.start,'errors':list(errors),'fences_retained':True,'within_cap':time.monotonic()-budget.start<=UNIT_CAP}
    exclusive_json(x['root']/'CLOSED.json',closed)
    return 0 if status=='LZMA_TLS_SCOPED_ACTUAL_PASS' and not errors and closed['within_cap'] else 1

if __name__=='__main__':sys.exit(main())
