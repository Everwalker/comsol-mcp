"""One reviewed make runtime; original installed R4 tree is immutable input."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
import openssl359_cpython_successor_r4 as prior
import cpython_universal2_stages as cp
import intel_deps_stages as deps
from acquire_exact_archives import Refusal,write_json
from intel_crypto_successor_r1 import checkpoint_bytes,verify_old_unit_tree
from intel_deps_prepare import exclusive_json
from run_intel_deps_continuation_r4_1 import retain_unknown_owner
from openssl359_cpython_checkpoint_r4 import preservation_verified_summary
HERE=prior.HERE;REPO=prior.REPO;PREFIX=prior.PREFIX
INSTALLED=prior.layout()['root'];ROOT=PREFIX/'intel-tls-openssl359-runtime-r1'
SNAPSHOT=PREFIX.parent/'comsol-openssl359-cpython-r4-execution-5h135itv/COMPLETE_OPENSSL359_CP_R4_TREE.json'
SNAPSHOT_PIN={'kind':'regular','mode':0o600,'identity':{'device':16777231,'inode':22996854},'bytes':4366330,'sha256':'63f7c4d7f0bda0fb93a8a923dcdf7b40a0476b7c0944fff8c260e086b73b1f4b'}
RECEIPT_SHA='ac0bc9e027a0f8741f31e585184a0ba3d183e059d03b59c01e90b62354cbf87f'
def source_gate(freeze_sha,review_sha):
    raw=(HERE/'OPENSSL359_RUNTIME_SOURCE_FREEZE_R1.json').read_bytes();review=(HERE/'ROOT_OPENSSL359_RUNTIME_SOURCE_REVIEW_R1.json').read_bytes()
    if prior.hashlib.sha256(raw).hexdigest()!=freeze_sha or prior.hashlib.sha256(review).hexdigest()!=review_sha:raise Refusal('runtime whole freeze/Root review mismatch')
    freeze=json.loads(raw);prior.verify_original_archives(freeze)
    for row in freeze['source_files']+freeze['primary_refs']:
        p=Path(row['path']);p=p if p.is_absolute() else REPO/p
        if prior.frozen_digest(p)!={'bytes':row['bytes'],'sha256':row['sha256']}:raise Refusal('frozen runtime source/evidence changed')
    return {'freeze_sha256':freeze_sha,'review_sha256':review_sha}
def verify_inputs():
    from security_snapshot._host_scoped_provider import verify_private_path_security
    verify_private_path_security(PREFIX,directory=True);verify_private_path_security(INSTALLED,directory=True)
    identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode&0o7777)
    expected=(16777231,22914005,os.getuid(),0o700)
    if identity(INSTALLED.lstat())!=expected:raise Refusal('fixed original R4 root identity changed')
    rows=json.loads(checkpoint_bytes(SNAPSHOT,SNAPSHOT_PIN)[0])
    if deps.tree_record(INSTALLED)!=rows or identity(INSTALLED.lstat())!=expected:raise Refusal('full immutable R4 tree/root differs')
    r=prior.record(HERE/'OPENSSL359_CPYTHON_EXECUTION_RESULT_R4.json')
    if r['sha256']!=RECEIPT_SHA:raise Refusal('R4 actual failure receipt changed')
    receipt=json.loads(checkpoint_bytes(Path(r['path']),r)[0])
    if receipt['closed']['status']!='TERMINAL_COMMAND_FAIL' or [r['rc'] for r in receipt['commands']]!=[0,0,0,0,0,2]:raise Refusal('original R4 actual terminal closure changed')
    preservation_verified_summary();verify_old_unit_tree(PREFIX/'intel-deps-r2')
    prepared=json.loads((INSTALLED/'PREPARED.json').read_bytes())
    for path,tree in prepared['original_trees'].items():
        prior.original_root_record(Path(path),prepared['original_root_records'][path])
        if deps.tree_record(Path(path))!=tree:raise Refusal('old original complete tree changed')
    return {'R4_records':len(rows),'R4_snapshot_sha256':SNAPSHOT_PIN['sha256'],'all_old_trees_unchanged':True,'R4_original_actual_receipt_sha256':RECEIPT_SHA}
def recipe():
    return 'runtime:\n\t'+str(INSTALLED/'cpython/bin/python3.12')+' -I -B '+str(HERE/'openssl359_runtime_profile_r2.py')+' '+str(ROOT/'ARM_RUNTIME_PROFILE.json')+'\n\t'+prior.HOST_PYTHON+' -B '+str(HERE/'openssl359_runtime_verify_r1.py')+' --root '+str(ROOT)+'\n'
def main():
    budget=prior.Budget();p=argparse.ArgumentParser();p.add_argument('--freeze-sha',required=True);p.add_argument('--review-sha',required=True);a=p.parse_args()
    pins=source_gate(a.freeze_sha,a.review_sha);inputs=verify_inputs()
    if ROOT.exists() or ROOT.is_symlink():raise Refusal('fresh runtime-only namespace exists; no replay')
    ps=subprocess.run(['/bin/ps','-p','3509,3514,12385,4402,15928,12316,12343,20139','-o','pid=,uid=,lstart=,command='],capture_output=True,text=True,timeout=10)
    if ps.returncode!=1 or ps.stdout.strip() or ps.stderr.strip():raise Refusal('R4 original owners not absent')
    for pgid in (3514,12385,4402,15928,12316,12343,20139):
        if not cp.group_absent(pgid):raise Refusal('R4 original group not absent')
    ROOT.mkdir(mode=0o700);(ROOT/'logs').mkdir(mode=0o700)
    fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);lfd=os.open(ROOT/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    errors=cp.DiagnosticErrors();status='UNKNOWN';command=None;unchanged=False;failure=None
    try:
        write_json(fd,'RUNTIME_ONCE_GUARD.json',{'pid':os.getpid(),'argv':sys.argv,'pins':pins,'unit_cap_s':1800,'cleanup_reserve_s':300,'runtime_cap_s':180})
        write_json(fd,'ACTIVE.json',{'pid':os.getpid(),'stage':'prepare'})
        write_json(fd,'INPUTS_VERIFIED.json',inputs);prior.exclusive_text(ROOT/'Runtime.mk',recipe());budget.completed(budget.start,'prepare')
        source_gate(a.freeze_sha,a.review_sha);verify_inputs();start=time.monotonic()
        command=cp.run(lfd,'runtime',['/usr/bin/make','-f',str(ROOT/'Runtime.mk'),'runtime'],ROOT,prior.cp.task_environment(PREFIX),timeout=budget.wait_remaining('runtime',start))
        budget.completed(start,'runtime')
        verified=prior.record(ROOT/'ACTUAL_RUNTIME_AND_INSTALL.json');checkpoint_bytes(Path(verified['path']),verified)
        write_json(fd,'RUNTIME_DONE.json',{'command':command,'artifact':verified});status='TLS_HASHLIB_SCOPED_ACTUAL_PASS'
    except cp.StageUnknown as exc:
        status='UNKNOWN';cp.diagnostic_write(fd,'UNKNOWN.json',{'token':exc.token,'original_result':exc.original_result,'original_cause':repr(exc.original_cause)},exc.diagnostic_errors)
        if exc.process is not None:retain_unknown_owner(lfd,exc)
    except cp.TerminalCommandFailure as exc:
        status='TERMINAL_COMMAND_FAIL';cp.diagnostic_write(fd,'FAIL.json',{'command':exc.result},errors)
    except BaseException as exc:
        status='VALIDATION_FAIL';cp.diagnostic_write(fd,'FAIL.json',{'type':type(exc).__name__,'cause':str(exc)},errors)
    finally:
        try:verify_inputs();unchanged=True
        except BaseException as exc:failure=repr(exc);errors.append({'phase':'old_whole_preservation','error':failure});status='PRESERVATION_FAIL'
        if time.monotonic()-budget.start>1800 or errors:status='UNKNOWN_CLEANUP_OR_CAP'
        cp.diagnostic_write(fd,'RESULT.json',{'status':status,'command':command,'old_roots_unchanged':unchanged,'preservation_failure':failure,'errors':list(errors),'elapsed_s':time.monotonic()-budget.start,'fences_retained':True,'_lzma':'REQUIRED_OPEN','Intel_hardware':'NOT_RUN','final_delivery':'OPEN'},errors)
        for handle in (lfd,fd):
            try:os.close(handle)
            except BaseException as exc:errors.append({'phase':'close','error':repr(exc)});deps.fallback(repr(exc))
    if errors or time.monotonic()-budget.start>1800:status='UNKNOWN_CLEANUP_OR_CAP'
    exclusive_json(ROOT/'CLOSED.json',{'status':status,'old_roots_unchanged':unchanged,'preservation_failure':failure,'errors':list(errors),'elapsed_including_cleanup_s':time.monotonic()-budget.start,'within_cap':time.monotonic()-budget.start<=1800,'fences_retained':True})
    return 0 if status=='TLS_HASHLIB_SCOPED_ACTUAL_PASS' and not errors else 1
if __name__=='__main__':sys.exit(main())
