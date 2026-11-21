"""Synthetic descriptor-ownership checks for project-root and source readers.

These tests use only task-owned temporary directories. They inject a completed
OS error after a real directory descriptor is acquired, and check both the
module's close attempts and kernel descriptor state before test-owned cleanup.
They do not launch COMSOL, a Worker, a daemon, or an SSH process.
"""
from __future__ import annotations

import errno
import os
from collections import Counter
from types import SimpleNamespace

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp._execution_contract import ExecutionContractError


_REAL_OS = os


class _TrackedOS:
    def __init__(self, target, fault):
        self.target = target
        self.fault = fault
        self.triggered = False
        self.target_fd = None
        self.owned = []
        self.close_attempts = Counter()

    def __getattr__(self, name):
        if name == "supports_dir_fd":
            return _REAL_OS.supports_dir_fd | {self.open}
        return getattr(_REAL_OS, name)

    def _record(self, fd):
        info = _REAL_OS.fstat(fd)
        self.owned.append((fd, info.st_dev, info.st_ino))
        return fd

    def open(self, path, flags, *args, **kwargs):
        fd = self._record(_REAL_OS.open(path, flags, *args, **kwargs))
        if _REAL_OS.fspath(path) == self.target:
            self.target_fd = fd
        return fd

    def dup(self, fd):
        return self._record(_REAL_OS.dup(fd))

    def fstat(self, fd):
        if self.fault == "fstat" and fd == self.target_fd and not self.triggered:
            self.triggered = True
            raise OSError(errno.EIO, "injected child fstat failure")
        return _REAL_OS.fstat(fd)

    def stat(self, path, *args, **kwargs):
        is_target = _REAL_OS.fspath(path) == self.target and self.target_fd is not None
        if is_target and not self.triggered and self.fault == "stat":
            self.triggered = True
            raise OSError(errno.EIO, "injected named stat failure")
        result = _REAL_OS.stat(path, *args, **kwargs)
        if is_target and not self.triggered and self.fault == "mismatch":
            self.triggered = True
            return SimpleNamespace(st_mode=result.st_mode, st_dev=result.st_dev,
                                   st_ino=result.st_ino + 1)
        return result

    def close(self, fd):
        self.close_attempts[fd] += 1
        return _REAL_OS.close(fd)

    def live_owned(self):
        result = []
        for fd, device, inode in self.owned:
            try:
                info = _REAL_OS.fstat(fd)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    continue
                raise
            if (info.st_dev, info.st_ino) == (device, inode):
                result.append(fd)
        return result

    def cleanup_confirmed_live(self):
        # No retry of a descriptor for which close was attempted. The fixture
        # only collects its own never-close-attempted handles on RED.
        for fd in self.live_owned():
            if self.close_attempts[fd] == 0:
                _REAL_OS.close(fd)


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fault model")
@pytest.mark.parametrize("route", ["open_binding", "read_source", "hash_file"])
@pytest.mark.parametrize("fault", ["fstat", "stat", "mismatch"])
def test_child_acquisition_fault_does_not_leak_and_is_attempted_once(
        tmp_path, monkeypatch, route, fault):
    root = tmp_path / "project_scope"
    source = root / "source_dir"
    source.mkdir(parents=True)
    (source / "Model.java").write_bytes(b"class Model {}\n")
    binding = None
    target = "project_scope" if route == "open_binding" else "source_dir"
    tracker = _TrackedOS(target, fault)
    try:
        if route != "open_binding":
            binding = project_root.open_project_root_binding(root)
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            with pytest.raises(ExecutionContractError) as observed:
                if route == "open_binding":
                    binding = project_root.open_project_root_binding(root)
                elif route == "read_source":
                    assert binding is not None
                    binding.read_file(("source_dir", "Model.java"), max_bytes=1024)
                else:
                    assert binding is not None
                    binding.hash_file(("source_dir", "Model.java"))
            assert observed.value.code == "ACCESS_VIOLATION"
            assert tracker.triggered and tracker.target_fd is not None
            live = tracker.live_owned()
            assert tracker.close_attempts[tracker.target_fd] == 1, (
                route, fault, tracker.target_fd, dict(tracker.close_attempts), "kernel_live", live)
            assert live == [], (route, fault, live)
    finally:
        tracker.cleanup_confirmed_live()
        if binding is not None:
            binding.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd positive control")
def test_nested_source_and_hash_work_with_valid_project_binding(tmp_path):
    root = tmp_path / "project_scope"
    source = root / "source_dir"
    source.mkdir(parents=True)
    raw = b"class Model {}\n"
    (source / "Model.java").write_bytes(raw)
    binding = project_root.open_project_root_binding(root)
    try:
        result, metadata = binding.read_file(("source_dir", "Model.java"), max_bytes=1024)
        digest, size = binding.hash_file(("source_dir", "Model.java"))
        import hashlib
        assert result == raw and metadata["size"] == len(raw)
        assert (digest, size) == (hashlib.sha256(raw).hexdigest(), len(raw))
    finally:
        binding.close()
