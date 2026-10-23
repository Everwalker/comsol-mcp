"""Original profile lifetime software controls, NO native/Delete/Resume power."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
import gc
import threading
import weakref

import pytest

from comsol_mcp import _windows_appcontainer_gate as gate
from comsol_mcp import _windows_t037 as win, _windows_t037_admission as admission
from comsol_mcp import _windows_t037_resume as resume, _windows_t037_broker as broker
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_scm_prebirth import daemon_factory,running_factory_chain,no_native_or_external_effect
from tests.test_windows_scm_birth_concurrency import _JobBoundary,_async_call,_join,_SoftwareBirthEnd
from tests.test_windows_job_limits import api as job_api,profile as job_profile


@contextmanager
def job_chain(monkeypatch,tmp_path,*,close_fault=None):
    """Actual factory/Job/closer; stop at explicit software child SDK seam."""
    daemon,factory=daemon_factory(tmp_path,monkeypatch,job_profile());api=job_api();factory._api=api
    api.hash_image=lambda path:factory.profile.server_image_sha256
    if close_fault=='native_raise':
        def close(handle):api.k.calls.append(('close',handle));raise OSError('SOFTWARE CloseHandle may have taken effect')
        api.k.CloseHandle=close  # The original producer captures this export before acquiring its Job.
    def child(*args,**kwargs):raise _SoftwareBirthEnd('SOFTWARE: child SDK NOT_RUN')
    api.create_suspended_appcontainer_child=child
    monkeypatch.setattr(resume,'require_static_prebirth',lambda *a,**k:None)  # Test-only A continuation, never issuer authority.
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,scm,provider):
            with pytest.raises(_SoftwareBirthEnd):factory([factory.profile._launch_image('server').path])
            assert factory._job==300 and factory._profile_participant.job_call is api._job_close_call
            yield daemon,factory,api,runtime,scm,provider
    finally:daemon.close()


@pytest.mark.parametrize('settlement',['settle','fail'])
def test_original_no_job_close_terminal_pause_survives_runtime_unlock_and_never_reopens(monkeypatch,tmp_path,settlement):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,scm,provider):
            record=factory._profile_participant
            assert gate._GATES[id(runtime)].participants[id(factory)] is record
            assert runtime._profile_gate.namespace==(factory.profile.service_account_sid,factory.profile.windows_policy.appcontainer_profile_name,factory.profile.appcontainer_sid)
            with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)
            factory.close();pause=gate._pause_terminal_profile(runtime)
            assert not runtime._lock._is_owned() and pause.check() is pause
            assert record.close_returned and record.terminal[1:3]==(None,None)
            assert factory._job is None and factory._api is None and not owned.registry_for(factory).unresolved()
            if settlement=='settle':pause.settle();assert runtime._profile_gate.state=='SETTLED'
            else:
                error=OSError('SOFTWARE uncertain storage outcome');pause.fail(error)
                assert runtime._profile_gate.first_error is error
            with pytest.raises(ExecutionContractError):pause.check()
            with pytest.raises(ExecutionContractError):daemon._new_t037_session_factory('project','after-pause')
            assert runtime._profile_gate.state!='ACTIVE' and not resume._pending
    finally:daemon.close()


def test_removed_factory_is_strongly_retained_and_original_cleanup_still_possible(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            record=factory._profile_participant;ref=weakref.ref(factory)
            daemon._session_t037_process_factories.clear();del factory;gc.collect()
            assert ref() is record.factory and runtime._profile_gate.participants[id(ref())] is record
            with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)
            record.factory.close()
            assert gate._pause_terminal_profile(runtime).check().participants==(record,)
    finally:daemon.close()


@pytest.mark.parametrize('fault',['closed_boolean','fake_terminal','observer_dict','observer_list','api','binding','participant','unregistered','foreign_factory'])
def test_no_job_metadata_owner_or_removed_participant_cannot_mint_terminal_pause(monkeypatch,tmp_path,fault):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            if fault=='closed_boolean':factory._closed=True
            elif fault=='fake_terminal':factory._profile_terminal_snapshot=lambda *a:('TERMINAL',True)
            elif fault=='observer_dict':factory._prebirth_observers.append(NS(retired=True,api=NS()))
            elif fault=='observer_list':factory._prebirth_observers=[]
            elif fault=='api':factory._api=NS(closed=True)
            elif fault=='binding':factory._scm_binding=None
            elif fault=='participant':factory._profile_participant=NS(close_returned=True,terminal=True)
            elif fault=='unregistered':daemon._session_t037_process_factories[('project','unknown')]=NS(_closed=True)
            else:
                foreign=win.T037SessionProcessFactory(factory.profile,factory.session_id)
                daemon._session_t037_process_factories[('project',factory.session_id)]=foreign
                with pytest.raises(ExecutionContractError):admission._bind_session_factory(daemon,foreign,('project',factory.session_id))
            with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)
            assert runtime._profile_gate.state=='ACTIVE' and factory._job is None
            if fault in {'binding','participant','observer_list','api'}:
                with pytest.raises(ExecutionContractError):factory.close()  # No ungated fallback after original registration.
    finally:daemon.close()


def test_participant_budget_rejects_before_new_native_work_and_keeps_original_records(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            monkeypatch.setattr(gate,'MAX_PARTICIPANTS',1)
            with pytest.raises(ExecutionContractError) as error:daemon._new_t037_session_factory('project','overflow')
            assert error.value.details['reasons']==['PROFILE_FACTORY_BUDGET_EXHAUSTED']
            assert tuple(runtime._profile_gate.participants.values())==(factory._profile_participant,)
            assert all(f._api is None and f._job is None for f in daemon._session_t037_process_factories.values())
    finally:daemon.close()


@pytest.mark.parametrize('mode',['preflight','birth'])
def test_sdk_blocked_preflight_is_counted_metadata_responsive_and_daemon_close_waits(monkeypatch,tmp_path,mode):
    daemon,factory=daemon_factory(tmp_path,monkeypatch);sdk=_JobBoundary(factory,monkeypatch);sdk.release.set()
    monkeypatch.setattr(factory,'preflight',win.T037SessionProcessFactory.preflight.__get__(factory))
    entered=threading.Event();release=threading.Event();closing=threading.Event();calls=[]
    def static(profile,*,owner):
        assert owner is factory and daemon._worker_admission_inflight==1
        assert factory._profile_participant.gate.depth==(2 if mode=='birth' else 1)
        entered.set();assert release.wait(3)
    monkeypatch.setattr(resume,'require_static_prebirth',static)
    wait=daemon._dispatch_condition.wait
    def waiting(*a,**k):
        if threading.current_thread().name=='profile-daemon-close':closing.set()
        return wait(*a,**k)
    monkeypatch.setattr(daemon._dispatch_condition,'wait',waiting)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            operation=factory.preflight if mode=='preflight' else sdk.launch
            work=_async_call(operation,name='blocked-static');calls.append(work)
            try:
                assert entered.wait(1)
                query=_async_call(lambda:daemon.dispatch({'operation':'registry_list','arguments':{},'execution':{}}),name='profile-metadata');calls.append(query)
                assert query[1].wait(.5) and not query[3] and query[2][0]['success'] is True
                with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)  # Nonblocking, no wait/drain.
                closer=_async_call(daemon.close,name='profile-daemon-close');calls.append(closer)
                assert closing.wait(1) and not closer[1].is_set() and not daemon._close_complete
            finally:release.set();_join(*calls)
            assert not closer[3] and daemon._close_complete and daemon._worker_admission_inflight==0
            assert sdk.calls==[] and factory._job is None
            if mode=='preflight':assert not work[3]
            else:assert len(work[3])==1 and isinstance(work[3][0],ExecutionContractError)
    finally:daemon.close()


def test_factory_close_waits_for_whole_birth_and_never_holds_global_control_condition(monkeypatch,tmp_path):
    # Keep the historical node ID; the reservation contract replaces waiting
    # retirement with bounded BUSY, followed by explicit close after settlement.
    daemon,factory=daemon_factory(tmp_path,monkeypatch);sdk=_JobBoundary(factory,monkeypatch)
    calls=[]
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            first=_async_call(sdk.launch,name='close-race-birth');calls.append(first)
            try:
                assert sdk.entered.wait(1)
                closer=_async_call(factory.close,name='original-factory-close');calls.append(closer)
                assert closer[1].wait(.5) and not factory._closed
                assert len(closer[3])==1 and isinstance(closer[3][0],ExecutionContractError)
                query=_async_call(lambda:daemon.dispatch({'operation':'session_health','arguments':{},'execution':{}}),name='close-race-query');calls.append(query)
                assert query[1].wait(.5) and not query[3] and query[2][0]['success'] is True
            finally:sdk.release.set();_join(*calls)
            assert isinstance(first[3][0],_SoftwareBirthEnd)
            factory.close()
            assert factory._closed and factory._profile_participant.close_returned and daemon._worker_admission_inflight==0
            assert gate._pause_terminal_profile(runtime).check()
    finally:daemon.close()


@pytest.mark.parametrize('mode',['preflight','birth'])
def test_pause_after_original_close_rejects_prevalidated_queued_activity_before_sdk(monkeypatch,tmp_path,mode):
    daemon,factory=daemon_factory(tmp_path,monkeypatch);sdk=_JobBoundary(factory,monkeypatch);sdk.release.set()
    if mode=='preflight':monkeypatch.setattr(factory,'preflight',win.T037SessionProcessFactory.preflight.__get__(factory))
    original=admission._require_factory_runtime;validated=threading.Event();proceed=threading.Event();calls=[]
    def require(value):
        result=original(value)
        if threading.current_thread().name=='queued-profile-activity' and not validated.is_set():
            validated.set();assert proceed.wait(3)
        return result
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            monkeypatch.setattr(admission,'_require_factory_runtime',require)
            work=_async_call(factory.preflight if mode=='preflight' else sdk.launch,name='queued-profile-activity');calls.append(work)
            try:
                assert validated.wait(1) and daemon._worker_admission_inflight==0
                factory.close();pause=gate._pause_terminal_profile(runtime);assert pause.check() is pause
            finally:proceed.set();_join(*calls)
            assert len(work[3])==1 and isinstance(work[3][0],ExecutionContractError)
            assert not sdk.calls and daemon._worker_admission_inflight==0 and pause.check() is pause
    finally:daemon.close()


def test_same_thread_pause_and_close_refuse_during_nested_preflight_and_counter_not_duplicated(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch);sdk=_JobBoundary(factory,monkeypatch);sdk.release.set()
    monkeypatch.setattr(factory,'preflight',win.T037SessionProcessFactory.preflight.__get__(factory));observed=[]
    def static(profile,*,owner):
        assert daemon._worker_admission_inflight==1 and owner._profile_participant.gate.depth==2
        for operation in (lambda:gate._pause_terminal_profile(owner._profile_participant.gate.runtime),owner.close):
            with pytest.raises(ExecutionContractError):operation()
        observed.append(1)
    monkeypatch.setattr(resume,'require_static_prebirth',static)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch):
            with pytest.raises(_SoftwareBirthEnd):sdk.launch()
            assert observed==[1] and daemon._worker_admission_inflight==0
    finally:daemon.close()


def test_genuine_cached_job_close_retains_original_capability_after_handle_removed_without_sdk_requery(monkeypatch,tmp_path):
    with job_chain(monkeypatch,tmp_path) as (daemon,factory,api,runtime,*_):
        factory.close();call=factory._profile_participant.job_call
        assert call is factory._job_close_call is api._job_close_call and call.phase=='NATIVE_CONFIRMED'
        assert call.entry.state=='RELEASED' and not api._job_limit_bindings and factory._job is None
        before=list(api.k.calls)
        pause=gate._pause_terminal_profile(runtime);assert pause.check() is pause
        factory.close();pause.check();gc.collect()
        assert api.k.calls==before and before.count(('close',300))==1


def test_confirmed_native_job_close_then_coordinator_ack_fault_can_acknowledge_without_second_sdk_effect(monkeypatch,tmp_path):
    with job_chain(monkeypatch,tmp_path) as (daemon,factory,api,runtime,*_):
        original=gate._Participant.acknowledge_close;calls=[]
        def ack(participant):
            if participant.factory is factory and not calls:
                calls.append('ack-fault');raise OSError('SOFTWARE coordinator acknowledgement failed')
            return original(participant)
        monkeypatch.setattr(gate._Participant,'acknowledge_close',ack)
        with pytest.raises(OSError):factory.close()
        record=factory._profile_participant
        assert record.close_returned and record.terminal is None and record.job_call.phase=='NATIVE_CONFIRMED'
        before=list(api.k.calls)
        with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)
        factory.close();assert api.k.calls==before and before.count(('close',300))==1
        assert gate._pause_terminal_profile(runtime).check() and record.terminal


@pytest.mark.parametrize('fault',['native_raise','native_opaque','live_job','unassigned_child','launch_cleanup'])
def test_original_unknown_or_live_job_cannot_pause_or_repeat_possibly_effective_native_close(monkeypatch,tmp_path,fault):
    with job_chain(monkeypatch,tmp_path,close_fault=fault) as (daemon,factory,api,runtime,*_):
        if fault=='native_opaque':api.k.close_result=None
        elif fault=='live_job':api.k.ids=[901]
        elif fault=='unassigned_child':api._unclassified_create_handles=[object()]
        elif fault=='launch_cleanup':api._retained_launch_cleanup=[('SOFTWARE unknown original release',)]
        with pytest.raises(Exception):factory.close()
        call=factory._profile_participant.job_call;before=list(api.k.calls)
        with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)
        if fault in {'native_raise','native_opaque'}:
            for _ in range(2):
                with pytest.raises(Exception):factory.close()
            gc.collect();assert api.k.calls==before and call.phase=='NATIVE_UNKNOWN'
            assert owned.registry_for(api).unresolved()[0] is call.entry
        else:
            assert call.phase=='PRE_SDK' and not any(c==('close',300) for c in before)
            api.k.ids=[];api._unclassified_create_handles=[];api._retained_launch_cleanup=[]
            factory.close();assert call.phase=='NATIVE_CONFIRMED' and api.k.calls.count(('close',300))==1
            assert gate._pause_terminal_profile(runtime).check()


@pytest.mark.parametrize('fault',['stop','closing','source','provider_bytes','participant','job_call'])
def test_pause_lease_rechecks_original_runtime_source_provider_and_terminal_participants(monkeypatch,tmp_path,fault):
    with job_chain(monkeypatch,tmp_path) as (daemon,factory,api,runtime,scm,provider):
        factory.close();pause=gate._pause_terminal_profile(runtime);before=list(api.k.calls)
        if fault=='stop':assert scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)==0
        elif fault=='closing':daemon._closing=True
        elif fault=='source':daemon._windows_t037_profile_path='changed-source'
        elif fault=='provider_bytes':provider._path.write_bytes(provider._path.read_bytes()+b' ')
        elif fault=='participant':factory._profile_participant=NS(close_returned=True)
        else:factory._job_close_call=NS(phase='NATIVE_CONFIRMED')
        with pytest.raises(Exception):pause.check()
        assert pause.state=='INVALID' and runtime._profile_gate.state=='UNKNOWN' and api.k.calls==before
        with pytest.raises(Exception):daemon._new_t037_session_factory('project','after-drift')
        assert api.k.calls==before


def test_stopped_runtime_allows_only_original_factory_cleanup_not_birth_or_pause(monkeypatch,tmp_path):
    with job_chain(monkeypatch,tmp_path) as (daemon,factory,api,runtime,scm,provider):
        scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)
        with pytest.raises(ExecutionContractError):factory.preflight()
        factory.close();assert api.k.calls.count(('close',300))==1
        with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)


def test_forged_runtime_profile_only_and_reused_coordinator_never_acquire_pause(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        for value in (None,{},factory.profile,NS(is_active=True,state='RUNNING')):
            with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(value)
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            with pytest.raises(ExecutionContractError):gate._bind_original_profile_gate(runtime._daemon_binding)
            forged=broker.ScmServiceRuntime(runtime._broker,broker._SCM_RUNTIME_SEAL)
            with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(forged)
            assert runtime._profile_gate.state=='ACTIVE' and not resume._pending
    finally:daemon.close()


def test_close_return_not_a_mutable_closed_boolean_ends_original_factory_activity(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            factory.close();factory._closed=False
            with pytest.raises(ExecutionContractError) as error:factory.preflight()
            assert error.value.details['reasons']==['PROFILE_FACTORY_NOT_ACTIVE']
            assert factory._api is None and factory._job is None
    finally:daemon.close()


def test_pending_original_observer_blocks_terminal_no_job_even_after_close_body_return(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            observed=NS(_finite_pending_sd=object());owned.attach_registry(observed,owned.registry_for(factory))
            observer=win.WindowsT037Observer(platform_name='windows',api=observed)
            with admission._factory_birth_scope(factory):factory._prebirth_observers.append(observer)
            with pytest.raises(ExecutionContractError):factory.close()
            record=factory._profile_participant
            assert record.close_returned and record.terminal is None and record.terminal_error is not None
            first=record.terminal_error
            observed._finite_pending_sd=None  # Clearing caller metadata cannot erase the original ambiguity.
            with pytest.raises(ExecutionContractError) as error:factory.close()
            assert error.value is first
            with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)
    finally:daemon.close()


def test_pause_is_local_to_one_original_runtime_and_not_a_process_wide_freeze(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path/'first',monkeypatch)
    other,second=daemon_factory(tmp_path/'second',monkeypatch)
    monkeypatch.setattr(resume,'require_static_prebirth',lambda *a,**k:None)  # SOFTWARE boundary only.
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm-first',monkeypatch) as (runtime,*_):
            factory.close();pause=gate._pause_terminal_profile(runtime)
            with running_factory_chain(other,second,tmp_path/'scm-second',monkeypatch) as (foreign,*_):
                assert foreign._profile_gate is not runtime._profile_gate
                assert second.preflight() is None and other._worker_admission_inflight==0
                assert pause.check() is pause and foreign._profile_gate.state=='ACTIVE'
    finally:daemon.close();other.close()


def test_preexisting_original_owned_job_is_pinned_at_registration_without_new_sdk_or_integer_adoption(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch,job_profile());api=job_api()
    factory._api=api;factory._job=api.create_job(profile=factory.profile);original=api._job_close_call;before=list(api.k.calls)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,*_):
            assert factory._profile_participant.job_call is original and api.k.calls==before
            foreign=win.T037SessionProcessFactory(factory.profile,'bare-number',api=NS())
            foreign._job=300;key=('project','bare-number');daemon._session_t037_process_factories[key]=foreign
            with pytest.raises(ExecutionContractError):admission._bind_session_factory(daemon,foreign,key)
            daemon._session_t037_process_factories.pop(key)
            assert api.k.calls==before
            factory.close();assert gate._pause_terminal_profile(runtime).check()
            assert api.k.calls.count(('close',300))==1 and original.phase=='NATIVE_CONFIRMED'
    finally:daemon.close()


def test_stopped_cleanup_keeps_original_owner_while_live_daemon_configuration_is_no_longer_selected(monkeypatch,tmp_path):
    with job_chain(monkeypatch,tmp_path) as (daemon,factory,api,runtime,scm,provider):
        scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)
        daemon._windows_t037_profile=None;daemon._scoped_host_provider=None
        daemon._windows_t037_profile_path=None;daemon.project_authority.authorization_verifier=None
        with pytest.raises(ExecutionContractError):factory.preflight()
        factory.close();assert api.k.calls.count(('close',300))==1
        with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)


def test_instance_close_helper_substitution_cannot_skip_original_job_emptiness_check(monkeypatch,tmp_path):
    with job_chain(monkeypatch,tmp_path) as (daemon,factory,api,runtime,*_):
        api.k.ids=[901];forged=[]
        factory._close_owned_resources=lambda:forged.append('skip-original-close')
        with pytest.raises(ExecutionContractError):factory.close()
        assert not forged and not factory._profile_participant.close_returned
        assert not any(c==('close',300) for c in api.k.calls)
        with pytest.raises(ExecutionContractError):gate._pause_terminal_profile(runtime)
        api.k.ids=[];factory.close();assert gate._pause_terminal_profile(runtime).check()


def test_paused_registration_rejects_before_any_fresh_provider_sdk_read(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,scm,provider):
            factory.close();gate._pause_terminal_profile(runtime)
            monkeypatch.setattr(provider,'_assert_stable',lambda:pytest.fail('a paused registration reached a fresh SDK/file read'))
            with pytest.raises(ExecutionContractError) as error:daemon._new_t037_session_factory('project','paused-registration')
            assert error.value.details['reasons']==['PROFILE_GATE_PAUSED']
            assert factory._api is None and factory._job is None
    finally:daemon.close()
