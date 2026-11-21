"""T20 finding 3 caller-layer controls: a failing project-root release must not
mask the primary error, must stay classified, must not touch borrowed bindings,
must not fake success inside an outer except, and must never retry a failed fd.

Exercises the four real production call sites:
  _g2_code.read_source_snapshot            (owned + borrowed binding)
  ExecutionService.project_root_scope      (owns + borrowed binding)
  ManagedBackend.project_root_scope        (delegated-ownership close per daemon evidence)
  ProjectAuthority.__init__                (self-opened identity binding)

Close failures are injected with a tracker os whose close fails exactly once
for the chosen descriptor, mirroring the frozen faults/edges adapters.  The
fd identity of the binding root is recorded from production open order (the
last open before the first dup) or from the deepest open at scope-close time.
Windows routes are not exercised here.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_code import read_source_snapshot
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._project_authority import ProjectAuthority

from tests.test_t20_project_root_cleanup_faults_parent_20261109 import _CloseFaultOS, _REAL_OS

pytestmark = pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixtures")


class _FailDeepestCloseOS(_CloseFaultOS):
    """Fail exactly one close of the most recently opened descriptor.

    For open->scope-close flows (project_root_scope, ProjectAuthority) the
    binding root fd is the deepest open when the caller finally closes it;
    the intermediate parent closes performed during open target shallower
    descriptors and still succeed.
    """

    def __init__(self):
        super().__init__()
        self.failed_fds = []

    def close(self, fd):
        self.attempts[fd] += 1
        deepest = self.opened[-1][0] if self.opened else None
        if fd == deepest and fd not in self.failed_fds:
            self.failed_fds.append(fd)
            raise OSError(errno.EIO, "injected binding close failure")
        return _REAL_OS.close(fd)


class _FailBindingCloseOS(_CloseFaultOS):
    """For the owned read_source_snapshot flow: the binding root fd is the
    last descriptor opened before the first dup (the read path's os.dup of
    the binding fd); fail that one close exactly once."""

    def __init__(self):
        super().__init__()
        self.failed_fds = []

    def dup(self, fd):
        if self.target_fd is None and self.opened:
            self.target_fd = self.opened[-1][0]
        return _REAL_OS.dup(fd)

    def fstat(self, fd):
        # stat faulting is never armed in this flow; only the close fault is.
        return _REAL_OS.fstat(fd)

    def close(self, fd):
        self.attempts[fd] += 1
        if fd == self.target_fd and fd not in self.failed_fds:
            self.failed_fds.append(fd)
            raise OSError(errno.EIO, "injected binding close failure")
        return _REAL_OS.close(fd)


def _project(tmp_path, *, bad_bytes=False):
    root = tmp_path / "project_scope"
    src = root / "Main.java"
    root.mkdir(parents=True)
    if bad_bytes:
        src.write_bytes(b"\xff\xfe not utf-8")
    else:
        src.write_text("class Main {}\n", encoding="utf-8")
    return root


# --------------------------------------------------------- _g2_code.read_source_snapshot


def test_g2_validation_primary_survives_binding_close_failure(tmp_path, monkeypatch):
    root = _project(tmp_path, bad_bytes=True)
    tracker = _FailBindingCloseOS()
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                read_source_snapshot(root, root / "Main.java")
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "close fault must be reached"
        failed = tracker.failed_fds[0]
        assert tracker.attempts[failed] == 1, "a failed release must not be retried"
        # The validation refusal stays the raised error and carries the
        # UNCONFIRMED release boundary next to its own classification.
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert raised.code == "INVALID_REQUEST", raised.as_dict()
        assert raised.stage == "validation", raised.as_dict()
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
        # The injected close cause is preserved on the final error, not lost.
        assert "injected binding close failure" in repr(raised.details), raised.as_dict()
        # Failed fd stays kernel alive: an unconfirmed boundary, not a closed one.
        _REAL_OS.fstat(failed)
    finally:
        tracker.test_only_release_known_handles()


def test_g2_success_with_binding_close_failure_is_classified_fail_closed(tmp_path, monkeypatch):
    root = _project(tmp_path)
    tracker = _FailBindingCloseOS()
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                read_source_snapshot(root, root / "Main.java")
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "close fault must be reached"
        assert raised is not None, "a successful read with an unconfirmed release must not pass"
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
    finally:
        tracker.test_only_release_known_handles()


def test_g2_pure_success_unchanged(tmp_path):
    root = _project(tmp_path)
    snapshot = read_source_snapshot(root, root / "Main.java")
    assert snapshot.source == "class Main {}\n"


def test_g2_borrowed_binding_is_not_closed(tmp_path):
    root = _project(tmp_path)
    binding = project_root.open_project_root_binding(root)
    fd = binding._fd
    try:
        snapshot = read_source_snapshot(binding, root / "Main.java")
        assert snapshot.sha256
        assert binding._closed is False
        _REAL_OS.fstat(fd)
    finally:
        binding.close()


# ---------------------------------------------------- ExecutionService.project_root_scope


class _StubStore:
    db = None
    lock = None


def _service(tmp_path):
    return ExecutionService(_StubStore(), _StubStore(), project_root=tmp_path)


def test_service_scope_contract_primary_survives_close_failure(tmp_path, monkeypatch):
    root = _project(tmp_path)
    service = _service(tmp_path)
    tracker = _FailDeepestCloseOS()
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                with service.project_root_scope(root):
                    raise ExecutionContractError("INVALID_REQUEST", "in-flight body refusal")
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "close fault must be reached"
        failed = tracker.failed_fds[0]
        assert tracker.attempts[failed] == 1, "a failed release must not be retried"
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert str(raised) == "in-flight body refusal", raised.as_dict()
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
        _REAL_OS.fstat(failed)  # unconfirmed: the descriptor is still alive
    finally:
        tracker.test_only_release_known_handles()


def test_service_scope_success_with_close_failure_is_classified(tmp_path, monkeypatch):
    root = _project(tmp_path)
    service = _service(tmp_path)
    tracker = _FailDeepestCloseOS()
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                with service.project_root_scope(root):
                    pass
            except BaseException as exc:
                raised = exc
        assert raised is not None, "close failure must not be a false success"
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
    finally:
        tracker.test_only_release_known_handles()


def test_service_scope_close_failure_inside_outer_except_is_not_false_success(tmp_path, monkeypatch):
    root = _project(tmp_path)
    service = _service(tmp_path)
    tracker = _FailDeepestCloseOS()
    outer_error = None
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                try:
                    raise RuntimeError("outer exception being handled")
                except RuntimeError as exc:
                    outer_error = exc
                    with service.project_root_scope(root):
                        pass
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "close fault must be reached"
        # The failed release must surface as its own classified failure; it may
        # not be swallowed nor annotated onto the unrelated outer exception.
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
        assert not getattr(outer_error, "__notes__", []), "outer exception must not be annotated"
        assert "cleanup_status" not in getattr(outer_error, "details", {})
    finally:
        tracker.test_only_release_known_handles()


def test_service_scope_borrowed_binding_is_not_closed(tmp_path):
    root = _project(tmp_path)
    service = _service(tmp_path)
    binding = project_root.open_project_root_binding(root)
    fd = binding._fd
    try:
        with service.project_root_scope(binding) as scoped:
            assert scoped == binding.path
        assert binding._closed is False
        _REAL_OS.fstat(fd)
    finally:
        binding.close()


# ---------------------------------------------------- ManagedBackend.project_root_scope


def _backend(tmp_path):
    return ManagedBackend(tmp_path / "home", _StubStore(), service=None,
                          project_root=tmp_path / "project_scope")


def test_managed_scope_contract_primary_survives_close_failure(tmp_path, monkeypatch):
    root = _project(tmp_path)
    backend = _backend(tmp_path)
    tracker = _FailDeepestCloseOS()
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                with backend.project_root_scope(root):
                    raise ExecutionContractError("INVALID_REQUEST", "in-flight body refusal")
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "close fault must be reached"
        failed = tracker.failed_fds[0]
        assert tracker.attempts[failed] == 1, "a failed release must not be retried"
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert str(raised) == "in-flight body refusal", raised.as_dict()
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
    finally:
        tracker.test_only_release_known_handles()
        backend.close()


def test_managed_scope_success_close_failure_is_caught_by_contract_classifier(tmp_path, monkeypatch):
    # _managed_backend/daemon classification points catch ExecutionContractError;
    # a bare OSError from a failed release slips past them (crash / UNKNOWN
    # downgrade). The release failure must therefore be contract-classified.
    root = _project(tmp_path)
    backend = _backend(tmp_path)
    tracker = _FailDeepestCloseOS()
    classified = None
    escaped = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                try:
                    with backend.project_root_scope(root):
                        pass
                except ExecutionContractError as exc:
                    classified = exc
            except BaseException as exc:
                escaped = exc
        assert escaped is None, ("release failure escaped the contract classifier", escaped)
        assert classified is not None, "close failure must not be a false success"
        assert classified.details.get("cleanup_status") == "UNCONFIRMED", classified.as_dict()
    finally:
        tracker.test_only_release_known_handles()
        backend.close()


def test_managed_scope_delegated_close_is_single_attempt(tmp_path, monkeypatch):
    # Daemon evidence (_project_authority.bind_project_workspace docstring plus
    # _control_daemon never closing workspace_binding) records the passed-in
    # binding's release as delegated to this scope.  Keep exactly one attempt.
    root = _project(tmp_path)
    backend = _backend(tmp_path)
    tracker = _CloseFaultOS()
    binding = project_root.open_project_root_binding(root)
    fd = binding._fd
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                with backend.project_root_scope(binding):
                    pass
            except BaseException:
                pass  # the failure itself is asserted in the other managed tests
        assert tracker.attempts[fd] == 1
        assert binding._closed is True
    finally:
        tracker.test_only_release_known_handles()
        backend.close()


# ----------------------------------------------------------- ProjectAuthority.__init__


def test_authority_constructor_close_failure_is_classified(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tracker = _FailDeepestCloseOS()
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                ProjectAuthority(_StubStore(), workspace_root=workspace,
                                 permission_provider=lambda: {"inspect"},
                                 grant_ceiling={"inspect"})
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "close fault must be reached"
        assert tracker.attempts[tracker.failed_fds[0]] == 1
        # The constructor must fail closed with a classified boundary, never a
        # bare OSError that escapes the daemon's contract handling.
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
    finally:
        tracker.test_only_release_known_handles()


def test_authority_constructor_success_path_unchanged(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    authority = ProjectAuthority(_StubStore(), workspace_root=workspace,
                                 permission_provider=lambda: {"inspect"},
                                 grant_ceiling={"inspect"})
    info = os.stat(workspace)
    assert authority.workspace_root_identity == (info.st_dev, info.st_ino)
