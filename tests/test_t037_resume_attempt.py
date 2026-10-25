"""Actual T037 factory/child consumer regressions; positive authority is test-only software."""
from __future__ import annotations

import copy
import threading

import pytest

from comsol_mcp import _windows_t037_resume as resume
from comsol_mcp import _windows_t037 as win
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._windows_owned_resources import registry_for
from tests import test_windows_t037_connected_b as connected

_TEST_AUDIT: list[dict[str, object]] = []


def _exception_chain(error):
    seen = set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ if error.__cause__ is not None else error.__context__


def _set_resume_leaf(api, leaf):
    """Retain the exact software-only SDK leaf return/exception for evidence."""
    leaf_code = getattr(leaf, "__code__", None)

    def tracked(*args, **kwargs):
        try:
            raw = leaf(*args, **kwargs)
        except BaseException as error:
            _TEST_AUDIT.append({
                "kind": "software_resume_leaf_exception",
                "leaf_co_filename": getattr(leaf_code, "co_filename", None),
                "leaf_co_name": getattr(leaf_code, "co_name", None),
                "exception": error,
            })
            raise
        _TEST_AUDIT.append({
            "kind": "software_resume_leaf_return",
            "leaf_co_filename": getattr(leaf_code, "co_filename", None),
            "leaf_co_name": getattr(leaf_code, "co_name", None),
            "raw_return": raw,
        })
        return raw

    api.k.ResumeThread = tracked
    return tracked


def _software_only_issuer(monkeypatch, issued, *, before_return=None):
    """Use the actual consumer under a named software-only qualification seam."""
    def issue(process, profile, _binding, _dacl):
        capability = resume._NativeResumeCapability(process, profile.sha256, seal=resume._SEAL)
        with resume._lock:
            resume._pending[id(capability)] = capability
        issued.append(capability)
        registry = process._registry
        job_binding = getattr(process._api, "_job_limit_bindings", {}).get(process._original_job)
        _TEST_AUDIT.append({
            "kind": "software_issuer_child", "process": process,
            "registry_entries_at_issue": tuple(
                (entry.label, entry.value, entry.state, entry.seq)
                for entry in registry._entries.values()),
            "process_entry_is_registered": any(
                entry is process._process_entry for entry in registry._entries.values()),
            "thread_entry_is_registered": any(
                entry is process._thread_entry for entry in registry._entries.values()),
            "job_entry_is_registered": (type(job_binding) is dict and any(
                entry is job_binding.get("entry") for entry in registry._entries.values())),
            "job_binding_state": job_binding.get("state") if type(job_binding) is dict else None,
        })
        if before_return is not None:
            before_return(process)
        return capability
    monkeypatch.setattr(resume, "issue_native_resume", issue)


def _retire_success(process, api, state, factory):
    state["alive"] = False
    owner = object()
    process.transfer_owner(api, owner)
    process.retire_owned(owner)
    factory.close()
    assert not registry_for(api).unresolved()


def _direct_api_fixture(monkeypatch):
    """Reuse the exact existing software SDK leaves for an original child API call."""
    from tests.test_windows_suspended_ownership import _create, _launch_api
    profile, api, events, state = _launch_api(monkeypatch)
    return profile, api, events, state, _create


def _retire_direct_success(process, api, state):
    state["alive"] = False
    process.retire_owned(api)
    api.close_job(300)
    assert not registry_for(api).unresolved()


def _factory_sdk_counts(events, sdk, state, native_calls):
    return (tuple(events), tuple(sdk.calls), tuple(sdk.closed), tuple(sdk.releases),
            tuple(state["primary_calls"]), tuple(native_calls))


@pytest.mark.parametrize("failure_point", [
    "thread-close", "preparation-close", "ancillary-close", "parent-close",
    "connected-b-settlement", "publication",
])
def test_actual_factory_raw_one_first_ownership_failure_is_sticky_unknown(monkeypatch, tmp_path, failure_point):
    """Exercise real factory cleanup boundaries with inert SDK leaves and raw Resume=1."""
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, sdk, command, events, state, _static
    ):
        issued = []
        targets = {}
        native_calls = []
        first_failure_snapshot = []
        failure = RuntimeError("SOFTWARE injected first post-Resume ownership failure: " + failure_point)

        def remember_roots(process):
            prep = process._birth_preparation
            entries = tuple(process._registry._entries.values())
            targets["thread"] = process._thread_handle
            targets["preparation"] = prep.entries[-1].value
            targets["ancillary"] = next(entry.value for entry in entries if entry.label == "attribute_list")
            targets["parent"] = process._parent_binding.handle
            targets["process"] = process

        original_close = api.k.CloseHandle
        original_api_close = api.close_handle
        original_delete = api.k.DeleteProcThreadAttributeList

        def maybe_fail(kind, value):
            normalized = getattr(value, "value", value)
            if failure_point == "thread-close" and kind == "CloseHandle" and normalized == getattr(targets.get("thread"), "value", targets.get("thread")):
                first_failure_snapshot.append(_factory_sdk_counts(events, sdk, state, native_calls))
                raise failure
            if failure_point == "preparation-close" and kind == "CloseHandle" and normalized == getattr(targets.get("preparation"), "value", targets.get("preparation")):
                first_failure_snapshot.append(_factory_sdk_counts(events, sdk, state, native_calls))
                raise failure
            if failure_point == "parent-close" and kind == "CloseHandle" and normalized == getattr(targets.get("parent"), "value", targets.get("parent")):
                first_failure_snapshot.append(_factory_sdk_counts(events, sdk, state, native_calls))
                raise failure
            if failure_point == "ancillary-close" and kind == "DeleteProcThreadAttributeList" and normalized == getattr(targets.get("ancillary"), "value", targets.get("ancillary")):
                first_failure_snapshot.append(_factory_sdk_counts(events, sdk, state, native_calls))

                raise failure

        def tracked_close(handle):
            native_calls.append(("CloseHandle", getattr(handle, "value", handle)))
            maybe_fail("CloseHandle", handle)
            return original_close(handle)

        def tracked_api_close(handle):
            native_calls.append(("launch_api.close_handle", getattr(handle, "value", handle)))
            maybe_fail("CloseHandle", handle)
            result = original_api_close(handle)
            if (failure_point == "connected-b-settlement"
                    and getattr(handle, "value", handle) == getattr(targets.get("parent"), "value", targets.get("parent"))):
                first_failure_snapshot.append(_factory_sdk_counts(events, sdk, state, native_calls))
            return result

        def tracked_delete(pointer):
            native_calls.append(("DeleteProcThreadAttributeList", getattr(pointer, "value", pointer)))
            maybe_fail("DeleteProcThreadAttributeList", pointer)
            return original_delete(pointer)

        api.k.CloseHandle = tracked_close
        api.close_handle = tracked_api_close
        api.k.DeleteProcThreadAttributeList = tracked_delete

        def issue_callback(process):
            process_box["process"] = process
            if failure_point in {"connected-b-settlement", "publication"}:
                def resume_leaf(_thread):
                    current = next(iter(api._retained_children.values()))
                    if failure_point == "connected-b-settlement":
                        object.__setattr__(current, "_connected_b_observer", object())
                    else:
                        class FailPublication(dict):
                            def __setitem__(self, key, value):
                                first_failure_snapshot.append(_factory_sdk_counts(events, sdk, state, native_calls))
                                raise failure
                        api._g2_session_launch_owners = FailPublication()
                    events.append("RESUME")
                    return 1
                _set_resume_leaf(api, resume_leaf)
            else:
                _set_resume_leaf(api, lambda _thread: events.append("RESUME") or 1)

        process_box = {}
        _software_only_issuer(monkeypatch, issued, before_return=remember_roots)
        # Install the post-resume callback after the actual issuer has retained
        # the original child but before the factory calls the consumer.
        original_issue = resume.issue_native_resume

        def issue_with_callback(process, profile, binding, dacl):
            capability = original_issue(process, profile, binding, dacl)
            issue_callback(process)
            return capability

        monkeypatch.setattr(resume, "issue_native_resume", issue_with_callback)
        try:
            caught = None
            try:
                process_result = factory(command)
            except BaseException as error:
                caught = error
                process_result = None
            process = (caught._process if isinstance(caught, win.NativeSuspendedChildLaunchError)
                       else process_result if process_result is not None else process_box.get("process"))
            attempt = getattr(process, "_resume_attempt", None)
            current_counters = _factory_sdk_counts(events, sdk, state, native_calls)
            _TEST_AUDIT.append({"kind": "resume_one_first_ownership_failure_observed",
                                "failure_point": failure_point, "process": process,
                                "caught_type": None if caught is None else type(caught).__module__ + "." + type(caught).__qualname__,
                                "caught_text": None if caught is None else str(caught),
                                "attempt_state": getattr(attempt, "state", None),
                                "ownership_error_type": type(getattr(attempt, "ownership_error", None)).__name__
                                    if attempt is not None and getattr(attempt, "ownership_error", None) is not None else None,
                                "ownership_error_stage": getattr(attempt, "ownership_error_stage", None),
                                "ownership_error_entry_label": getattr(
                                    getattr(attempt, "ownership_error_entry", None), "label", None),
                                "ownership_error_entry_state": getattr(
                                    getattr(attempt, "ownership_error_entry", None), "state", None),
                                "ownership_error_owner_type": (type(attempt.ownership_error_owner).__module__ + "."
                                    + type(attempt.ownership_error_owner).__qualname__
                                    if attempt is not None and getattr(attempt, "ownership_error_owner", None) is not None
                                    else None),
                                "counter_snapshot_at_failure": first_failure_snapshot[0] if first_failure_snapshot else None,
                                "counters_after_factory": current_counters,
                                "thread_state": getattr(getattr(process, "_thread_entry", None), "state", None),
                                "process_state": getattr(getattr(process, "_process_entry", None), "state", None)})
            assert caught is not None and isinstance(caught, win.NativeSuspendedChildLaunchError)
            assert process is process_box["process"] and process is caught._process
            assert attempt.raw_return is not None and type(attempt.raw_return) is int and attempt.raw_return == 1
            assert attempt.raw_return_captured and attempt.previous_count == 1 and attempt.entered is True
            assert attempt.state == "UNKNOWN"
            witness_counters = _factory_sdk_counts(events, sdk, state, native_calls)
            witness_eligible = resume.original_no_effect_attempt_witness(process)
            _TEST_AUDIT.append({"kind": "unknown_attempt_refused_by_no_effect_witness",
                                "failure_point": failure_point,
                                "attempt_state": attempt.state,
                                "witness_eligible": witness_eligible})
            assert witness_eligible is False
            assert _factory_sdk_counts(events, sdk, state, native_calls) == witness_counters
            if failure_point == "connected-b-settlement":
                assert isinstance(attempt.ownership_error, ExecutionContractError)
                assert attempt.ownership_error.code == "B_ORIGINAL_CONTEXT_CHANGED"
                assert attempt.ownership_error_stage == "factory_connected_B_release"
            else:
                assert attempt.ownership_error is failure
            assert process._resume_confirmed is True and process._returned is False
            assert api._retained_children.get(id(process)) is process
            assert attempt.dispatch_reserved is True
            assert attempt.registry._resume_dispatch_reservation is attempt
            assert process._process_entry._resume_dispatch_reservation is attempt
            assert issued[0].used is True and id(issued[0]) not in resume._pending
            assert first_failure_snapshot
            assert current_counters == first_failure_snapshot[0], (
                f"SDK activity continued after first {failure_point} uncertainty"
            )
        finally:
            monkeypatch.setattr(daemon, "close", lambda: None)


def test_actual_consumer_refuses_wrong_original_api_owner_before_consuming(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, _state, _static
    ):
        issued = []
        wrong_owner = object()
        process_box = []
        resume_calls = []

        def move_original_owner(process):
            process_box.append(process)
            process.transfer_owner(api, wrong_owner)

        _software_only_issuer(monkeypatch, issued, before_return=move_original_owner)
        _set_resume_leaf(api, lambda _thread: resume_calls.append("entered") or events.append("RESUME") or 1)
        caught = None
        try:
            try:
                factory(command)
            except BaseException as error:
                caught = error
            process = process_box[0]
            _TEST_AUDIT.append({"kind": "wrong_original_owner_before_consume_observed",
                                "process": process,
                                "caught_type": None if caught is None else type(caught).__module__ + "." + type(caught).__qualname__,
                                "resume_attempt_state": getattr(getattr(process, "_resume_attempt", None), "state", None),
                                "resume_calls": tuple(resume_calls),
                                "capability_used": issued[0].used,
                                "capability_pending": id(issued[0]) in resume._pending,
                                "resume_epoch": process._resume_epoch})
            assert caught is not None, "consumer dispatched after original API ownership had transferred"
            assert process._owner is wrong_owner
            assert process._resume_attempt is None
            assert process._resume_epoch == 0 and process._resume_attempted is False
            assert issued[0].used is False and id(issued[0]) in resume._pending
            assert not resume_calls and "RESUME" not in events
            _TEST_AUDIT.append({"kind": "wrong_original_owner_refused_before_consume",
                                "process": process, "resume_calls": tuple(resume_calls),
                                "capability_used": issued[0].used,
                                "resume_epoch": process._resume_epoch})
        finally:
            if issued:
                resume._pending.pop(id(issued[0]), None)
            monkeypatch.setattr(daemon, "close", lambda: None)


def test_actual_consumer_rechecks_original_api_owner_after_job_query(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, _state, _static
    ):
        issued = []
        wrong_owner = object()
        seen = []
        from comsol_mcp import _windows_job_limits as limits
        original = limits.require_configured_job

        def transfer_during_actual_job_query(api_arg, job, profile, *, for_child_birth=True):
            result = original(api_arg, job, profile, for_child_birth=for_child_birth)
            if for_child_birth is False:
                process = next(iter(api._retained_children.values()))
                seen.append(process)
                object.__setattr__(process, "_owner", wrong_owner)
            return result

        _software_only_issuer(monkeypatch, issued)
        monkeypatch.setattr(limits, "require_configured_job", transfer_during_actual_job_query)
        resume_calls = []
        _set_resume_leaf(api, lambda _thread: resume_calls.append("entered") or events.append("RESUME") or 1)
        caught = None
        try:
            try:
                factory(command)
            except BaseException as error:
                caught = error
            assert seen, "real original Job query did not reach the post-query owner recheck"
            process = seen[0]
            _TEST_AUDIT.append({"kind": "wrong_original_owner_after_job_query_observed",
                                "process": process,
                                "caught_type": None if caught is None else type(caught).__module__ + "." + type(caught).__qualname__,
                                "attempt_state": getattr(getattr(process, "_resume_attempt", None), "state", None),
                                "attempt_entered": getattr(getattr(process, "_resume_attempt", None), "entered", None),
                                "resume_calls": tuple(resume_calls),
                                "capability_used": issued[0].used,
                                "capability_pending": id(issued[0]) in resume._pending})
            assert caught is not None, "consumer dispatched after original API owner changed during Job query"
            assert process._owner is wrong_owner
            assert process._resume_attempt is not None
            assert process._resume_attempt.state == "NO_EFFECT"
            assert process._resume_attempt.entered is False
            assert process._resume_attempt.raw_return_captured is False
            assert issued[0].used is True and id(issued[0]) not in resume._pending
            assert not resume_calls and "RESUME" not in events
            _TEST_AUDIT.append({"kind": "wrong_original_owner_refused_after_job_query",
                                "process": process,
                                "attempt_state": process._resume_attempt.state,
                                "resume_calls": tuple(resume_calls),
                                "capability_used": issued[0].used})
        finally:
            if seen:
                object.__setattr__(seen[0], "_owner", api)
            monkeypatch.setattr(daemon, "close", lambda: None)


def test_actual_original_child_api_consumer_releases_global_lock_before_sdk(monkeypatch, tmp_path):
    profile, api, events, state, create = _direct_api_fixture(monkeypatch)
    issued = []
    _software_only_issuer(monkeypatch, issued)
    lock_probe = []
    original_query = api.k.QueryInformationJobObject

    def probe_lock(label):
        acquired = []

        def contender():
            value = resume._lock.acquire(blocking=False)
            acquired.append(value)
            if value:
                resume._lock.release()

        worker = threading.Thread(target=contender, name="resume-original-lock-probe")
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        lock_probe.append((label, acquired[0] if acquired else None))

    def query_leaf(*args):
        probe_lock("job_query")
        return original_query(*args)

    api.k.QueryInformationJobObject = query_leaf

    def resume_leaf(_thread):
        probe_lock("ResumeThread")
        events.append("RESUME")
        return 1

    _set_resume_leaf(api, resume_leaf)
    process = None
    try:
        process = create(profile, api)
        assert len(issued) == 1
        _TEST_AUDIT.append({"kind": "issuer_global_lock_probe_observations",
                            "observations": tuple(lock_probe)})
        assert lock_probe and all(value is True for _label, value in lock_probe), (
            f"Job query and ResumeThread must run outside issuer lock; observations={lock_probe!r}"
        )
        attempt = getattr(process, "_resume_attempt", None)
        if attempt is not None:
            assert type(attempt.raw_return) is int and attempt.raw_return == 1
            assert attempt.raw_return_captured and attempt.state == "HANDED_OFF"
        assert process._resume_confirmed and process._returned
        assert process._thread_entry.state == "RELEASED"
        before_repeat = tuple(events)
        with pytest.raises(ExecutionContractError):
            resume.resume_native_child(process, issued[0])
        assert tuple(events) == before_repeat and events.count("RESUME") == 1
        _TEST_AUDIT.append({"kind": "direct_original_child_api_consumer_returned",
                            "process": process, "issued_capability": issued[0]})
    finally:
        if process is not None and process._resume_confirmed and process._returned:
            _retire_direct_success(process, api, state)


def test_direct_original_child_dispatch_reservation_blocks_close_and_reentry(monkeypatch, tmp_path):
    profile, api, events, state, create = _direct_api_fixture(monkeypatch)
    issued = []
    _software_only_issuer(monkeypatch, issued)
    close_events = []
    query_calls = []
    original_close = api.close_handle
    original_query = api.k.QueryInformationJobObject

    def tracked_close(handle):
        close_events.append(handle.value if hasattr(handle, "value") else handle)
        return original_close(handle)

    def tracked_query(*args):
        query_calls.append("QueryInformationJobObject")
        return original_query(*args)

    api.close_handle = tracked_close
    api.k.QueryInformationJobObject = tracked_query
    process_box = []
    ancillary_box = []
    errors = {name: [] for name in (
        "transfer", "thread_release", "process_release", "ancillary_release",
        "job_close", "consumer_reentry", "cleanup", "retire", "thread_release_lifecycle",
        "poll", "wait", "terminate", "close",
    )}
    callback_snapshot = []

    def resume_leaf(_thread):
        process = next(iter(api._retained_children.values()))
        process_box.append(process)
        attempt = process._resume_attempt
        ancillary = next((entry for entry in attempt.registry_entries
                          if entry.state == "ACQUIRED"
                          and not any(entry is reserved for reserved in attempt.reserved_entries)), None)
        assert ancillary is not None, "original child API fixture must retain an ancillary registry entry"
        ancillary_box.append(ancillary)
        before = (tuple(events), tuple(close_events), len(query_calls))
        callback_snapshot.append(before)

        def contender():
            operations = (
                ("transfer", lambda: process.transfer_owner(api, object())),
                ("thread_release", lambda: process._thread_entry.release_owned(api)),
                ("process_release", lambda: process._process_entry.release_owned(api)),
                ("ancillary_release", lambda: ancillary.release_owned(api)),
                ("job_close", lambda: api.close_job(300)),
                ("consumer_reentry", lambda: resume.resume_native_child(process, issued[0])),
                ("cleanup", lambda: process.cleanup_owned(api)),
                ("retire", lambda: process.retire_owned(api)),
                ("thread_release_lifecycle", process.release_main_thread),
                ("poll", process.poll),
                ("wait", lambda: process.wait(timeout=0)),
                ("terminate", process.terminate),
                ("close", process.close),
            )
            for name, operation in operations:
                try:
                    operation()
                except BaseException as error:
                    errors[name].append(error)

        worker = threading.Thread(target=contender, name="direct-resume-reservation-contender")
        worker.start()
        worker.join(2)
        assert not worker.is_alive(), "dispatch callback contender deadlocked on lifecycle ownership"
        assert all(errors[name] and isinstance(errors[name][0], ExecutionContractError)
                   for name in errors)
        assert (tuple(events), tuple(close_events), len(query_calls)) == before
        events.append("RESUME")
        return 1

    _set_resume_leaf(api, resume_leaf)
    process = create(profile, api)
    try:
        assert process_box == [process]
        assert process._resume_confirmed and process._returned
        assert process._owner is api
        assert process._thread_entry.state == "RELEASED"
        assert process._process_entry.state == "ACQUIRED"
        assert ancillary_box[0].state == "RELEASED"
        assert close_events.count(201) == 1
        assert close_events.count(200) == 0
        assert errors["consumer_reentry"] and isinstance(errors["consumer_reentry"][0], ExecutionContractError)
        _TEST_AUDIT.append({"kind": "direct_dispatch_reservation_reentry_blocked",
                            "process": process,
                            "callback_snapshot": callback_snapshot[0],
                            "blocked_operations": tuple(sorted(errors))})
    finally:
        if process._resume_confirmed and process._returned:
            _retire_direct_success(process, api, state)


def test_real_issuer_refuses_on_original_child_without_registering_authority(monkeypatch, tmp_path):
    from tests.test_windows_suspended_ownership import (
        test_actual_production_launch_gate_refuses_resume_and_confirms_exact_cleanup,
    )
    before = dict(resume._pending)
    test_actual_production_launch_gate_refuses_resume_and_confirms_exact_cleanup(monkeypatch, False)
    assert resume._pending == before
    issuer_code = resume.issue_native_resume.__code__
    _TEST_AUDIT.append({"kind": "real_issuer_original_child_refusal",
                        "issuer_co_filename": issuer_code.co_filename,
                        "issuer_co_name": issuer_code.co_name,
                        "fixture_co_filename": test_actual_production_launch_gate_refuses_resume_and_confirms_exact_cleanup.__code__.co_filename})


def test_actual_consumer_releases_global_issuer_lock_before_resume_sdk(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        _daemon, factory, api, sdk, command, events, state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        lock_probe = []
        original_query = api.k.QueryInformationJobObject

        def query_leaf(*args):
            if getattr(api, "_last_connected_b_observer", None) is not None:
                acquired = []
                def probe_query_lock():
                    value = resume._lock.acquire(blocking=False)
                    acquired.append(value)
                    if value:
                        resume._lock.release()
                contender = threading.Thread(target=probe_query_lock, name="job-query-lock-probe")
                contender.start()
                contender.join(2)
                assert not contender.is_alive()
                lock_probe.extend(acquired)
            return original_query(*args)

        api.k.QueryInformationJobObject = query_leaf

        def resume_leaf(_thread):
            def probe():
                acquired = resume._lock.acquire(blocking=False)
                lock_probe.append(acquired)
                if acquired:
                    resume._lock.release()
            contender = threading.Thread(target=probe, name="resume-lock-probe")
            contender.start()
            contender.join(2)
            assert not contender.is_alive()
            events.append("RESUME")
            return 1

        _set_resume_leaf(api, resume_leaf)
        process = factory(command)
        try:
            assert lock_probe and all(lock_probe), "Job and ResumeThread SDK calls must run outside issuer lock"
            assert len(issued) == 1
            if hasattr(process, "_resume_attempt"):
                attempt = process._resume_attempt
                assert attempt.raw_return == 1 and attempt.entered is True
            assert process._resume_confirmed and process._returned
            after_first_resume = tuple(event for event in events if event == "RESUME")
            with pytest.raises(ExecutionContractError):
                resume.resume_native_child(process, issued[0])
            assert tuple(event for event in events if event == "RESUME") == after_first_resume
        finally:
            _retire_success(process, api, state, factory)


def test_dispatch_reservation_rejects_concurrent_transfer_and_registry_close(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        _daemon, factory, api, sdk, command, events, state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        entered = threading.Event()
        close_done = threading.Event()
        transfer_errors = []
        process_box = []
        ancillary_box = []
        thread_close_errors = []
        process_close_errors = []
        job_close_errors = []
        ancillary_close_errors = []
        factory_close_errors = []
        reentry_errors = []
        lifecycle_errors = []
        callback_snapshots = []
        close_events = []
        original_close = api.close_handle

        def tracked_close(handle):
            close_events.append(handle.value if hasattr(handle, "value") else handle)
            return original_close(handle)

        api.close_handle = tracked_close

        def resume_leaf(_thread):
            process = next(iter(api._retained_children.values()))
            process_box.append(process)
            attempt = process._resume_attempt
            ancillary = next((entry for entry in attempt.registry_entries
                              if entry.label == "attribute_list" and entry.state == "ACQUIRED"
                              and not any(entry is reserved for reserved in attempt.reserved_entries)), None)
            assert ancillary is not None, "fixture must expose an original ancillary registry entry"
            ancillary_box.append(ancillary)
            before = (tuple(events), tuple(sdk.calls), tuple(sdk.closed), tuple(state["primary_calls"]))
            callback_snapshots.append(before)

            def contender():
                try:
                    process.transfer_owner(api, object())
                except BaseException as error:
                    transfer_errors.append(error)
                try:
                    process._thread_entry.release_owned(api)
                except BaseException as error:
                    thread_close_errors.append(error)
                try:
                    process._process_entry.release_owned(api)
                except BaseException as error:
                    process_close_errors.append(error)
                try:
                    ancillary.release_owned(api)
                except BaseException as error:
                    ancillary_close_errors.append(error)
                try:
                    api.close_job(factory._job)
                except BaseException as error:
                    job_close_errors.append(error)
                try:
                    factory._close_owned_resources()
                except BaseException as error:
                    factory_close_errors.append(error)
                try:
                    resume.resume_native_child(process, issued[0])
                except BaseException as error:
                    reentry_errors.append(error)
                for operation in (process.cleanup_owned, process.retire_owned,
                                  process.release_main_thread, process.poll,
                                  lambda: process.wait(timeout=0), process.terminate,
                                  process.close):
                    try:
                        if operation in (process.cleanup_owned, process.retire_owned):
                            operation(api)
                        else:
                            operation()
                    except BaseException as error:
                        lifecycle_errors.append(error)
                close_done.set()

            entered.set()
            contender_thread = threading.Thread(target=contender, name="resume-close-contender")
            contender_thread.start()
            assert close_done.wait(2)
            contender_thread.join(2)
            assert not contender_thread.is_alive()
            events.append("RESUME")
            return 1

        _set_resume_leaf(api, resume_leaf)
        process = factory(command)
        try:
            assert entered.is_set() and process_box == [process]
            assert transfer_errors and isinstance(transfer_errors[0], ExecutionContractError)
            assert (thread_close_errors and process_close_errors and ancillary_close_errors
                    and job_close_errors and factory_close_errors)
            assert reentry_errors and isinstance(reentry_errors[0], ExecutionContractError)
            assert len(lifecycle_errors) == 7
            assert all(isinstance(error, ExecutionContractError) for error in lifecycle_errors)
            assert process._owner is api
            _TEST_AUDIT.append({"kind": "factory_dispatch_reservation_completed",
                                "process": process,
                                "process_entry_state": process._process_entry.state,
                                "thread_entry_state": process._thread_entry.state,
                                "owner_is_original_api": process._owner is api,
                                "main_thread_release_count": close_events.count(201)})
            assert process._process_entry.state == "ACQUIRED"
            assert process._thread_entry.state == "RELEASED"
            assert ancillary_box[0].state == "RELEASED"
            assert close_events.count(201) == 1  # only the authorized normal main-thread release
            assert close_events.count(300) == 0  # Job close cannot race the active child dispatch.
            assert tuple(sdk.calls) == callback_snapshots[0][1]
            assert tuple(sdk.closed) == callback_snapshots[0][2]
            assert tuple(state["primary_calls"]) == callback_snapshots[0][3]
            if hasattr(process, "_resume_attempt"):
                assert process._resume_attempt.state in {"RETURNED_ONE", "HANDED_OFF"}
        finally:
            if process._owner is not api:
                process.transfer_owner(process._owner, api)
            _retire_success(process, api, state, factory)


class _IntLikeOne:
    def __init__(self):
        self.int_calls = 0

    def __int__(self):
        self.int_calls += 1
        return 1


@pytest.mark.parametrize("raw", [True, _IntLikeOne(), 0, 2, 0xFFFFFFFF],
                         ids=["bool-true", "int-like-object", "previous-zero", "previous-two", "previous-dword-minus-one"])
def test_direct_original_child_unknown_return_stays_rooted_without_followup_sdk(monkeypatch, tmp_path, raw):
    profile, api, events, _state, create = _direct_api_fixture(monkeypatch)
    issued = []
    _software_only_issuer(monkeypatch, issued)
    query_calls = []
    original_query = api.k.QueryInformationJobObject

    def tracked_query(*args):
        query_calls.append("QueryInformationJobObject")
        return original_query(*args)

    api.k.QueryInformationJobObject = tracked_query
    closed = []
    original_close = api.close_handle

    def tracked_close(handle):
        closed.append(handle.value if hasattr(handle, "value") else handle)
        return original_close(handle)

    api.close_handle = tracked_close
    leaf_snapshot = []

    def resume_leaf(_thread):
        leaf_snapshot.append((tuple(events), len(query_calls), tuple(closed)))
        events.append("RESUME")
        return raw

    _set_resume_leaf(api, resume_leaf)
    with pytest.raises(win.NativeSuspendedChildLaunchError) as captured:
        create(profile, api)

    process = captured.value._process
    attempt = process._resume_attempt
    assert captured.value.resume_attempt is attempt
    assert process._owner is api and process._returned is False
    assert process._resume_confirmed is False and process._resume_attempted is True
    assert process.never_resumed is False
    assert attempt.state == "UNKNOWN" and attempt.entered is True
    assert attempt.raw_return_captured is True
    assert attempt.raw_return is raw and attempt.raw_return_storage[0] is raw
    assert attempt.return_transfer == "RAW_RETURN_CAPTURED"
    assert api._retained_children.get(id(process)) is process
    assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
    assert issued[0].used is True and id(issued[0]) not in resume._pending
    assert events == list(leaf_snapshot[0][0]) + ["RESUME"]
    assert len(query_calls) == leaf_snapshot[0][1]
    assert tuple(closed) == leaf_snapshot[0][2]
    assert events.count("RESUME") == 1
    if type(raw) is int:
        assert attempt.previous_count == raw
    else:
        assert attempt.previous_count is None
    if isinstance(raw, _IntLikeOne):
        assert raw.int_calls == 0

    before_repeat = (tuple(events), len(query_calls), tuple(closed))
    with pytest.raises(ExecutionContractError):
        resume.resume_native_child(process, issued[0])
    assert (tuple(events), len(query_calls), tuple(closed)) == before_repeat
    _TEST_AUDIT.append({"kind": "direct_unknown_return_remains_rooted",
                        "process": process, "raw_return": raw,
                        "raw_return_storage_value": attempt.raw_return_storage[0],
                        "previous_count": attempt.previous_count,
                        "return_transfer": attempt.return_transfer,
                        "query_count_at_leaf": leaf_snapshot[0][1],
                        "close_handles_at_leaf": leaf_snapshot[0][2]})


def test_direct_original_child_exact_dword_one_is_success(monkeypatch, tmp_path):
    profile, api, events, state, create = _direct_api_fixture(monkeypatch)
    issued = []
    _software_only_issuer(monkeypatch, issued)
    raw = api.W.DWORD(1)

    def resume_leaf(_thread):
        events.append("RESUME")
        return raw

    _set_resume_leaf(api, resume_leaf)
    process = create(profile, api)
    try:
        attempt = process._resume_attempt
        assert type(raw) is api.W.DWORD and raw.value == 1
        assert attempt.raw_return is raw and attempt.raw_return_storage[0] is raw
        assert attempt.raw_return_captured is True and attempt.previous_count == 1
        assert attempt.state == "HANDED_OFF" and attempt.return_transfer == "RAW_RETURN_CAPTURED"
        assert process._resume_confirmed and process._returned
        assert process._thread_entry.state == "RELEASED"
        assert issued[0].used is True and id(issued[0]) not in resume._pending
        _TEST_AUDIT.append({"kind": "direct_exact_dword_one_succeeded",
                            "process": process, "raw_return": raw,
                            "raw_return_storage_value": attempt.raw_return_storage[0],
                            "previous_count": attempt.previous_count,
                            "return_transfer": attempt.return_transfer})
    finally:
        if process._resume_confirmed and process._returned:
            _retire_direct_success(process, api, state)


def test_direct_original_child_resume_exception_is_sticky_unknown(monkeypatch, tmp_path):
    profile, api, events, _state, create = _direct_api_fixture(monkeypatch)
    issued = []
    _software_only_issuer(monkeypatch, issued)
    query_calls = []
    original_query = api.k.QueryInformationJobObject

    def tracked_query(*args):
        query_calls.append("QueryInformationJobObject")
        return original_query(*args)

    api.k.QueryInformationJobObject = tracked_query
    closed = []
    original_close = api.close_handle

    def tracked_close(handle):
        closed.append(handle.value if hasattr(handle, "value") else handle)
        return original_close(handle)

    api.close_handle = tracked_close
    leaf_snapshot = []
    resume_error = RuntimeError("software-only ResumeThread leaf raised after call entry")

    def resume_leaf(_thread):
        leaf_snapshot.append((tuple(events), len(query_calls), tuple(closed)))
        events.append("RESUME")
        raise resume_error

    _set_resume_leaf(api, resume_leaf)
    with pytest.raises(win.NativeSuspendedChildLaunchError) as captured:
        create(profile, api)

    process = captured.value._process
    attempt = process._resume_attempt
    assert captured.value.resume_attempt is attempt
    assert attempt.entered is True and attempt.state == "UNKNOWN"
    assert attempt.call_error is resume_error
    assert attempt.return_transfer == "CALL_RAISED_OR_TRANSFER_UNKNOWN"
    assert attempt.raw_return_captured is False
    assert process._owner is api and process._returned is False
    assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
    assert api._retained_children.get(id(process)) is process
    assert events == list(leaf_snapshot[0][0]) + ["RESUME"]
    assert len(query_calls) == leaf_snapshot[0][1]
    assert tuple(closed) == leaf_snapshot[0][2]
    _TEST_AUDIT.append({"kind": "direct_resume_exception_sticky_unknown",
                        "process": process, "exception": resume_error,
                        "query_count_at_leaf": leaf_snapshot[0][1],
                        "close_handles_at_leaf": leaf_snapshot[0][2]})


@pytest.mark.parametrize("failure_stage", ["storage_assignment", "capture_raw_return"])
def test_direct_original_child_return_transfer_failure_retains_raw_root(monkeypatch, tmp_path, failure_stage):
    profile, api, events, _state, create = _direct_api_fixture(monkeypatch)
    issued = []
    _software_only_issuer(monkeypatch, issued)
    query_calls = []
    original_query = api.k.QueryInformationJobObject

    def tracked_query(*args):
        query_calls.append("QueryInformationJobObject")
        return original_query(*args)

    api.k.QueryInformationJobObject = tracked_query
    closed = []
    original_close = api.close_handle

    def tracked_close(handle):
        closed.append(handle.value if hasattr(handle, "value") else handle)
        return original_close(handle)

    api.close_handle = tracked_close
    raw = object()
    transfer_error = RuntimeError("software-only raw-return transfer fault")
    leaf_snapshot = []

    class _StoreThenRaise(list):
        def __init__(self):
            super().__init__([None])
            self.stored = None

        def __setitem__(self, index, value):
            self.stored = value
            raise transfer_error

    def fail_capture(_attempt, _value):
        raise transfer_error

    if failure_stage == "capture_raw_return":
        monkeypatch.setattr(resume._OriginalResumeAttempt, "capture_raw_return", fail_capture)

    def resume_leaf(_thread):
        process = next(iter(api._retained_children.values()))
        if failure_stage == "storage_assignment":
            process._resume_attempt.raw_return_storage = _StoreThenRaise()
        leaf_snapshot.append((tuple(events), len(query_calls), tuple(closed)))
        events.append("RESUME")
        return raw

    _set_resume_leaf(api, resume_leaf)
    with pytest.raises(win.NativeSuspendedChildLaunchError) as captured:
        create(profile, api)

    process = captured.value._process
    attempt = process._resume_attempt
    storage_root = (attempt.raw_return_storage.stored if failure_stage == "storage_assignment"
                    else attempt.raw_return_storage[0])
    assert captured.value.resume_attempt is attempt
    assert attempt.state == "UNKNOWN" and attempt.entered is True
    assert attempt.transfer_error is transfer_error
    assert attempt.return_transfer == "RETURN_TRANSFER_FAILED"
    assert storage_root is raw
    assert attempt.raw_return is None
    assert attempt.raw_return_captured is (failure_stage == "capture_raw_return")
    assert process._owner is api and process._returned is False
    assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
    assert api._retained_children.get(id(process)) is process
    assert events == list(leaf_snapshot[0][0]) + ["RESUME"]
    assert len(query_calls) == leaf_snapshot[0][1]
    assert tuple(closed) == leaf_snapshot[0][2]
    _TEST_AUDIT.append({"kind": "direct_return_transfer_failure_retained_raw",
                        "process": process, "raw_return": raw,
                        "raw_return_storage_value": storage_root,
                        "return_transfer": attempt.return_transfer,
                        "failure_stage": failure_stage,
                        "exception": transfer_error})


@pytest.mark.parametrize("raw", [True, _IntLikeOne()], ids=["bool", "int-like-object"])
def test_non_exact_resume_return_type_is_retained_unknown_without_cleanup(monkeypatch, tmp_path, raw):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, sdk, command, events, state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        calls = []

        def resume_leaf(_thread):
            calls.append("entered")
            events.append("RESUME")
            return raw

        _set_resume_leaf(api, resume_leaf)
        try:
            with pytest.raises(win.NativeSuspendedChildLaunchError) as caught:
                factory(command)
            process = caught.value._process
            assert process is api._retained_children[id(process)]
            attempt = process._resume_attempt
            assert attempt.raw_return is raw
            assert attempt.state == "UNKNOWN" and attempt.entered is True
            assert process._resume_attempted and not process._resume_confirmed
            assert process.never_resumed is False and not win.is_native_suspended_child(process)
            assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
            assert process._birth_preparation.state == "BOUND"
            assert not process._parent_binding.closed
            assert process._connected_b_observer.state == "OBSERVED"
            assert factory._held_static_a.state == "UNKNOWN"
            assert calls == ["entered"]
            assert len([event for event in events if event == "RESUME"]) == 1
            after_dispatch = (list(events), list(state["primary_calls"]))
            monkeypatch.setattr(daemon, "close", lambda: None)
            with pytest.raises(ExecutionContractError):
                process.transfer_owner(api, object())
            with pytest.raises(ExecutionContractError):
                resume.resume_native_child(process, issued[0])
            assert after_dispatch == (events, state["primary_calls"])
            before_forbidden_release = (tuple(events), tuple(sdk.calls), tuple(sdk.closed),
                                        tuple(state["primary_calls"]))
            for entry in (process._thread_entry, process._process_entry):
                with pytest.raises(ExecutionContractError):
                    entry.release_owned(api)
            with pytest.raises(ExecutionContractError):
                api.close_job(factory._job)
            with pytest.raises(ExecutionContractError):
                factory._close_owned_resources()
            with pytest.raises(ExecutionContractError):
                process.cleanup_owned(api)
            assert before_forbidden_release == (tuple(events), tuple(sdk.calls), tuple(sdk.closed),
                                                tuple(state["primary_calls"]))
        except BaseException:
            # Baseline success via int(bool)/int(obj) must still be retired so
            # the red control leaves no fixture-owned synthetic child behind.
            children = tuple(api._retained_children.values())
            for process in children:
                if process._returned and not process._closed:
                    _retire_success(process, api, state, factory)
            raise


@pytest.mark.parametrize("raw", [0, 2, 0xFFFFFFFF], ids=["zero", "two", "dword-minus-one"])
def test_non_one_previous_count_stops_all_factory_finally_sdk_cleanup(monkeypatch, tmp_path, raw):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        calls = []

        def resume_leaf(_thread):
            calls.append(raw)
            events.append("RESUME")
            return raw

        _set_resume_leaf(api, resume_leaf)
        with pytest.raises(win.NativeSuspendedChildLaunchError) as caught:
            factory(command)
        process = caught.value._process
        attempt = process._resume_attempt
        assert attempt.raw_return == raw and attempt.state == "UNKNOWN"
        assert calls == [raw]
        assert process._resume_confirmed is False and process._resume_attempted is True
        assert process.never_resumed is False and not win.is_native_suspended_child(process)
        assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
        assert process._birth_preparation.state == "BOUND"
        assert not process._parent_binding.closed
        assert process._connected_b_observer.state == "OBSERVED"
        assert factory._held_static_a.state == "UNKNOWN"
        assert state["alive"] is True
        after_dispatch = (list(events), list(state["primary_calls"]))
        monkeypatch.setattr(daemon, "close", lambda: None)
        with pytest.raises(ExecutionContractError):
            factory.close()
        with pytest.raises(ExecutionContractError):
            resume.resume_native_child(process, issued[0])
        assert after_dispatch == (events, state["primary_calls"])


def test_resume_call_exception_is_sticky_unknown_and_preserves_original_child(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        failure = RuntimeError("SOFTWARE ResumeThread return-transfer exception")
        calls = []

        def resume_leaf(_thread):
            calls.append("entered")
            events.append("RESUME")
            raise failure

        _set_resume_leaf(api, resume_leaf)
        with pytest.raises(win.NativeSuspendedChildLaunchError) as caught:
            factory(command)
        process = caught.value._process
        attempt = process._resume_attempt
        assert attempt.call_error is failure and attempt.raw_return is None
        assert attempt.state == "UNKNOWN" and attempt.entered is True
        assert calls == ["entered"] and process._resume_attempted
        assert process.never_resumed is False and not win.is_native_suspended_child(process)
        assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
        assert process._birth_preparation.state == "BOUND" and not process._parent_binding.closed
        assert factory._held_static_a.state == "UNKNOWN"
        monkeypatch.setattr(daemon, "close", lambda: None)


@pytest.mark.parametrize("previous", [0, 1, 2, 0xFFFFFFFF],
                         ids=["dword-zero", "dword-one", "dword-two", "dword-minus-one"])
def test_exact_dword_previous_count_is_captured_without_coercion(monkeypatch, tmp_path, previous):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        raw = api.W.DWORD(previous)
        _set_resume_leaf(api, lambda _thread: raw)
        if previous == 1:
            process = factory(command)
            try:
                attempt = process._resume_attempt
                assert attempt.raw_return is raw and attempt.raw_return_captured
                assert attempt.previous_count == 1 and attempt.state == "HANDED_OFF"
                assert process._resume_confirmed and process._returned
            finally:
                _retire_success(process, api, state, factory)
        else:
            with pytest.raises(win.NativeSuspendedChildLaunchError) as caught:
                factory(command)
            process = caught.value._process
            attempt = process._resume_attempt
            assert attempt.raw_return is raw and attempt.raw_return_captured
            assert attempt.previous_count == previous and attempt.state == "UNKNOWN"
            assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
            monkeypatch.setattr(daemon, "close", lambda: None)


def test_raw_return_transfer_failure_retains_raw_and_blocks_followup(monkeypatch, tmp_path):
    class BrokenModuleName:
        pass
    BrokenModuleName.__module__ = object()
    raw = BrokenModuleName()
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, _state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        calls = []

        def resume_leaf(_thread):
            calls.append("entered")
            events.append("RESUME")
            return raw

        _set_resume_leaf(api, resume_leaf)
        with pytest.raises(win.NativeSuspendedChildLaunchError) as caught:
            factory(command)
        process = caught.value._process
        attempt = process._resume_attempt
        assert attempt.raw_return is raw and attempt.raw_return_storage[0] is raw
        assert attempt.raw_return_captured
        assert isinstance(attempt.transfer_error, TypeError)
        assert attempt.return_transfer == "RETURN_TRANSFER_FAILED"
        assert attempt.state == "UNKNOWN" and calls == ["entered"]
        assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
        assert process.never_resumed is False
        monkeypatch.setattr(daemon, "close", lambda: None)


def test_raw_return_assignment_transfer_failure_keeps_preallocated_exact_root(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, _state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        raw = api.W.DWORD(1)
        failure = RuntimeError("SOFTWARE exact raw-return assignment transfer failure")
        original_capture = resume._OriginalResumeAttempt.capture_raw_return

        def fail_after_original_store(attempt, value):
            original_capture(attempt, value)
            raise failure

        monkeypatch.setattr(resume._OriginalResumeAttempt, "capture_raw_return", fail_after_original_store)
        calls = []

        def resume_leaf(_thread):
            calls.append("entered")
            events.append("RESUME")
            return raw

        _set_resume_leaf(api, resume_leaf)
        with pytest.raises(win.NativeSuspendedChildLaunchError) as caught:
            factory(command)
        process = caught.value._process
        attempt = process._resume_attempt
        assert attempt.raw_return is raw and attempt.raw_return_storage[0] is raw
        assert attempt.raw_return_captured and attempt.transfer_error is failure
        assert attempt.return_transfer == "RETURN_TRANSFER_FAILED"
        assert attempt.state == "UNKNOWN" and calls == ["entered"]
        assert process.never_resumed is False
        assert process._process_entry.state == process._thread_entry.state == "ACQUIRED"
        monkeypatch.setattr(daemon, "close", lambda: None)


@pytest.mark.parametrize("replacement", ["function", "issuer-profile", "epoch", "job-profile",
                                           "launch-inputs", "connected-B", "thread-entry"])
def test_actual_consumer_rechecks_original_bindings_after_job_query(monkeypatch, tmp_path, replacement):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, _sdk, command, events, _state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        from comsol_mcp import _windows_job_limits as limits
        original = limits.require_configured_job
        seen = []
        replacement_calls = []

        def alter_after_real_job_check(api_arg, job, profile, *, for_child_birth=True):
            result = original(api_arg, job, profile, for_child_birth=for_child_birth)
            if for_child_birth is False:
                process = next(iter(api._retained_children.values()))
                seen.append(process)
                if replacement == "function":
                    _set_resume_leaf(api, lambda _thread: replacement_calls.append("called") or 1)
                elif replacement == "issuer-profile":
                    issued[0].profile_sha256 = "f" * 64
                elif replacement == "epoch":
                    object.__setattr__(process, "_resume_epoch", process._resume_epoch + 1)
                elif replacement == "job-profile":
                    api._job_limit_bindings[job]["profile"] = object()
                elif replacement == "launch-inputs":
                    object.__setattr__(process, "_launch_inputs", object())
                elif replacement == "connected-B":
                    object.__setattr__(process, "_connected_b_observer", object())
                elif replacement == "thread-entry":
                    object.__setattr__(process, "_thread_entry", object())
            return result

        monkeypatch.setattr(limits, "require_configured_job", alter_after_real_job_check)
        _set_resume_leaf(api, lambda _thread: events.append("RESUME") or 1)
        try:
            factory(command)
        except BaseException as error:
            caught = error
        else:
            raise AssertionError("substituted original Resume binding unexpectedly dispatched")
        assert seen and "RESUME" not in events and not replacement_calls
        process = seen[0]
        assert process._resume_attempt is not None
        assert process._resume_attempt.entered is False
        assert process._resume_attempt.raw_return_captured is False
        assert process._resume_attempt.state in {"NO_EFFECT", "UNKNOWN"}
        if isinstance(caught, win.NativeSuspendedChildLaunchError):
            assert caught._process is process
            monkeypatch.setattr(daemon, "close", lambda: None)
        else:
            assert isinstance(caught, ExecutionContractError)
            assert caught.code in {"NATIVE_CHILD_OWNER_UNKNOWN", "JOB_LIMITS_OWNER_UNKNOWN"}


def test_no_resume_entry_keeps_existing_known_cleanup_path(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        _daemon, factory, api, sdk, command, events, state, _static
    ):
        issued = []
        _software_only_issuer(monkeypatch, issued)
        witness_results = []
        # Freeze the exact SOFTWARE_ONLY leaf callable. A Python class method
        # creates a fresh bound-method wrapper on each lookup; the native
        # ctypes leaf and this pinned test leaf both retain stable identity.
        resume_leaf = api.k.ResumeThread
        api.k.ResumeThread = resume_leaf
        _TEST_AUDIT.append({"kind": "software_resume_leaf_identity_pinned",
                            "leaf_co_filename": resume_leaf.__func__.__code__.co_filename,
                            "leaf_co_name": resume_leaf.__func__.__code__.co_name})
        from comsol_mcp import _windows_job_limits as limits
        original = limits.require_configured_job

        def fail_only_before_resume(api_arg, job, profile, *, for_child_birth=True):
            if for_child_birth is False:
                raise ExecutionContractError("SOFTWARE_JOB_PREFLIGHT", "known no ResumeThread entry")
            return original(api_arg, job, profile, for_child_birth=for_child_birth)

        original_poll = api.poll_process

        def witness_at_first_cleanup_query(handle):
            process = next(iter(api._retained_children.values()), None)
            if process is not None and getattr(process._resume_attempt, "state", None) == "NO_EFFECT":
                attempt = process._resume_attempt
                first = resume.original_no_effect_attempt_witness(process)
                owner = process._owner
                object.__setattr__(process, "_owner", object())
                wrong_owner = resume.original_no_effect_attempt_witness(process)
                object.__setattr__(process, "_owner", owner)
                epoch = process._resume_epoch
                object.__setattr__(process, "_resume_epoch", attempt.epoch_before)
                rolled_back_epoch = resume.original_no_effect_attempt_witness(process)
                object.__setattr__(process, "_resume_epoch", epoch)
                witness_results.append((first, wrong_owner, rolled_back_epoch, process, attempt))
            return original_poll(handle)

        api.poll_process = witness_at_first_cleanup_query

        def install_failure():
            monkeypatch.setattr(limits, "require_configured_job", fail_only_before_resume)

        sdk.on_query = install_failure
        with pytest.raises(ExecutionContractError) as caught:
            factory(command)
        _TEST_AUDIT.append({"kind": "known_noeffect_refusal_observed",
                            "error_type": type(caught.value).__module__ + "." + type(caught.value).__qualname__,
                            "error_code": getattr(caught.value, "code", None),
                            "error_text": str(caught.value),
                            "resume_events": tuple(event for event in events if event == "RESUME"),
                            "retained_children": len(api._retained_children),
                            "software_capability_pending": tuple(
                                id(capability) for capability in issued
                                if id(capability) in resume._pending),
                            "held_static_a_state": getattr(factory._held_static_a, "state", None)})
        assert caught.value.code == "SOFTWARE_JOB_PREFLIGHT"
        assert witness_results and witness_results[0][0] is True
        assert witness_results[0][1] is False and witness_results[0][2] is False
        assert issued[0].used is True and id(issued[0]) not in resume._pending
        assert not [event for event in events if event == "RESUME"]
        assert not api._retained_children
        assert [(entry.label, entry.value, entry.state)
                for entry in registry_for(api).unresolved()] == [
                    ("owned_session_job", factory._job, "ACQUIRED")]
        assert factory._held_static_a is None
        _TEST_AUDIT.append({"kind": "pure_no_effect_attempt_witness",
                            "process": witness_results[0][3],
                            "attempt_state_at_witness": witness_results[0][4].state,
                            "original_roots_eligible": witness_results[0][0],
                            "wrong_owner_refused": witness_results[0][1] is False,
                            "rolled_back_epoch_refused": witness_results[0][2] is False,
                            "epoch_before": witness_results[0][4].epoch_before,
                            "epoch_consumed": witness_results[0][4].epoch_consumed})
        factory.close()
        assert not registry_for(api).unresolved()


def test_original_worker_late_exit_noeffect_witness(monkeypatch, tmp_path, use_retired_worker_branch):
    """Use the original Worker transfer/close after a no-effect child times out."""
    assert type(use_retired_worker_branch) is bool
    from comsol_mcp._java_worker import JavaWorkerError, PersistentJavaWorker
    from comsol_mcp._windows_job_limits import require_configured_job
    from comsol_mcp import _windows_job_limits as limits

    with connected.connected_chain(monkeypatch, tmp_path) as (
        daemon, factory, api, sdk, command, events, state, _static
    ):
        issued = []
        wait_calls = []
        termination_requests = []
        exit_observations = []
        attempt_snapshots = []
        preflight_calls = []
        _software_only_issuer(monkeypatch, issued)
        # This SOFTWARE_ONLY class leaf creates a fresh bound-method wrapper
        # on each lookup; pin the exact original wrapper just as the existing
        # no-entry consumer control does.
        resume_leaf = api.k.ResumeThread
        api.k.ResumeThread = resume_leaf
        _TEST_AUDIT.append({"kind": "software_resume_leaf_identity_pinned",
                            "leaf_co_filename": resume_leaf.__func__.__code__.co_filename,
                            "leaf_co_name": resume_leaf.__func__.__code__.co_name})

        def fail_only_before_resume(api_arg, job, profile, *, for_child_birth=True):
            preflight_calls.append({"for_child_birth": for_child_birth,
                                    "retained": tuple(
                                        (id(child), getattr(child, "_resume_attempt", None),
                                         getattr(child, "_owner", None))
                                        for child in api._retained_children.values())})
            if for_child_birth is False:
                raise ExecutionContractError("SOFTWARE_JOB_PREFLIGHT", "known no ResumeThread entry")
            return require_configured_job(api_arg, job, profile, for_child_birth=for_child_birth)

        original_poll = api.poll_process

        def poll_leaf(handle):
            for child in tuple(api._retained_children.values()):
                current = getattr(child, "_resume_attempt", None)
                attempt_snapshots.append({
                    "process_id": id(child), "attempt": current,
                    "attempt_type": type(current).__module__ + "." + type(current).__qualname__,
                    "attempt_state": getattr(current, "state", None),
                    "owner": getattr(child, "_owner", None),
                    "capability_used": getattr(issued[0], "used", None) if issued else None,
                    "capability_pending": bool(issued and resume._pending.get(id(issued[0])) is issued[0]),
                })
            result = original_poll(handle)
            exit_observations.append({"result": result, "alive": state["alive"]})
            return result

        def terminate_leaf(handle, code):
            termination_requests.append((handle, code))
            # Exact SOFTWARE_ONLY leaf: model a successful request while the
            # child remains live until the later known-exit observation.
            return None

        def wait_leaf(handle, timeout, args):
            wait_calls.append((handle, timeout, tuple(args)))
            if state["alive"]:
                raise TimeoutError("SOFTWARE wait timeout after successful termination request")
            return 1

        api.poll_process = poll_leaf
        api.terminate_process = terminate_leaf
        api.wait_process = wait_leaf

        def install_failure():
            monkeypatch.setattr(limits, "require_configured_job", fail_only_before_resume)

        sdk.on_query = install_failure
        with pytest.raises(win.NativeSuspendedChildLaunchError) as captured:
            factory(command)

        process = captured.value._process
        attempt = getattr(process, "_resume_attempt", None)
        if type(attempt) is not resume._OriginalResumeAttempt:
            _TEST_AUDIT.append({
                "kind": "f3_setup_diagnostic_not_counter",
                "attempt_type": type(attempt).__module__ + "." + type(attempt).__qualname__,
                "attempt_present": attempt is not None,
                "cause_type": type(getattr(captured.value, "cause", None)).__module__ + "."
                    + type(getattr(captured.value, "cause", None)).__qualname__,
                "cleanup_error_type": type(getattr(captured.value, "cleanup_error", None)).__module__ + "."
                    + type(getattr(captured.value, "cleanup_error", None)).__qualname__,
                "launch_error_details": getattr(captured.value, "details", None),
                "cause_chain": tuple({
                    "type": type(error).__module__ + "." + type(error).__qualname__,
                    "code": getattr(error, "code", None), "text": str(error),
                } for error in _exception_chain(captured.value)),
                "recent_events": tuple(events[-20:]),
                "resume_events": tuple(event for event in events if event == "RESUME"),
                "issued_capabilities": len(issued),
                "issued_capability_used": tuple(cap.used for cap in issued),
                "issued_capability_pending": tuple(id(cap) in resume._pending for cap in issued),
                "preflight_calls": tuple(preflight_calls),
                "attempt_snapshots": tuple(attempt_snapshots),
                "issue_child_ids": tuple(id(row["process"]) for row in _TEST_AUDIT
                                          if row.get("kind") == "software_issuer_child"),
            })
            raise AssertionError("F3 software scenario did not retain the exact original Resume attempt")
        assert type(process) is win._RetainedSuspendedChild
        assert captured.value._process is process
        assert attempt.state == "NO_EFFECT" and attempt.entered is False
        assert attempt.raw_return_captured is False and attempt.raw_return is None
        assert type(attempt.raw_return_storage) is list and attempt.raw_return_storage == [None]
        assert attempt.return_transfer == "NOT_ENTERED" and attempt.previous_count is None
        assert attempt.epoch_consumed == attempt.epoch_before + 1 == process._resume_epoch
        assert attempt.owner is attempt.api is api and process._api is api
        assert process._owner is api and process._thread_entry.state == "ACQUIRED"
        assert process._process_entry.state == "ACQUIRED" and not process._closed
        assert process._termination_requested is True and process._terminate_error is None
        assert len(issued) == 1 and issued[0].used is True and id(issued[0]) not in resume._pending
        assert not [event for event in events if event == "RESUME"]
        assert len(termination_requests) == 1 and len(wait_calls) == 1
        assert wait_calls[0][0] == process._handle
        assert any(row["result"] is None and row["alive"] is True for row in exit_observations)

        # Construct the exact original class. The next two assignments mirror
        # PersistentJavaWorker.start's NativeSuspendedChildLaunchError handler;
        # transfer_to and close below are the real original methods.
        worker = PersistentJavaWorker(None, state_dir=tmp_path / "worker-state", process_factory=factory)
        assert type(worker) is PersistentJavaWorker
        assert worker._process_factory is worker._original_process_factory is factory
        transferred = captured.value.transfer_to(worker)
        assert transferred is process and process._owner is worker
        worker._process = transferred
        startup_unknown = JavaWorkerError("native Worker child cleanup is uncertain; actual owner retained")
        startup_unknown.execution_state_unknown = True
        worker._startup_unknown = startup_unknown

        before_nonterminal = (tuple(events), tuple(sdk.calls), tuple(sdk.closed), tuple(sdk.releases))
        nonterminal = (resume.original_no_effect_attempt_witness(process, retired_worker=worker)
                       if use_retired_worker_branch
                       else resume.original_no_effect_attempt_witness(process))
        if use_retired_worker_branch:
            assert nonterminal is False
        assert before_nonterminal == (tuple(events), tuple(sdk.calls), tuple(sdk.closed), tuple(sdk.releases))

        # The inert process-poll leaf now reports known exit. The original
        # Worker.close -> child.cleanup_owned -> child.close_owned path must
        # consume the same process/thread entries exactly once.
        state["alive"] = False
        before_close_observations = len(exit_observations)
        worker.close()
        assert len(exit_observations) == before_close_observations + 1
        assert exit_observations[-1] == {"result": 1, "alive": False}
        assert process.returncode == 1 and process._closed is True
        assert process._owner is worker and attempt.owner is attempt.api is api
        assert process._thread_handle is None
        assert process._process_entry.state == "RELEASED"
        assert process._thread_entry.state == "RELEASED"
        assert events.count(("close", 200)) == 1 and events.count(("close", 201)) == 1
        assert not api._retained_children
        assert all(getattr(worker, slot) is None for slot in (
            "_process", "_fixed_process", "_fixed_launch_owner",
            "_startup_unknown", "_retirement_unknown"))
        assert worker._original_process_factory is worker._process_factory is attempt.factory
        assert process._original_factory is attempt.factory
        assert attempt.dispatch_reserved is False
        assert attempt.raw_return_captured is False and attempt.raw_return_storage == [None]
        assert attempt.epoch_consumed == attempt.epoch_before + 1 == process._resume_epoch
        assert not [event for event in events if event == "RESUME"]

        b_settlement = None
        if attempt.connected_b is not None:
            # Keep the original B UNKNOWN refusal until the same original,
            # SDK-free known-close settler has its actual terminal preconditions.
            from comsol_mcp._windows_t037_b_observation import settle_original_child_b
            assert process._closed and attempt.parent.closed
            assert not [entry for entry in attempt.registry.unresolved()
                        if entry.state == "UNKNOWN"]
            before_b_settlement = (tuple(events), tuple(sdk.calls),
                                  tuple(sdk.closed), tuple(sdk.releases))
            settle_original_child_b(process, captured.value)
            assert attempt.connected_b.state == "RELEASED"
            assert api._last_connected_b_observer is attempt.connected_b
            assert id(process) not in api._connected_b_observers
            assert before_b_settlement == (tuple(events), tuple(sdk.calls),
                                           tuple(sdk.closed), tuple(sdk.releases))
            b_settlement = {
                "kind": "original_b_known_close_pure_settlement",
                "settler_co_filename": settle_original_child_b.__code__.co_filename,
                "state": attempt.connected_b.state,
                "process_closed": process._closed,
                "parent_closed": attempt.parent.closed,
                "unknown_entries": (),
                "event_and_sdk_counters_unchanged": True,
                "same_original_observer_retained": api._last_connected_b_observer is attempt.connected_b,
            }

        witness = (resume.original_no_effect_attempt_witness(process, retired_worker=worker)
                   if use_retired_worker_branch
                   else resume.original_no_effect_attempt_witness(process))
        _TEST_AUDIT.append({
            "kind": "original_worker_late_exit_noeffect_witness",
            "branch_enabled": use_retired_worker_branch,
            "witness_result": witness,
            "process": process,
            "attempt_owner_is_original_api": attempt.owner is attempt.api is api,
            "current_child_owner_is_exact_worker": process._owner is worker,
            "worker_type": type(worker).__module__ + "." + type(worker).__qualname__,
            "worker_process_factory_is_captured": worker._process_factory is attempt.factory,
            "worker_original_process_factory_is_captured": worker._original_process_factory is attempt.factory,
            "process_original_factory_is_captured": process._original_factory is attempt.factory,
            "worker_retired_slots_all_none": all(getattr(worker, slot) is None for slot in (
                "_process", "_fixed_process", "_fixed_launch_owner",
                "_startup_unknown", "_retirement_unknown")),
            "connected_b_state": getattr(attempt.connected_b, "state", None),
            "connected_b_is_current_original_observer": (
                getattr(process, "_connected_b_observer", None) is attempt.connected_b),
            "b_settlement": b_settlement,
            "parent_closed": getattr(attempt.parent, "closed", None),
            "registry_unknown_entries": tuple(
                (entry.label, entry.state) for entry in attempt.registry_entries
                if entry.state == "UNKNOWN"),
            "held_a_was_captured": attempt.held_a_present,
            "held_a_current_identity_matches": (
                not attempt.held_a_present
                or getattr(attempt.factory, "_held_static_a", None) is attempt.held_a),
            "process_entry_state": process._process_entry.state,
            "thread_entry_state": process._thread_entry.state,
            "resume_calls": tuple(event for event in events if event == "RESUME"),
            "terminate_requests": len(termination_requests),
            "wait_calls": len(wait_calls),
            "poll_leaf_co_filename": poll_leaf.__code__.co_filename,
            "terminate_leaf_co_filename": terminate_leaf.__code__.co_filename,
            "wait_leaf_co_filename": wait_leaf.__code__.co_filename,
            "transfer_method_co_filename": win.NativeSuspendedChildLaunchError.transfer_to.__code__.co_filename,
            "worker_start_method_co_filename": PersistentJavaWorker.start.__code__.co_filename,
            "worker_close_method_co_filename": PersistentJavaWorker.close.__code__.co_filename,
            "child_cleanup_method_co_filename": type(process).cleanup_owned.__code__.co_filename,
            "child_close_method_co_filename": type(process).close_owned.__code__.co_filename,
            "worker_start_invoked": False,
            "worker_start_catch_slot_wiring": "manual harness mirrors exact source handler after actual transfer_to return",
        })

        # Same positive oracle is used for the old-source red and repaired run.
        assert witness is True, "original Worker known late-exit NO_EFFECT witness must be eligible"

        if use_retired_worker_branch:
            snapshot = (tuple(events), tuple(sdk.calls), tuple(sdk.closed), tuple(sdk.releases))
            assert resume.original_no_effect_attempt_witness(process) is False
            wrong_worker = PersistentJavaWorker(None, state_dir=tmp_path / "wrong-worker-state",
                                                process_factory=factory)
            assert resume.original_no_effect_attempt_witness(process, retired_worker=wrong_worker) is False
            assert resume.original_no_effect_attempt_witness(process, retired_worker=object()) is False
            copied_process = copy.copy(process)
            assert resume.original_no_effect_attempt_witness(copied_process, retired_worker=worker) is False

            for field in ("_process_factory", "_original_process_factory"):
                original = getattr(worker, field)
                setattr(worker, field, object())
                assert resume.original_no_effect_attempt_witness(process, retired_worker=worker) is False
                setattr(worker, field, original)
            for field in ("_process", "_fixed_process", "_fixed_launch_owner",
                          "_startup_unknown", "_retirement_unknown"):
                original = getattr(worker, field)
                setattr(worker, field, object())
                assert resume.original_no_effect_attempt_witness(process, retired_worker=worker) is False
                setattr(worker, field, original)

            original_state = attempt.state
            object.__setattr__(attempt, "state", "UNKNOWN")
            unknown_refused = resume.original_no_effect_attempt_witness(process, retired_worker=worker) is False
            object.__setattr__(attempt, "state", original_state)
            original_entered = attempt.entered
            object.__setattr__(attempt, "entered", True)
            entered_refused = resume.original_no_effect_attempt_witness(process, retired_worker=worker) is False
            object.__setattr__(attempt, "entered", original_entered)
            original_raw = attempt.raw_return
            original_captured = attempt.raw_return_captured
            object.__setattr__(attempt, "raw_return", 0xFFFFFFFF)
            object.__setattr__(attempt, "raw_return_captured", True)
            attempt.raw_return_storage[0] = 0xFFFFFFFF
            raw_refused = resume.original_no_effect_attempt_witness(process, retired_worker=worker) is False
            object.__setattr__(attempt, "raw_return", original_raw)
            object.__setattr__(attempt, "raw_return_captured", original_captured)
            attempt.raw_return_storage[0] = None
            original_epoch = process._resume_epoch
            object.__setattr__(process, "_resume_epoch", attempt.epoch_before)
            rolled_back_epoch_refused = resume.original_no_effect_attempt_witness(
                process, retired_worker=worker) is False
            object.__setattr__(process, "_resume_epoch", original_epoch)
            assert unknown_refused and entered_refused and raw_refused and rolled_back_epoch_refused
            assert snapshot == (tuple(events), tuple(sdk.calls), tuple(sdk.closed), tuple(sdk.releases))
            _TEST_AUDIT.append({
                "kind": "retired_worker_witness_negative_controls",
                "default_api_owner_branch_refused_after_transfer": True,
                "different_exact_worker_refused": True,
                "wrong_type_refused": True,
                "copied_child_refused": True,
                "current_and_original_factory_replacements_refused": True,
                "all_five_live_worker_slots_refused": True,
                "unknown_state_refused": unknown_refused,
                "resume_entered_refused": entered_refused,
                "raw_return_and_capture_refused": raw_refused,
                "spent_epoch_rollback_refused": rolled_back_epoch_refused,
                "sdk_counters_unchanged": True,
            })


def test_copied_capability_is_rejected_by_actual_consumer_without_resume(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        _daemon, factory, api, _sdk, command, events, _state, _static
    ):
        issued = []
        copied = []

        def issue(process, profile, _binding, _dacl):
            original = resume._NativeResumeCapability(process, profile.sha256, seal=resume._SEAL)
            duplicate = resume._NativeResumeCapability(process, profile.sha256, seal=resume._SEAL)
            with resume._lock:
                resume._pending[id(original)] = original
            issued.append(original)
            copied.append(duplicate)
            return duplicate

        monkeypatch.setattr(resume, "issue_native_resume", issue)
        calls = []
        _set_resume_leaf(api, lambda _thread: calls.append("called") or 1)
        with pytest.raises(ExecutionContractError) as caught:
            factory(command)
        assert caught.value.code == "NATIVE_CHILD_PROOF_UNAVAILABLE"
        assert not calls and "RESUME" not in events
        assert issued[0].used is False and id(issued[0]) in resume._pending
        assert copied[0] not in resume._pending.values()
        resume._pending.pop(id(issued[0]), None)


def test_wrong_thread_consumer_reentry_is_rejected_before_sdk(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        _daemon, factory, api, _sdk, command, events, _state, _static
    ):
        issued = []
        wrong_thread_errors = []
        copied = []

        def issue(process, profile, _binding, _dacl):
            original = resume._NativeResumeCapability(process, profile.sha256, seal=resume._SEAL)
            duplicate = resume._NativeResumeCapability(process, profile.sha256, seal=resume._SEAL)
            with resume._lock:
                resume._pending[id(original)] = original
            issued.append(original)
            copied.append(duplicate)

            def wrong_thread():
                try:
                    resume.resume_native_child(process, original)
                except BaseException as error:
                    wrong_thread_errors.append(error)

            worker = threading.Thread(target=wrong_thread, name="wrong-resume-thread")
            worker.start()
            worker.join(2)
            assert not worker.is_alive()
            return duplicate

        monkeypatch.setattr(resume, "issue_native_resume", issue)
        _set_resume_leaf(api, lambda _thread: events.append("RESUME") or 1)
        with pytest.raises(ExecutionContractError) as caught:
            factory(command)
        assert caught.value.code == "NATIVE_CHILD_PROOF_UNAVAILABLE"
        assert wrong_thread_errors and isinstance(wrong_thread_errors[0], ExecutionContractError)
        assert issued[0].used is False and not [event for event in events if event == "RESUME"]
        resume._pending.pop(id(issued[0]), None)


def test_real_issuer_still_refuses_without_registering_any_capability(monkeypatch, tmp_path):
    with connected.connected_chain(monkeypatch, tmp_path) as (
        _daemon, factory, api, _sdk, command, events, _state, _static
    ):
        before = dict(resume._pending)
        error = connected.launch_refusal(factory, command)
        assert error.code == "NATIVE_CHILD_PROOF_UNAVAILABLE"
        assert resume._pending == before
        assert "RESUME" not in events
        connected.known_child_cleanup(api, factory)


def test_existing_connected_b_full_factory_refusal_regression(monkeypatch, tmp_path):
    from tests import test_windows_t037_connected_b as original
    node = original.test_normal_v4_connected_original_B_full_observer_matches_but_issuer_still_denies
    result = node(monkeypatch, tmp_path)
    _TEST_AUDIT.append({"kind": "delegated_original_regression_node",
                        "node": "tests.test_windows_t037_connected_b::" + node.__name__,
                        "co_filename": node.__code__.co_filename,
                        "co_name": node.__code__.co_name,
                        "return_value": result})


def test_existing_suspended_ownership_refusal_cleanup_regression(monkeypatch, tmp_path):
    from tests import test_windows_suspended_ownership as original
    node = original.test_actual_production_launch_gate_refuses_resume_and_confirms_exact_cleanup
    results = []
    for acl_fail in (False, True):
        result = node(monkeypatch, acl_fail)
        results.append({"acl_fail": acl_fail, "return_value": result})
        _TEST_AUDIT.append({"kind": "delegated_original_regression_node",
                            "node": "tests.test_windows_suspended_ownership::" + node.__name__,
                            "co_filename": node.__code__.co_filename,
                            "co_name": node.__code__.co_name,
                            "parameter": acl_fail,
                            "return_value": result})
    return tuple(results)


def test_existing_prepared_launch_manifest_regression(monkeypatch, tmp_path):
    from tests import test_windows_prepared_launch as original
    node = original.test_protected_manifest_accepts_only_retired_fixed_compiler_material
    result = node()
    _TEST_AUDIT.append({"kind": "delegated_original_regression_node",
                        "node": "tests.test_windows_prepared_launch::" + node.__name__,
                        "co_filename": node.__code__.co_filename,
                        "co_name": node.__code__.co_name,
                        "return_value": result,
                        "scope": "synthetic manifest/parser only; no Java/JDK/compiler/file access"})


def test_existing_installer_abi_regression(monkeypatch, tmp_path):
    from tests import test_windows_installer_context as original
    node = original.test_fixed_abi_and_no_service_privilege_contract_change
    result = node()
    _TEST_AUDIT.append({"kind": "delegated_original_regression_node",
                        "node": "tests.test_windows_installer_context::" + node.__name__,
                        "co_filename": node.__code__.co_filename,
                        "co_name": node.__code__.co_name,
                        "return_value": result,
                        "scope": "ctypes structure-size assertions only; no SDK calls"})
