"""Original late-exit dependency -> first held-A finish, on inert SDK leaves.

No native issuer, process, SDK DLL, C or Resume qualification is exercised.
"""
import threading

import pytest

from comsol_mcp import _windows_t037_targets as targets, _windows_t037_resume as resume
from comsol_mcp import _java_worker as java
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_native_child_lifecycle import (
    worker_case, REAL_ISSUER, no_real_native_or_external_effect,
    prepared_consumer_scope, software_only,
)


def terminal_dependency(monkeypatch, tmp_path):
    factory,worker,processes,model=worker_case(monkeypatch,tmp_path)
    api=factory._api
    monkeypatch.setattr(resume,'issue_native_resume',REAL_ISSUER)
    attempts=[]
    def unknown_terminate(handle,code):
        attempts.append(handle)
        raise OSError('SOFTWARE original first terminate lost its return')
    api.terminate_process=unknown_terminate
    with pytest.raises(java.JavaWorkerError):worker.start()
    child=worker._process
    with pytest.raises(OSError):worker.close()
    held=factory._held_static_a
    assert held.state=='UNKNOWN' and not held.reader.retired and attempts==[200]
    model['alive']=False
    worker.close()  # Real original HANDLE read, then the original thread/process closers.
    assert child._closed and child.returncode==1 and not child._resume_attempted
    assert child._process_entry.state==child._thread_entry.state=='RELEASED'
    assert worker._process is None and not api._retained_children
    assert held is targets._HELD[id(factory)] and held.primary_error._process is child
    return factory,worker,child,held,api._prepared_fixture,attempts


def effects(environment):
    api=environment.api
    return (list(environment.sdk.calls),list(environment.query.calls),
        list(api._software_handle_events),list(api._software_sdk['reads']))


def test_original_late_exit_finishes_same_pins_and_job_once_with_cached_repeat(monkeypatch,tmp_path):
    factory,worker,child,held,env,attempts=terminal_dependency(monkeypatch,tmp_path)
    reader=held.reader;entries=tuple(registry_for(factory).unresolved());primary=held.primary_error
    assert len(entries)==158 and all(e.state=='ACQUIRED' for e in entries)
    before_closes=len([x for x in env.sdk.calls if x[0]=='close'])
    child_reads=list(env.api._software_sdk['reads'])
    factory.close()
    assert reader.retired and held.state=='RELEASED' and held.primary_error is primary
    assert all(e.state=='RELEASED' for e in entries) and not registry_for(factory).unresolved()
    assert targets._HELD.get(id(factory)) is None and factory._held_static_a is None
    assert len([x for x in env.sdk.calls if x[0]=='close'])-before_closes==158
    assert env.api._software_handle_events.count(('close',300))==1
    assert env.api._software_handle_events.count(('close',200))==env.api._software_handle_events.count(('close',201))==1
    assert env.api._software_sdk['reads']==child_reads and attempts==[200]
    known=effects(env);factory.close();worker.close()
    assert effects(env)==known and not registry_for(env.api).unresolved()


@pytest.mark.parametrize('fault',[
    'not_closed','returncode_bool','wrong_owner','wrong_error_child','foreign_thread',
    'entry_unknown','entry_closer','reader_finish_unknown','resume_attempted',
    'retirement_error','ancillary_pending','mic_pending','primary_pending',
])
def test_unproven_dependency_refuses_before_any_sdk_or_finish(monkeypatch,tmp_path,fault):
    factory,worker,child,held,env,attempts=terminal_dependency(monkeypatch,tmp_path)
    if fault=='not_closed':child._closed=False
    elif fault=='returncode_bool':child.returncode=True
    elif fault=='wrong_owner':object.__setattr__(child,'_owner',object())
    elif fault=='wrong_error_child':held.primary_error._process=object()
    elif fault=='entry_unknown':
        entry=registry_for(factory).unresolved()[0];entry.state='UNKNOWN';entry.error=OSError('SOFTWARE unknown entry')
    elif fault=='entry_closer':child._process_entry.closer=lambda h:None
    elif fault=='reader_finish_unknown':held.reader._finish_error=OSError('SOFTWARE prior finish unknown')
    elif fault=='resume_attempted':child._resume_attempted=True
    elif fault=='retirement_error':child._retirement_error=OSError('SOFTWARE retirement bookkeeping unknown')
    elif fault=='ancillary_pending':env.api._retained_launch_cleanup=[('unclassified',object())]
    elif fault=='mic_pending':held.reader.mic.pending=object()
    elif fault=='primary_pending':
        prep=child._birth_preparation;prep.state='UNKNOWN';prep.error=OSError('SOFTWARE primary pending')
        env.api._birth_preparations[id(prep)]=prep;factory._birth_preparations[id(prep)]=prep
    before=effects(env);errors=[]
    def rejected():
        try:factory.close()
        except BaseException as error:errors.append(error)
    if fault=='foreign_thread':
        thread=threading.Thread(target=rejected);thread.start();thread.join(3)
        assert not thread.is_alive()
    else:rejected()
    assert len(errors)==1 and isinstance(errors[0],(ExecutionContractError,OSError))
    assert effects(env)==before and attempts==[200]
    assert held.state=='UNKNOWN' and not held.reader.retired and not held._deferred_finish_entered
    assert factory._held_static_a is held and targets._HELD[id(factory)] is held


def test_first_reader_close_unknown_keeps_original_pins_and_never_enters_finish_twice(monkeypatch,tmp_path):
    factory,worker,child,held,env,attempts=terminal_dependency(monkeypatch,tmp_path)
    entries=tuple(registry_for(factory).unresolved());env.sdk.fault='close_written_raise'
    before=len([x for x in env.sdk.calls if x[0]=='close'])
    with pytest.raises(OSError) as original:factory.close()
    assert held._deferred_finish_entered and held.state=='UNKNOWN' and not held.reader.retired
    assert held.cleanup_error is original.value is held.reader._finish_error
    assert sum(e.state=='UNKNOWN' for e in entries)==1
    assert len([x for x in env.sdk.calls if x[0]=='close'])-before==1
    assert ('close',300) not in env.api._software_handle_events
    first=effects(env)
    for _ in range(2):
        with pytest.raises(OSError) as repeated:factory.close()
        assert repeated.value is original.value
    assert effects(env)==first and attempts==[200]
    assert targets._HELD[id(factory)] is held and factory._held_static_a is held
