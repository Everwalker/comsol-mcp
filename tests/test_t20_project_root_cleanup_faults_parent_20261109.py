"""Project-root cleanup fault controls on disposable POSIX/Fake-Windows handles.

An injected close failure happens before the underlying close in the test
adapter, so tests can identify and later release only their own real descriptor.
A real close failure is not assumed to have those semantics and must not be
retried merely by descriptor number.
"""
from __future__ import annotations

import errno
import os
from collections import Counter
from pathlib import Path

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp._execution_contract import ExecutionContractError


_REAL_OS = os


class _CloseFaultOS:
    def __init__(self, *, fail_fstat_component=None):
        self.fail_fstat_component = fail_fstat_component
        self.stat_fault_reached = False
        self.target_fd = None
        self.opened = []
        self.attempts = Counter()
        self.failed_fd = None

    def __getattr__(self, name):
        if name == "supports_dir_fd":
            return _REAL_OS.supports_dir_fd | {self.open}
        return getattr(_REAL_OS, name)

    def _record(self, fd):
        info = _REAL_OS.fstat(fd)
        self.opened.append((fd, info.st_dev, info.st_ino))
        return fd

    def open(self, path, flags, *args, **kwargs):
        fd = self._record(_REAL_OS.open(path, flags, *args, **kwargs))
        if _REAL_OS.fspath(path) == self.fail_fstat_component:
            self.target_fd = fd
        return fd

    def dup(self, fd):
        return self._record(_REAL_OS.dup(fd))

    def fstat(self, fd):
        if fd == self.target_fd and not self.stat_fault_reached:
            self.stat_fault_reached = True
            raise OSError(errno.EIO, "injected primary fstat failure")
        return _REAL_OS.fstat(fd)

    def close(self, fd):
        self.attempts[fd] += 1
        if self.failed_fd is None:
            self.failed_fd = fd
            raise OSError(errno.EIO, "injected close before syscall")
        return _REAL_OS.close(fd)

    def live(self):
        live = []
        for fd, device, inode in self.opened:
            try:
                info = _REAL_OS.fstat(fd)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    continue
                raise
            if (info.st_dev, info.st_ino) == (device, inode):
                live.append(fd)
        return live

    def test_only_release_known_handles(self):
        # The adapter itself injected the one failure *before* the real close;
        # no other thread runs in these tests. Recheck identity before releasing
        # any synthetic live handle, including the deliberately failed one.
        for fd in self.live():
            _REAL_OS.close(fd)


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
@pytest.mark.parametrize("route", ["open_binding", "read_source", "hash_file"])
@pytest.mark.parametrize("primary_fault", [False, True])
def test_posix_close_failure_attempts_all_owned_handles_and_preserves_primary(
        tmp_path, monkeypatch, route, primary_fault):
    root = tmp_path / "project_scope"
    nested = root / "source_dir"
    nested.mkdir(parents=True)
    (nested / "source.dat").write_bytes(b"fixture data\n")
    target = "project_scope" if route == "open_binding" else "source_dir"
    tracker = _CloseFaultOS(fail_fstat_component=target if primary_fault else None)
    binding = None
    error = None
    try:
        if route != "open_binding":
            binding = project_root.open_project_root_binding(root)
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                if route == "open_binding":
                    binding = project_root.open_project_root_binding(root)
                elif route == "read_source":
                    assert binding is not None
                    binding.read_file(("source_dir", "source.dat"), max_bytes=1024)
                else:
                    assert binding is not None
                    binding.hash_file(("source_dir", "source.dat"))
            except Exception as exc:
                error = exc
            assert tracker.failed_fd is not None, "close fault must be reached"
            assert tracker.stat_fault_reached is primary_fault
            live = tracker.live()
            missing_attempts = [fd for fd, _, _ in tracker.opened if tracker.attempts[fd] == 0]
            repeated_attempts = {fd: n for fd, n in tracker.attempts.items() if n != 1}
            assert not missing_attempts and not repeated_attempts, (
                route, primary_fault, "unattempted", missing_attempts,
                "repeated", repeated_attempts, "kernel_live", live)
            assert live == [tracker.failed_fd], (route, primary_fault, live)
            assert isinstance(error, ExecutionContractError), (route, primary_fault, error)
            assert error.code == "ACCESS_VIOLATION"
            assert error.details.get("cleanup_status") == "UNCONFIRMED", error.as_dict()
            if primary_fault:
                assert isinstance(error.__cause__, OSError)
                assert "injected primary fstat failure" in str(error.__cause__)
            if binding is not None:
                assert binding._fd is not None
                assert _REAL_OS.fstat(binding._fd)
    finally:
        tracker.test_only_release_known_handles()
        if binding is not None:
            binding.close()


class _FakeWindowsAPI:
    def __init__(self, fail_handle):
        self.fail_handle = fail_handle
        self.failure_reached = False
        self.attempts = []
        self.live = {"root"}
        self.data = b"fixture data\n"
        self.offset = 0

    def open_child_directory(self, parent, name):
        assert parent == "root" and name == "source_dir"
        self.live.add("child")
        return "child"

    def open_source_for_snapshot(self, parent, name):
        assert parent == "child" and name == "source.dat"
        self.live.add("file")
        return "file"

    def source_fingerprint(self, handle):
        assert handle == "file"
        return ((1, 2), len(self.data), 3, 116444736000000000, 1)

    def read_chunk(self, handle, count):
        assert handle == "file"
        chunk = self.data[self.offset:self.offset + count]
        self.offset += len(chunk)
        return chunk

    def close(self, handle):
        self.attempts.append(handle)
        if handle == self.fail_handle and not self.failure_reached:
            self.failure_reached = True
            raise OSError(errno.EIO, "fake Windows close failure")
        self.live.remove(handle)


@pytest.mark.parametrize("route", ["read_source", "hash_file"])
def test_fake_windows_file_close_failure_does_not_skip_owned_directory(route):
    api = _FakeWindowsAPI("file")
    binding = project_root.ProjectRootBinding(Path("project"), (1, 2), api=api, handles=["root"])
    error = None
    try:
        try:
            if route == "read_source":
                binding.read_file(("source_dir", "source.dat"), max_bytes=1024)
            else:
                binding.hash_file(("source_dir", "source.dat"))
        except Exception as exc:
            error = exc
        assert api.failure_reached
        attempts_before_test_cleanup = list(api.attempts)
        assert attempts_before_test_cleanup == ["file", "child"], attempts_before_test_cleanup
        assert api.live == {"root", "file"}
        assert isinstance(error, ExecutionContractError), error
        assert error.details.get("cleanup_status") == "UNCONFIRMED"
    finally:
        # The fake itself knows its injected close had no effect.
        api.live.discard("file")
        api.live.discard("child")
        binding.close()


def test_fake_windows_binding_close_attempts_all_handles_once_after_error():
    api = _FakeWindowsAPI("leaf")
    api.live = {"root", "child", "leaf"}
    binding = project_root.ProjectRootBinding(Path("project"), (1, 2), api=api,
                                              handles=["root", "child", "leaf"])
    with pytest.raises(OSError):
        binding.close()
    assert api.failure_reached
    assert api.attempts == ["leaf", "child", "root"], api.attempts
    assert api.live == {"leaf"}
    # A repeated close must not blindly retry the unconfirmed handle.
    binding.close()
    assert api.attempts == ["leaf", "child", "root"]
    api.live.clear()
