"""Process identity probes with harmless child-process coverage."""
import ctypes
import errno
import os
import subprocess
import sys

import pytest

from comsol_mcp import _platform_process as processes


def test_windows_probe_never_uses_os_kill(monkeypatch):
    calls = []
    monkeypatch.setattr(processes, "_windows_process_identity", lambda pid: calls.append(pid) or {"alive": True, "start_epoch_ms": 7})
    monkeypatch.setattr(processes.os, "kill", lambda *_: (_ for _ in ()).throw(AssertionError("Windows probe must not signal")))
    assert processes.process_identity(99, platform_name="nt") == {"alive": True, "start_epoch_ms": 7}
    assert calls == [99]


def test_invalid_pid_is_dead_without_platform_probe(monkeypatch):
    monkeypatch.setattr(processes, "_windows_process_identity", lambda _: (_ for _ in ()).throw(AssertionError("invalid PID")))
    assert processes.process_identity(1, platform_name="nt") == {"alive": False, "start_epoch_ms": None}


def test_darwin_bsdinfo_ctypes_layout_matches_sdk_abi():
    # Verified against this host's SDK: PROC_PIDTBSDINFO=3,
    # PROC_PIDTBSDINFO_SIZE=136, pbi_pid=12, pbi_start_tvsec=120,
    # pbi_start_tvusec=128.
    info = processes._darwin_proc_bsdinfo_type()
    assert ctypes.sizeof(info) == 136
    assert info.pbi_pid.offset == 12
    assert info.pbi_start_tvsec.offset == 120
    assert info.pbi_start_tvusec.offset == 128


def test_darwin_probe_rejects_returned_identity_for_another_pid(monkeypatch):
    info_type = processes._darwin_proc_bsdinfo_type()

    def wrong_pid(_pid, flavor, arg, buffer, size):
        assert (flavor, arg, size) == (3, 0, 136)
        info = ctypes.cast(buffer, ctypes.POINTER(info_type)).contents
        info.pbi_pid = 4322
        info.pbi_status = 2
        info.pbi_start_tvsec = 1_790_000_000
        info.pbi_start_tvusec = 123_000
        return size

    monkeypatch.setattr(processes, "_darwin_proc_pidinfo", lambda: wrong_pid)
    assert processes.process_identity(4321, platform_name="darwin") == {
        "alive": True, "start_epoch_ms": None,
    }


@pytest.mark.parametrize(
    ("result", "call_errno", "stale_errno", "expected"),
    [
        (0, 0, errno.ESRCH, {"alive": True, "start_epoch_ms": None}),
        (135, errno.ESRCH, 0, {"alive": True, "start_epoch_ms": None}),
        (0, errno.EACCES, 0, {"alive": True, "start_epoch_ms": None}),
        (0, errno.ESRCH, 0, {"alive": False, "start_epoch_ms": None}),
    ],
)
def test_darwin_probe_uses_only_current_full_failure_errno(monkeypatch, result, call_errno,
                                                            stale_errno, expected):
    def response(_pid, _flavor, _arg, _buffer, _size):
        ctypes.set_errno(call_errno)
        return result

    monkeypatch.setattr(processes, "_darwin_proc_pidinfo", lambda: response)
    ctypes.set_errno(stale_errno)
    assert processes.process_identity(4321, platform_name="darwin") == expected


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS libproc")
def test_darwin_birth_is_stable_for_exact_harmless_child_and_clears_after_exit():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        first = processes.process_identity(child.pid)
        second = processes.process_identity(child.pid)
        assert first["alive"] is True
        assert type(first["start_epoch_ms"]) is int and first["start_epoch_ms"] > 0
        assert second == first
        assert processes.process_identity_matches(child.pid, first["start_epoch_ms"])
        # The exact birth is part of the ownership predicate; even a one-ms
        # mismatch rejects this otherwise live child.
        assert not processes.process_identity_matches(child.pid, first["start_epoch_ms"] + 1)
    finally:
        child.terminate()
        child.wait(timeout=5)
    dead = processes.process_identity(child.pid)
    assert dead["alive"] is False
    assert dead["start_epoch_ms"] is None
    assert not processes.process_identity_matches(child.pid, first["start_epoch_ms"])
