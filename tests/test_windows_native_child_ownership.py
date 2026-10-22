"""Full-launch ownership regressions, SOFTWARE ONLY.

The original red logs/source remain preserved in the private evidence folder.
Only SDK boundaries are synthetic; the complete production create function,
attribute-list owner, child binding, cleanup and return path execute. Positive
Resume branches explicitly replace the unavailable issuer/resume functions in
test memory. They create NO native issuer capability and never call a real SDK,
ResumeThread, remote host, subprocess, SCM or COMSOL process.
"""
from __future__ import annotations

import ctypes as C
import gc
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import traceback
import weakref

import pytest

from comsol_mcp import _windows_t037 as win
from comsol_mcp import _windows_t037_resume as resume
from comsol_mcp._windows_owned_resources import registry_for
from tests.test_windows_suspended_ownership import _launch_api
from tests.test_windows_child_process_policy import _sdk

SOURCE_SHA_AT_IMPORT = hashlib.sha256(Path(win.__file__).read_bytes()).hexdigest()
CREATE_SOURCE_SHA_AT_IMPORT = hashlib.sha256(inspect.getsource(
    win._Win32LaunchApi.create_suspended_appcontainer_child).encode()).hexdigest()


class InjectedAfterOutputError(RuntimeError):
    pass


class InjectedReturnConversionError(RuntimeError):
    pass


class InjectedBookkeepingError(RuntimeError):
    pass


class _AmbiguousSoftwareReturn:
    def __bool__(self):
        raise InjectedReturnConversionError("SOFTWARE SDK return conversion after original outputs")


class _SoftwareResumeBranchOnly:
    """Test branch token, never a _NativeResumeCapability or pending authority."""
    def __init__(self, process):
        self.process = process


@pytest.fixture(autouse=True)
def forbid_real_native_and_subprocess(monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail("this ownership unit must not call a real SDK/subprocess")
    monkeypatch.setattr(C, "WinDLL", prohibited, raising=False)
    monkeypatch.setattr(subprocess, "Popen", prohibited)


def _case(monkeypatch, *, creation="normal", software_resume=False, resume_result=1):
    profile, api, events, state = _launch_api(monkeypatch)
    api, sdk = _sdk(monkeypatch, api)
    # Exercise the actual attribute-list function too; replace DLL exports only.
    api._attribute_list = win._Win32LaunchApi._attribute_list.__get__(api)
    api._environment_buffer = win._Win32LaunchApi._environment_buffer.__get__(api)
    trace = {"original": None, "slot": None, "live_process_closes": [],
             "software_resume_override": software_resume, "resume_calls": 0,
             "creation_calls": 0, "closed_handles": set(), "policy_sdk": sdk}
    original_create = api.a.CreateProcessAsUserW
    original_close, original_poll = api.close_handle, api.poll_process

    def create(*args):
        trace["creation_calls"] += 1
        trace["slot"] = args[-1]._obj  # Exact caller PROCESS_INFORMATION storage.
        precall = [child for child in getattr(api, "_retained_children", {}).values()
                   if child._process_info is trace["slot"]]
        trace["precall_owner"] = precall[0] if len(precall) == 1 else None
        trace["slot_owner_at_entry"] = [entry.label for entry in registry_for(api).unresolved()
            if any(value is trace["slot"] for value in entry.keepalive)]
        if creation == "known_false_zero":
            events.append("SOFTWARE_CREATE_FALSE_ZERO_OUTPUT")
            return 0
        if creation == "raise_zero":
            raise InjectedAfterOutputError("SOFTWARE ambiguous call exception, zero output")
        original_create(*args)  # SDK fixture writes the original output slot.
        if creation == "false_with_outputs":
            return 0
        if creation == "missing_thread":
            trace["slot"].hThread = None
        if creation == "missing_tid":
            trace["slot"].dwThreadId = 0
        if creation == "invalid_process":
            trace["slot"].hProcess = C.c_void_p(-1).value
        if creation == "aliased_handles":
            trace["slot"].hThread = trace["slot"].hProcess
        if creation == "write_then_raise":
            raise InjectedAfterOutputError("SOFTWARE call exception after PROCESS_INFORMATION writes")
        if creation == "write_then_conversion_raise":
            return _AmbiguousSoftwareReturn()
        return 1

    def close(handle):
        value = getattr(handle, "value", handle)
        if value == 200 and state["alive"]:
            trace["live_process_closes"].append(value)
        trace["closed_handles"].add(value)
        return original_close(handle)

    def poll(handle):
        value = getattr(handle, "value", handle)
        if value == 200 and value in trace["closed_handles"]:
            raise OSError("SOFTWARE SDK model: original process HANDLE already closed")
        return original_poll(handle)

    def prohibited_sdk_resume(*args):
        pytest.fail("only the explicitly labeled software Resume branch may execute")

    api.a.CreateProcessAsUserW = create
    api.close_handle, api.poll_process = close, poll
    api.k.ResumeThread = prohibited_sdk_resume
    if software_resume:
        def software_issue(process, bound_profile, child_binding, dacl):
            assert bound_profile is profile and process._api is api
            assert child_binding.pid == process.pid and dacl.child_pid == process.pid
            trace["original"] = process  # Retained actual object created by full function.
            if "before_software_resume" in trace:
                trace["before_software_resume"](process)
            token = _SoftwareResumeBranchOnly(process)
            trace["software_token_weakref"] = weakref.ref(token)
            return token
        def software_resume_branch(process, token):
            assert type(token) is _SoftwareResumeBranchOnly and token.process is process
            trace["resume_calls"] += 1
            process._resume_epoch += 1
            process.note_resume_attempt()
            events.append("SOFTWARE_RESUME_BRANCH_NO_NATIVE_AUTHORITY")
            return resume_result
        monkeypatch.setattr(resume, "issue_native_resume", software_issue)
        monkeypatch.setattr(resume, "resume_native_child", software_resume_branch)
    return profile, api, events, state, trace


def _launch(profile, api, *, factory=None, role="server"):
    return api.create_suspended_appcontainer_child([profile._launch_image(role).path], {},
        profile=profile, job=300, role=role, session_factory=factory)


def _capture_error(fn):
    try:
        fn()
    except Exception as error:
        return error
    pytest.fail("expected a production launch exception")


def _clear_exception_frames(error):
    """Simulate consumers discarding diagnostics, without changing finalizers."""
    seen, pending = set(), [error]
    while pending:
        item = pending.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        pending.extend((item.__cause__, item.__context__))
        tb = item.__traceback__
        item.__traceback__ = item.__cause__ = item.__context__ = None
        if tb is not None:
            traceback.clear_frames(tb)
    gc.collect()


def _record(tmp_path, api, events, state, trace, **facts):
    slot, original = trace.get("slot"), trace.get("original")
    value = {"scope": "SOFTWARE_ONLY_NO_NATIVE_ISSUER_OR_HOST_EFFECT",
        "module_sha256_at_import": SOURCE_SHA_AT_IMPORT,
        "create_function_source_sha256_at_import": CREATE_SOURCE_SHA_AT_IMPORT,
        "software_resume_override": trace["software_resume_override"],
        "software_resume_calls": trace["resume_calls"], "software_creation_calls": trace["creation_calls"],
        "slot_owner_labels_at_SDK_entry": trace.get("slot_owner_at_entry"),
        "exact_output_slot_owned_before_SDK": trace.get("precall_owner") is not None,
        "slot_outputs": {name: getattr(slot, name) for name in (
            "hProcess", "hThread", "dwProcessId", "dwThreadId")} if slot is not None else None,
        "original_retained": {"type": type(original).__name__, "handle": original._handle,
            "thread_handle": original._thread_handle, "closed": original._closed,
            "resume_attempted": original._resume_attempted,
            "never_resumed": original.never_resumed} if original is not None else None,
        "live_process_closes": list(trace["live_process_closes"]), "model_process_alive": state["alive"],
        "original_handle_events": [item for item in events if isinstance(item, tuple)
            and item[0] in {"close", "terminate"} and item[1] in {200, 201}],
        "original_registry_unresolved": [{"label": entry.label, "state": entry.state}
            for entry in registry_for(api).unresolved()], **facts}
    (tmp_path / "case.json").write_text(json.dumps(value, indent=2) + "\n")


@pytest.mark.parametrize("creation", ["write_then_raise", "write_then_conversion_raise"])
def test_original_sdk_outputs_survive_call_or_conversion_exception(monkeypatch, tmp_path, creation):
    profile, api, events, state, trace = _case(monkeypatch, creation=creation)
    error = _capture_error(lambda: _launch(profile, api))
    retained = getattr(error, "_process", None)
    _record(tmp_path, api, events, state, trace, exception_type=type(error).__name__,
        exception_code=getattr(error, "code", None), typed_original_owner_retained=retained is not None,
        raw_integer_tuple_only=getattr(api, "_unclassified_create_handles", None))
    assert trace["slot"].hProcess == 200 and trace["slot"].hThread == 201
    assert not trace["live_process_closes"] and ("terminate", 200) not in events
    assert isinstance(error, win.NativeSuspendedChildLaunchError), (
        "written original PROCESS_INFORMATION was not handed off with an actual retained owner")
    assert retained is trace["precall_owner"] and retained._process_info is trace["slot"]
    assert retained._api is api and not retained.never_resumed


def test_success_return_has_one_process_owner_and_no_stale_retained_thread(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    returned = _launch(profile, api)
    original = trace["original"]
    duplicate = (returned is not original and returned._handle == original._handle
                 and not returned._closed and not original._closed)
    stale_thread = original is not returned and original._thread_handle == 201 and ("close", 201) in events
    _record(tmp_path, api, events, state, trace, returned_type=type(returned).__name__,
        distinct_active_wrappers_same_process_handle=duplicate, retained_thread_stale_after_close=stale_thread)
    # Explicit model teardown is not the assertion under test or native cleanup.
    returned.terminate(); returned.wait(timeout=1); returned.close()
    assert returned is original is trace["precall_owner"]
    assert not duplicate and not stale_thread, "successful Resume returned an additional owning wrapper without exclusive transfer"


def test_current_old_retained_and_software_capability_gc_does_not_close_live_returned_handle(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    returned = _launch(profile, api)
    original_ref = weakref.ref(trace.pop("original"))
    gc.collect()
    old_gone = original_ref() is None
    token_gone = trace["software_token_weakref"]() is None
    _record(tmp_path, api, events, state, trace, old_retained_collected=old_gone,
        software_capability_collected=token_gone, returned_still_live=returned.poll() is None)
    # The corrected representation returns the original object itself. Its GC
    # cannot occur while the caller/API still owns it; branch token GC is safe.
    assert not old_gone and original_ref() is returned and token_gone and returned.poll() is None
    assert not trace["live_process_closes"]  # Current no-op __del__ is a real counter-control.
    returned.terminate(); returned.wait(timeout=1); returned.close()


def test_post_resume_bookkeeping_exception_and_gc_keep_live_recovery_handle(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    factory = win.T037SessionProcessFactory(profile, "software-owner-unit", api=api)
    factory._job = 300
    def fail_success_bookkeeping(**kwargs):
        trace["new_return_wrapper_weakref"] = weakref.ref(kwargs["process"])
        raise InjectedBookkeepingError("SOFTWARE G2 provenance allocation failure after Resume branch")
    monkeypatch.setattr(win, "_Win32G2SessionLaunchOwner", fail_success_bookkeeping)
    error = _capture_error(lambda: _launch(profile, api, factory=factory))
    assert isinstance(error, win.NativeSuspendedChildLaunchError)
    original = error._process
    assert original is trace["original"] and not original.never_resumed
    _clear_exception_frames(error)
    new_gone = trace["new_return_wrapper_weakref"]() is None
    _record(tmp_path, api, events, state, trace, exception_code=error.code,
        lost_new_wrapper_collected=new_gone, original_recovery_owner_retained=
        getattr(api, "_retained_children", {}).get(id(original)) is original)
    assert state["alive"] and trace["resume_calls"] == 1
    assert not trace["live_process_closes"], "GC of a fallible post-Resume wrapper closed the live original recovery HANDLE"
    assert not new_gone and trace["new_return_wrapper_weakref"]() is original
    assert original.poll() is None


def test_unmodified_issuer_still_refuses_and_reaps_only_original_model_child(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch)
    error = _capture_error(lambda: _launch(profile, api))
    _record(tmp_path, api, events, state, trace, exception_code=getattr(error, "code", None))
    assert error.code == "NATIVE_CHILD_PROOF_UNAVAILABLE"
    assert trace["resume_calls"] == 0 and not state["alive"]
    assert events.count(("terminate", 200)) == 1
    assert events.count(("close", 200)) == events.count(("close", 201)) == 1


def test_known_false_zero_output_has_no_child_owner_or_effect(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, creation="known_false_zero")
    error = _capture_error(lambda: _launch(profile, api))
    _record(tmp_path, api, events, state, trace, exception_code=getattr(error, "code", None))
    assert trace["slot"].hProcess is None and trace["slot"].hThread is None
    assert not getattr(api, "_retained_children", {}) and not getattr(api, "_unclassified_create_handles", None)
    assert trace["resume_calls"] == 0 and not trace["live_process_closes"] and ("terminate", 200) not in events


def test_unexpected_resume_return_retains_original_owner_without_simplified_cleanup(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True, resume_result=2)
    error = _capture_error(lambda: _launch(profile, api))
    original = getattr(error, "_process", None)
    _clear_exception_frames(error)
    _record(tmp_path, api, events, state, trace, exception_code=getattr(error, "code", None))
    assert isinstance(error, win.NativeSuspendedChildLaunchError) and original is trace["original"]
    assert not original.never_resumed and state["alive"]
    assert trace["resume_calls"] == 1 and not trace["live_process_closes"] and ("terminate", 200) not in events


def test_software_success_preserves_existing_g2_consumer_provenance_contract(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    factory = win.T037SessionProcessFactory(profile, "software-g2-owner-contract", api=api)
    factory._job = 300
    returned = _launch(profile, api, factory=factory)
    provenance = win._native_g2_session_launch_owner(returned)
    assert provenance is not None
    assert provenance.process is returned and provenance.api is api
    assert provenance.process_handle is returned._handle
    assert provenance.profile is profile and provenance.session_factory is factory
    assert provenance.job_handle is factory._job and provenance.role == "server"
    assert provenance.birth_filetime_100ns == 123 and provenance.argv == tuple(returned.args)
    assert provenance.profile_sha256 == profile.sha256
    original_ref = weakref.ref(trace.pop("original"))
    gc.collect()
    _record(tmp_path, api, events, state, trace, software_G2_receipt_lookup_valid=True,
        G2_original_factory_profile_job_argv_birth_bound=True,
        old_retained_collected=original_ref() is None, G2_receipt_is_native_admission=False)
    assert returned.poll() is None and not trace["live_process_closes"]
    assert original_ref() is returned is trace["precall_owner"]
    returned.terminate(); returned.wait(timeout=1); returned.close()
    assert win._native_g2_session_launch_owner(returned) is None


@pytest.mark.parametrize('creation', ['raise_zero', 'false_with_outputs', 'missing_thread',
                                    'missing_tid', 'invalid_process', 'aliased_handles'])
def test_unconfirmed_original_output_never_allows_observation_cleanup_or_second_birth(monkeypatch, tmp_path, creation):
    profile, api, events, state, trace = _case(monkeypatch, creation=creation)
    error = _capture_error(lambda: _launch(profile, api))
    assert isinstance(error, win.NativeSuspendedChildLaunchError)
    child = error._process
    assert child is trace['precall_owner'] and child._process_info is trace['slot']
    assert not child._creation_confirmed and not child.never_resumed
    before = list(events)
    for attempt in (child.poll, child.wait, child.terminate,
                    lambda: child.close_owned(api), lambda: child.cleanup_owned(api),
                    child.create_original, lambda: _launch(profile, api)):
        with pytest.raises(win.WindowsT037Error):
            attempt()
    assert events == before and trace['creation_calls'] == 1
    assert not trace['live_process_closes'] and ('terminate', 200) not in events
    gc.collect()
    assert api._retained_children[id(child)] is child
    _record(tmp_path, api, events, state, trace, unconfirmed_refused_all_effects=True,
            second_birth_refused=True, exact_original_storage_retained=True)


def test_only_exact_zero_return_and_zero_output_allows_fresh_owner_attempt(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, creation='known_false_zero')
    first = _capture_error(lambda: _launch(profile, api))
    old = trace['precall_owner']
    assert old._no_effect and old._closed and not api._retained_children
    second = _capture_error(lambda: _launch(profile, api))
    assert trace['precall_owner'] is not old and trace['creation_calls'] == 2
    assert not api._retained_children and not trace['live_process_closes']
    assert first.code == second.code == 'NATIVE_TEST_FAILURE'
    _record(tmp_path, api, events, state, trace, known_no_effect_fresh_owner_only=True)


@pytest.mark.parametrize('fault', ['slot_process', 'slot_pid', 'entry_closer', 'entry_value',
                                  'entry_adapter', 'entry_replaced', 'profile_binding',
                                  'api_internal_corruption'])
def test_original_slot_registry_and_fixed_binding_drift_refuses_before_software_resume(monkeypatch, tmp_path, fault):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    def corrupt(child):
        if fault == 'slot_process': child._process_info.hProcess = 999
        elif fault == 'slot_pid': child._process_info.dwProcessId = 999
        elif fault == 'entry_closer': child._process_entry.closer = lambda h: pytest.fail('foreign close')
        elif fault == 'entry_value': child._process_entry.value = 999
        elif fault == 'entry_adapter': child._process_entry.adapter = object()
        elif fault == 'entry_replaced': child._process_entry = child._thread_entry
        elif fault == 'profile_binding': child._launch_profile_sha256 = 'e' * 64
        else: object.__setattr__(child, '_api', object())
    trace['before_software_resume'] = corrupt
    error = _capture_error(lambda: _launch(profile, api))
    assert isinstance(error, win.NativeSuspendedChildLaunchError)
    child = error._process
    assert child is trace['precall_owner'] and not child._resume_attempted
    assert ('terminate', 200) not in events and not trace['live_process_closes']
    assert ('close', 201) not in events and ('close', 200) not in events
    assert api._retained_children[id(child)] is child
    _record(tmp_path, api, events, state, trace, drift=fault, original_retained_without_cleanup=True)


def test_original_api_output_slot_and_native_closer_are_fixed_without_rerouting(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    original_close, closes = api.close_handle, []
    def native_close(handle):
        closes.append(handle)
        original_close(handle)
        return 1
    api.k.CloseHandle = native_close
    def before_resume(child):
        for field, value in [('_api', object()), ('_process_info', object()),
                             ('_native_closer', lambda h: None), ('_call_storage', ())]:
            with pytest.raises(AttributeError, match='immutable'):
                setattr(child, field, value)
        api.k.CloseHandle = lambda h: pytest.fail('changed export used for original child')
    trace['before_software_resume'] = before_resume
    returned = _launch(profile, api)
    assert returned is trace['precall_owner'] and closes.count(201) == 1
    returned.terminate(); returned.wait(timeout=1); returned.close()
    assert closes.count(200) == 1 and returned._closed
    assert not api._retained_children and win._native_g2_session_launch_owner(returned) is None
    _record(tmp_path, api, events, state, trace, fixed_original_closer_used_once=True)


@pytest.mark.parametrize('fault', ['false', 'exception_after_effect'])
def test_post_resume_thread_close_unknown_never_retries_or_closes_process(monkeypatch, tmp_path, fault):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    original_close, attempts = api.k.CloseHandle, []
    def close(handle):
        if handle == 201:
            attempts.append(handle)
            if fault == 'false': return 0
            raise OSError('SOFTWARE native thread CloseHandle completed then conversion raised')
        return original_close(handle)
    api.k.CloseHandle = close
    error = _capture_error(lambda: _launch(profile, api))
    assert isinstance(error, win.NativeSuspendedChildLaunchError)
    child = error._process
    assert child._thread_entry.state == 'UNKNOWN' and child._thread_handle == 201
    assert not child._returned and not child.never_resumed and state['alive']
    for operation in (lambda: child.close_owned(api), lambda: child.cleanup_owned(api),
                      lambda: registry_for(api).retry_owned(child._thread_entry, api),
                      lambda: _launch(profile, api)):
        with pytest.raises(Exception): operation()
    assert attempts == [201] and trace['creation_calls'] == 1
    assert not trace['live_process_closes'] and ('terminate', 200) not in events
    _clear_exception_frames(error)
    assert api._retained_children[id(child)] is child
    holder = object()
    assert error.transfer_to(holder) is child and child._owner is holder
    with pytest.raises(Exception): child.close_owned(holder)
    assert attempts == [201]  # Handoff retained the UNKNOWN, it did not retry release.
    _record(tmp_path, api, events, state, trace, thread_close_attempts=attempts,
            thread_release_unknown_blocks_repetition=True)


def test_ancillary_release_after_resume_never_publishes_success_or_loses_original_owner(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    factory = win.T037SessionProcessFactory(profile, 'software-ancillary-failure', api=api)
    factory._job = 300
    deletes = []
    def fail_delete(pointer):
        deletes.append(pointer)
        raise OSError('SOFTWARE DeleteProcThreadAttributeList release uncertain')
    api.k.DeleteProcThreadAttributeList = fail_delete
    error = _capture_error(lambda: _launch(profile, api, factory=factory))
    assert isinstance(error, win.NativeSuspendedChildLaunchError)
    child = error._process
    _clear_exception_frames(error)
    assert child is trace['precall_owner'] and child._resume_attempted and not child._returned
    assert child._thread_handle is None and child._process_entry.state == 'ACQUIRED'
    assert not getattr(api, '_g2_session_launch_owners', {})
    assert win._native_g2_session_launch_owner(child) is None and len(deletes) == 1
    with pytest.raises(Exception): factory.close()
    with pytest.raises(Exception): _launch(profile, api, factory=factory)
    assert state['alive'] and not trace['live_process_closes'] and ('terminate', 200) not in events
    assert trace['creation_calls'] == 1 and len(deletes) == 1
    _record(tmp_path, api, events, state, trace, no_success_publication_on_finally_fault=True)


def test_g2_dictionary_publication_fault_keeps_same_resumed_owner_unreturned(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    factory = win.T037SessionProcessFactory(profile, 'software-g2-publication-failure', api=api)
    factory._job = 300
    class FailingPublication(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            raise InjectedBookkeepingError('SOFTWARE publication wrote then raised')
    api._g2_session_launch_owners = FailingPublication()
    error = _capture_error(lambda: _launch(profile, api, factory=factory))
    child = error._process
    _clear_exception_frames(error)
    assert child is trace['precall_owner'] and not child._returned and not child.never_resumed
    assert win._native_g2_session_launch_owner(child) is None
    assert api._retained_children[id(child)] is child
    assert state['alive'] and not trace['live_process_closes']
    _record(tmp_path, api, events, state, trace, publication_fault_not_successful_return=True)


def test_process_release_unknown_preserves_exit_fact_and_original_closer_without_retry(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    original_close, attempts = api.k.CloseHandle, []
    def close(handle):
        if handle == 200:
            attempts.append(handle)
            raise OSError('SOFTWARE process CloseHandle may have completed')
        return original_close(handle)
    api.k.CloseHandle = close
    child = _launch(profile, api)
    child.terminate(); assert child.wait(timeout=1) == 1
    with pytest.raises(OSError): child.close()
    assert child._process_entry.state == 'UNKNOWN' and not child._closed
    for operation in (child.close, child.poll, child.terminate,
                      lambda: registry_for(api).retry_owned(child._process_entry, api),
                      lambda: _launch(profile, api)):
        with pytest.raises(Exception): operation()
    assert attempts == [200] and trace['creation_calls'] == 1
    assert not state['alive'] and api._retained_children[id(child)] is child
    _record(tmp_path, api, events, state, trace, process_close_attempts=attempts,
            original_exit_fact_retained=True)


def test_termination_exception_does_not_repeat_effect_but_later_exit_can_be_observed(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    calls = []
    def terminate(handle, code):
        calls.append((handle, code))
        raise OSError('SOFTWARE TerminateProcess effect unknown')
    api.terminate_process = terminate
    child = _launch(profile, api)
    with pytest.raises(OSError): child.terminate()
    with pytest.raises(OSError): child.terminate()
    with pytest.raises(win.WindowsT037Error): _launch(profile, api)
    assert calls == [(200, 1)] and state['alive']
    state['alive'] = False  # A later SDK observation, not a second termination.
    assert child.poll() == 1
    child.close()
    assert child._closed and not api._retained_children
    _record(tmp_path, api, events, state, trace, ambiguous_termination_not_repeated=True)


def test_original_registry_observer_bridge_keeps_image_adapter_and_borrows_without_release(monkeypatch, tmp_path):
    from comsol_mcp import _windows_t037_child_evidence as evidence
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    query = evidence.WindowsChildEvidenceApi.__new__(evidence.WindowsChildEvidenceApi)
    def borrow(child):
        binding, pin = evidence.borrow_original_child(child, api=query)
        assert binding.process_entry is child._process_entry and binding.thread_entry is child._thread_entry
        assert evidence._borrow(binding) == (200, 201)
        assert pin.entry is child._image_entry and pin.original_adapter is child._image_api
        assert pin.entry.adapter is child._image_api and pin.api is query
        assert registry_for(query) is registry_for(api)
        trace['borrowed'] = binding, pin
    trace['before_software_resume'] = borrow
    child = _launch(profile, api)
    binding, pin = trace['borrowed']
    assert binding.expected_pid == 456 and binding.expected_thread_id == 9 and binding.expected_birth_filetime == 123
    assert child._thread_entry.state == 'RELEASED' and child._process_entry.state == 'ACQUIRED'
    assert pin.entry.state == 'RELEASED'  # Original launcher cleanup; observer never releases it.
    with pytest.raises(evidence.ObservationError): evidence._borrow(binding)
    with pytest.raises(evidence.ObservationError): evidence.borrow_original_child(child, api=query)
    assert events.count(('close', 201)) == 1 and ('close', 200) not in events
    child.terminate(); child.wait(timeout=1); child.close()
    _record(tmp_path, api, events, state, trace, original_image_adapter_not_reassigned=True,
            bridge_borrowed_exact_original_process_thread_image=True)


def test_confirmed_termination_request_followed_by_timeout_is_not_repeated(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch)
    attempts = []
    api.terminate_process = lambda handle, code: attempts.append((handle, code))
    error = _capture_error(lambda: _launch(profile, api))
    child = error._process
    assert attempts == [(200, 1)] and child.never_resumed
    with pytest.raises(TimeoutError): child.cleanup_owned(api, timeout=0)
    assert attempts == [(200, 1)] and state['alive']
    state['alive'] = False
    assert child.cleanup_owned(api) == 1 and child._closed
    _record(tmp_path, api, events, state, trace, termination_requests=attempts,
            wait_timeout_did_not_repeat_termination=True)


@pytest.mark.parametrize('target', ['thread', 'process'])
def test_registry_bookkeeping_fault_after_native_child_close_keeps_original_unknown_without_reclose(monkeypatch, tmp_path, target):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    registry = registry_for(api)
    if target == 'thread':
        def install(child):
            wanted = child._thread_entry.seq
            class PopFailure(dict):
                def pop(self, key, *args):
                    if key == wanted: raise InjectedBookkeepingError('SOFTWARE registry pop after native close')
                    return super().pop(key, *args)
            registry._entries = PopFailure(registry._entries)
        trace['before_software_resume'] = install
        error = _capture_error(lambda: _launch(profile, api))
        child, entry = error._process, error._process._thread_entry
    else:
        child = _launch(profile, api)
        child.terminate(); child.wait(timeout=1)
        wanted = child._process_entry.seq
        class PopFailure(dict):
            def pop(self, key, *args):
                if key == wanted: raise InjectedBookkeepingError('SOFTWARE registry pop after native close')
                return super().pop(key, *args)
        registry._entries = PopFailure(registry._entries)
        with pytest.raises(InjectedBookkeepingError): child.close()
        entry = child._process_entry
    assert entry.state == 'UNKNOWN' and entry in registry.unresolved()
    before = list(events)
    with pytest.raises(Exception): child.close_owned(api)
    with pytest.raises(Exception): registry.retry_owned(entry, api)
    assert events == before and api._retained_children[id(child)] is child
    handle = 201 if target == 'thread' else 200
    assert events.count(('close', handle)) == 1
    _record(tmp_path, api, events, state, trace, registry_fault=target,
            native_close_once_original_unknown_preserved=True)


def test_terminal_owner_bookkeeping_failure_keeps_unique_original_and_never_recloses(monkeypatch, tmp_path):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    child = _launch(profile, api)
    class FailedRetirement(dict):
        def pop(self, key, *args):
            raise InjectedBookkeepingError('SOFTWARE original owner retirement bookkeeping')
    api._retained_children = FailedRetirement(api._retained_children)
    child.terminate(); child.wait(timeout=1)
    with pytest.raises(InjectedBookkeepingError): child.close()
    assert not child._closed and child._retirement_error is not None
    assert child._process_entry.state == 'RELEASED' and child.returncode == 1
    with pytest.raises(InjectedBookkeepingError): child.close()
    with pytest.raises(win.WindowsT037Error): _launch(profile, api)
    assert api._retained_children[id(child)] is child and events.count(('close', 200)) == 1
    _record(tmp_path, api, events, state, trace, terminal_owner_record_unknown_no_native_retry=True)


@pytest.mark.parametrize('target', ['process', 'thread'])
@pytest.mark.parametrize('route', ['entry', 'registry', 'captured_closer'])
def test_only_original_owner_can_reconcile_known_pre_sdk_release_deferral(monkeypatch, tmp_path, target, route):
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True, resume_result=2)
    error = _capture_error(lambda: _launch(profile, api))
    child = error._process
    entry = child._process_entry if target == 'process' else child._thread_entry
    original_events = list(events)
    if route == 'entry': action = lambda: entry.release_owned(api)
    elif route == 'registry': action = lambda: registry_for(api).release(api, entry.handle)
    else: action = lambda: entry.closer(entry.handle)
    with pytest.raises(win.WindowsT037Error): action()
    assert events == original_events and target not in child._release_attempts
    # No SDK closer was entered. Even an exit fact does not let a foreign
    # generic registry retry impersonate the explicit lifecycle owner.
    state['alive'] = False
    assert child.poll() == 1
    with pytest.raises(win.WindowsT037Error): registry_for(api).retry_owned(entry, api)
    assert events == original_events
    holder = object(); error.transfer_to(holder)
    with pytest.raises(win.WindowsT037Error): child.close_owned(api)
    child.close_owned(holder)
    assert child._closed and events.count(('close', 200)) == events.count(('close', 201)) == 1
    assert trace['creation_calls'] == 1 and trace['resume_calls'] == 1
    _record(tmp_path, api, events, state, trace, known_no_effect_deferral=target,
            only_actual_owner_terminal_release=True, release_route=route)


def test_existing_worker_original_handle_birth_consumer_accepts_same_retained_representation(monkeypatch, tmp_path):
    from comsol_mcp._java_worker import PersistentJavaWorker, JavaWorkerError
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    birth = (11644473600000 + 123456) * 10000 + 789
    api.process_times = lambda h: (10, None) if h == 777 else (birth, None)
    api._hash_image_handle = lambda *args: profile.worker_image_sha256
    original_image = api._open_image
    api._open_image = lambda path: (*original_image(path)[:4], path)
    api.process_image_path = lambda handle: profile._launch_image('worker').path
    factory = win.T037SessionProcessFactory(profile, 'software-ready-contract', api=api)
    factory._job = 300
    child = _launch(profile, api, factory=factory, role='worker')
    owner = win._native_g2_session_launch_owner(child)
    worker = PersistentJavaWorker.__new__(PersistentJavaWorker)
    worker._fixed_process = worker._process = child
    worker._fixed_launch_owner = owner
    worker._original_process_factory = worker._process_factory = factory
    worker._launch_argv = tuple(child.args)
    assert worker._fixed_owner_birth() == 123456
    api.process_times = lambda handle: (birth + 1, None)
    with pytest.raises(JavaWorkerError): worker._fixed_owner_birth()
    assert child is trace['precall_owner'] and not trace['live_process_closes']
    child.terminate(); child.wait(timeout=1); child.close()
    _record(tmp_path, api, events, state, trace, existing_worker_birth_getter_compatible=True,
            full_worker_startup_and_state_aware_close='NOT_RUN_PENDING_CONSUMER_LEASE')


@pytest.mark.parametrize('wrong_backing', [False, True])
def test_borrowed_original_image_uses_actual_query_file_hash_mapping_and_original_file_adapter(monkeypatch, tmp_path, wrong_backing):
    from types import SimpleNamespace
    from comsol_mcp import _windows_t037_child_evidence as evidence
    profile, api, events, state, trace = _case(monkeypatch, software_resume=True)
    query = evidence.WindowsChildEvidenceApi.__new__(evidence.WindowsChildEvidenceApi)
    positions, own_closes, file_reads = {}, [], []
    native = r'\Device\volume\server.exe'
    def text(storage, value):
        raw = value.encode('utf-16-le')
        C.memmove(storage, raw + b'\0\0', len(raw) + 2)
        return len(raw) // 2
    def info(handle, pointer):
        item = C.cast(pointer, C.POINTER(evidence._FileInfo)).contents
        item.attributes, item.creation, item.access, item.write = 32, 10, 11, 12
        item.volume, item.low, item.links = 1, 3, 1
        item.id_low = 99 if wrong_backing and handle == 44 else 9
        return 1
    def seek(handle, *args): positions[handle] = 0; return 1
    def read(handle, storage, cap, used, overlap):
        file_reads.append(handle)
        payload = b'abc' if positions[handle] == 0 else b''
        C.memmove(storage, payload, len(payload))
        C.cast(used, C.POINTER(evidence.U32)).contents.value = len(payload)
        positions[handle] += len(payload)
        return 1
    def process_name(handle, flags, storage, used):
        value = native if flags else profile._launch_image('server').path
        C.cast(used, C.POINTER(evidence.U32)).contents.value = text(storage, value)
        return 1
    def region(handle, address, pointer, size):
        item = C.cast(pointer, C.POINTER(evidence._MemoryInfo)).contents
        item.base, item.allocation_base, item.size, item.state, item.type = 4096, 4096, 8192, 0x1000, 0x1000000
        return size
    query.k = SimpleNamespace(GetFileInformationByHandle=info, GetFileType=lambda h: 1,
        GetFinalPathNameByHandleW=lambda h, storage, cap, flags: text(storage, native),
        SetFilePointerEx=seek, ReadFile=read, QueryFullProcessImageNameW=process_name,
        VirtualQueryEx=region, CreateFileW=lambda *args: 44,
        CloseHandle=lambda handle: own_closes.append(handle) or 1)
    query.ps = SimpleNamespace(GetMappedFileNameW=lambda h, base, storage, cap: text(storage, native))
    def inspect_pin(child):
        binding, pin = evidence.borrow_original_child(child, api=query)
        before = list(file_reads)
        foreign = evidence.PinnedImage(pin.entry, query, object())
        with pytest.raises(evidence.ObservationError): query.image(child._handle, foreign)
        assert file_reads == before and pin.entry.adapter is child._image_api
        observed = query.image(child._handle, pin)
        assert observed.pinned_stable and observed.backing_file_matches_pin is (not wrong_backing)
        assert observed.pinned_before.sha256 == hashlib.sha256(b'abc').hexdigest()
        assert observed.pinned_before.file_id == 9
        assert observed.mapped_backing_file.file_id == (99 if wrong_backing else 9)
        assert own_closes == [44] and pin.entry.state == 'ACQUIRED'
        assert evidence._borrow(binding) == (200, 201)
        trace['bridge_file_mapping_observed'] = True
    trace['before_software_resume'] = inspect_pin
    child = _launch(profile, api)
    assert trace['bridge_file_mapping_observed'] and own_closes == [44]
    assert file_reads.count(12) == 4 and file_reads.count(44) == 2
    child.terminate(); child.wait(timeout=1); child.close()
    _record(tmp_path, api, events, state, trace, bridge_mapped_backing_comparison=not wrong_backing,
            actual_file_parser_sha256=hashlib.sha256(b'abc').hexdigest(),
            observer_released_only_own_backing_handle=own_closes,
            executable_file_hash_is_not_all_loaded_memory_hash=True)


@pytest.mark.parametrize('drift',['output_slot','captured_closer'])
def test_post_return_binding_drift_blocks_fresh_birth_without_poll_or_cleanup(monkeypatch,tmp_path,drift):
    profile,api,events,state,trace=_case(monkeypatch,software_resume=True)
    child=_launch(profile,api)
    with pytest.raises(AttributeError,match='immutable'):child._owner=object()
    if drift=='output_slot':child._process_info.dwProcessId=999
    else:child._process_entry.closer=lambda h:pytest.fail('replacement child closer')
    before=list(events)
    with pytest.raises(win.WindowsT037Error):_launch(profile,api)
    with pytest.raises(win.WindowsT037Error):child.poll()
    gc.collect()
    assert trace['creation_calls']==1 and events==before and state['alive']
    assert api._retained_children[id(child)] is child
    _record(tmp_path,api,events,state,trace,post_return_binding_drift=drift,
            no_second_birth_poll_or_cleanup=True)


def test_unreturned_resume_unknown_cannot_use_direct_normal_termination_surface(monkeypatch,tmp_path):
    profile,api,events,state,trace=_case(monkeypatch,software_resume=True,resume_result=2)
    error=_capture_error(lambda:_launch(profile,api));child=error._process
    before=list(events)
    failure=None
    try:child.terminate()
    except win.WindowsT037Error as exc:failure=exc
    _record(tmp_path,api,events,state,trace,unreturned_resume_direct_terminate_refused=failure is not None,
            handle_events_before=before,handle_events_after=events)
    assert failure is not None and events==before and state['alive']
    with pytest.raises(win.WindowsT037Error):child.kill()
    with pytest.raises(win.WindowsT037Error):child.retire_owned(api)
