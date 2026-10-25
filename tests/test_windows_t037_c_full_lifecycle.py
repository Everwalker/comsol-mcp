"""Actual C/Worker/held-A composition over inert exports, never native proof."""
from contextlib import ExitStack
import copy
import json
import subprocess
import pytest

from comsol_mcp import _java_worker as java, _windows_t037 as win
from comsol_mcp import _windows_t037_resume as resume, _windows_t037_targets as targets
from comsol_mcp import _windows_t037_qualification as q
from comsol_mcp import _windows_t037_qualification_run as qualification
from comsol_mcp._windows_owned_resources import registry_for
from comsol_mcp._execution_contract import ExecutionContractError
from tests._prepared_launch_software_fixture import enter_test
from tests.test_windows_native_child_lifecycle import server_case
from tests.test_session_server_control import _start,_stop


def effects(env,state):
    return (tuple(env.sdk.calls),tuple(state['query'].calls),tuple(state['events']),
            tuple(state['sdk_reads']),tuple(state['model']['primary_queries']),
            tuple(state['model']['primary_calls']))


def test_genuine_c_full_worker_start_preserves_resume_and_deferred_pin_contract(monkeypatch,tmp_path):
    fault='known_no_effect'
    with ExitStack() as stack:
        enter_test(stack)
        _,api,model,processes,launcher,_,_=server_case(monkeypatch,tmp_path/'fixture',controller=True)
        env=api._prepared_fixture
        daemon,project,originals=env.public_controller(launcher)
        try:
            live=qualification.require_live_c(daemon,runtime_id='fixture-runtime',live=True)
            metadata_checks=[]
            for name in ('get_operation','job'):
                method=getattr(daemon.store,name)
                def checked(*args,_method=method,**kwargs):
                    assert not daemon._windows_t037_scm_runtime._lock._is_owned()
                    assert not resume._lock._is_owned()
                    metadata_checks.append(True)
                    return _method(*args,**kwargs)
                monkeypatch.setattr(daemon.store,name,checked)
            # Original-reference consumers, not serialized/copyable C facts.
            with pytest.raises(ExecutionContractError):copy.copy(live).check(runtime_id='fixture-runtime')
            with pytest.raises(ExecutionContractError):live.check(runtime_id='another-runtime')
            started=_start(daemon,project,key='full-c-'+fault)
            assert started['success'],started
            sid=started['data']['session_id'];factory,=originals
            state=env._full_c_software_driver.states[id(api)]
            owner=q.require_purpose(factory)
            assert type(owner) is q.OriginalProjectOwner and owner.live_c is live
            for substituted in (None,copy.copy(owner),object()):
                before=(tuple(env.sdk.calls),tuple(state['query'].calls),tuple(state['events']))
                with monkeypatch.context() as m:
                    m.setattr(factory,'_qualification_purpose',substituted)
                    with pytest.raises(ExecutionContractError):factory.preflight()
                assert (tuple(env.sdk.calls),tuple(state['query'].calls),tuple(state['events']))==before
            # The actual normal Worker start is used; no manual mirroring of
            # its exception catch or transfer_to/close ownership fields.
            worker=java.PersistentJavaWorker(live.run.worker.paths,
                state_dir=env.Path(env.profile.workspace_root)/'combined-worker',process_factory=factory)
            env._full_c_software_driver.source_control_workers=[worker]
            calls=[];query=api.k.QueryInformationJobObject
            def query_limits(*args):
                child=state.get('current')
                attempt=getattr(child,'_resume_attempt',None)
                if (fault=='known_no_effect' and attempt is not None and attempt.state=='CONSUMED'
                        and not calls):
                    calls.append(('known_query_failure',child));return 0
                return query(*args)
            api.k.QueryInformationJobObject=query_limits
            terminate=api.terminate_process;wait=api.wait_process
            def keep_live(handle,code):calls.append(('original_terminate',handle))
            def timeout(handle,timeout,args):
                calls.append(('original_wait',handle))
                raise subprocess.TimeoutExpired(args,timeout)
            api.terminate_process=keep_live;api.wait_process=timeout
            with pytest.raises(java.JavaWorkerError) as failed:worker.start(startup_timeout_s=30)
            child=worker._process;held=factory._held_static_a;attempt=child._resume_attempt
            assert child is state['current'] and child._owner is worker
            assert worker._startup_unknown is failed.value and held.state=='UNKNOWN'
            assert held.primary_error._process is child and not held._deferred_finish_entered
            assert not held.reader.retired and child._resume_epoch==1
            assert attempt.state=='NO_EFFECT' and attempt.entered is False
            assert attempt.raw_return_captured is False and not child._resume_attempted
            assert calls[0]==('known_query_failure',child)
            assert [x[0] for x in calls]==['known_query_failure','original_terminate','original_wait']
            before=effects(env,state)
            with pytest.raises(java.JavaWorkerError):worker.start()
            assert effects(env,state)==before
            # Only the original SDK-owned process record changes to known
            # exit; no owner/epoch/attempt/registry state is rewritten.
            row=next(r for r in state['records'] if r['child'] is child)
            row['alive']=False
            api.terminate_process=terminate;api.wait_process=wait
            worker.close()
            assert child._closed and child._process_entry.state==child._thread_entry.state=='RELEASED'
            assert worker._process is worker._startup_unknown is None
            assert held.state=='UNKNOWN' and not held._deferred_finish_entered
            entries=tuple(registry_for(factory).unresolved())
            assert entries and all(e.state=='ACQUIRED' for e in entries)
            reader=held.reader;primary=held.primary_error;witnesses=[]
            original_info=env.sdk.k.GetFileInformationByHandleEx
            def info(*args):
                if held._deferred_finish_entered:
                    witnesses.append((resume.original_no_effect_attempt_witness(child),
                        resume.original_no_effect_attempt_witness(child,retired_worker=worker)))
                return original_info(*args)
            monkeypatch.setattr(env.sdk.k,'GetFileInformationByHandleEx',info)
            # Observe the real STOPPING transition before normal Server
            # retirement, while the original purpose/runtime is still
            # registered. It cannot admit a new child; no state is reset.
            stopping_checks=[]
            def stop_server(sdk_api,handle,code):
                assert sdk_api is api
                before=effects(env,state)
                assert daemon.session_lifecycle.get(project,sid)['state']=='STOPPING'
                with pytest.raises(ExecutionContractError) as denied:owner.require_birth()
                assert 'current PROJECT lifecycle' in str(denied.value)
                assert effects(env,state)==before
                stopping_checks.append(True)
            env._full_c_software_driver.source_before_terminate=stop_server
            # Normal public stop also closes its original factory. Install
            # the witness observation before that actual close, not after.
            stopped=_stop(daemon,project,sid,key='full-c-no-effect-stop')
            assert stopped['success'],stopped
            assert all(c._closed for c in processes) and stopping_checks==[True]
            assert reader.retired and held.state=='RELEASED' and held.primary_error is primary
            assert metadata_checks
            assert held._deferred_finish_entered and all(e.state=='RELEASED' for e in entries)
            assert witnesses and all(pair==(False,True) for pair in witnesses)
            assert child._resume_epoch==1 and attempt.state=='NO_EFFECT' and attempt.capability.used
            assert not registry_for(factory).unresolved() and not registry_for(api).unresolved()
            assert api._software_handle_events.count(('close',300))==1
            before=effects(env,state)
            with pytest.raises(ExecutionContractError):factory.preflight()
            assert effects(env,state)==before
            before=effects(env,state);factory.close();worker.close()
            assert effects(env,state)==before
            (tmp_path/'actual-composition.json').write_text(json.dumps(dict(
                scope='SOURCE_SOFTWARE_ONLY_NO_WINDOWS_NATIVE',fault=fault,
                original_start=started['success'],attempt_state=attempt.state,
                entered=attempt.entered,raw_captured=attempt.raw_return_captured,
                worker_retired=worker._process is None,held_state=held.state,
                first_finish_entered=held._deferred_finish_entered,
                C_source=type(live).__name__,C_native='NOT_RUN'),indent=1)+'\n')
        finally:daemon.close()


def test_actual_apply_resume_unknown_retains_original_api_and_blocks_all_reentry(monkeypatch,tmp_path):
    with ExitStack() as stack:
        enter_test(stack)
        _,api,_,_,launcher,_,_=server_case(monkeypatch,tmp_path/'fixture',controller=True)
        env=api._prepared_fixture
        original=OSError('SOFTWARE original fixed OS Resume entered, return lost')
        env._source_file_resume_error=original
        try:
            with pytest.raises(ExecutionContractError):env.public_controller(launcher)
            driver=env._full_c_software_driver
            api=driver.apis['files'];state=driver.states[id(api)]
            child=state['current'];factory=child._original_factory
            owner=q._PURPOSES[id(factory)]
            operation=owner.operation;run=operation.coordinator;held=factory._held_static_a
            attempt=child._resume_attempt
            assert attempt.state=='UNKNOWN' and attempt.entered and attempt.call_error is original
            assert attempt.raw_return_captured is False and child._resume_attempted
            assert child._owner is api and api._retained_children[id(child)] is child
            assert child._birth_preparation.state=='BOUND' and not child._parent_binding.closed
            assert child._process_entry.state==child._thread_entry.state=='ACQUIRED'
            assert held.state=='UNKNOWN' and not held.reader.retired and not held._deferred_finish_entered
            assert operation.state==run.state=='UNKNOWN' and run.first_error is operation.first_error
            assert operation.live_c is None and state['events'].count('RESUME')==1
            before=effects(env,state)
            for operation_call in (run.execute,lambda:operation.after_resource_readback(),factory.close,
                    child.poll,lambda:child.wait(timeout=0.01),child.terminate,
                    lambda:held.primary_error.transfer_to(object()),
                    lambda:qualification.require_live_c(env.daemon,runtime_id='fixture-runtime')):
                with pytest.raises(ExecutionContractError):operation_call()
            assert effects(env,state)==before and state['events'].count('RESUME')==1
            assert run.first_error is operation.first_error and run.state=='UNKNOWN'
            assert child._owner is api and not child._closed and not held._deferred_finish_entered
            # There is no admitted Server or Worker to retire on this failed
            # first OS stage. Real daemon teardown retains the original effect.
            env.daemon.close()
            assert effects(env,state)==before and child._owner is api
            assert api._retained_children[id(child)] is child and operation.live_c is None
            (tmp_path/'unknown-composition.json').write_text(json.dumps(dict(
                scope='ORIGINAL_APPLY_FIRST_OS_STAGE_SOURCE_SOFTWARE_ONLY',
                state=run.state,original_owner='original launch API',entered=attempt.entered,
                child_retired=child._closed,C='UNQUALIFIED',native='NOT_RUN'),indent=1)+'\n')
        finally:env.daemon.close()


def test_actual_scm_stop_after_consumed_job_query_refuses_resume_without_new_authority(monkeypatch,tmp_path):
    with ExitStack() as stack:
        enter_test(stack)
        _,api,_,_,launcher,_,_=server_case(monkeypatch,tmp_path/'fixture',controller=True)
        env=api._prepared_fixture;env._source_runtime_stop_after_consume=True
        try:
            with pytest.raises(ExecutionContractError):env.public_controller(launcher)
            driver=env._full_c_software_driver;api=driver.apis['files'];state=driver.states[id(api)]
            child=state['current'];attempt=child._resume_attempt
            owner=q._PURPOSES[id(child._original_factory)];operation=owner.operation
            assert state['scm_stop_called'] and operation.enrollment.runtime.stop_requested
            assert attempt.state=='NO_EFFECT' and attempt.entered is False
            assert attempt.capability.used and child._resume_epoch==1
            assert not child._resume_attempted and attempt.raw_return_captured is False
            assert state['events'].count('RESUME')==0 and operation.live_c is None
            assert operation.state==operation.coordinator.state=='UNKNOWN'
            # Known no-entry cleanup may retire original acquisitions; it may
            # not restore SCM, the spent epoch, C, or a new birth/Resume entry.
            assert child._process_info is attempt.process_info and child._api is attempt.api
            before=effects(env,state)
            for call in (operation.coordinator.execute,child._original_factory.preflight,
                         lambda:qualification.require_live_c(env.daemon)):
                with pytest.raises(ExecutionContractError):call()
            assert effects(env,state)==before and state['events'].count('RESUME')==0
            assert attempt.capability.used and child._resume_epoch==1
        finally:env.daemon.close()
