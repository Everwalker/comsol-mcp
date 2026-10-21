from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
R6G_PACKAGE = ROOT / "repository/_state/windows-primary-owner-r6g-source-prep-r2-20261015/package"
HELPER_PATH = R6G_PACKAGE / "windows_primary_token_owner_probe_r6g.py"
GATE_PATH = R6G_PACKAGE / "windows_primary_token_owner_outer_gate_r6g.py"
HELPER_SHA256 = "6f8f9847af052c4b5f37af313b0c1e5540bccd996718b5152b63cb1191d88f8c"
GATE_SHA256 = "0d8f0b11e5c83bbda6ce3b71595723a246b4f3fbec58bbf09b5bcbbe7679cc99"
EXISTING_SID = "S-1-5-21-2844656816-882443001-4055095909-1001"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("r6g_probe_under_r6h_review", HELPER_PATH)
gate = _load("r6g_gate_under_r6h_review", GATE_PATH)


def _token(*, owner_sid: str = EXISTING_SID, session: int = 3) -> dict:
    return {
        "user_sid": EXISTING_SID,
        "owner_sid": owner_sid,
        "primary_group_sid": "S-1-5-32-545",
        "groups": (("S-1-5-32-545", 7), ("S-1-5-11", 4)),
        "privileges": ((2, 0, 2), (3, 0, 0)),
        "token_id": (12, 0),
        "authentication_id": (34, 0),
        "modified_id": (56, 0),
        "token_type": probe.TOKEN_PRIMARY,
        "session_id": session,
        "elevation_type": 2,
        "elevated": False,
        "integrity_sid": "S-1-16-8192",
        "logon_sids": (("S-1-5-5-1-2", 3221225472),),
    }


def _call_names(function: ast.FunctionDef) -> list[tuple[int, str]]:
    values = []
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                values.append((node.lineno, node.func.id))
            elif isinstance(node.func, ast.Attribute):
                values.append((node.lineno, node.func.attr))
    return sorted(values)


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def test_review_is_bound_to_exact_frozen_r6g_helper_gate_and_current_prep_audit():
    assert hashlib.sha256(HELPER_PATH.read_bytes()).hexdigest() == HELPER_SHA256
    assert hashlib.sha256(GATE_PATH.read_bytes()).hexdigest() == GATE_SHA256
    audit = ROOT / "repository/docs/full_project_continuation_v3/state/WINDOWS_PRIMARY_OWNER_R6G_DETAILED_ACTUAL_READBACK_AUDIT_R1.json"
    assert hashlib.sha256(audit.read_bytes()).hexdigest() == "2e026e8788f949cbaf3dadf2a17e3db9a5581130abdb12b4cdcd316e11bd076a"


def test_duplicate_owner_transition_accepts_only_duplicate_token_owner_change():
    parent = _token(owner_sid="S-1-5-18")
    duplicate_before = _token(owner_sid="S-1-5-18")
    duplicate_after = _token(owner_sid=EXISTING_SID)
    duplicate_after["modified_id"] = (57, 0)
    probe._assert_duplicate_owner_transition(
        parent, copy.deepcopy(parent), duplicate_before, duplicate_after,
        expected_user_sid=EXISTING_SID,
    )


@pytest.mark.parametrize("mutation", [
    "original_privilege",
    "duplicate_groups",
    "duplicate_privileges",
    "duplicate_authentication_id",
    "duplicate_session",
    "duplicate_owner",
    "duplicate_type",
])
def test_duplicate_owner_transition_rejects_any_non_owner_change(mutation: str):
    parent_before = _token(owner_sid="S-1-5-18")
    parent_after = copy.deepcopy(parent_before)
    duplicate_before = _token(owner_sid="S-1-5-18")
    duplicate_after = _token(owner_sid=EXISTING_SID)
    if mutation == "original_privilege":
        parent_after["privileges"] = ((2, 0, 0),)
    elif mutation == "duplicate_groups":
        duplicate_after["groups"] = ()
    elif mutation == "duplicate_privileges":
        duplicate_after["privileges"] = ()
    elif mutation == "duplicate_authentication_id":
        duplicate_after["authentication_id"] = (99, 0)
    elif mutation == "duplicate_session":
        duplicate_after["session_id"] = 4
    elif mutation == "duplicate_owner":
        duplicate_after["owner_sid"] = "S-1-5-18"
    elif mutation == "duplicate_type":
        duplicate_after["token_type"] = 2
    with pytest.raises(probe.ProbeError):
        probe._assert_duplicate_owner_transition(
            parent_before, parent_after, duplicate_before, duplicate_after,
            expected_user_sid=EXISTING_SID,
        )


def test_parent_identity_and_child_token_controls_accept_exact_same_sid_session_image():
    process = {"pid": 901, "birth_filetime_100ns": 123456, "image_path": r"C:\Python\python.exe"}
    child = _token(owner_sid=EXISTING_SID)
    probe._assert_child_identity(
        process, child, expected_user_sid=EXISTING_SID, expected_session=3,
        expected_image=r"c:\python\PYTHON.EXE",
    )
    duplicate = _token(owner_sid=EXISTING_SID)
    probe._assert_child_token_matches_duplicate(duplicate, child)


@pytest.mark.parametrize("field,value", [
    ("image_path", r"C:\other\python.exe"),
    ("session_id", 8),
])
def test_parent_identity_refuses_wrong_image_or_session(field: str, value):
    process = {"pid": 901, "birth_filetime_100ns": 123456, "image_path": r"C:\Python\python.exe"}
    child = _token(owner_sid=EXISTING_SID)
    if field == "image_path":
        process[field] = value
        args = {"expected_session": 3}
    else:
        child[field] = value
        args = {"expected_session": 3}
    with pytest.raises(probe.ProbeError):
        probe._assert_child_identity(process, child, expected_user_sid=EXISTING_SID,
                                     expected_image=r"C:\Python\python.exe", **args)


@pytest.mark.parametrize("field", ["owner_sid", "groups", "privileges", "authentication_id", "session_id"])
def test_child_token_comparison_refuses_mutated_security_context(field: str):
    duplicate = _token(owner_sid=EXISTING_SID)
    child = copy.deepcopy(duplicate)
    child[field] = () if field in {"groups", "privileges"} else "S-1-5-18"
    with pytest.raises(probe.ProbeError):
        probe._assert_child_token_matches_duplicate(duplicate, child)


def test_process_lease_refuses_to_close_before_natural_terminal():
    closed = []
    pi = probe.PROCESS_INFORMATION()
    pi.hProcess, pi.hThread, pi.dwProcessId, pi.dwThreadId = 101, 202, 901, 902
    api = types.SimpleNamespace(close=lambda handle, label: closed.append((handle, label)))
    lease = probe.ProcessLease(api, pi, command_line="fixed", started_monotonic=0.0)
    try:
        with pytest.raises(probe.ProbeError, match="cannot be released before"):
            lease.close_after_terminal()
        assert closed == []
        assert lease in probe._PROCESS_LEASES
    finally:
        if lease in probe._PROCESS_LEASES:
            probe._PROCESS_LEASES.remove(lease)


def test_process_lease_closes_only_original_process_information_handles_after_terminal():
    closed = []
    pi = probe.PROCESS_INFORMATION()
    pi.hProcess, pi.hThread, pi.dwProcessId, pi.dwThreadId = 101, 202, 901, 902
    api = types.SimpleNamespace(close=lambda handle, label: closed.append((handle, label)))
    lease = probe.ProcessLease(api, pi, command_line="fixed", started_monotonic=0.0)
    lease.terminal_observed = True
    lease.terminal_code = 0
    try:
        lease.close_after_terminal()
        assert closed == [(202, "original process-information thread"),
                          (101, "original process-information process")]
        assert lease.state == "RELEASED_AFTER_NATURAL_TERMINAL"
        assert lease not in probe._PROCESS_LEASES
    finally:
        if lease in probe._PROCESS_LEASES:
            probe._PROCESS_LEASES.remove(lease)


def test_process_owner_controller_marks_cutoff_unknown_then_passively_waits_same_handle(monkeypatch):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(probe, "time", clock)
    wait_calls = []
    closed = []

    class Kernel:
        def WaitForSingleObject(self, handle, timeout_ms):
            wait_calls.append((probe._handle_value(handle), timeout_ms))
            if len(wait_calls) == 1:
                clock.now = 61.0
                return probe.WAIT_TIMEOUT
            return probe.WAIT_OBJECT_0

        def GetExitCodeProcess(self, handle, output):
            output._obj.value = 0
            return 1

    class API:
        k = Kernel()

        @staticmethod
        def _check(result, label):
            if not result:
                raise AssertionError(label)

        @staticmethod
        def close(handle, label):
            closed.append((probe._handle_value(handle), label))

    class RootLease:
        state = "PINNED_PREBIRTH"
        unknown_reason = None

        def __init__(self):
            self.timeouts = []
            self.finished = []

        def observe_wait_timeout(self, handle, result):
            self.timeouts.append((probe._handle_value(handle), result))

        def finish_after_natural_terminal(self, handle, wait_result, exit_code):
            self.finished.append((probe._handle_value(handle), wait_result, exit_code))
            return {"root_owner_retained_through_terminal": True,
                    "timeout_unknown_resolved_by_original_handle_terminal": True}

    persisted = []

    def persist(path, record):
        persisted.append(dict(record))
        return True, None

    monkeypatch.setattr(probe, "_append_event_best_effort", persist)
    pi = probe.PROCESS_INFORMATION()
    pi.hProcess, pi.hThread, pi.dwProcessId, pi.dwThreadId = 0x1111, 0x2222, 901, 902
    lease = probe.ProcessLease(API(), pi, command_line="fixed", started_monotonic=0.0)
    lease.identity = {"pid": 901, "birth_filetime_100ns": 123456,
                      "image_path": r"C:\Python\python.exe"}
    lease.child_token = _token(owner_sid=EXISTING_SID)
    root_lease = RootLease()
    controller = probe.R6GProcessOwnerController(root_lease)
    controller.process_lease = lease
    try:
        assert controller.wait_to_natural_terminal() == 0
        assert wait_calls == [(0x1111, 60_000), (0x1111, probe.PASSIVE_WAIT_SLICE_MS)]
        assert root_lease.timeouts == [(0x1111, probe.WAIT_TIMEOUT)]
        assert root_lease.finished == [(0x1111, probe.WAIT_OBJECT_0, 0)]
        assert closed == [(0x2222, "original process-information thread"),
                          (0x1111, "original process-information process")]
        assert lease.deadline_exceeded is True
        assert lease.terminal_observed is True
        assert controller.state == "R6G_OWNER_AND_PROCESS_HANDLES_RELEASED_AFTER_TERMINAL"
        assert any(r.get("status") == "UNKNOWN_RUNNING_AFTER_60S_CUTOFF" for r in persisted)
        assert any(r.get("status") == "NATURALLY_TERMINAL_R6G_ROOT_RELEASED" for r in persisted)
    finally:
        if lease in probe._PROCESS_LEASES:
            probe._PROCESS_LEASES.remove(lease)


def test_actual_parent_checks_identity_before_releasing_event_and_child_only_waits_then_writes(tmp_path, monkeypatch):
    helper_tree = ast.parse(HELPER_PATH.read_text(encoding="utf-8"))
    parent_main = _function(helper_tree, "_main")
    parent_calls = _call_names(parent_main)
    line_for = {name: min(line for line, actual in parent_calls if actual == name)
                for name in ("_assert_child_identity", "_assert_child_token_matches_duplicate",
                             "SetEvent", "wait_to_natural_terminal")}
    assert line_for["_assert_child_identity"] < line_for["SetEvent"]
    assert line_for["_assert_child_token_matches_duplicate"] < line_for["SetEvent"]
    assert line_for["wait_to_natural_terminal"] > line_for["SetEvent"]

    child = _function(helper_tree, "_child_wait_and_create")
    child_calls = _call_names(child)
    child_names = {name for _, name in child_calls}
    assert "WaitForSingleObject" in child_names
    assert "OpenEventW" in child_names
    assert not child_names & {"SetEvent", "process_identity", "child_token_snapshot",
                              "CreateProcessWithTokenW", "OpenProcess"}
    assert "O_EXCL" in ast.unparse(child)

    output_root = tmp_path / "owner-probe"
    output_root.mkdir()
    output_file = output_root / "owner-probe.bin"
    event_name = "Local\\R6H-SYNTHETIC-EVENT"

    class Kernel:
        wait_result = probe.WAIT_TIMEOUT

        @staticmethod
        def OpenEventW(_access, _inherit, name):
            assert name == event_name
            return 0x991

        def WaitForSingleObject(self, event, timeout):
            assert event == 0x991
            assert timeout == probe.CHILD_EVENT_WAIT_MS
            return self.wait_result

        @staticmethod
        def GetFileAttributesW(path):
            assert path == str(output_root)
            return probe.FILE_ATTRIBUTE_DIRECTORY

    class FakeAPI:
        def __init__(self):
            self.k = Kernel()

        @staticmethod
        def close(_handle, _label):
            return None

    fake_api = FakeAPI()
    fake_os = types.SimpleNamespace(
        name="nt", O_WRONLY=os.O_WRONLY, O_CREAT=os.O_CREAT, O_EXCL=os.O_EXCL,
        O_BINARY=getattr(os, "O_BINARY", 0), open=os.open, fdopen=os.fdopen, fsync=os.fsync,
    )
    monkeypatch.setattr(probe, "os", fake_os)
    monkeypatch.setattr(probe, "sys", types.SimpleNamespace(
        executable=probe.PYTHON_EXE, version=probe.PYTHON_VERSION,
        argv=[str(HELPER_PATH), "--child-wait", event_name],
    ))
    monkeypatch.setattr(probe, "HELPER_PATH", __import__("ntpath").abspath(str(HELPER_PATH)))
    monkeypatch.setattr(probe, "OUTPUT_ROOT", str(output_root))
    monkeypatch.setattr(probe, "OUTPUT_FILE", str(output_file))
    monkeypatch.setattr(probe, "Win32", lambda: fake_api)
    assert probe._child_wait_and_create(event_name) == 74
    assert not output_file.exists()
    fake_api.k.wait_result = probe.WAIT_OBJECT_0
    assert probe._child_wait_and_create(event_name) == 0
    assert output_file.read_bytes() == probe.PROBE_BYTES
    assert sorted(path.name for path in output_root.iterdir()) == ["owner-probe.bin"]


def test_gate_verifies_approval_and_live_snapshot_before_same_process_dispatch():
    gate_tree = ast.parse(GATE_PATH.read_text(encoding="utf-8"))
    dispatch = _function(gate_tree, "_dispatch_after_prebirth")
    names = [name for _, name in _call_names(dispatch)]
    assert names.index("_validate_approval") < names.index("verify_snapshot")
    assert names.index("verify_snapshot") < names.index("invoke_helper")
    gate_main = _function(gate_tree, "_main")
    assert sum(name == "_dispatch_after_prebirth" for _, name in _call_names(gate_main)) == 1
    invoke = _function(gate_tree, "_invoke_reviewed_helper")
    call_text = ast.unparse(invoke)
    assert "run_path" in call_text and "run_name='__main__'" in call_text
    assert "sys.argv = [HELPER_PATH, '--binding', INNER_BINDING_PATH" in call_text


def test_parent_consumes_once_before_duplicate_and_has_no_privilege_or_launch_fallbacks():
    tree = ast.parse(HELPER_PATH.read_text(encoding="utf-8"))
    parent_main = _function(tree, "_main")
    calls = _call_names(parent_main)
    first_line = {name: min(line for line, actual in calls if actual == name)
                  for name in ("consume_once_relative", "DuplicateTokenEx", "SetTokenInformation",
                               "CreateProcessWithTokenW")}
    assert first_line["consume_once_relative"] < first_line["DuplicateTokenEx"]
    assert first_line["DuplicateTokenEx"] < first_line["SetTokenInformation"]
    assert first_line["SetTokenInformation"] < first_line["CreateProcessWithTokenW"]
    call_names = {name for _, name in calls}
    assert not call_names & {"AdjustTokenPrivileges", "CreateProcessAsUserW", "LogonUserW",
                             "TerminateProcess", "OpenProcess"}
    assert sum(name == "CreateProcessWithTokenW" for _, name in calls) == 1
