from __future__ import annotations

import ctypes as C
import hashlib
import json
from pathlib import Path
import time

import pytest

from tools import windows_primary_owner_authority_root_lease_r6b as lease_module
from tools import windows_primary_token_owner_outer_gate_r6b as gate
from tools import windows_primary_token_owner_probe_r6b as probe


def _raw(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _inner(helper_sha):
    return {
        "schema": "cv3-windows-primary-token-owner-binding-r6b/1",
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
    assert "helpers-primary-owner-r6b" in gate.HELPER_DIR
    assert "authority-primary-owner-r6b" in gate.AUTHORITY_ROOT
    assert "primary-owner-probe-r6b" in gate.OUTPUT_ROOT
    assert "cases-primary-owner-r6b" in gate.TASK_ROOT + r"\cases-primary-owner-r6b"
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
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)
    yield
    lease_module._ACTIVE_LEASES.clear()
    lease_module._UNKNOWN_LEASES.clear()


def _live_root_lease(handle):
    ops = FakeLeaseOps()
    root = lease_module.AuthorityRootLeaseR6B(ops)
    root.pin_after_prebirth()
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    root.consume_once_relative(raw, _sha(raw))
    root.bind_original_birth(handle, 31940, 638960000000000000, r"C:\Python\python.exe")
    return root, ops


def test_local_controller_retains_exact_process_and_root_leases_through_timeout_terminal():
    handle = FakeHandle()
    root, ops = _live_root_lease(handle)
    kernel = FakeKernel(handle, [probe.WAIT_TIMEOUT, probe.WAIT_OBJECT_0], exit_code=0)
    process = FakeProcessLease(handle, kernel)
    controller = probe.R6BProcessOwnerController(root)
    controller.process_lease = process

    result = controller.wait_to_natural_terminal()

    assert result == 0
    assert controller.state == "R6B_OWNER_AND_PROCESS_HANDLES_RELEASED_AFTER_TERMINAL"
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
    class Parked(RuntimeError):
        pass

    handle = FakeHandle()
    root, ops = _live_root_lease(handle)
    kernel = FakeKernel(handle, [probe.WAIT_FAILED, probe.WAIT_OBJECT_0], exit_code=0)
    process = FakeProcessLease(handle, kernel)
    controller = probe.R6BProcessOwnerController(root)
    controller.process_lease = process
    monkeypatch.setattr(controller, "_park_forever", lambda reason: (_ for _ in ()).throw(Parked(reason)))

    with pytest.raises(Parked, match="could not be released"):
        controller.wait_to_natural_terminal()

    assert root.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert root.unknown_terminal_release_eligible is False
    assert ops.finish_calls == 0
    assert process.close_calls == 0
    assert controller.process_lease is process
    assert all(row[0] is handle for row in kernel.wait_calls)
    assert kernel.pid_reopens == kernel.signals == 0


def test_once_consumed_without_process_handle_parks_controller_instead_of_registry_only_owner(monkeypatch):
    class Parked(RuntimeError):
        pass

    handle = FakeHandle()
    root, ops = _live_root_lease(handle)
    root.state = "ONCE_CONSUMED_ROOT_RETAINED"
    controller = probe.R6BProcessOwnerController(root)
    monkeypatch.setattr(controller, "_park_forever", lambda reason: (_ for _ in ()).throw(Parked(reason)))

    with pytest.raises(Parked, match="without an original process handle"):
        controller.hold_before_parent_handle_cleanup()

    assert controller.root_lease is root
    assert controller.process_lease is None
    assert root.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease_module.active_lease_count() == lease_module.unknown_lease_count() == 1
    assert ops.finish_calls == 0

