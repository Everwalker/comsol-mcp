"""Gate reservation and original finite-A lifetime; software SDK seams only."""
import threading

import pytest

from comsol_mcp import _windows_appcontainer_gate as gate
from comsol_mcp import _windows_t037 as win, _windows_t037_targets as targets
from comsol_mcp import _windows_owned_resources as owned
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_scm_prebirth import daemon_factory, running_factory_chain, no_native_or_external_effect
from tests.test_windows_scm_birth_concurrency import _JobBoundary, _async_call, _join, _SoftwareBirthEnd
from tests.test_windows_t037_targets import full_chain, software_only
from tests.test_windows_job_limits import api as job_api


def test_runtime_lock_available_while_original_activity_blocks_second_birth_close_and_pause(monkeypatch, tmp_path):
    daemon, factory = daemon_factory(tmp_path, monkeypatch)
    sdk = _JobBoundary(factory, monkeypatch)
    calls = []
    try:
        with running_factory_chain(daemon, factory, tmp_path / 'scm', monkeypatch) as (runtime, *_):
            first = _async_call(sdk.launch, name='held-reservation-birth'); calls.append(first)
            try:
                assert sdk.entered.wait(1)
                acquired = runtime._lock.acquire(timeout=.25)
                if acquired: runtime._lock.release()
                assert acquired, 'runtime lock must be available while the SDK observation is blocked'
                participant = factory._profile_participant
                assert runtime._profile_gate.activity == (participant, first[0].ident)
                assert daemon._worker_admission_inflight == 1
                for operation in (sdk.launch, factory.close, lambda: gate._pause_terminal_profile(runtime)):
                    blocked = _async_call(operation, name='held-reservation-contender'); calls.append(blocked)
                    assert blocked[1].wait(.5), 'reservation contenders must refuse without waiting for SDK work'
                    assert len(blocked[3]) == 1 and isinstance(blocked[3][0], ExecutionContractError)
                    assert not factory._closed and sdk.calls == ['create_job_boundary']
            finally:
                sdk.release.set(); _join(*calls)
            assert isinstance(first[3][0], _SoftwareBirthEnd)
            assert daemon._worker_admission_inflight == 0 and runtime._profile_gate.activity is None
            factory.close()
            assert gate._pause_terminal_profile(runtime).check()
    finally:
        daemon.close()


def test_normal_factory_keeps_original_finite_reader_through_child_boundary(monkeypatch, tmp_path):
    with full_chain(monkeypatch, tmp_path) as (daemon, factory, sdk, snapshot, events):
        api = job_api(); factory._api = api
        api.hash_image = lambda path: factory.profile.server_image_sha256
        observed = []
        def child_boundary(command, options, *, profile, job, role, session_factory):
            readers = [r for r in targets._READS.values() if r.owner is factory]
            assert len(readers) == 1, 'normal factory must retain the original finite-A reader past preflight'
            reader, = readers
            assert not reader.retired and reader.pins and owned.registry_for(factory).unresolved()
            assert session_factory is factory and role == 'server' and job == 300
            observed.append(reader)
            raise _SoftwareBirthEnd('software child SDK boundary, no native child')
        api.create_suspended_appcontainer_child = child_boundary
        with pytest.raises(_SoftwareBirthEnd):
            factory([factory.profile._launch_image('server').path])
        assert len(observed) == 1 and observed[0].retired
        assert not owned.registry_for(factory).unresolved()
        factory.close()


@pytest.mark.parametrize('fault', [None, 'source_before', 'source_after', 'image_after', 'project_after', 'stop_after', 'closing_after', 'cleanup_unknown'])
def test_actual_factory_create_child_observe_refuse_and_cleanup_keep_original_A(monkeypatch, tmp_path, fault):
    from tests.test_windows_suspended_ownership import _launch_api
    from comsol_mcp import _windows_t037_broker as broker, _windows_t037_resume as resume
    from tests.test_windows_t037_targets import sd
    with full_chain(monkeypatch, tmp_path, child_acl=True) as (daemon, factory, sdk, snapshot, static_events):
        profile, api, events, state = _launch_api(monkeypatch, profile_override=factory.profile,
                                                cleanup_fail=fault == 'cleanup_unknown', acl_descriptor=sd())
        factory._api = api
        api.hash_image = lambda path: profile.server_image_sha256
        create = api.a.CreateProcessAsUserW
        captured = []
        closing_calls=[];closing_wait=threading.Event()
        original_wait=daemon._dispatch_condition.wait
        def wait(*args,**kwargs):
            if threading.current_thread().name=='held-A-original-daemon-close':closing_wait.set()
            return original_wait(*args,**kwargs)
        monkeypatch.setattr(daemon._dispatch_condition,'wait',wait)
        def drift(path):
            original = sdk.contents[path]
            assert original
            sdk.contents[path] = bytes((original[0] ^ 1,)) + original[1:]  # Same identity/size/timestamps.
        if fault == 'source_before':
            attributes = api._attribute_list
            def before(*args):
                result = attributes(*args); drift(r'c:\comsol\config\profile.json')
                return result
            api._attribute_list = before
        def native_create(*args):
            reader, = [r for r in targets._READS.values() if r.owner is factory]
            held = targets._HELD[id(factory)]
            assert held.reader is reader and not reader.retired and reader.scope.thread_id == threading.get_ident()
            assert factory._profile_participant.gate.depth == 1 and daemon._worker_admission_inflight == 1
            acquired = []
            def lock_probe():
                ok = factory._profile_participant.gate.runtime._lock.acquire(timeout=.25)
                acquired.append(ok)
                if ok: factory._profile_participant.gate.runtime._lock.release()
            thread=threading.Thread(target=lock_probe);thread.start();thread.join(1)
            assert not thread.is_alive() and acquired == [True]
            captured.append((held, reader))
            result = create(*args)
            if fault == 'source_after': drift(r'c:\comsol\config\profile.json')
            elif fault == 'image_after':
                target=next(t for t in profile.finite_targets.files if 'server_image' in t.roles)
                import ntpath
                drift(ntpath.normcase(target.path))
            elif fault == 'project_after':
                authority=daemon.project_authority
                import json
                record=authority.get_project(profile.host_resource_profile.project_id)
                record['revision'] += 1
                with authority.store.lock:
                    authority.store.db.execute('UPDATE projects SET revision=?,record_json=? WHERE project_id=?',
                        (record['revision'], json.dumps(record), record['project_id']))
            elif fault == 'stop_after':
                runtime=factory._profile_participant.gate.runtime
                assert runtime._broker._api.handler(broker.SERVICE_CONTROL_STOP,0,None,None) == broker.ERROR_SUCCESS
            elif fault == 'closing_after':
                closing_calls.append(_async_call(daemon.close,name='held-A-original-daemon-close'))
                assert closing_wait.wait(1) and daemon._closing and daemon._worker_admission_inflight==1
                assert not closing_calls[0][1].is_set() and not factory._closed
            return result
        api.a.CreateProcessAsUserW = native_create
        terminate=api.terminate_process
        def cleanup(handle, code):
            held, reader = captured[0]
            assert targets._HELD[id(factory)] is held and not reader.retired and held.state == 'ACQUIRED'
            return terminate(handle, code)
        api.terminate_process=cleanup
        with pytest.raises(ExecutionContractError) as error:
            factory([profile._launch_image('server').path])
        assert 'RESUME' not in events and not resume._pending
        if fault == 'source_before':
            assert not captured and 'CREATE_SUSPENDED' not in events
            assert error.value.details['reasons'] == ['FINITE_CONTENT_CHANGED']
        else:
            assert len(captured) == 1 and events.count('CREATE_SUSPENDED') == 1
            held, reader = captured[0]
            assert held.reader is reader
            if fault == 'cleanup_unknown':
                assert type(error.value) is win.NativeSuspendedChildLaunchError
                assert held.state == 'UNKNOWN' and not reader.retired
                assert targets._HELD[id(factory)] is held and targets._READS[id(reader)] is reader
                assert held.primary_error is error.value and owned.registry_for(factory).unresolved()
                before_events=list(events); before_reads=list(sdk.calls)
                for operation in (lambda: factory([profile._launch_image('server').path]), factory.close):
                    with pytest.raises(ExecutionContractError): operation()
                assert events == before_events and sdk.calls == before_reads  # No reacquisition/cleanup replay.
            else:
                assert reader.retired and held.state == 'RELEASED'
                assert not state['alive'] and ('terminate', 200) in events
                assert events.count(('close', 200)) == events.count(('close', 201)) == 1
                assert not owned.registry_for(factory).unresolved()
                if fault is None: assert error.value.code == 'NATIVE_CHILD_PROOF_UNAVAILABLE'
                elif fault in ('source_after','image_after'):
                    assert error.value.details['reasons'] == ['FINITE_CONTENT_CHANGED']
                elif fault == 'project_after':
                    assert error.value.details['reasons'] == ['FINITE_A_LIFETIME_CHANGED']
                elif fault == 'stop_after':
                    assert error.value.code == 'SCM_RUNTIME_MISMATCH'
                elif fault == 'closing_after':
                    assert error.value.details['reasons'] == ['SCM_DAEMON_BINDING_MISMATCH']
                    _join(*closing_calls)
                    assert not closing_calls[0][3] and daemon._close_complete
                    # This fixture has no registered Server/Worker lifecycle:
                    # daemon.close drains it but cannot invent a Server stop.
                    # Retire the now-known original factory explicitly below.
                    assert not factory._closed and reader.retired
                    assert daemon._worker_admission_inflight==0
        if fault != 'cleanup_unknown': factory.close()


def test_blocked_original_A_sdk_keeps_runtime_available_and_stop_prevents_create(monkeypatch,tmp_path):
    from tests.test_windows_t037_mic import Function
    from comsol_mcp import _windows_t037_broker as broker
    with full_chain(monkeypatch,tmp_path) as (daemon,factory,sdk,snapshot,events):
        entered=threading.Event();release=threading.Event();calls=[]
        native_security=sdk.security
        def security(*args):
            if threading.current_thread().name=='blocked-original-finite-A' and not entered.is_set():
                entered.set();assert release.wait(3)
            return native_security(*args)
        sdk.a.GetSecurityInfo=Function(security)
        def unexpected_api(self):pytest.fail('SCM stop must precede original launch API/Job/child acquisition')
        monkeypatch.setattr(win._Win32LaunchApi,'__init__',unexpected_api)
        launch=lambda:factory([factory.profile._launch_image('server').path])
        runtime=factory._profile_participant.gate.runtime
        work=_async_call(launch,name='blocked-original-finite-A');calls.append(work)
        try:
            assert entered.wait(1)
            assert runtime._lock.acquire(timeout=.25);runtime._lock.release()
            held=targets._HELD[id(factory)];reader=held.reader
            assert not reader.retired and daemon._worker_admission_inflight==1
            for operation in (launch,factory.close,lambda:gate._pause_terminal_profile(runtime)):
                contender=_async_call(operation,name='blocked-A-contender');calls.append(contender)
                assert contender[1].wait(.5) and len(contender[3])==1
                assert isinstance(contender[3][0],ExecutionContractError) and not factory._closed
                assert targets._HELD[id(factory)] is held
            assert daemon.dispatch({'operation':'registry_list','arguments':{},'execution':{}})['success'] is True
            assert runtime._broker._api.handler(broker.SERVICE_CONTROL_STOP,0,None,None)==broker.ERROR_SUCCESS
        finally:release.set();_join(*calls)
        assert len(work[3])==1 and work[3][0].code=='SCM_RUNTIME_MISMATCH'
        assert reader.retired and held.state=='RELEASED' and factory._job is None
        assert factory._api is None and daemon._worker_admission_inflight==0
        factory.close()


def test_held_A_wrong_thread_and_duplicate_scope_refuse_without_reopening(monkeypatch,tmp_path):
    with full_chain(monkeypatch,tmp_path) as (daemon,factory,sdk,snapshot,events):
        api=job_api();factory._api=api;api.hash_image=lambda path:factory.profile.server_image_sha256
        observed=[]
        def child(*a,**k):
            held=targets._HELD[id(factory)];before=list(sdk.calls)
            foreign=_async_call(held.check_current,name='foreign-A-thread');_join(foreign)
            assert len(foreign[3])==1 and foreign[3][0].details['reasons']==['FINITE_A_LIFETIME_CHANGED']
            with pytest.raises(ExecutionContractError) as error:
                with targets._held_finite_target_scope(factory.profile,factory,daemon):pytest.fail('duplicate entered')
            assert error.value.details['reasons']==['STATIC_NATIVE_OWNER_UNRESOLVED']
            assert list(sdk.calls)==before and targets._HELD[id(factory)] is held
            observed.append(held);raise _SoftwareBirthEnd('known no-child fixture boundary')
        api.create_suspended_appcontainer_child=child
        with pytest.raises(_SoftwareBirthEnd):factory([factory.profile._launch_image('server').path])
        assert observed[0].state=='RELEASED' and observed[0].reader.retired
        factory.close()


def test_original_A_primary_and_cleanup_errors_stay_retained_without_second_close(monkeypatch,tmp_path):
    with full_chain(monkeypatch,tmp_path) as (daemon,factory,sdk,snapshot,events):
        api=job_api();factory._api=api;api.hash_image=lambda path:factory.profile.server_image_sha256
        primary=_SoftwareBirthEnd('known original child-boundary refusal');observed=[]
        def child(*a,**k):
            held=targets._HELD[id(factory)];observed.append(held)
            sdk.fault='close_written_raise'
            raise primary
        api.create_suspended_appcontainer_child=child
        with pytest.raises(OSError) as error:factory([factory.profile._launch_image('server').path])
        held,=observed
        assert held.primary_error is primary and isinstance(held.cleanup_error,OSError)
        assert error.value is held.cleanup_error and error.value.__cause__ is primary
        assert held.state=='UNKNOWN' and not held.reader.retired and targets._HELD[id(factory)] is held
        before=list(sdk.calls)
        for operation in (held.reader.finish,factory.preflight,factory.close):
            with pytest.raises(Exception):operation()
        assert sdk.calls==before and owned.registry_for(factory).unresolved()
