#!/usr/bin/env python3
"""One exact reviewed continuation, never replaying the successful prepare stage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
PREFIX=Path('/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-intel-inputs-r1-obneuyy4')
PYTHON='/private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python3'
ABI_SHA='96d56276c6405e1b81d6bd21907998b4314ece9dfa82800d11e1b936936a456f'
PREVIOUS_RESULT_SHA='3ed33876d67181b6f0286a703fbe4ce99905cca2b61fb89b349be81dc762f296'
OLD_PIDS=(94095,94101,94124)
CAP=15000
RESERVE=15

def verify_sources(freeze_sha,review_sha):
    paths={'INTEL_DEPS_SOURCE_FREEZE_R4_1.json':freeze_sha,'ROOT_INTEL_DEPS_SOURCE_REVIEW_R4_1.json':review_sha,
           'ROOT_INTEL_CRYPTO_ABI_CHOICE_R1.json':ABI_SHA,'INTEL_DEPS_EXECUTION_READER_FAILURE_R3_1.json':PREVIOUS_RESULT_SHA}
    for name,expected in paths.items():
        if not re.fullmatch('[0-9a-f]{64}',expected) or hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('exact source/review/ABI/original terminal pin differs: '+name)
    frozen=json.loads((HERE/'INTEL_DEPS_SOURCE_FREEZE_R4_1.json').read_text())
    for row in frozen['source_files']+[frozen['execution_wrapper_source']]:
        path=REPO/row['path']
        if not path.resolve().is_relative_to(REPO):raise RuntimeError('source reference leaves repository')
        raw=path.read_bytes()
        if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise RuntimeError('frozen source bytes differ: '+str(path))
    return paths

def verify_initial_checkpoint(work):
    import intel_deps_stages as deps
    prior=deps.prior_stage(work,'prepare')
    roots={'inputs':'inputs','vendor':'vendor','site':'host-site','scripts':'host-bin'}
    for key,relative in roots.items():
        if deps.tree_record(work/relative)!=prior['artifacts'][key]:raise deps.Refusal('complete initial checkpoint tree differs: '+key)
    return prior

def verify_unchanged_inputs(work,prior,after_openssl=False):
    import intel_deps_stages as deps
    actual=deps.tree_record(work/'inputs');expected=prior['artifacts']['inputs']
    if after_openssl:
        def unchanged(row):
            name=row['relative_path'];return name!='openssl-source-4.0.2' and not name.startswith('openssl-source-4.0.2/')
        actual=[r for r in actual if unchanged(r)];expected=[r for r in expected if unchanged(r)]
    if actual!=expected:raise deps.Refusal('prepared immutable source inputs changed')

def retain_unknown_owner(logfd,error):
    from cpython_universal2_stages import observe_unknown,diagnostic_write,fallback,failure_detail
    # Even an observer-interface failure cannot discard the original Popen.
    while True:
        try:return observe_unknown(logfd,error)
        except BaseException as exc:
            error.diagnostic_errors.append(failure_detail('continuation_observer_interface',exc))
            if hasattr(error.diagnostic_errors,'causes'):error.diagnostic_errors.causes.append(exc)
            fallback('UNKNOWN same original handle retained: '+repr(exc))
            diagnostic_write(logfd,error.token+'.observer_interface.'+str(len(error.diagnostic_errors))+'.json',
                             {'pid':error.process.pid,'error_type':type(exc).__name__,'error':str(exc),'same_original_handle_retained':True},error.diagnostic_errors)
            try:time.sleep(10)
            except BaseException as pause_error:fallback('UNKNOWN observation pause failed: '+repr(pause_error))

def main():
    started=time.monotonic()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--freeze-sha',required=True);parser.add_argument('--review-sha',required=True);a=parser.parse_args()
    pins=verify_sources(a.freeze_sha,a.review_sha)
    # Only import the owned executable helpers after their exact bytes are verified.
    import intel_deps_stages as deps
    from cpython_universal2_stages import run,StageUnknown,TerminalCommandFailure,observe_unknown,diagnostic_write,diagnostic_print,DiagnosticErrors
    from acquire_exact_archives import write_json,identity
    from security_snapshot._host_scoped_provider import verify_private_path_security
    work=PREFIX/'intel-deps-r2'
    verify_private_path_security(PREFIX.parent,directory=True);verify_private_path_security(PREFIX,directory=True);verify_private_path_security(work,directory=True)
    for name in ('ACTIVE_STAGE.json','rust.GUARD.json','openssl.GUARD.json','crypto.GUARD.json'):
        try:(work/name).lstat()
        except FileNotFoundError:pass
        else:raise deps.Refusal('continuation admission state already exists: '+name)
    old=subprocess.run(['/bin/ps','-p',','.join(map(str,OLD_PIDS)),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,env=dict(os.environ,LC_ALL='C'),timeout=10)
    if old.returncode!=1 or old.stdout.strip() or old.stderr.strip():raise deps.Refusal('previous exact owned process absence unresolved')
    prior=verify_initial_checkpoint(work)
    if time.monotonic()-started>=600:raise deps.Refusal('initial verification exceeded600s; no native command admitted')
    wrapper=Path(tempfile.mkdtemp(prefix='comsol-intel-deps-r41-continuation-',dir=PREFIX.parent));security=verify_private_path_security(wrapper,directory=True)
    logfd=os.open(wrapper,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);workfd=os.open(work,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    errors=DiagnosticErrors();outcomes=[];status='UNKNOWN'
    try:
        # Permanent no-clobber attempt guard, independent of each stage guard.
        write_json(workfd,'CONTINUATION_R4_1.GUARD.json',{'pid':os.getpid(),'uid':os.getuid(),'argv':sys.argv,'wrapper':str(wrapper),'pins':pins,'started_unix':time.time()})
        write_json(logfd,'PREVIOUS_OWNED_PIDS_ABSENCE.json',{'argv':old.args,'rc':old.returncode,'stdout':old.stdout,'stderr':old.stderr})
        self_ps=subprocess.run(['/bin/ps','-p',str(os.getpid()),'-o','pid=,ppid=,uid=,lstart=,command='],capture_output=True,text=True,env=dict(os.environ,LC_ALL='C'),timeout=10)
        write_json(logfd,'OWNER.json',{'pid':os.getpid(),'uid':os.getuid(),'argv':sys.argv,'security':security,'pins':pins,'birth_raw':{'argv':self_ps.args,'rc':self_ps.returncode,'stdout':self_ps.stdout,'stderr':self_ps.stderr},'prepare_checkpoint_identity':deps.PREPARE_CHECKPOINT_IDENTITY,'prepare_checkpoint_sha':deps.PREPARE_CHECKPOINT_SHA,'total_cap_s':CAP,'reserve_s':RESERVE,'initial_complete_checkpoint_verified':True})
        diagnostic_print({'wrapper':str(wrapper),'pid':os.getpid(),'status':'STARTED_CONTINUATION_ONLY','initial_complete_checkpoint_verified':True},errors)
        for stage,budget in (('rust',7200),('openssl',5400),('crypto',1800)):
            verify_sources(a.freeze_sha,a.review_sha)
            for previous in deps.STAGES[:deps.STAGES.index(stage)]:deps.prior_stage(work,previous)
            verify_unchanged_inputs(work,prior,after_openssl=stage=='crypto')
            remaining=CAP-RESERVE-(time.monotonic()-started)
            if remaining<=0:status='UNKNOWN_BATCH_DEADLINE';break
            argv=[PYTHON,'-B',str(HERE/'intel_deps_stages.py'),'--prefix',str(PREFIX),'--stage',stage,'--execute-stage']
            write_json(logfd,stage+'.stage_request.json',{'argv':argv,'stage':stage,'source_pins_reverified':pins,'remaining_cap_s':remaining,'stage_wait_budget_s':min(budget,remaining),'started_unix':time.time()})
            diagnostic_print({'stage':stage,'status':'RUNNING_SAME_UNIT','elapsed_s':time.monotonic()-started},errors)
            phase=time.monotonic()
            try:
                result=run(logfd,'continuation-'+stage,argv,HERE,deps.task_environment(PREFIX),timeout=min(budget,remaining))
            except StageUnknown as exc:
                status='UNKNOWN';diagnostic_write(logfd,stage+'.UNKNOWN.json',{'stage':stage,'token':exc.token,'original_result':exc.original_result,'guard_retained':True},exc.diagnostic_errors)
                if exc.process is not None:retain_unknown_owner(logfd,exc)
                break
            except TerminalCommandFailure as exc:
                status='UNKNOWN_SOURCE_STAGE' if (work/(stage+'.UNKNOWN.json')).exists() else 'CLI_TERMINAL_STAGE_FAILURE';outcomes.append({'stage':stage,'result':exc.result,'elapsed_s':time.monotonic()-started})
                diagnostic_write(logfd,stage+'.terminal.json',outcomes[-1],errors);break
            outcome={'stage':stage,'result':result,'elapsed_s':time.monotonic()-started,'stage_elapsed_s':time.monotonic()-phase}
            outcomes.append(outcome);diagnostic_write(logfd,stage+'.terminal.json',outcome,errors);diagnostic_print({'stage':stage,'status':'CLI_TERMINAL_PASS','elapsed_s':outcome['elapsed_s']},errors)
            if errors or outcome['stage_elapsed_s']>budget or outcome['elapsed_s']>CAP:status='UNKNOWN_DIAGNOSTICS_OR_DEADLINE';break
            deps.prior_stage(work,stage)
        else:status='ALL_REMAINING_STAGES_COMPLETE'
    except BaseException as exc:
        status='PRECONDITION_OR_DIAGNOSTIC_FAILURE';diagnostic_write(logfd,'WRAPPER_FAILURE.json',{'type':type(exc).__name__,'error':str(exc),'no_replay':True},errors)
    finally:
        result={'status':status,'stages':outcomes,'wrapper':str(wrapper),'elapsed_s':time.monotonic()-started,'total_cap_s':CAP,'within_total_cap':time.monotonic()-started<=CAP,'diagnostic_errors':list(errors),'permanent_continuation_guard_retained':True,'prepare_replayed':False,'x86_execution':'NOT_RUN','final_delivery':'NOT_RUN'}
        diagnostic_write(logfd,'RESULT.json',result,errors);diagnostic_print(result,errors)
        for fd in (workfd,logfd):
            try:os.close(fd)
            except BaseException as exc:deps.fallback('wrapper close failed: '+repr(exc))
    return 0 if status=='ALL_REMAINING_STAGES_COMPLETE' and not errors else 1
if __name__=='__main__':sys.exit(main())
