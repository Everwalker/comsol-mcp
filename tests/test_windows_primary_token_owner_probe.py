"""Source/software controls for the review-gated Windows primary-owner probe."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import windows_primary_token_owner_probe as probe


USER_SID = "S-1-5-21-2844656816-882443001-4055095909-1001"
OLD_OWNER = "S-1-5-32-544"


def _token(*, owner=OLD_OWNER, groups=("S-1-5-18",), privileges=((10, 0, 2),),
           authentication_id=(4, 0), session_id=2, token_type=probe.TOKEN_PRIMARY):
    return {
        "user_sid": USER_SID,
        "owner_sid": owner,
        "primary_group_sid": "S-1-5-32-545",
        "groups": tuple((sid, 7) for sid in groups),
        "privileges": privileges,
        "token_id": (100, 0),
        "authentication_id": authentication_id,
        "modified_id": (200, 0),
        "token_type": token_type,
        "session_id": session_id,
        "elevation_type": 2,
        "elevated": False,
        "integrity_sid": "S-1-16-8192",
        "logon_sids": (("S-1-5-5-1-2", 7),),
    }


def test_duplicate_owner_transition_changes_only_token_owner_and_keeps_primary_exact():
    primary = _token(owner=OLD_OWNER, groups=("S-1-5-32-544",))
    duplicate_before = _token(owner=OLD_OWNER, groups=("S-1-5-32-544",))
    duplicate_after = {**duplicate_before, "owner_sid": USER_SID}

    probe._assert_duplicate_owner_transition(
        primary, dict(primary), duplicate_before, duplicate_after, expected_user_sid=USER_SID,
    )


@pytest.mark.parametrize("change", [
    "primary", "primary_token_id", "primary_modified_id", "groups", "privileges",
    "duplicate_token_id", "authentication", "session", "owner",
])
def test_duplicate_owner_transition_rejects_any_unapproved_token_change(change):
    primary = _token(owner=OLD_OWNER, groups=("S-1-5-32-544",))
    duplicate_before = _token(owner=OLD_OWNER, groups=("S-1-5-32-544",))
    duplicate_after = {**duplicate_before, "owner_sid": USER_SID}
    primary_after = dict(primary)
    if change == "primary":
        primary_after["owner_sid"] = USER_SID
    elif change == "primary_token_id":
        primary_after["token_id"] = (101, 0)
    elif change == "primary_modified_id":
        primary_after["modified_id"] = (201, 0)
    elif change == "groups":
        duplicate_after["groups"] = (("S-1-5-32-544", 7), ("S-1-5-32-545", 7))
    elif change == "privileges":
        duplicate_after["privileges"] = ((10, 0, 0),)
    elif change == "duplicate_token_id":
        duplicate_after["token_id"] = (101, 0)
    elif change == "authentication":
        duplicate_after["authentication_id"] = (99, 0)
    elif change == "session":
        duplicate_after["session_id"] = 9
    elif change == "owner":
        duplicate_after["owner_sid"] = OLD_OWNER

    with pytest.raises(probe.ProbeError):
        probe._assert_duplicate_owner_transition(
            primary, primary_after, duplicate_before, duplicate_after,
            expected_user_sid=USER_SID,
        )


def test_child_identity_requires_exact_handle_image_user_owner_and_session():
    process = {"pid": 43210, "birth_filetime_100ns": 123456, "image_path": probe.PYTHON_EXE}
    token = _token(owner=USER_SID, groups=("S-1-5-32-544",), session_id=2)
    probe._assert_child_identity(
        process, token, expected_user_sid=USER_SID, expected_session=2, expected_image=probe.PYTHON_EXE,
    )
    for field, bad in (
        ("image_path", r"C:\Windows\System32\python.exe"),
        ("user_sid", "S-1-5-18"),
        ("owner_sid", OLD_OWNER),
        ("session_id", 3),
        ("token_type", 2),
    ):
        changed = dict(process)
        bad_token = dict(token)
        if field == "image_path":
            changed[field] = bad
        else:
            bad_token[field] = bad
        with pytest.raises(probe.ProbeError):
            probe._assert_child_identity(
                changed, bad_token, expected_user_sid=USER_SID,
                expected_session=2, expected_image=probe.PYTHON_EXE,
            )


def test_child_token_security_context_must_match_owner_updated_duplicate():
    duplicate = _token(owner=USER_SID, groups=("S-1-5-18",), session_id=2)
    child = {**duplicate, "token_id": (900, 0), "modified_id": (901, 0)}
    probe._assert_child_token_matches_duplicate(duplicate, child)

    for field, bad in (
        ("owner_sid", OLD_OWNER),
        ("primary_group_sid", "S-1-5-32-544"),
        ("groups", (("S-1-5-18", 3),)),
        ("privileges", ((10, 0, 0),)),
        ("authentication_id", (99, 0)),
        ("session_id", 9),
        ("elevation_type", 3),
        ("elevated", True),
        ("integrity_sid", "S-1-16-12288"),
        ("logon_sids", (("S-1-5-5-9-9", 7),)),
    ):
        mismatched = dict(child)
        mismatched[field] = bad
        with pytest.raises(probe.ProbeError):
            probe._assert_child_token_matches_duplicate(duplicate, mismatched)


def test_owner_lease_deadline_persists_unknown_then_waits_on_original_handle(tmp_path, monkeypatch):
    calls = []

    class Kernel:
        @staticmethod
        def WaitForSingleObject(handle, milliseconds):
            calls.append(("wait", handle, milliseconds))
            return probe.WAIT_OBJECT_0

        @staticmethod
        def GetExitCodeProcess(handle, out):
            calls.append(("exit", handle))
            import ctypes as C
            C.cast(out, C.POINTER(probe.W.DWORD))[0] = 0
            return 1

    api = SimpleNamespace(k=Kernel(), _check=probe.Win32._check)
    monkeypatch.setattr(probe, "LEASE_EVENTS", str(tmp_path / "lease.jsonl"))
    monkeypatch.setattr(probe, "_PROCESS_LEASES", [])
    info = probe.PROCESS_INFORMATION()
    info.hProcess = 700
    info.hThread = 701
    info.dwProcessId = 702
    info.dwThreadId = 703
    lease = probe.ProcessLease(api, info, command_line="python.exe fixed-child", started_monotonic=0.0)
    lease.identity = {"pid": 702, "birth_filetime_100ns": 123, "image_path": probe.PYTHON_EXE}
    lease.child_token = _token(owner=USER_SID, session_id=2)

    assert lease.wait_natural(events_path="unused", budget_seconds=60) == 0
    event = json.loads(Path(probe.LEASE_EVENTS).read_text().splitlines()[0])
    assert lease.deadline_exceeded is True
    assert lease.terminal_observed is True
    assert event["status"] == "UNKNOWN_RUNNING_AFTER_60S_CUTOFF"
    assert event["original_process_information"]["process_handle_value"] == 700
    assert event["original_process_information"]["pid_from_PROCESS_INFORMATION"] == 702
    assert calls[0] == ("wait", 700, probe.PASSIVE_WAIT_SLICE_MS)
    assert calls[1] == ("exit", 700)


def test_parent_unwind_retains_original_process_handle_until_natural_terminal(monkeypatch):
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)

    class Lease:
        terminal_observed = False
        state = "CREATED_UNVERIFIED"
        identity = {"pid": 42, "birth_filetime_100ns": 123, "image_path": probe.PYTHON_EXE}
        child_token = _token(owner=USER_SID)
        events = []
        waits = 0
        closed = False

        @staticmethod
        def original_handles():
            return {"process_handle_value": 700, "pid_from_PROCESS_INFORMATION": 42}

        def record_event(self, event):
            self.events.append(dict(event))

        def wait_natural(self, *, events_path, budget_seconds):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt()
            self.terminal_observed = True
            self.terminal_code = 0
            return 0

        def close_after_terminal(self):
            assert self.terminal_observed
            self.closed = True

    lease = Lease()
    probe._drain_process_lease_before_unwind(lease)

    assert lease.waits == 2
    assert lease.terminal_observed is True
    assert lease.closed is True
    assert lease.events[0]["status"] == "UNKNOWN_PARENT_UNWIND_PASSIVE_WAIT"
    assert lease.events[0]["action_after_unwind"] == (
        "RETAIN_ORIGINAL_HANDLES; PASSIVE_WAIT_ONLY; NO_SIGNAL_OR_CLOSE"
    )
    assert lease.events[1]["observation_error"].startswith("KeyboardInterrupt:")


def test_console_interrupts_are_deferred_only_until_process_handles_can_be_captured(monkeypatch):
    class FakeSignal:
        SIGINT = 2
        SIGBREAK = 21

        def __init__(self):
            self.handlers = {self.SIGINT: "old-int", self.SIGBREAK: "old-break"}

        def getsignal(self, signum):
            return self.handlers[signum]

        def signal(self, signum, handler):
            old = self.handlers[signum]
            self.handlers[signum] = handler
            if callable(handler):
                handler(signum, None)
            return old

    fake = FakeSignal()
    monkeypatch.setattr(probe, "signal", fake)
    previous, pending = probe._defer_console_interrupts()

    assert pending == [FakeSignal.SIGINT, FakeSignal.SIGBREAK]
    assert all(callable(fake.handlers[signum]) for signum in previous)
    probe._restore_console_interrupts(previous)
    assert fake.handlers == {FakeSignal.SIGINT: "old-int", FakeSignal.SIGBREAK: "old-break"}


@pytest.mark.parametrize(
    ("outcome", "expected_reason"),
    [
        ("returned_false", "CREATE_PROCESS_RETURNED_FALSE_WITH_PARTIAL_PROCESS_INFORMATION"),
        ("raised_after_dispatch", "CREATE_PROCESS_CALL_RAISED_AFTER_NATIVE_DISPATCH"),
    ],
)
def test_partial_process_information_is_retained_unknown_for_false_and_raised_outcomes(
    monkeypatch, outcome, expected_reason,
):
    events = []
    waits = []
    closes = []

    class Kernel:
        results = [probe.WAIT_TIMEOUT, probe.WAIT_OBJECT_0]

        def WaitForSingleObject(self, handle, milliseconds):
            waits.append((handle, milliseconds))
            return self.results.pop(0)

        def CloseHandle(self, handle):
            closes.append(handle)
            return 1

    api = SimpleNamespace(k=Kernel())
    monkeypatch.setattr(probe, "_PARTIAL_PROCESS_INFORMATION", [])
    monkeypatch.setattr(probe, "_append_event", lambda _path, event: events.append(dict(event)))
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)
    info = probe.PROCESS_INFORMATION()
    info.hThread = 701
    info.dwProcessId = 702
    info.dwThreadId = 703
    create_error = "RuntimeError:raised after native dispatch" if outcome == "raised_after_dispatch" else None

    process_lease, partial_hold = probe._adopt_process_information_result(
        api, info, create_ok=False, create_error=create_error,
        command_line="python.exe fixed-child", started_monotonic=probe.time.monotonic(),
    )

    assert process_lease is None
    assert partial_hold is not None
    assert partial_hold.process_info is info
    assert partial_hold.thread_handle == info.hThread
    assert probe._PARTIAL_PROCESS_INFORMATION == [partial_hold]
    assert events[0]["creation_outcome"] == expected_reason
    assert events[0]["process_terminal_observed"] is False

    assert partial_hold.observe_once() == "UNKNOWN_PARTIAL_PROCESS_INFORMATION_NO_PROCESS_HANDLE"
    assert partial_hold.thread_terminal_observed is False
    assert partial_hold.observe_once() == "UNKNOWN_PARTIAL_PROCESS_INFORMATION_NO_PROCESS_HANDLE"
    assert partial_hold.thread_terminal_observed is True
    assert events[-1]["status"] == "PARTIAL_THREAD_TERMINAL_PROCESS_STILL_UNKNOWN"
    assert events[-1]["thread_terminal_observed"] is True
    assert events[-1]["process_terminal_observed"] is False
    assert waits == [(701, probe.PASSIVE_WAIT_SLICE_MS), (701, probe.PASSIVE_WAIT_SLICE_MS)]
    assert closes == []
    assert not hasattr(api.k, "OpenProcess")
    assert not hasattr(api.k, "GetExitCodeProcess")


def test_partial_pid_without_thread_handle_never_reopens_process(monkeypatch):
    events = []
    waited = []
    monkeypatch.setattr(probe, "_PARTIAL_PROCESS_INFORMATION", [])
    monkeypatch.setattr(probe, "_append_event", lambda _path, event: events.append(dict(event)))
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)

    class Kernel:
        def WaitForSingleObject(self, handle, milliseconds):
            waited.append((handle, milliseconds))
            return probe.WAIT_TIMEOUT

    info = probe.PROCESS_INFORMATION()
    info.dwProcessId = 702
    api = SimpleNamespace(k=Kernel())
    _, hold = probe._adopt_process_information_result(
        api, info, create_ok=False, create_error=None,
        command_line="python.exe fixed-child", started_monotonic=probe.time.monotonic(),
    )

    assert hold is not None
    assert hold.process_info is info
    assert hold.observe_once() == "UNKNOWN_PARTIAL_PROCESS_INFORMATION_NO_PROCESS_HANDLE"
    assert waited == []
    assert hold.pid == 702
    assert events[0]["original_process_information"]["pid_from_PROCESS_INFORMATION"] == 702


def test_partial_information_60_second_cutoff_persists_unknown_and_keeps_waiting(monkeypatch):
    events = []
    waits = []
    closes = []
    monkeypatch.setattr(probe, "_PARTIAL_PROCESS_INFORMATION", [])
    monkeypatch.setattr(probe, "_append_event", lambda _path, event: events.append(dict(event)))
    monkeypatch.setattr(probe.time, "monotonic", lambda: 61.0)
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)

    class Kernel:
        def WaitForSingleObject(self, handle, milliseconds):
            waits.append((handle, milliseconds))
            return probe.WAIT_TIMEOUT

        def CloseHandle(self, handle):
            closes.append(handle)
            return 1

    info = probe.PROCESS_INFORMATION()
    info.hThread = 701
    api = SimpleNamespace(k=Kernel())
    _, hold = probe._adopt_process_information_result(
        api, info, create_ok=False, create_error=None, command_line="fixed-child",
        started_monotonic=0.0,
    )

    assert hold is not None
    assert hold.observe_once() == "UNKNOWN_PARTIAL_PROCESS_INFORMATION_NO_PROCESS_HANDLE"
    assert hold.deadline_exceeded is True
    assert events[-1]["status"] == "UNKNOWN_PROCESS_INFORMATION_AFTER_60S_CUTOFF"
    assert events[-1]["action_after_cutoff"].startswith("NO_FURTHER_ADMISSION")
    assert events[-1]["process_terminal_observed"] is False
    assert waits == [(701, probe.PASSIVE_WAIT_SLICE_MS)]
    assert closes == []


def test_post_dispatch_exception_without_any_handle_remains_unknown_owner(monkeypatch):
    events = []
    monkeypatch.setattr(probe, "_PARTIAL_PROCESS_INFORMATION", [])
    monkeypatch.setattr(probe, "_append_event", lambda _path, event: events.append(dict(event)))
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)
    info = probe.PROCESS_INFORMATION()
    _, hold = probe._adopt_process_information_result(
        SimpleNamespace(k=SimpleNamespace()), info, create_ok=False,
        create_error="RuntimeError:after dispatch", command_line="fixed-child",
        started_monotonic=probe.time.monotonic(),
    )

    assert hold is not None
    assert hold.state == "UNKNOWN_CREATE_DISPATCH_OUTCOME_NO_PROCESS_HANDLE"
    assert hold.process_info is info
    assert hold.observe_once() == hold.state
    assert events[0]["process_terminal_observed"] is False


def test_main_finalizer_holds_partial_owner_before_any_handle_cleanup():
    source = Path(probe.__file__).read_text()
    tree = ast.parse(source)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_main")
    finalizer = next(node for node in ast.walk(main) if isinstance(node, ast.Try) and node.finalbody)
    final_body = finalizer.finalbody
    hold_index = next(
        index for index, node in enumerate(final_body)
        if any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "hold_forever"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "partial_hold"
            for call in ast.walk(node)
        )
    )
    close_index = next(
        index for index, node in enumerate(final_body)
        if any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
               and call.func.attr == "close" for call in ast.walk(node))
    )
    assert hold_index < close_index

    partial_cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == "PartialProcessInformationHold")
    hold_method = next(node for node in partial_cls.body if isinstance(node, ast.FunctionDef)
                       and node.name == "hold_forever")
    assert any(isinstance(node, ast.While) and isinstance(node.test, ast.Constant)
               and node.test.value is True for node in ast.walk(hold_method))
    assert not any(isinstance(node, (ast.Return, ast.Break)) for node in ast.walk(hold_method))
    called = {
        node.func.attr for node in ast.walk(hold_method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint({"CloseHandle", "OpenProcess", "TerminateProcess"})


def test_parent_handle_close_anomaly_is_persisted_and_never_leaves_clean_pass(monkeypatch):
    events = []
    monkeypatch.setattr(probe, "_append_event", lambda _path, event: events.append(dict(event)))

    with pytest.raises(probe.ProbeError, match="superseded by UNKNOWN"):
        probe._record_handle_cleanup_anomaly(
            ["duplicate primary token:OSError:close failed"],
            "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE", prior_exception_active=False,
        )
    assert events[-1]["status"] == "UNKNOWN_PARENT_TOKEN_OR_EVENT_HANDLE_CLOSE"
    assert events[-1]["probe_status_before_cleanup"] == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"
    assert events[-1]["first_cause_preserved"] is False

    probe._record_handle_cleanup_anomaly(
        ["duplicate primary token:OSError:close failed"],
        "FAIL_OR_HOLD_OWNER_PROBE", prior_exception_active=False,
    )
    assert events[-1]["first_cause_preserved"] is True


def test_source_has_no_privilege_group_acl_or_process_termination_fallbacks():
    source = Path(probe.__file__).read_text()
    tree = ast.parse(source)
    set_token_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "SetTokenInformation"
    ]
    assert len(set_token_calls) == 1
    assert ast.unparse(set_token_calls[0].args[0]) == "duplicate_token"
    assert ast.unparse(set_token_calls[0].args[1]) == "TOKEN_OWNER"
    forbidden_calls = {"AdjustTokenPrivileges", "AdjustTokenGroups", "TerminateProcess",
                       "CreateProcessAsUserW", "CreateProcessWithLogonW", "SetNamedSecurityInfoW",
                       "SetSecurityInfo", "taskkill", "Popen"}
    called = {
        node.func.attr if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        else node.func.id if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        else ""
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert called.isdisjoint(forbidden_calls)

