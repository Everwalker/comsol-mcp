"""Original Job close effect/receipt controls. SOFTWARE SDK boundary only.

No native isolation, SCM, CreateProcess, Resume, COMSOL or host acceptance.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import ctypes as C
import gc
import hashlib
import json
import weakref

import pytest

from comsol_mcp import _windows_job_limits as limits, _windows_t037 as win
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._session_server import OwnedServerLauncher
from tests import test_windows_job_limits as controls
from tests.test_windows_job_release_routes import release_route
from tests.test_windows_scm_prebirth import no_native_or_external_effect, running_factory_chain
from tests.test_session_server_control import _make_daemon, _start, _stop

ROUTES=['close_job','retry_owned','entry_release','registry_release','drain','captured_closer']


def record(tmp_path, **facts):
    sources={str(Path(m.__file__).relative_to(Path(win.__file__).parents[1])):
        hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (limits,win,owned)}
    (tmp_path/'job-close-case.json').write_text(json.dumps(dict(
        scope='SOFTWARE_ONLY_NOT_NATIVE_OR_ISSUER',source_hashes=sources,**facts),indent=2)+'\n')


def prepared(*, fault=None):
    api=controls.api();profile=controls.profile();attempts=[]
    class UnreadableReturn:
        @property
        def value(self):raise ValueError('SOFTWARE return normalization unavailable')
    def original_close(handle):
        attempts.append(handle);api.k.calls.append(('close',handle))
        if fault=='write_then_raise':raise OSError('SOFTWARE close may already have taken effect')
        if fault=='normalize_raise':return UnreadableReturn()
        return C.c_int32(1)
    api.k.CloseHandle=original_close
    api._software_initial_scope=owned.registry_for(api).scope()
    job=api.create_job(profile=profile)
    factory=win.T037SessionProcessFactory(profile,'original-job-close',api=api);factory._job=job
    return api,factory,owned.registry_for(api),attempts


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('fault',['write_then_raise','normalize_raise'])
def test_possibly_effective_close_all_release_routes_stay_unknown_without_requery(monkeypatch,tmp_path,route,fault):
    api,factory,registry,attempts=prepared(fault=fault);entry,=registry.unresolved();scope=api._software_initial_scope
    with pytest.raises((OSError,ValueError)) as first:factory.close()
    assert attempts==[300] and entry.state=='UNKNOWN' and entry.error is first.value
    assert api._job_close_call.phase=='NATIVE_UNKNOWN' and not factory._closed
    before=list(api.k.calls)
    def denied(*a,**k):pytest.fail('possibly reused Job HANDLE queried or closed again')
    api._job_process_ids=denied;api.k.QueryInformationJobObject=denied;api.k.CloseHandle=denied
    with pytest.raises((OSError,ValueError)) as retry:release_route(route,api,entry,scope)
    assert retry.value is first.value
    with pytest.raises((OSError,ValueError)):factory.close()
    with pytest.raises(ExecutionContractError):api.create_job(profile=factory.profile)
    with pytest.raises(ExecutionContractError):limits.require_configured_job(api,300,factory.profile)
    ref=weakref.ref(factory);roots={'original_factory':factory};del factory;gc.collect()
    assert ref() is roots['original_factory'] and roots['original_factory']._job==300
    assert registry.unresolved()==(entry,) and api._job_limit_bindings[300]['entry'] is entry
    assert entry.error is first.value and api.k.calls==before and attempts==[300]
    record(tmp_path,case='ambiguous_no_reentry',route=route,fault=fault,attempts=attempts,
           phase=api._job_close_call.phase,registry_state=entry.state,first_cause=type(first.value).__name__)


@pytest.mark.parametrize('gate',['child_wait','child_poll_error','ancillary','unclassified','parent','launch_cleanup','membership'])
def test_pre_sdk_deferral_multiple_children_requires_all_terminal_and_same_empty_job(tmp_path,gate):
    api,factory,registry,attempts=prepared();entry,=registry.unresolved();state=[1,None];polls=[]
    def poll(index):
        polls.append(index)
        if gate=='child_poll_error' and state[index] is None:raise OSError('SOFTWARE wait unavailable')
        return state[index]
    api._retained_children={1:NS(poll=lambda:poll(0)),2:NS(poll=lambda:poll(1))}
    ancillary=None
    if gate not in {'child_wait','child_poll_error'}:state[1]=2
    if gate=='ancillary':ancillary=registry.acquire(api,700,lambda h:None,mode='VOID',label='SOFTWARE_ancillary')
    if gate=='unclassified':api._unclassified_create_handles=[object()]
    if gate=='parent':api._retained_parent_bindings={1:NS(closed=False)}
    if gate=='launch_cleanup':api._retained_launch_cleanup=[('SOFTWARE_pending',lambda:None)]
    if gate=='membership':api.k.ids=[456]
    with pytest.raises((ExecutionContractError,OSError)) as first:api.close_job(300)
    assert not attempts and api._job_close_call.phase=='PRE_SDK' and entry.state=='UNKNOWN'
    with pytest.raises((ExecutionContractError,OSError)):factory.close()
    assert not attempts and entry.error is first.value and factory._job==300 and not factory._closed
    state[1]=2;api.k.ids=[];api._unclassified_create_handles=[];api._retained_launch_cleanup=[]
    if gate=='parent':api._retained_parent_bindings[1].closed=True
    if ancillary is not None:registry.retry_owned(ancillary,api)
    before_polls=len(polls)
    factory.close()
    assert polls[before_polls:]==[0,1,0,1]  # Factory plus original captured-closer terminal checks.
    assert attempts==[300] and entry.state=='RELEASED' and entry.error is first.value
    assert not registry.unresolved() and not api._job_limit_bindings and factory._closed
    factory.close();assert attempts==[300]
    record(tmp_path,case='original_pre_sdk_deferral',gate=gate,attempts=attempts,phase=api._job_close_call.phase)


class _PopFailure(dict):
    def __init__(self, original, *, after):
        super().__init__(original);self.after=after;self.pending=True
    def pop(self, key, *args):
        if self.pending:
            self.pending=False
            if self.after:super().pop(key,*args)
            raise OSError('SOFTWARE original bookkeeping pop failed')
        return super().pop(key,*args)


@pytest.mark.parametrize('fault',['entry_state','entry_keepalive','registry_pop_before','registry_pop_after',
    'after_registry','binding_pop_before','binding_pop_after','factory_job_clear','factory_closed_flag'])
def test_confirmed_native_result_survives_registry_binding_and_factory_bookkeeping_failure(monkeypatch,tmp_path,fault):
    api,factory,registry,attempts=prepared();entry,=registry.unresolved();fired=[]
    api._retained_children={1:NS(poll=lambda:1)}
    if fault in {'registry_pop_before','registry_pop_after'}:
        registry._entries=_PopFailure(registry._entries,after=fault.endswith('after'))
    if fault in {'binding_pop_before','binding_pop_after'}:
        api._job_limit_bindings=_PopFailure(api._job_limit_bindings,after=fault.endswith('after'))
    if fault in {'entry_state','entry_keepalive'}:
        original=owned._OwnedResource.__setattr__
        def entry_set(self,name,value):
            if self is entry and not fired and ((fault=='entry_state' and name=='state' and value=='RELEASED')
                    or (fault=='entry_keepalive' and name=='keepalive' and value==())):
                fired.append(name);raise OSError('SOFTWARE entry completion failed')
            return original(self,name,value)
        monkeypatch.setattr(owned._OwnedResource,'__setattr__',entry_set)
    if fault=='after_registry':
        original=limits._complete_confirmed_job_release
        def complete(call):
            original(call)
            if not fired:fired.append('completion');raise OSError('SOFTWARE completion acknowledgement failed')
        monkeypatch.setattr(limits,'_complete_confirmed_job_release',complete)
    if fault in {'factory_job_clear','factory_closed_flag'}:
        original=win.T037SessionProcessFactory.__setattr__
        def factory_set(self,name,value):
            if self is factory and not fired and ((fault=='factory_job_clear' and name=='_job' and value is None)
                    or (fault=='factory_closed_flag' and name=='_closed' and value is True)):
                fired.append(name);raise OSError('SOFTWARE factory terminal acknowledgement failed')
            return original(self,name,value)
        monkeypatch.setattr(win.T037SessionProcessFactory,'__setattr__',factory_set)
    with pytest.raises(OSError):factory.close()
    call=api._job_close_call
    assert attempts==[300] and call.phase=='NATIVE_CONFIRMED' and call.entry is entry
    assert not factory._closed
    before=list(api.k.calls);weak=weakref.ref(call);gc.collect();assert weak() is call
    def denied(*a,**k):pytest.fail('known closed Job/child was queried or native closer called again')
    api._job_process_ids=denied;api.k.QueryInformationJobObject=denied;api.k.CloseHandle=denied
    api._retained_children[1].poll=denied
    factory.close();factory.close()
    assert factory._closed and factory._job is None and attempts==[300] and api.k.calls==before
    assert entry.state=='RELEASED' and entry.keepalive==() and not registry.unresolved()
    assert not api._job_limit_bindings
    record(tmp_path,case='known_success_bookkeeping_only',fault=fault,attempts=attempts,
           phase=call.phase,registry_state=entry.state,factory_closed=factory._closed)


def test_captured_success_before_registry_ack_cannot_requery_recycled_job_or_authorize_birth(tmp_path):
    api,factory,registry,attempts=prepared();entry,=registry.unresolved()
    entry.closer(300)
    assert attempts==[300] and entry.state=='ACQUIRED' and api._job_close_call.phase=='NATIVE_CONFIRMED'
    def denied(*a,**k):pytest.fail('known closed Job queried or SDK close called again')
    api._job_process_ids=denied;api.k.QueryInformationJobObject=denied;api.k.CloseHandle=denied
    with pytest.raises(ExecutionContractError):limits.require_configured_job(api,300,factory.profile)
    registry.retry_owned(entry,api);factory.close()
    assert attempts==[300] and entry.state=='RELEASED' and not registry.unresolved()
    record(tmp_path,case='captured_success_before_ack',attempts=attempts,phase=api._job_close_call.phase)


def test_unclassified_private_phase_is_not_treated_as_no_sdk_entry(tmp_path):
    api,factory,registry,attempts=prepared();entry,=registry.unresolved();call=api._job_close_call
    call.phase='UNCLASSIFIED'
    before=list(api.k.calls)
    with pytest.raises(ExecutionContractError):factory.close()
    with pytest.raises(ExecutionContractError):entry.closer(300)
    assert attempts==[] and api.k.calls==before and registry.unresolved()==(entry,)
    assert factory._job==300 and not factory._closed
    record(tmp_path,case='unclassified_phase_refuses',attempts=attempts,phase=call.phase)


@pytest.mark.parametrize('fault',['dict_phase','foreign_call','replaced_closer'])
def test_scalar_lookalike_or_foreign_release_state_never_substitutes_original_call(tmp_path,fault):
    api,factory,registry,attempts=prepared();entry,=registry.unresolved();original=api._job_close_call
    if fault=='dict_phase':api._job_close_call={'phase':'NATIVE_CONFIRMED','entry':entry,'value':300}
    if fault=='foreign_call':api._job_close_call=prepared()[0]._job_close_call
    if fault=='replaced_closer':entry.closer=lambda h:None
    before=list(api.k.calls)
    with pytest.raises(ExecutionContractError):factory.close()
    assert attempts==[] and api.k.calls==before and registry.unresolved()==(entry,)
    assert factory._job==300 and not factory._closed
    api._job_close_call=original;entry.closer=original;factory.close()
    assert attempts==[300] and not registry.unresolved()
    record(tmp_path,case='no_release_state_substitution',fault=fault,attempts=attempts)


@pytest.mark.parametrize('fault',['write_then_raise','normalize_raise'])
def test_full_public_cleanup_cannot_retry_ambiguous_job_close_or_rewrite_unknown_receipts(monkeypatch,tmp_path,fault):
    from tests.test_windows_network_intent import network_profile_and_plan
    import os
    profile,api,events,state,sdk=controls.prepared_child_api(monkeypatch,cleanup_fail=True)
    host,_=network_profile_and_plan();profile=replace(profile,host_resource_profile=host)
    attempts=[];factories=[];original=sdk.CloseHandle
    class Unreadable:
        @property
        def value(self):raise ValueError('SOFTWARE Job BOOL result unavailable')
    def close(handle):
        if handle!=300:return original(handle)
        attempts.append(handle);sdk.calls.append(('close',handle))
        if fault=='write_then_raise':raise OSError('SOFTWARE original Job close may have completed')
        return Unreadable()
    api.k.CloseHandle=close
    class Launcher(OwnedServerLauncher):
        def start(self,runtime,project_id,session_id,**kwargs):
            factory=win.T037SessionProcessFactory(profile,session_id,api=api)
            monkeypatch.setattr(factory,'preflight',lambda:None)  # SOFTWARE topology only; no A/issuer authority.
            factories.append(factory);prior=daemon._windows_t037_profile;daemon._windows_t037_profile=profile
            daemon._session_t037_process_factories[(project_id,session_id)]=factory
            with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch):
                try:return super().start(runtime,project_id,session_id,process_factory=factory,**kwargs)
                finally:daemon._windows_t037_profile=prior
    monkeypatch.setattr(win,'os',NS(**dict(vars(os),name='nt')))
    daemon,project,*_=_make_daemon(tmp_path,monkeypatch,server_launcher=Launcher(platform_name='darwin'))
    try:
        started=_start(daemon,project);sid=started['data']['session_id'];key=(project,sid)
        factory,=factories;handle=daemon._session_server_handles[key];child=handle.process
        state['cleanup_fail']=False;state['alive']=False;sdk.ids=[]
        failed=_stop(daemon,project,sid,key='ambiguous-Job-first')
        assert not failed['success'] and failed['data']['native_cleanup_state']=='EXIT_CONFIRMED'
        assert failed['data']['server_stopped'] and failed['data']['job_retained'] and child._closed
        entry,=owned.registry_for(api).unresolved();first=entry.error
        assert attempts==[300] and entry.state=='UNKNOWN' and api._job_close_call.phase=='NATIVE_UNKNOWN'
        prior=daemon.store.get_operation(failed['execution']['operation_id']);before=list(sdk.calls);prior_events=list(events)
        api._job_process_ids=lambda *a:pytest.fail('ambiguous original Job re-queried from public cleanup')
        second=_stop(daemon,project,sid,key='ambiguous-Job-new-cleanup')
        assert not second['success'] and second['data']['job_retained'] and second['data']['server_stopped']
        assert _stop(daemon,project,sid,key='ambiguous-Job-first')==failed
        assert daemon.store.get_operation(failed['execution']['operation_id'])==prior
        assert daemon._session_server_handles[key] is handle and daemon._session_t037_process_factories[key] is factory
        assert entry.error is first and attempts==[300] and sdk.calls==before and events==prior_events
        assert factory._job==300 and not factory._closed and not child._resume_attempted
        record(tmp_path,case='public_ambiguous_cleanup_retains_original',fault=fault,attempts=attempts,
               first=failed,second=second,registry_state=entry.state)
    finally:daemon.close()
