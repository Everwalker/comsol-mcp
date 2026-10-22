"""Actual factory/dispatch/close concurrency over a no-effect Job boundary.

Only software scheduling is checked. A is bypassed explicitly in test memory;
no Job, child, native SDK, Resume capability or admission authority is created.
"""
import threading

import pytest

from comsol_mcp import _windows_t037_admission as admission
from comsol_mcp import _windows_t037_broker as broker
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_windows_scm_prebirth import (
    daemon_factory, running_factory_chain, no_native_or_external_effect, record,
)


class _SoftwareBirthEnd(RuntimeError):
    pass


class _SoftwareAbort(BaseException):
    pass


class _JobBoundary:
    """Stop at the original factory's SDK call before any native acquisition."""
    def __init__(self, factory, monkeypatch, error_type=_SoftwareBirthEnd):
        self.factory=factory
        self.entered=threading.Event();self.release=threading.Event()
        self.exited=threading.Event();self.calls=[];self.error_type=error_type
        monkeypatch.setattr(factory,'preflight',lambda:None)  # SOFTWARE continuation only.
        monkeypatch.setattr(factory,'_api',self)

    def hash_image(self, path):
        return self.factory.profile.server_image_sha256

    def create_job(self, *, profile):
        assert profile is self.factory.profile
        self.calls.append('create_job_boundary');self.entered.set()
        try:
            assert self.release.wait(3),'software birth boundary not released'
            raise self.error_type('SOFTWARE: no Job or child acquired')
        finally:
            self.exited.set()

    def create_suspended_appcontainer_child(self, *args, **kwargs):
        pytest.fail('software concurrency control must stop before child creation')

    def launch(self):
        return self.factory([self.factory.profile._launch_image('server').path])


def _async_call(function, *, name):
    done=threading.Event();results=[];errors=[]
    def run():
        try:results.append(function())
        except BaseException as error:errors.append(error)
        finally:done.set()
    thread=threading.Thread(target=run,name=name)
    thread.start()
    return thread,done,results,errors


def _join(*calls):
    for thread,*_rest in calls:
        thread.join(2)
        assert not thread.is_alive(),thread.name


@pytest.mark.parametrize('operation',['session_health','registry_list'])
def test_full_dispatch_metadata_completes_during_blocked_factory_birth(monkeypatch,tmp_path,operation):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    sdk=_JobBoundary(factory,monkeypatch);calls=[]
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch):
            birth=_async_call(sdk.launch,name='blocked-birth');calls.append(birth)
            try:
                assert sdk.entered.wait(1)
                assert daemon._worker_admission_inflight==1
                query=_async_call(lambda:daemon.dispatch({'operation':operation,'arguments':{},'execution':{}}),name='metadata')
                calls.append(query)
                assert query[1].wait(0.5),'actual public metadata dispatch blocked behind birth'
                assert not query[3] and query[2][0]['success'] is True
                assert not birth[1].is_set() and factory._job is None
                assert daemon._control_dispatch_inflight==0
            finally:
                sdk.release.set();_join(*calls)
            assert len(birth[3])==1 and isinstance(birth[3][0],_SoftwareBirthEnd)
            assert daemon._worker_admission_inflight==0
            record(tmp_path,case='birth_full_dispatch_responsive',operation=operation,
                   job_calls=sdk.calls,inflight_after=daemon._worker_admission_inflight)
    finally:daemon.close()


def test_actual_close_waits_for_admitted_birth_before_resource_retirement(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    sdk=_JobBoundary(factory,monkeypatch);calls=[]
    close_waited=threading.Event();retirement=threading.Event()
    original_wait=daemon._dispatch_condition.wait
    original_retire=daemon._retire_owned_session_workers_for_close
    def wait(*args,**kwargs):
        if threading.current_thread().name=='daemon-close':
            assert daemon._closing and daemon._worker_admission_inflight==1
            close_waited.set()
        return original_wait(*args,**kwargs)
    def retire():
        assert sdk.exited.is_set() and daemon._worker_admission_inflight==0
        retirement.set()
        return original_retire()
    monkeypatch.setattr(daemon._dispatch_condition,'wait',wait)
    monkeypatch.setattr(daemon,'_retire_owned_session_workers_for_close',retire)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch):
            birth=_async_call(sdk.launch,name='admitted-birth');calls.append(birth)
            try:
                assert sdk.entered.wait(1)
                closer=_async_call(daemon.close,name='daemon-close');calls.append(closer)
                assert close_waited.wait(1),'actual close did not reach inflight wait'
                assert not closer[1].is_set() and not retirement.is_set()
                assert not daemon._close_complete and not daemon.closed.is_set()
                assert daemon._session_t037_process_factories[('project','scm-session')] is factory
                query=_async_call(lambda:daemon.dispatch({'operation':'session_health','arguments':{},'execution':{}}),name='closing-query')
                calls.append(query)
                assert query[1].wait(0.5) and not query[3]
                assert query[2][0]['error']['code']=='CONTROL_DAEMON_CLOSING'
                assert not retirement.is_set() and not birth[1].is_set()
            finally:
                sdk.release.set();_join(*calls)
            assert isinstance(birth[3][0],_SoftwareBirthEnd) and not closer[3]
            assert retirement.is_set() and daemon._close_complete
            assert daemon._worker_admission_inflight==0 and sdk.calls==['create_job_boundary']
            record(tmp_path,case='actual_close_waits_before_retirement',inflight_after=0,close_complete=True)
    finally:daemon.close()


def test_second_birth_waiting_original_runtime_does_not_hold_global_control_gate(monkeypatch,tmp_path):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    sdk=_JobBoundary(factory,monkeypatch);waiting=threading.Event();calls=[]
    original=broker._require_original_runtime
    def require(runtime,**kwargs):
        assert not daemon._dispatch_condition._is_owned(),'runtime lock waited under global condition'
        if threading.current_thread().name=='second-birth':waiting.set()
        return original(runtime,**kwargs)
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch):
            monkeypatch.setattr(broker,'_require_original_runtime',require)
            first=_async_call(sdk.launch,name='first-birth');calls.append(first)
            try:
                assert sdk.entered.wait(1)
                second=_async_call(sdk.launch,name='second-birth');calls.append(second)
                assert waiting.wait(1) and not second[1].is_set()
                query=_async_call(lambda:daemon.dispatch({'operation':'registry_list','arguments':{},'execution':{}}),name='second-birth-query')
                calls.append(query)
                assert query[1].wait(0.5),'runtime waiter blocked unrelated public dispatch'
                assert not query[3] and query[2][0]['success'] is True
                assert sdk.calls==['create_job_boundary'] and daemon._worker_admission_inflight==1
            finally:
                sdk.release.set();_join(*calls)
            assert all(len(call[3])==1 and isinstance(call[3][0],_SoftwareBirthEnd) for call in (first,second))
            assert sdk.calls==['create_job_boundary']*2 and daemon._worker_admission_inflight==0
            record(tmp_path,case='second_original_runtime_waiter',job_calls=sdk.calls,inflight_after=0)
    finally:daemon.close()


@pytest.mark.parametrize('first',['closing','SCM_stop'])
def test_close_or_stop_between_original_validation_and_admission_prevents_birth(monkeypatch,tmp_path,first):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    sdk=_JobBoundary(factory,monkeypatch);validated=threading.Event();proceed=threading.Event()
    original=admission._require_factory_runtime;calls=[]
    def require(value):
        result=original(value)
        if threading.current_thread().name=='not-yet-admitted' and not validated.is_set():
            validated.set();assert proceed.wait(3)
        return result
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,scm,_provider):
            monkeypatch.setattr(admission,'_require_factory_runtime',require)
            birth=_async_call(sdk.launch,name='not-yet-admitted');calls.append(birth)
            try:
                assert validated.wait(1) and daemon._worker_admission_inflight==0
                if first=='closing':
                    closer=_async_call(daemon.close,name='close-before-admission');calls.append(closer)
                    assert closer[1].wait(1) and not closer[3]
                    assert daemon._close_complete
                else:
                    assert scm.handler(broker.SERVICE_CONTROL_STOP,0,None,None)==broker.ERROR_SUCCESS
                    assert runtime.stop_requested
            finally:
                proceed.set();sdk.release.set();_join(*calls)
            assert len(birth[3])==1 and isinstance(birth[3][0],ExecutionContractError)
            assert sdk.calls==[] and factory._job is None and daemon._worker_admission_inflight==0
            record(tmp_path,case='close_or_stop_wins_before_birth',first=first,no_sdk_job_or_child=True,inflight_after=0)
    finally:daemon.close()


@pytest.mark.parametrize('error_type',[_SoftwareBirthEnd,_SoftwareAbort])
def test_body_exceptions_balance_counter_and_notify_only_after_original_runtime_unlock(monkeypatch,tmp_path,error_type):
    daemon,factory=daemon_factory(tmp_path,monkeypatch)
    sdk=_JobBoundary(factory,monkeypatch,error_type=error_type);sdk.release.set();notifications=[]
    try:
        with running_factory_chain(daemon,factory,tmp_path/'scm',monkeypatch) as (runtime,_scm,_provider):
            original=daemon._dispatch_condition.notify_all
            def notify():
                assert daemon._dispatch_condition._is_owned() and not runtime._lock._is_owned()
                notifications.append(daemon._worker_admission_inflight)
                return original()
            monkeypatch.setattr(daemon._dispatch_condition,'notify_all',notify)
            with pytest.raises(error_type):sdk.launch()
            assert notifications==[0] and daemon._worker_admission_inflight==0
            assert factory._job is None and not factory._closed
            record(tmp_path,case='exception_counter_notify',error_type=error_type.__name__,notifications=notifications)
    finally:daemon.close()
