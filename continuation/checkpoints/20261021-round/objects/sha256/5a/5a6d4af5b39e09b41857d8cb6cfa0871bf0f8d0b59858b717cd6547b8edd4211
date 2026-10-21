from __future__ import annotations

import ctypes as C
import builtins
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from tools import windows_primary_owner_authority_root_lease_r6b as lease_module
from tools import windows_primary_token_owner_outer_gate_r6d as gate
from tools import windows_primary_token_owner_probe_r6d as probe


def _raw(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _inner(helper_sha):
    return {
        "schema": "cv3-windows-primary-token-owner-binding-r6d/1",
        "task_root": gate.TASK_ROOT,
        "python_executable": gate.PYTHON_EXE,
        "python_version": gate.PYTHON_VERSION,
        "helper_path": gate.HELPER_PATH,
        "helper_sha256": helper_sha,
        "r6b_lease_source_sha256": gate.R6B_LEASE_SOURCE_SHA256,
        "r6b_lease_controls_sha256": gate.R6B_LEASE_CONTROLS_SHA256,
        "authority_root": gate.AUTHORITY_ROOT,
        "binding_path": gate.INNER_BINDING_PATH,
        "approval_path": gate.APPROVAL_PATH,
        "once_path": gate.ONCE_PATH,
        "output_root": gate.OUTPUT_ROOT,
        "output_file": gate.OUTPUT_FILE,
        "expected_user_sid": gate.EXPECTED_USER_SID,
        "case_processes": 1,
        "max_active_seconds": 60,
        "COMSOL": 0,
        "GUI": 0,
        "JVM": 0,
        "Study": 0,
        "solver": 0,
        "no_privilege_enablement": True,
        "no_account_or_acl_mutation": True,
        "no_process_signal_or_pid_reopen": True,
    }


def _candidate():
    helper_raw = b"candidate r6b helper source"
    gate_raw = b"candidate r6b gate source"
    inner = _inner(_sha(helper_raw))
    inner_raw = _raw(inner)
    source = {name: "d" * 64 for name in gate.SOURCE_FINGERPRINT_FIELDS}
    source.update({
        "helper_sha256": _sha(helper_raw),
        "gate_sha256": _sha(gate_raw),
        "inner_binding_sha256": _sha(inner_raw),
        "r6b_lease_source_sha256": gate.R6B_LEASE_SOURCE_SHA256,
        "r6b_lease_controls_sha256": gate.R6B_LEASE_CONTROLS_SHA256,
    })
    outer = {
        "schema": gate.OUTER_SCHEMA,
        "status": "REMOTE_PREPARED_CLOSED",
        "candidate": gate.OUTER_CANDIDATE,
        "identity_domains": gate.ROOT_IDENTITY_DOMAINS,
        "source_fingerprints": source,
        "paths": gate.EXPECTED_PATHS,
        "path_proofs": {name: {} for name in gate.PATH_PROOF_FIELDS},
        "r14_source_path_readback": {f"member-{index:03d}": {} for index in range(179)},
        "r14_source_path_readback_count": 179,
        "limits": gate.OUTER_LIMITS,
        "native_execution": gate.OUTER_NATIVE_EXECUTION,
        "closed_authority": gate.OUTER_CLOSED_AUTHORITY,
    }
    outer_raw = _raw(outer)
    proof = {"identity": {"device": 8, "inode": 13}, "closed": True}
    proof_sha = gate._sha256(gate._canonical_json(gate._r5j_identity_projection(proof)))
    approval = {
        "schema": gate.APPROVAL_SCHEMA,
        "status": "APPROVED_EXECUTE_ONCE",
        "binding_sha256": source["inner_binding_sha256"],
        "outer_binding_sha256": _sha(outer_raw),
        "helper_sha256": source["helper_sha256"],
        "helper_controls_sha256": source["helper_controls_sha256"],
        "gate_sha256": source["gate_sha256"],
        "gate_controls_sha256": source["gate_controls_sha256"],
        "r6b_lease_source_sha256": source["r6b_lease_source_sha256"],
        "r6b_lease_controls_sha256": source["r6b_lease_controls_sha256"],
        "python_executable_sha256": source["python_executable_sha256"],
        "python_executable_path_proof_sha256": source["python_executable_path_proof_sha256"],
        "protected13_manifest_sha256": source["protected13_manifest_sha256"],
        "current_resume_sha256": source["current_resume_sha256"],
        "source_plan_sha256": source["source_plan_sha256"],
        "source_review_sha256": source["source_review_sha256"],
        "r14_binding_sha256": source["r14_binding_sha256"],
        "r14_source_closure_sha256": source["r14_source_closure_sha256"],
        "source_path_proofs_sha256": source["source_path_proofs_sha256"],
        "outer_binding_file_proof_sha256": proof_sha,
        "budget": dict(gate.APPROVAL_BUDGET),
        "native_nonce": "a" * 32,
    }
    approval_raw = _raw(approval)
    live = {
        "outer_binding": {"private_path": proof, "sha256": _sha(outer_raw)},
        "approval": {"sha256": _sha(approval_raw)},
    }
    return outer, outer_raw, inner_raw, approval, approval_raw, helper_raw, gate_raw, live


def test_r5j_snapshot_precedes_same_process_dispatch_and_passes_r6b_source_module():
    outer, outer_raw, inner_raw, approval, approval_raw, helper_raw, gate_raw, live = _candidate()
    calls = []

    def invoke_helper(*, outer, live, r6b_lease_module):
        calls.append(("helper", outer, live, r6b_lease_module))
        return "helper-in-same-process"

    result = gate._dispatch_after_prebirth(
        outer=outer, approval=approval, outer_raw=outer_raw,
        inner_raw=inner_raw, approval_raw=approval_raw,
        helper_raw=helper_raw, gate_raw=gate_raw,
        verify_snapshot=lambda: calls.append(("snapshot",)) or live,
        invoke_helper=invoke_helper,
    )

    assert result == "helper-in-same-process"
    assert [row[0] for row in calls] == ["snapshot", "helper"]
    _, passed_outer, passed_live, module = calls[1]
    assert passed_outer is outer and passed_live is live
    assert module.R6B_SCHEMA == "cv3-windows-primary-owner-authority-root-lease-r6b/1"
    lease_source = (Path(probe.__file__).resolve().parents[1]
                    / "tools/windows_primary_owner_authority_root_lease_r6b.py").read_bytes()
    assert _sha(lease_source) == gate.R6B_LEASE_SOURCE_SHA256
    assert gate.R6B_LEASE_CONTROLS_SHA256 == _sha(
        (Path(probe.__file__).resolve().parents[1]
         / "tests/test_windows_primary_owner_authority_root_lease_r6b.py").read_bytes()
    )


def test_r6b_lease_hash_mismatch_rejects_before_helper_dispatch():
    outer, outer_raw, inner_raw, approval, approval_raw, helper_raw, gate_raw, live = _candidate()
    outer["source_fingerprints"]["r6b_lease_source_sha256"] = "e" * 64
    inner = json.loads(inner_raw)
    inner["r6b_lease_source_sha256"] = "e" * 64
    inner_raw = _raw(inner)
    outer["source_fingerprints"]["inner_binding_sha256"] = _sha(inner_raw)
    outer_raw = _raw(outer)
    approval["outer_binding_sha256"] = _sha(outer_raw)
    approval["binding_sha256"] = _sha(inner_raw)
    approval["r6b_lease_source_sha256"] = "e" * 64
    approval_raw = _raw(approval)
    live["outer_binding"]["sha256"] = _sha(outer_raw)
    live["approval"]["sha256"] = _sha(approval_raw)
    calls = []
    with pytest.raises(gate.GateError, match="embedded reviewed bytes"):
        gate._dispatch_after_prebirth(
            outer=outer, approval=approval, outer_raw=outer_raw,
            inner_raw=inner_raw, approval_raw=approval_raw,
            helper_raw=helper_raw, gate_raw=gate_raw,
            verify_snapshot=lambda: live,
            invoke_helper=lambda **kwargs: calls.append(kwargs),
        )
    assert calls == []


def test_r6b_fixed_namespace_is_distinct_and_has_exact_five_file_prep_shape():
    assert "helpers-primary-owner-r6d" in gate.HELPER_DIR
    assert "authority-primary-owner-r6d" in gate.AUTHORITY_ROOT
    assert "primary-owner-probe-r6d" in gate.OUTPUT_ROOT
    assert "cases-primary-owner-r6d" in gate.TASK_ROOT + r"\cases-primary-owner-r6d"
    future_prep_files = {
        Path(gate.HELPER_PATH).name,
        Path(gate.GATE_PATH).name,
        "BINDING.json",
        "PREBIRTH_BINDING.json",
        "MAIN_APPROVAL.json",
    }
    assert len(future_prep_files) == 5
    assert gate.OUTER_CLOSED_AUTHORITY["MAIN_APPROVAL"] == "ABSENT"
    assert gate.OUTER_CLOSED_AUTHORITY["ONCE_USED"] == "ABSENT"
    assert gate.OUTER_NATIVE_EXECUTION["CreateProcessWithTokenW"] == "NOT_CALLED"


class FakeHandle:
    def __init__(self, value=125):
        self.value = value


class FakeLeaseOps:
    def __init__(self):
        self.finish_calls = 0
        self.effect_started = False

    def pin_and_verify_prebirth(self):
        return {"status": "PREBIRTH_ROOT_PINNED_STRICT"}

    def create_once_relative(self, raw, expected_sha256):
        self.effect_started = True
        return {
            "status": "ONCE_CREATED_RELATIVE_FILE_CREATE_READBACK_PASS",
            "create_disposition": "FILE_CREATE",
            "relative_to_original_root_handle": True,
            "sha256": expected_sha256,
            "bytes": len(raw),
        }

    def finish_original_owner(self):
        self.finish_calls += 1

    def cleanup_before_once(self, cause):
        pass


class FakeKernel:
    def __init__(self, handle, wait_results, exit_code=0):
        self.handle = handle
        self.wait_results = list(wait_results)
        self.exit_code = exit_code
        self.wait_calls = []
        self.pid_reopens = 0
        self.signals = 0

    def WaitForSingleObject(self, handle, timeout_ms):
        self.wait_calls.append((handle, timeout_ms))
        assert handle is self.handle
        if not self.wait_results:
            return probe.WAIT_TIMEOUT
        return self.wait_results.pop(0)

    def GetExitCodeProcess(self, handle, output):
        assert handle is self.handle
        C.cast(output, C.POINTER(C.c_uint32)).contents.value = self.exit_code
        return 1


class FakeWinApi:
    def __init__(self, kernel):
        self.k = kernel

    def _check(self, result, _label):
        if not result:
            raise OSError("injected Win32 failure")
        return result

    def process_identity(self, _handle):
        return {"pid": 31940, "birth_filetime_100ns": 638960000000000000,
                "image_path": r"C:\Python\python.exe"}


class TrackingWinApi(FakeWinApi):
    def __init__(self, kernel, *, fail_close=False):
        super().__init__(kernel)
        self.fail_close = fail_close
        self.close_attempts = []
        self.closed_handles = []

    def close(self, handle, label):
        self.close_attempts.append((handle, label))
        if self.fail_close:
            raise OSError("injected original-handle close failure")
        self.closed_handles.append(handle)


class FakeProcessLease:
    def __init__(self, handle, kernel):
        self.api = FakeWinApi(kernel)
        self.process_handle = handle
        self.pid = 31940
        self.thread_id = 11
        self.started_monotonic = time.monotonic() - probe.MAX_ACTIVE_SECONDS - 2
        self.deadline_exceeded = False
        self.terminal_observed = False
        self.terminal_code = None
        self.state = "CREATED_UNVERIFIED"
        self.identity = {"pid": self.pid}
        self.child_token = {"user_sid": "S-1-test"}
        self.events = []
        self.close_calls = 0

    def original_handles(self):
        return {"process_handle_object": id(self.process_handle), "pid_from_PROCESS_INFORMATION": self.pid}

    def record_event(self, row):
        self.events.append(dict(row))

    def close_after_terminal(self):
        assert self.terminal_observed
        self.close_calls += 1
        self.state = "RELEASED_AFTER_NATURAL_TERMINAL"


@pytest.fixture(autouse=True)
def clean_r6b_lease_indexes(monkeypatch):
    lease_module._ACTIVE_LEASES.clear()
    lease_module._UNKNOWN_LEASES.clear()
    probe._PROCESS_LEASES.clear()
    probe._PARTIAL_PROCESS_INFORMATION.clear()
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)
    yield
    lease_module._ACTIVE_LEASES.clear()
    lease_module._UNKNOWN_LEASES.clear()
    probe._PROCESS_LEASES.clear()
    probe._PARTIAL_PROCESS_INFORMATION.clear()


def _live_root_lease(handle):
    ops = FakeLeaseOps()
    root = lease_module.AuthorityRootLeaseR6B(ops)
    root.pin_after_prebirth()
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    root.consume_once_relative(raw, _sha(raw))
    root.bind_original_birth(handle, 31940, 638960000000000000, r"C:\Python\python.exe")
    return root, ops


def _unbound_once_root_lease():
    ops = FakeLeaseOps()
    root = lease_module.AuthorityRootLeaseR6B(ops)
    root.pin_after_prebirth()
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    root.consume_once_relative(raw, _sha(raw))
    return root, ops


def _actual_process_lease(handle, kernel, *, api=None, started_monotonic=None):
    api = api or TrackingWinApi(kernel)
    process_info = SimpleNamespace(
        hProcess=handle,
        hThread=FakeHandle(126),
        dwProcessId=31940,
        dwThreadId=11,
    )
    lease = probe.ProcessLease(
        api, process_info, command_line='"C:\\Python\\python.exe" -B probe.py',
        started_monotonic=time.monotonic() if started_monotonic is None else started_monotonic,
    )
    return lease, api


def _start_permanent_hold(monkeypatch, target):
    entered = threading.Event()
    never_release = threading.Event()

    def passive_wait(_seconds):
        entered.set()
        never_release.wait()

    monkeypatch.setattr(probe.time, "sleep", passive_wait)
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    assert entered.wait(2), "controller did not enter its passive owner hold"
    assert thread.is_alive(), "controller escaped its owner hold"
    return thread


def test_local_controller_retains_exact_process_and_root_leases_through_timeout_terminal():
    handle = FakeHandle()
    root, ops = _live_root_lease(handle)
    kernel = FakeKernel(handle, [probe.WAIT_TIMEOUT, probe.WAIT_OBJECT_0], exit_code=0)
    process = FakeProcessLease(handle, kernel)
    controller = probe.R6DProcessOwnerController(root)
    controller.process_lease = process

    result = controller.wait_to_natural_terminal()

    assert result == 0
    assert controller.state == "R6D_OWNER_AND_PROCESS_HANDLES_RELEASED_AFTER_TERMINAL"
    assert root.state == "TERMINAL_AND_ORIGINAL_OWNER_RELEASED"
    assert root.original_process_handle is handle
    assert all(row[0] is handle for row in kernel.wait_calls)
    assert kernel.wait_calls == [(handle, 0), (handle, probe.PASSIVE_WAIT_SLICE_MS)]
    assert ops.finish_calls == 1
    assert process.close_calls == 1
    assert process.terminal_observed is True
    assert kernel.pid_reopens == kernel.signals == 0
    assert lease_module.active_lease_count() == lease_module.unknown_lease_count() == 0


def test_wait_failed_unknown_stays_in_live_controller_even_if_later_wait_is_signaled(monkeypatch):
    handle = FakeHandle()
    root, ops = _live_root_lease(handle)
    kernel = FakeKernel(handle, [probe.WAIT_FAILED, probe.WAIT_OBJECT_0], exit_code=0)
    process = FakeProcessLease(handle, kernel)
    controller = probe.R6DProcessOwnerController(root)
    controller.process_lease = process
    entered = threading.Event()
    never_release = threading.Event()

    def allow_one_passive_retry_then_block(_seconds):
        if len(kernel.wait_calls) < 2:
            return
        entered.set()
        never_release.wait()

    monkeypatch.setattr(probe.time, "sleep", allow_one_passive_retry_then_block)
    thread = threading.Thread(target=controller.hold_before_parent_handle_cleanup, daemon=True)
    thread.start()
    assert entered.wait(2), "unqualified UNKNOWN did not remain in its owner hold"

    assert root.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert root.unknown_terminal_release_eligible is False
    assert ops.finish_calls == 0
    assert process.close_calls == 0
    assert controller.process_lease is process
    assert thread.is_alive()
    assert all(row[0] is handle for row in kernel.wait_calls)
    assert len(kernel.wait_calls) == 2
    assert kernel.pid_reopens == kernel.signals == 0


def test_once_consumed_without_process_handle_parks_controller_instead_of_registry_only_owner(monkeypatch):
    handle = FakeHandle()
    root, ops = _live_root_lease(handle)
    root.state = "ONCE_CONSUMED_ROOT_RETAINED"
    controller = probe.R6DProcessOwnerController(root)
    thread = _start_permanent_hold(monkeypatch, controller.hold_before_parent_handle_cleanup)

    assert controller.root_lease is root
    assert controller.process_lease is None
    assert root.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease_module.active_lease_count() == lease_module.unknown_lease_count() == 1
    assert ops.finish_calls == 0
    assert thread.is_alive()


def test_process_lease_append_and_broken_stdout_failures_are_diagnostic_only(monkeypatch):
    handle = FakeHandle()
    kernel = FakeKernel(handle, [probe.WAIT_TIMEOUT])
    process, api = _actual_process_lease(handle, kernel)

    def fail_append(*_args, **_kwargs):
        raise OSError("injected _append_event failure")

    def broken_stdout(*_args, **_kwargs):
        raise BrokenPipeError("injected stdout BrokenPipe")

    monkeypatch.setattr(probe, "_append_event", fail_append)
    monkeypatch.setattr(builtins, "print", broken_stdout)
    process.record_event({"status": "POSTBIRTH_TEST"})

    assert process in probe._PROCESS_LEASES
    assert process.process_handle is handle
    assert api.close_attempts == api.closed_handles == []
    assert process.persistence_errors and "injected _append_event failure" in process.persistence_errors[0]
    assert process.persistence_error_count == 1


def test_initial_park_log_failure_cannot_prevent_same_controller_passive_hold(monkeypatch):
    handle = FakeHandle()
    root, ops = _live_root_lease(handle)
    kernel = FakeKernel(handle, [probe.WAIT_TIMEOUT])
    process, api = _actual_process_lease(handle, kernel)
    controller = probe.R6DProcessOwnerController(root)
    controller.process_lease = process

    def fail_append(*_args, **_kwargs):
        raise OSError("injected initial park append failure")

    def broken_stdout(*_args, **_kwargs):
        raise BrokenPipeError("injected stdout BrokenPipe")

    monkeypatch.setattr(probe, "_append_event", fail_append)
    monkeypatch.setattr(builtins, "print", broken_stdout)
    thread = _start_permanent_hold(monkeypatch, lambda: controller._park_forever("injected park"))

    assert controller.state == "UNKNOWN_R6D_LOCAL_CONTROLLER_RETAINED"
    assert controller.process_lease is process
    assert controller.root_lease is root
    assert process.process_handle is handle
    assert api.close_attempts == api.closed_handles == []
    assert ops.finish_calls == 0
    assert thread.is_alive()


def test_terminal_close_and_diagnostic_failures_keep_original_controller_parked(monkeypatch):
    handle = FakeHandle()
    root, ops = _unbound_once_root_lease()
    kernel = FakeKernel(handle, [probe.WAIT_OBJECT_0], exit_code=0)
    api = TrackingWinApi(kernel, fail_close=True)
    process, _ = _actual_process_lease(handle, kernel, api=api,
                                       started_monotonic=time.monotonic() - probe.MAX_ACTIVE_SECONDS - 1)
    controller = probe.R6DProcessOwnerController(root)
    monkeypatch.setattr(probe, "_append_event", lambda *_args, **_kwargs: None)
    controller.attach_process(process)

    def fail_append(*_args, **_kwargs):
        raise OSError("injected terminal-close event append failure")

    def broken_stdout(*_args, **_kwargs):
        raise BrokenPipeError("injected stdout BrokenPipe")

    monkeypatch.setattr(probe, "_append_event", fail_append)
    monkeypatch.setattr(builtins, "print", broken_stdout)
    thread = _start_permanent_hold(monkeypatch, controller.hold_before_parent_handle_cleanup)

    assert controller.state == "UNKNOWN_R6D_LOCAL_CONTROLLER_RETAINED"
    assert controller.process_lease is process
    assert controller.root_lease is root
    assert process.process_handle is handle
    assert process.terminal_observed is True
    assert len(api.close_attempts) == 2
    assert api.closed_handles == []
    assert process in probe._PROCESS_LEASES
    assert root.state == "TERMINAL_AND_ORIGINAL_OWNER_RELEASED"
    assert ops.finish_calls == 1
    assert thread.is_alive()


def test_postbirth_attach_log_failure_then_outer_holder_exception_keeps_exact_owner(monkeypatch):
    handle = FakeHandle()
    root, ops = _unbound_once_root_lease()
    kernel = FakeKernel(handle, [])
    process, api = _actual_process_lease(handle, kernel)
    controller = probe.R6DProcessOwnerController(root)

    def fail_append(*_args, **_kwargs):
        raise OSError("injected postbirth attach append failure")

    def broken_stdout(*_args, **_kwargs):
        raise BrokenPipeError("injected stdout BrokenPipe")

    monkeypatch.setattr(probe, "_append_event", fail_append)
    monkeypatch.setattr(builtins, "print", broken_stdout)
    controller.attach_process(process)
    assert controller.process_lease is process
    assert controller.bind_error is None
    assert root.original_process_handle is handle
    assert process.process_handle is handle
    assert api.close_attempts == api.closed_handles == []
    assert ops.finish_calls == 0

    def unexpected_holder_error():
        raise OSError("injected holder observer exception")

    monkeypatch.setattr(controller, "wait_to_natural_terminal", unexpected_holder_error)
    thread = _start_permanent_hold(monkeypatch, controller.hold_before_parent_handle_cleanup)

    assert controller.state == "UNKNOWN_R6D_OUTER_HOLDER_EXCEPTION_RETAINED"
    assert controller.process_lease is process
    assert controller.root_lease is root
    assert process.process_handle is handle
    assert api.close_attempts == api.closed_handles == []
    assert ops.finish_calls == 0
    assert thread.is_alive()




def _released_terminal_functional_pass():
    handle = FakeHandle(7701)
    root, ops = _unbound_once_root_lease()
    kernel = FakeKernel(handle, [probe.WAIT_OBJECT_0], exit_code=0)
    process, api = _actual_process_lease(handle, kernel)
    process.identity = api.process_identity(handle)
    process.child_token = {
        "user_sid": probe.EXPECTED_USER_SID,
        "owner_sid": probe.EXPECTED_USER_SID,
        "token_type": probe.TOKEN_PRIMARY,
        "session_id": 2,
    }
    controller = probe.R6DProcessOwnerController(root)
    controller.attach_process(process)
    terminal_code = controller.wait_to_natural_terminal()
    assert terminal_code == 0
    assert controller.state == "R6D_OWNER_AND_PROCESS_HANDLES_RELEASED_AFTER_TERMINAL"
    assert process.terminal_observed is True
    assert process.state == "RELEASED_AFTER_NATURAL_TERMINAL"
    assert root.state == "TERMINAL_AND_ORIGINAL_OWNER_RELEASED"
    assert ops.finish_calls == 1
    result = {
        "schema": "cv3-windows-primary-token-owner-probe-result-r6d/1",
        "status": "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE",
        "source": {"helper_sha256": "a" * 64},
        "process": process.identity,
        "child_token": process.child_token,
        "original_process_information": process.original_handles(),
        "command_argv": [r"C:\Python\python.exe", "-I", "-B", "probe.py"],
        "command_line": r'"C:\Python\python.exe" -I -B probe.py',
        "child_return_code": terminal_code,
        "deadline_exceeded": process.deadline_exceeded,
        "parent_primary_unchanged": True,
        "file_owner_sid": probe.EXPECTED_USER_SID,
        "file_sha256": hashlib.sha256(probe.PROBE_BYTES).hexdigest(),
        "file_bytes": len(probe.PROBE_BYTES),
        "native_tests": 0,
        "COMSOL": 0, "GUI": 0, "JVM": 0, "Study": 0, "solver": 0,
        "scope": "one same-SID process-primary owner control; not COMSOL/product/scientific acceptance",
    }
    return result, process


def test_prior_postbirth_event_write_failure_overrides_pass_after_exact_release(monkeypatch):
    persisted=[]
    calls=0

    def fail_once_then_store(_path, record):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected early event-log failure")
        persisted.append(dict(record))

    monkeypatch.setattr(probe, "_append_event", fail_once_then_store)
    result, process = _released_terminal_functional_pass()
    assert process.persistence_error_count == 1

    status, rc, final_persisted = probe._finalize_terminal_evidence(result, process)

    assert (status, rc, final_persisted) == ("FAIL_EVIDENCE_PERSISTENCE", 1, True)
    assert result["functional_status"] == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"
    assert result["status"] == "FAIL_EVIDENCE_PERSISTENCE"
    assert result["event_persistence_error_count"] == 1
    assert result["child_return_code"] == 0
    assert result["parent_primary_unchanged"] is True
    assert result["file_owner_sid"] == probe.EXPECTED_USER_SID
    assert result["file_sha256"] == hashlib.sha256(probe.PROBE_BYTES).hexdigest()
    assert result["file_bytes"] == len(probe.PROBE_BYTES)
    final_rows = [row for row in persisted if row.get("schema") == "cv3-windows-primary-token-owner-probe-result-r6d/1"]
    assert len(final_rows) == 1
    assert final_rows[0]["status"] == "FAIL_EVIDENCE_PERSISTENCE"
    assert final_rows[0]["functional_status"] == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"


def test_final_receipt_append_failure_overrides_functional_pass_without_retry(monkeypatch):
    attempts=[]

    def fail_final_only(_path, record):
        if record.get("schema") == "cv3-windows-primary-token-owner-probe-result-r6d/1":
            attempts.append(dict(record))
            raise OSError("injected final receipt fsync failure")

    monkeypatch.setattr(probe, "_append_event", fail_final_only)
    result, process = _released_terminal_functional_pass()
    assert process.persistence_error_count == 0

    status, rc, final_persisted = probe._finalize_terminal_evidence(result, process)

    assert (status, rc, final_persisted) == ("FAIL_EVIDENCE_PERSISTENCE", 1, False)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"
    assert result["status"] == "FAIL_EVIDENCE_PERSISTENCE"
    assert result["functional_status"] == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"
    assert result["final_receipt_persisted"] is False
    assert "final receipt fsync failure" in result["final_receipt_persistence_error"]
    assert result["child_return_code"] == 0
    assert result["file_owner_sid"] == probe.EXPECTED_USER_SID
    assert result["file_sha256"] == hashlib.sha256(probe.PROBE_BYTES).hexdigest()
    assert result["file_bytes"] == len(probe.PROBE_BYTES)


def test_durable_final_receipt_and_zero_prior_errors_are_required_for_exit_zero(monkeypatch):
    persisted=[]

    def durable_append(_path, record):
        persisted.append(dict(record))

    monkeypatch.setattr(probe, "_append_event", durable_append)
    result, process = _released_terminal_functional_pass()
    assert process.persistence_error_count == 0

    status, rc, final_persisted = probe._finalize_terminal_evidence(result, process)

    assert (status, rc, final_persisted) == ("PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE", 0, True)
    final_rows = [row for row in persisted if row.get("schema") == "cv3-windows-primary-token-owner-probe-result-r6d/1"]
    assert len(final_rows) == 1
    assert final_rows[0]["status"] == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"
    assert final_rows[0]["functional_status"] == "PASS_SAME_SID_PRIMARY_OWNER_FILE_PROBE"
    assert result["final_receipt_persisted"] is True
    assert result["event_persistence_error_count"] == 0
    assert result["child_return_code"] == 0
    assert result["file_owner_sid"] == probe.EXPECTED_USER_SID
    assert result["file_sha256"] == hashlib.sha256(probe.PROBE_BYTES).hexdigest()
    assert result["file_bytes"] == len(probe.PROBE_BYTES)
