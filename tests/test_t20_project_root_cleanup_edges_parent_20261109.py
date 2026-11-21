"""Supplemental close-failure edge controls; Windows is a fake API only."""
from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp import _windows_parameter_publish as windows_publish
from comsol_mcp._execution_contract import ExecutionContractError
from tests.test_t20_project_root_cleanup_faults_parent_20261109 import _CloseFaultOS, _REAL_OS


class _DoubleCloseFaultOS(_CloseFaultOS):
    def __init__(self):
        super().__init__()
        self.failed_fds = []

    def close(self, fd):
        self.attempts[fd] += 1
        if len(self.failed_fds) < 2:
            self.failed_fds.append(fd)
            raise OSError(errno.EIO, "injected close before syscall")
        return _REAL_OS.close(fd)


class _InterruptFaultOS(_CloseFaultOS):
    def fstat(self, fd):
        if fd == self.target_fd and not self.stat_fault_reached:
            self.stat_fault_reached = True
            raise KeyboardInterrupt("injected primary interruption")
        return _REAL_OS.fstat(fd)


def _fixture_root(tmp_path):
    root = tmp_path / "project_scope"
    nested = root / "source_dir"
    nested.mkdir(parents=True)
    (nested / "source.dat").write_bytes(b"fixture data\n")
    return root


def _assert_attempted_once(tracker):
    assert all(tracker.attempts[fd] == 1 for fd, _, _ in tracker.opened)


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
def test_two_close_failures_attempt_other_handles_and_report_count(tmp_path, monkeypatch):
    binding = project_root.open_project_root_binding(_fixture_root(tmp_path))
    tracker = _DoubleCloseFaultOS()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            with pytest.raises(ExecutionContractError) as caught:
                binding.read_file(("source_dir", "source.dat"), max_bytes=1024)
            _assert_attempted_once(tracker)
            assert len(tracker.failed_fds) == 2
            assert set(tracker.live()) == set(tracker.failed_fds)
            assert caught.value.details["cleanup_status"] == "UNCONFIRMED"
            assert caught.value.details["cleanup_failure_count"] == 2
    finally:
        tracker.test_only_release_known_handles()
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
def test_cleanup_failure_during_outer_except_is_not_false_success(tmp_path, monkeypatch):
    binding = project_root.open_project_root_binding(_fixture_root(tmp_path))
    tracker = _CloseFaultOS()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                raise RuntimeError("unrelated caller exception")
            except RuntimeError:
                with pytest.raises(ExecutionContractError) as caught:
                    binding.hash_file(("source_dir", "source.dat"))
            _assert_attempted_once(tracker)
            assert tracker.live() == [tracker.failed_fd]
            assert caught.value.details["cleanup_status"] == "UNCONFIRMED"
    finally:
        tracker.test_only_release_known_handles()
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
def test_contract_error_is_not_masked_by_close_error(tmp_path, monkeypatch):
    binding = project_root.open_project_root_binding(_fixture_root(tmp_path))
    tracker = _CloseFaultOS()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            with pytest.raises(ExecutionContractError) as caught:
                binding.read_file(("source_dir", "source.dat"), max_bytes=1)
            _assert_attempted_once(tracker)
            assert tracker.live() == [tracker.failed_fd]
            assert caught.value.code == "INVALID_REQUEST"
            assert caught.value.details["cleanup_status"] == "UNCONFIRMED"
    finally:
        tracker.test_only_release_known_handles()
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
def test_interruption_is_preserved_even_if_cleanup_fails(tmp_path, monkeypatch):
    binding = project_root.open_project_root_binding(_fixture_root(tmp_path))
    tracker = _InterruptFaultOS(fail_fstat_component="source_dir")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            with pytest.raises(KeyboardInterrupt, match="injected primary interruption") as caught:
                binding.read_file(("source_dir", "source.dat"), max_bytes=1024)
            _assert_attempted_once(tracker)
            assert tracker.live() == [tracker.failed_fd]
            if hasattr(caught.value, "add_note"):
                assert "could not be confirmed" in " ".join(caught.value.__notes__)
    finally:
        tracker.test_only_release_known_handles()
        binding.close()


class _FakeWindowsOpenAPI:
    def __init__(self):
        self.attempts = []
        self.live = set()

    def open(self, path, *, directory):
        assert path == "C:\\" and directory
        self.live.add("drive")
        return "drive"

    def info(self, handle, *, directory):
        assert handle == "drive" and directory
        return ((1, 2),)

    def open_child_directory(self, handle, component):
        if handle == "drive" and component == "workspace":
            self.live.add("child")
            return "child"
        assert handle == "child" and component == "project"
        raise ExecutionContractError("ACCESS_VIOLATION", "injected primary Windows open error")

    def close(self, handle):
        self.attempts.append(handle)
        if handle == "child":
            raise OSError(errno.EIO, "injected fake Windows close failure")
        self.live.remove(handle)


def test_fake_windows_open_preserves_primary_and_attempts_remaining_handles(monkeypatch):
    api = _FakeWindowsOpenAPI()
    monkeypatch.setattr(windows_publish, "_API", lambda: api)
    with pytest.raises(ExecutionContractError, match="injected primary Windows open error") as caught:
        project_root.ProjectRootBinding._open_windows(Path(r"C:\workspace\project"))
    assert api.attempts == ["child", "drive"]
    assert api.live == {"child"}
    assert caught.value.details["cleanup_status"] == "UNCONFIRMED"
    assert caught.value.details["cleanup_failure_count"] == 1
    api.live.clear()
