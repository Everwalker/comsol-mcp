from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "repository/tools/windows_primary_owner_authority_root_lease_r6b.py"
SPEC = importlib.util.spec_from_file_location("windows_primary_owner_authority_root_lease_r6b_under_test", SOURCE)
assert SPEC is not None and SPEC.loader is not None
R6B = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R6B
SPEC.loader.exec_module(R6B)


@pytest.fixture(autouse=True)
def isolated_lease_registries():
    R6B._ACTIVE_LEASES.clear()
    R6B._UNKNOWN_LEASES.clear()
    yield
    R6B._ACTIVE_LEASES.clear()
    R6B._UNKNOWN_LEASES.clear()


class FakeLeaseOps:
    def __init__(self, finish_error=None):
        self.finish_calls = 0
        self.cleanup_calls = 0
        self.finish_error = finish_error

    def pin_and_verify_prebirth(self):
        return {"status": "PREBIRTH_ROOT_PINNED_STRICT"}

    def create_once_relative(self, raw, expected_sha256):
        return {
            "status": "ONCE_CREATED_RELATIVE_FILE_CREATE_READBACK_PASS",
            "create_disposition": "FILE_CREATE",
            "relative_to_original_root_handle": True,
            "sha256": expected_sha256,
            "bytes": len(raw),
        }

    def finish_original_owner(self):
        self.finish_calls += 1
        if self.finish_error is not None:
            raise self.finish_error

    def cleanup_before_once(self, cause):
        self.cleanup_calls += 1


class HandleWrapper:
    def __init__(self, value):
        self.value = value


def live_lease(ops=None):
    ops = ops or FakeLeaseOps()
    lease = R6B.AuthorityRootLeaseR6B(ops)
    lease.pin_after_prebirth()
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    lease.consume_once_relative(raw, hashlib.sha256(raw).hexdigest())
    original_handle = HandleWrapper(125)
    lease.bind_original_birth(original_handle, 31940, 638960000000000000, r"C:\Python\python.exe")
    return lease, ops, original_handle


def test_timeout_unknown_releases_only_after_same_handle_natural_terminal():
    lease, ops, handle = live_lease()
    lease.observe_wait_timeout(handle, R6B.WAIT_TIMEOUT)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease.unknown_terminal_release_eligible is True
    assert lease.unknown_reason == "ACTIVE_DEADLINE_WAIT_TIMEOUT"
    assert R6B.active_lease_count() == 1
    assert R6B.unknown_lease_count() == 1
    assert ops.finish_calls == 0

    # A second bounded passive wait remains the same timeout-qualified UNKNOWN.
    lease.observe_wait_timeout(handle, R6B.WAIT_TIMEOUT)
    assert lease.unknown_terminal_release_eligible is True
    assert ops.finish_calls == 0

    result = lease.finish_after_natural_terminal(handle, R6B.WAIT_OBJECT_0, 0)
    assert result["status"] == "TERMINAL_AND_ORIGINAL_OWNER_RELEASED"
    assert result["root_owner_retained_through_terminal"] is True
    assert result["replay"] is False
    assert result["timeout_unknown_resolved_by_original_handle_terminal"] is True
    assert ops.finish_calls == 1
    assert R6B.active_lease_count() == 0
    assert R6B.unknown_lease_count() == 0


def test_unqualified_unknown_cannot_release_even_after_terminal_tuple():
    lease, ops, handle = live_lease()
    lease.observe_wait_timeout(object(), R6B.WAIT_TIMEOUT)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease.unknown_terminal_release_eligible is False
    with pytest.raises(R6B.LeaseError, match="timeout-qualified UNKNOWN"):
        lease.finish_after_natural_terminal(handle, R6B.WAIT_OBJECT_0, 0)
    assert ops.finish_calls == 0
    assert R6B.active_lease_count() == 1
    assert R6B.unknown_lease_count() == 1


def test_timeout_recovery_rejects_a_different_handle_and_does_not_retry_cleanup():
    lease, ops, handle = live_lease()
    lease.observe_wait_timeout(handle, R6B.WAIT_TIMEOUT)
    with pytest.raises(R6B.LeaseError, match="original returned PROCESS_INFORMATION handle"):
        lease.finish_after_natural_terminal(HandleWrapper(125), R6B.WAIT_OBJECT_0, 0)
    assert lease.unknown_terminal_release_eligible is False
    with pytest.raises(R6B.LeaseError, match="timeout-qualified UNKNOWN"):
        lease.finish_after_natural_terminal(handle, R6B.WAIT_OBJECT_0, 0)
    assert ops.finish_calls == 0
    assert R6B.active_lease_count() == 1
    assert R6B.unknown_lease_count() == 1


def test_failure_while_releasing_after_terminal_keeps_owner_unknown_without_retry():
    ops = FakeLeaseOps(finish_error=OSError("injected close failure"))
    lease, ops, handle = live_lease(ops)
    lease.observe_wait_timeout(handle, R6B.WAIT_TIMEOUT)
    with pytest.raises(OSError, match="injected close failure"):
        lease.finish_after_natural_terminal(handle, R6B.WAIT_OBJECT_0, 0)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease.unknown_terminal_release_eligible is False
    with pytest.raises(R6B.LeaseError, match="timeout-qualified UNKNOWN"):
        lease.finish_after_natural_terminal(handle, R6B.WAIT_OBJECT_0, 0)
    assert ops.finish_calls == 1
    assert R6B.active_lease_count() == 1
    assert R6B.unknown_lease_count() == 1


@pytest.mark.parametrize(
    "wait_result,exit_code",
    [
        (R6B.WAIT_TIMEOUT, 0),
        (R6B.WAIT_OBJECT_0, R6B.STILL_ACTIVE),
        (R6B.WAIT_OBJECT_0, -1),
        (R6B.WAIT_OBJECT_0, 0x1_0000_0000),
        (0xFFFFFFFF, 0),
    ],
)
def test_timeout_unknown_requires_exact_natural_terminal_proof(wait_result, exit_code):
    lease, ops, handle = live_lease()
    lease.observe_wait_timeout(handle, R6B.WAIT_TIMEOUT)
    with pytest.raises(R6B.LeaseError):
        lease.finish_after_natural_terminal(handle, wait_result, exit_code)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease.unknown_terminal_release_eligible is False
    assert ops.finish_calls == 0
    assert R6B.active_lease_count() == 1


def test_live_process_can_still_release_on_first_exact_natural_terminal():
    lease, ops, handle = live_lease()
    result = lease.finish_after_natural_terminal(handle, R6B.WAIT_OBJECT_0, 7)
    assert result["exit_code"] == 7
    assert result["timeout_unknown_resolved_by_original_handle_terminal"] is False
    assert ops.finish_calls == 1
    assert R6B.active_lease_count() == 0
    assert R6B.unknown_lease_count() == 0


class FakeRoot:
    pass


class FakeFile:
    def __init__(self, initial_bytes=b""):
        self.identity = (9, 77)
        self.data = initial_bytes


class FakeApi:
    def __init__(self, initial_bytes=b"", postwrite_identity=None, postwrite_size=None):
        self.root = FakeRoot()
        self.file = FakeFile(initial_bytes)
        self.postwrite_identity = postwrite_identity
        self.postwrite_size = postwrite_size
        self.create_calls = []
        self.file_info_sizes = []
        self.info_count = 0

    def create_owned(self, root_handle, leaf):
        self.create_calls.append((root_handle, leaf))
        return self.file

    def info(self, handle, *, directory):
        if directory:
            return ((9, 50), 3)
        self.info_count += 1
        identity = handle.identity
        if self.info_count >= 2 and self.postwrite_identity is not None:
            identity = self.postwrite_identity
        size = len(handle.data)
        if self.info_count >= 2 and self.postwrite_size is not None:
            size = self.postwrite_size
        self.file_info_sizes.append(size)
        return (identity, size)


class FakeGate:
    def _normalize_native_full_fingerprint(self, fingerprint, *, label):
        values = list(fingerprint)
        assert len(values) == 6, label
        assert isinstance(values[0], tuple) and len(values[0]) == 2, label
        assert type(values[1]) is int and values[1] >= 0, label
        assert type(values[5]) is int and values[5] >= 1, label
        return values


class R14OpsHarness(R6B.R14AuthorityRootOpsR6B):
    def __init__(self, *, initial_bytes=b"", postwrite_identity=None, postwrite_size=None,
                 write_digest_override=None):
        self.api = FakeApi(initial_bytes=initial_bytes, postwrite_identity=postwrite_identity,
                           postwrite_size=postwrite_size)
        self.gate = FakeGate()
        self.root_handle = self.api.root
        self.expected_root = {"path_chain": []}
        self.root_full_before = [(9, 50), 3, 100, 200, 300, 1]
        self.once_handle = None
        self.effect_started = False
        self.events = []
        self.write_calls = 0
        self.write_digest_override = write_digest_override
        self.full_getter = self._fingerprint

    def pin_and_verify_prebirth(self):
        return {"status": "PREBIRTH_ROOT_PINNED_STRICT"}

    def finish_original_owner(self):
        self.events.append(("finish_owner",))

    def cleanup_before_once(self, cause):
        self.events.append(("cleanup_before_once",))

    def _fingerprint(self, handle, *, directory):
        if directory:
            return list(self.root_full_before)
        return [handle.identity, len(handle.data), 10, 20, 30, 1]

    def _write_flush_readback(self, handle, raw):
        self.write_calls += 1
        assert handle is self.api.file
        assert len(handle.data) == 0, "FILE_CREATE must be inspected empty before the first write"
        self.events.append(("write_from_empty_same_handle", handle))
        handle.data = bytes(raw)
        digest = self.write_digest_override or hashlib.sha256(raw).hexdigest()
        return digest, len(raw)

    def _verify_parent_chain_after_once(self):
        self.events.append(("parents_rechecked",))

    def _verify_root_security_after_once(self):
        self.events.append(("root_security_rechecked",))
        return {"rechecked_on_same_exclusive_root_handle": True}

    def _directory_entries(self, handle):
        assert handle is self.root_handle
        self.events.append(("enumerate_root",))
        return {
            "BINDING.json": 1,
            "PREBIRTH_BINDING.json": 2,
            "MAIN_APPROVAL.json": 3,
            "ONCE_USED.json": 77,
        }

    def _expected_pre_once_entries(self):
        return {"BINDING.json": 1, "PREBIRTH_BINDING.json": 2, "MAIN_APPROVAL.json": 3}


def test_file_create_once_is_verified_empty_then_written_and_read_back_on_same_handle():
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    ops = R14OpsHarness()
    lease = R6B.AuthorityRootLeaseR6B(ops)
    lease.pin_after_prebirth()
    receipt = lease.consume_once_relative(raw, hashlib.sha256(raw).hexdigest())

    assert ops.api.create_calls == [(ops.root_handle, R6B.ONCE_LEAF)]
    assert ops.api.file_info_sizes == [0, len(raw)]
    assert ops.write_calls == 1
    assert ops.api.file.data == raw
    assert receipt["bytes"] == len(raw)
    assert receipt["new_file_size_before_write"] == 0
    assert receipt["file_size_after_same_handle_write"] == len(raw)
    assert receipt["same_handle_file_identity_before_after_write"] is True
    assert receipt["once_native_before_write_full_fingerprint"][1] == 0
    assert receipt["once_native_after_write_full_fingerprint"][1] == len(raw)
    assert receipt["once_native_before_write_full_fingerprint"][0] == receipt["once_native_after_write_full_fingerprint"][0]
    assert receipt["directory_entry_exact_set_delta"]["created"] == [{"name": R6B.ONCE_LEAF, "file_id": 77}]
    assert lease.state == "ONCE_CONSUMED_ROOT_RETAINED"


def test_nonempty_file_create_result_fails_closed_before_write_or_cleanup():
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    ops = R14OpsHarness(initial_bytes=b"unexpected preexisting content")
    lease = R6B.AuthorityRootLeaseR6B(ops)
    lease.pin_after_prebirth()
    with pytest.raises(R6B.LeaseError, match="empty ordinary single-link"):
        lease.consume_once_relative(raw, hashlib.sha256(raw).hexdigest())
    assert ops.api.create_calls == [(ops.root_handle, R6B.ONCE_LEAF)]
    assert ops.write_calls == 0
    assert not any(event[0] == "finish_owner" for event in ops.events)
    assert ops.effect_started is True
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert R6B.active_lease_count() == 1
    assert R6B.unknown_lease_count() == 1


def test_postwrite_identity_change_fails_closed_and_retains_owner():
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    ops = R14OpsHarness(postwrite_identity=(9, 78))
    lease = R6B.AuthorityRootLeaseR6B(ops)
    lease.pin_after_prebirth()
    with pytest.raises(R6B.LeaseError, match="identity or exact content size"):
        lease.consume_once_relative(raw, hashlib.sha256(raw).hexdigest())
    assert ops.write_calls == 1
    assert not any(event[0] == "finish_owner" for event in ops.events)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease.unknown_terminal_release_eligible is False
    assert R6B.active_lease_count() == 1


def test_postwrite_exact_size_mismatch_fails_closed_and_retains_owner():
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    ops = R14OpsHarness(postwrite_size=len(raw) - 1)
    lease = R6B.AuthorityRootLeaseR6B(ops)
    lease.pin_after_prebirth()
    with pytest.raises(R6B.LeaseError, match="identity or exact content size"):
        lease.consume_once_relative(raw, hashlib.sha256(raw).hexdigest())
    assert ops.api.file.data == raw
    assert ops.write_calls == 1
    assert not any(event[0] == "finish_owner" for event in ops.events)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease.unknown_terminal_release_eligible is False


def test_same_handle_readback_digest_mismatch_fails_closed_and_retains_owner():
    raw = b'{"status":"CONSUMED_BEFORE_TOKEN_DUPLICATION"}\n'
    ops = R14OpsHarness(write_digest_override="0" * 64)
    lease = R6B.AuthorityRootLeaseR6B(ops)
    lease.pin_after_prebirth()
    with pytest.raises(R6B.LeaseError, match="same-handle write/flush/readback differs"):
        lease.consume_once_relative(raw, hashlib.sha256(raw).hexdigest())
    assert ops.api.file.data == raw
    assert ops.write_calls == 1
    assert not any(event[0] == "finish_owner" for event in ops.events)
    assert lease.state == "UNKNOWN_RETAIN_ORIGINAL_HANDLES_NO_REPLAY"
    assert lease.unknown_terminal_release_eligible is False

