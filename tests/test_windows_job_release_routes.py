"""All original production release routes share the captured Job close gate.

Synthetic SDK only; real Controller, Store, launcher, factory, child and registry.
"""
from types import SimpleNamespace
from dataclasses import replace
import gc,os,weakref
import pytest
from comsol_mcp import _windows_t037 as win, _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._session_server import OwnedServerLauncher
from tests import test_windows_job_limits as controls
from tests.test_session_server_control import _make_daemon,_start,_stop


def release_route(route,value,entry,scope):
    registry=owned.registry_for(value)
    if route=='close_job':value.close_job(300)
    elif route=='retry_owned':registry.retry_owned(entry,value)
    elif route=='entry_release':entry.release_owned(value,retry=True)
    elif route=='registry_release':registry.release(value,300)
    elif route=='drain':
        failures=registry.drain(scope)
        # Child entries now also participate in the shared drain. This control
        # checks the target Job's own first refusal, retaining all other errors.
        if failures:raise entry.error if entry.error is not None else failures[0][1]
    elif route=='captured_closer':entry.closer(300)
    else:raise AssertionError(route)


@pytest.mark.parametrize('route',['close_job','retry_owned','entry_release','registry_release','drain','captured_closer'])
def test_actual_controller_unknown_child_all_original_release_exits_deny_then_confirmed_cleanup(tmp_path,monkeypatch,route):
    p,value,events,state,sdk=controls.prepared_child_api(monkeypatch,cleanup_fail=True)
    registry=owned.registry_for(value);scope=registry.scope()
    from tests.test_windows_network_intent import network_profile_and_plan
    host,_=network_profile_and_plan();p=replace(p,host_resource_profile=host)
    factories=[]
    class OriginalSessionLauncher(OwnedServerLauncher):
        def start(self,runtime,project_id,session_id,**kwargs):
            original=win.T037SessionProcessFactory(p,session_id,api=value)
            monkeypatch.setattr(original,'preflight',lambda:None)  # SOFTWARE policy continuation only.
            factories.append(original)
            from tests.test_windows_scm_prebirth import running_factory_chain
            prior_profile=daemon._windows_t037_profile
            daemon._windows_t037_profile=p
            daemon._session_t037_process_factories[(project_id,session_id)]=original
            with running_factory_chain(daemon,original,tmp_path/'scm-chain',monkeypatch):
                try:
                    return super().start(runtime,project_id,session_id,process_factory=original,**kwargs)
                finally:
                    # Leave this cleanup-route fixture's original project
                    # provider/profile intact. The stopped SCM binding refuses
                    # further births without blocking exact owned cleanup.
                    daemon._windows_t037_profile=prior_profile
    monkeypatch.setattr(win,'os',SimpleNamespace(**dict(vars(os),name='nt')))
    launcher=OriginalSessionLauncher(platform_name='darwin')
    daemon,project,*_=_make_daemon(tmp_path,monkeypatch,server_launcher=launcher)
    try:
        result=_start(daemon,project);sid=result['data']['session_id'];key=(project,sid)
        factory,=factories
        handle=daemon._session_server_handles[key];child=handle.process
        entry=next(e for e in registry.unresolved() if e.label=='owned_session_job')
        handle_ref=weakref.ref(handle);factory_ref=weakref.ref(factory);child_ref=weakref.ref(child)
        assert child.poll() is None and sdk.ids==[456] and not child._resume_attempted
        assert daemon.store.job(result['execution']['job_id'])['status']=='UNKNOWN'
        before=sdk.calls.count(('close',300))
        assert before==0
        with pytest.raises(ExecutionContractError) as first:
            release_route(route,value,entry,scope)
        first_cause=first.value
        assert entry.state!='RELEASED' and sdk.calls.count(('close',300))==before
        assert value._job_limit_bindings[300]['entry'] is entry and child._job_handle==factory._job==300
        # Repeated attempts preserve the first close cause and never touch native CloseHandle.
        with pytest.raises(ExecutionContractError) as repeated:registry.retry_owned(entry,value)
        assert repeated.value is first_cause
        assert sdk.calls.count(('close',300))==before and entry.error is first_cause
        del first,repeated;gc.collect()
        assert daemon._session_server_handles[key] is handle_ref() and factory_ref() is not None and child_ref() is not None
        assert daemon.store.job(result['execution']['job_id'])['status']=='UNKNOWN'
        # A genuine failed retained-child cleanup does not authorize Job close.
        failed=_stop(daemon,project,sid,key='close-route-cleanup-failure-'+route)
        assert not failed['success'] and child.poll() is None and sdk.ids==[456]
        with pytest.raises(ExecutionContractError) as denied:registry.retry_owned(entry,value)
        assert denied.value is first_cause and sdk.calls.count(('close',300))==before
        state['cleanup_fail']=False;state['alive']=False  # Child exit is known; whole Job is not empty yet.
        lifecycle_before=daemon.session_lifecycle.get(project,sid)
        stopped=_stop(daemon,project,sid,key='close-route-confirmed-exit-'+route)
        # Child exit alone cannot complete the whole session's UNKNOWN Job.
        assert not stopped['success'] and child._closed and type(child.poll()) is int and sdk.ids==[456]
        assert stopped['data']['server_stopped'] is True
        assert stopped['data']['native_cleanup_state']=='EXIT_CONFIRMED'
        assert stopped['data']['job_retained'] is True and stopped['data']['server_handle_preserved'] is True
        assert daemon._session_server_handles[key] is handle_ref() is handle
        assert daemon._session_t037_process_factories[key] is factory_ref() is factory
        assert handle.process is child_ref() is child and not child._resume_attempted
        assert daemon.session_lifecycle.get(project,sid)==lifecycle_before
        assert entry.state=='UNKNOWN' and entry.error is first_cause
        assert value._job_limit_bindings[300]['entry'] is entry and factory._job==300
        assert sdk.calls.count(('close',300))==before and registry.unresolved()==(entry,)
        unknown_receipts={}
        for receipt in (result,failed,stopped):
            operation=daemon.store.get_operation(receipt['execution']['operation_id'])
            assert operation['status']=='UNKNOWN' and operation['result']==receipt
            assert daemon.store.job(receipt['execution']['job_id'])['status']=='UNKNOWN'
            unknown_receipts[operation['operation_id']]=operation
        # Cached UNKNOWN replay cannot reconcile even after Job emptiness changes.
        sdk.ids=[];before_reconcile=list(sdk.calls);child_events=list(events)
        assert _stop(daemon,project,sid,key='close-route-confirmed-exit-'+route)==stopped
        assert sdk.calls==before_reconcile and entry.state=='UNKNOWN'
        queries=[];original_ids=value._job_process_ids
        def ids(job):queries.append(job);return original_ids(job)
        value._job_process_ids=ids
        # A new public cleanup request lets this original factory reconcile
        # only its pre-SDK deferral after all terminal/empty observations.
        completed=_stop(daemon,project,sid,key='close-route-session-terminal-'+route)
        assert entry.state=='RELEASED' and queries==[300]
        assert sdk.calls==before_reconcile+[('close',300)]
        before_reconcile=list(sdk.calls)
        value._job_process_ids=lambda *a:pytest.fail('confirmed released HANDLE queried during reconcile')
        assert completed['success'] and completed['data']['state']=='STOPPED'
        assert completed['data']['server_stopped'] is True
        assert completed['data']['native_child_exit_evidence']['exit_confirmed'] is True
        terminal=daemon.session_lifecycle.get(project,sid)
        assert terminal['state']=='STOPPED' and terminal['revision']==lifecycle_before['revision']+1
        assert daemon.store.get_operation(completed['execution']['operation_id'])['status']=='SUCCEEDED'
        assert daemon.store.job(completed['execution']['job_id'])['status']=='SUCCEEDED'
        assert key not in daemon._session_server_handles and key not in daemon._session_t037_process_factories
        factory.close();factory.close()
        assert factory._closed and factory._job is None and not value._job_limit_bindings
        # New cleanup completes CAS/unregistration without rewriting/reexecuting
        # any earlier UNKNOWN receipt, including idempotent public replays.
        assert _stop(daemon,project,sid,key='close-route-cleanup-failure-'+route)==failed
        assert _stop(daemon,project,sid,key='close-route-confirmed-exit-'+route)==stopped
        for operation_id,original in unknown_receipts.items():
            assert daemon.store.get_operation(operation_id)==original
            old_job=daemon.store.operation_job(operation_id)
            assert old_job['status']=='UNKNOWN' and old_job['result']==original['result']
        assert daemon.session_lifecycle.get(project,sid)==terminal
        assert sdk.calls==before_reconcile and events==child_events and not registry.unresolved()
        assert entry.error is first_cause and sdk.calls.count(('close',300))==1
    finally:daemon.close()


@pytest.mark.parametrize('route',['close_job','retry_owned','entry_release','registry_release','drain','captured_closer'])
@pytest.mark.parametrize('gate',['membership','ancillary','child_unknown','malformed_membership','wrong_handle'])
def test_captured_original_closer_required_gate_controls_for_every_registry_route(route,gate):
    value=controls.api();p=controls.profile();registry=owned.registry_for(value);scope=registry.scope()
    value.create_job(profile=p);entry,=registry.unresolved()
    ancillary=None
    if gate=='membership':value.k.ids=[456]
    if gate=='ancillary':
        ancillary=registry.acquire(value,700,lambda handle:None,mode='VOID',label='synthetic_original_ancillary')
        if route=='drain':
            # Keep UNKNOWN ancillary: drain cannot report it released before the Job gate.
            def fail(handle):raise ExecutionContractError('ORIGINAL_ANCILLARY_UNKNOWN','SDK close unknown')
            ancillary.closer=fail
    if gate=='child_unknown':value._retained_children={1:SimpleNamespace(poll=lambda:None)}
    if gate=='malformed_membership':value._job_process_ids=lambda job:None
    if gate=='wrong_handle':entry.handle=301
    before=list(value.k.calls)
    with pytest.raises(ExecutionContractError):release_route(route,value,entry,scope)
    assert ('close',300) not in value.k.calls and ('close',301) not in value.k.calls
    assert entry.state!='RELEASED' and value._job_limit_bindings[300]['entry'] is entry
    # Clear real synthetic state; cleanup remains explicit and uses original closure.
    value.k.ids=[];value._job_process_ids=lambda job:[];value._retained_children={};entry.handle=300
    if ancillary:
        ancillary.closer=lambda handle:None;registry.retry_owned(ancillary,value)
    registry.retry_owned(entry,value)
    assert value.k.calls.count(('close',300))==1 and entry.state=='RELEASED'
    value.close_job(300)
    assert not registry.unresolved() and not value._job_limit_bindings


def test_original_job_sdk_callback_cannot_be_redirected_and_close_failure_keeps_first_cause():
    value=controls.api();p=controls.profile();job=value.create_job(profile=p)
    entry,=owned.registry_for(value).unresolved();value.k.close_result=0
    with pytest.raises(ExecutionContractError) as initial:owned.registry_for(value).retry_owned(entry,value)
    first=initial.value;assert entry.state=='UNKNOWN' and entry.error is first
    value.k.CloseHandle=lambda *a:pytest.fail('replacement SDK callback was used')
    value.k.ids=[456]
    with pytest.raises(ExecutionContractError) as denied:owned.registry_for(value).retry_owned(entry,value)
    assert denied.value is first and entry.error is first
    assert value.k.calls.count(('close',300))==1
    value.k.ids=[];value.k.close_result=1;before=list(value.k.calls)
    with pytest.raises(ExecutionContractError) as terminal:owned.registry_for(value).retry_owned(entry,value)
    assert terminal.value is first and entry.error is first
    with pytest.raises(ExecutionContractError):value.close_job(job)
    assert value.k.calls==before and value.k.calls.count(('close',300))==1 and entry.state=='UNKNOWN'


def test_config_failed_empty_original_job_can_be_explicitly_released_without_config_retry():
    value=controls.api();p=controls.profile();value.k.set_result=0
    with pytest.raises(ExecutionContractError):value.create_job(profile=p)
    entry,=owned.registry_for(value).unresolved();binding=value._job_limit_bindings[300]
    original_config_error=binding['error'];before=list(value.k.calls)
    owned.registry_for(value).retry_owned(entry,value)
    assert binding['error'] is original_config_error and entry.state=='RELEASED'
    assert value.k.calls==before+[('close',300)]
    value.close_job(300)
    assert not value._job_limit_bindings and not owned.registry_for(value).unresolved()


def test_original_registry_serializes_safe_terminal_close_and_repeated_release():
    import threading
    value=controls.api();p=controls.profile();job=value.create_job(profile=p)
    registry=owned.registry_for(value);entry,=registry.unresolved()
    entered=threading.Event();proceed=threading.Event();results=[]
    original=value._job_process_ids
    def membership(handle):
        assert registry._lock._is_owned()
        entered.set();assert proceed.wait(2);return original(handle)
    value._job_process_ids=membership
    def release():
        try:registry.retry_owned(entry,value);results.append('RELEASED')
        except BaseException as error:results.append(error)
    first=threading.Thread(target=release);second=threading.Thread(target=release)
    first.start();assert entered.wait(2);second.start();proceed.set();first.join(2);second.join(2)
    assert not first.is_alive() and not second.is_alive() and results==['RELEASED','RELEASED']
    assert value.k.calls.count(('close',300))==1 and entry.state=='RELEASED'
    value.close_job(job)
