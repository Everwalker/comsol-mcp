"""T20 r3 controls on the frozen close-candidate: interrupts during release
must propagate, and multi-handle close failures must keep their true count.

Semantics pinned here (main-agent decision on the r2 review findings):
  * An ordinary Exception close failure with a primary in flight keeps the
    existing behavior: primary survives, annotated UNCONFIRMED.
  * A non-Exception BaseException (KeyboardInterrupt / SystemExit) raised
    while releasing must NOT be swallowed when a primary is in flight: it
    propagates, while the in-flight primary and the unconfirmed-release
    facts stay reachable (annotation on the primary, __context__ chain, a
    note on the interrupt).  Nothing may pretend the release was confirmed.
  * With no primary, the interrupt keeps propagating as before.
  * Windows-route multi-handle (fake-API) co-failure must annotate the
    primary with the true failure count and the first cause, must
    accumulate truthfully across independent release sites without
    overwriting prior state, and must not falsely accumulate on a no-op
    re-close (no retry).

Synthetic injection only: tracker os around real fds / fake handle APIs.
No COMSOL, daemon, Worker, SSH.  Native Windows is not exercised: every
Windows-route case uses the fake-API contract, and no stronger claim is
made.
"""
from __future__ import annotations

import errno
import os

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp._project_root import (_ReleaseUnconfirmed,
                                      close_binding_releasing,
                                      open_project_root_binding)
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_code import read_source_snapshot

from tests.test_t20_project_root_cleanup_faults_parent_20261109 import _CloseFaultOS, _REAL_OS

pytestmark = pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixtures")


def _chain_has(exc, target, max_depth=12):
    seen, cur, depth = set(), exc, 0
    while cur is not None and id(cur) not in seen and depth < max_depth:
        if cur is target:
            return True
        seen.add(id(cur))
        cur = cur.__context__
        depth += 1
    return False


class _SigCloseOS(_CloseFaultOS):
    """Armed close raises a chosen non-OSError BaseException instead of the
    syscall, simulating a signal handler firing inside os.close's EINTR
    retry (PEP 475) during the release.  Injection is before the real
    close, so the fd identity stays recoverable."""

    def __init__(self, kind, arm="dup"):
        super().__init__()
        self.kind = kind
        self.arm = arm
        self.failed_fds = []

    def dup(self, fd):
        if self.target_fd is None and self.opened:
            self.target_fd = self.opened[-1][0]
        return _REAL_OS.dup(fd)

    def close(self, fd):
        self.attempts[fd] += 1
        armed = (fd == self.target_fd if self.arm == "dup"
                 else bool(self.opened) and fd == self.opened[-1][0])
        if armed and fd not in self.failed_fds:
            self.failed_fds.append(fd)
            raise self.kind()
        return _REAL_OS.close(fd)


class _RecordOS(_CloseFaultOS):
    def close(self, fd):
        self.attempts[fd] += 1
        return _REAL_OS.close(fd)


class _FakeAPI:
    """Fake Windows handle API; every closer raises RuntimeError once per
    handle so the real close loop (all handles, one attempt each) runs."""

    def __init__(self, kind=RuntimeError):
        self.kind = kind
        self.attempted = []

    def close(self, handle):
        self.attempted.append(handle)
        raise self.kind(f"fake handle close boom {handle}")


def _project(tmp_path, *, bad_bytes=False):
    root = tmp_path / "project_scope"
    src = root / "Main.java"
    root.mkdir(parents=True)
    if bad_bytes:
        src.write_bytes(b"\xff\xfe not utf-8")
    else:
        src.write_text("class Main {}\n", encoding="utf-8")
    return root


class _StubStore:
    db = None
    lock = None


def _service(tmp_path):
    return ExecutionService(_StubStore(), _StubStore(), project_root=tmp_path)


# ---------------------------------------------------------------- KeyboardInterrupt / SystemExit vs Exception, POSIX route

def _snapshot_interrupt_with_primary_propagates(tmp_path, monkeypatch, kind):
    root = _project(tmp_path, bad_bytes=True)
    tracker = _SigCloseOS(kind)
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                read_source_snapshot(root, root / "Main.java")
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "release fault must be reached"
        failed = tracker.failed_fds[0]
        assert tracker.attempts[failed] == 1, "a failed release must not be retried"
        # The interrupt is the propagated error, not a recorded-and-dropped one.
        assert isinstance(raised, kind), (type(raised).__name__, raised)
        # The in-flight primary survives on the chain and keeps its cleanup
        # facts: an UNCONFIRMED boundary, never a claimed confirmation.
        primary = raised.__context__
        assert isinstance(primary, ExecutionContractError), (type(primary).__name__, primary)
        assert primary.code == "INVALID_REQUEST", primary.as_dict()
        assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.as_dict()
        assert primary.details.get("cleanup_failure_count") == 1, primary.as_dict()
        assert kind.__name__ in repr(primary.details), primary.as_dict()
        # The interrupt itself carries an unconfirmed-release note.
        assert any("could not be confirmed" in n for n in getattr(raised, "__notes__", [])), \
            getattr(raised, "__notes__", [])
        _REAL_OS.fstat(failed)  # unconfirmed: descriptor still alive
    finally:
        tracker.test_only_release_known_handles()


def test_keyboardinterrupt_during_release_with_primary_propagates(tmp_path, monkeypatch):
    _snapshot_interrupt_with_primary_propagates(tmp_path, monkeypatch, KeyboardInterrupt)


def test_systemexit_during_release_with_primary_propagates(tmp_path, monkeypatch):
    _snapshot_interrupt_with_primary_propagates(tmp_path, monkeypatch, SystemExit)


def test_keyboardinterrupt_during_release_without_primary_propagates(tmp_path, monkeypatch):
    root = _project(tmp_path)
    tracker = _SigCloseOS(KeyboardInterrupt)
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                read_source_snapshot(root, root / "Main.java")
            except BaseException as exc:
                raised = exc
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        assert tracker.failed_fds
        _REAL_OS.fstat(tracker.failed_fds[0])  # identity lost, still open
    finally:
        tracker.test_only_release_known_handles()


def test_oserror_during_release_with_primary_still_preserves_primary(tmp_path, monkeypatch):
    # Contrast control: the ordinary-Exception path keeps the existing
    # primary+UNCONFIRMED behavior; only interrupts gain propagation.
    root = _project(tmp_path, bad_bytes=True)
    tracker = _SigCloseOS(OSError)  # armed close raises bare OSError (kind=OSError)
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                read_source_snapshot(root, root / "Main.java")
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds
        assert isinstance(raised, ExecutionContractError), (type(raised).__name__, raised)
        assert raised.code == "INVALID_REQUEST", raised.as_dict()
        assert raised.details.get("cleanup_status") == "UNCONFIRMED", raised.as_dict()
        assert raised.details.get("cleanup_failure_count") == 1, raised.as_dict()
    finally:
        tracker.test_only_release_known_handles()


def test_service_scope_keyboardinterrupt_with_primary_propagates(tmp_path, monkeypatch):
    # Second real call site: ExecutionService.project_root_scope.
    root = _project(tmp_path)
    service = _service(tmp_path)
    tracker = _SigCloseOS(KeyboardInterrupt, arm="deepest")
    primary = ExecutionContractError("INVALID_REQUEST", "in-flight body refusal")
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                with service.project_root_scope(root):
                    raise primary
            except BaseException as exc:
                raised = exc
        assert tracker.failed_fds, "release fault must be reached"
        assert tracker.attempts[tracker.failed_fds[0]] == 1, "no retry"
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        assert _chain_has(raised, primary), "primary must stay reachable on the chain"
        assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.as_dict()
        assert "KeyboardInterrupt" in repr(primary.details), primary.as_dict()
        _REAL_OS.fstat(tracker.failed_fds[0])
    finally:
        tracker.test_only_release_known_handles()


def test_outer_handled_error_with_close_interrupt_is_not_false_success(tmp_path, monkeypatch):
    # sys.exc_info trap under the new propagation rule: an unrelated outer
    # exception must never become the "primary"; the interrupt must surface.
    root = _project(tmp_path)
    service = _service(tmp_path)
    tracker = _SigCloseOS(KeyboardInterrupt, arm="deepest")
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
        assert tracker.failed_fds
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        assert not getattr(outer_error, "__notes__", []), "outer exception must not be annotated"
        assert "cleanup_status" not in getattr(outer_error, "details", {})
    finally:
        tracker.test_only_release_known_handles()


# --------------------------------------------------------- Windows route (fake API only): true counts

def _fake_api_binding(tmp_path, tracker, monkeypatch, handles, kind=RuntimeError,
                      name="project_scope"):
    root = tmp_path / name
    src = root / "Main.java"
    root.mkdir(parents=True)
    src.write_text("class Main {}\n", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(project_root, "os", tracker)
        binding = open_project_root_binding(root)
    binding._api = _FakeAPI(kind=kind)
    binding._handles = list(handles)
    return binding


def test_fake_windows_three_failures_without_primary_keep_true_count(tmp_path, monkeypatch):
    # Shape pin: standalone multi-failure already reports count=3 + first cause.
    tracker = _RecordOS()
    binding = _fake_api_binding(tmp_path, tracker, monkeypatch, [101, 102, 103])
    try:
        with pytest.raises(_ReleaseUnconfirmed) as ei:
            close_binding_releasing(binding, None)
        assert ei.value.details["cleanup_failure_count"] == 3, ei.value.details
        assert ei.value.details["close_cause"] == "RuntimeError: fake handle close boom 103"
        assert isinstance(ei.value.__cause__, RuntimeError)
        assert binding._api.attempted == [103, 102, 101]
    finally:
        tracker.test_only_release_known_handles()


def test_fake_windows_three_failures_with_primary_keep_true_count(tmp_path, monkeypatch):
    # r2 finding: co-failure annotation collapsed the true N into count=1.
    tracker = _RecordOS()
    binding = _fake_api_binding(tmp_path, tracker, monkeypatch, [201, 202, 203])
    primary = ExecutionContractError("INVALID_REQUEST", "body refusal", stage="validation")
    try:
        close_binding_releasing(binding, primary)
        assert primary.details["cleanup_status"] == "UNCONFIRMED", primary.details
        assert primary.details["cleanup_failure_count"] == 3, primary.details
        assert primary.details["close_cause"] == "RuntimeError: fake handle close boom 203", \
            primary.details
        assert binding._api.attempted == [203, 202, 201]
    finally:
        tracker.test_only_release_known_handles()


def test_fake_windows_single_failure_with_primary_count_one(tmp_path, monkeypatch):
    tracker = _RecordOS()
    binding = _fake_api_binding(tmp_path, tracker, monkeypatch, [301])
    primary = ExecutionContractError("INVALID_REQUEST", "body refusal")
    try:
        close_binding_releasing(binding, primary)
        assert primary.details["cleanup_failure_count"] == 1, primary.details
    finally:
        tracker.test_only_release_known_handles()


def test_annotations_accumulate_truthfully_without_overwrite(tmp_path, monkeypatch):
    # Two independent release sites under one primary: 2 then 1 true
    # failures -> 3 total (no overwrite, no reset).  A no-op re-close of an
    # already-closed binding must not add anything (no retry, no false
    # accumulation), and close_cause keeps the FIRST cause.
    tracker = _RecordOS()
    b1 = _fake_api_binding(tmp_path, tracker, monkeypatch, [11, 12], name="root_b1")
    b2 = _fake_api_binding(tmp_path, tracker, monkeypatch, [21], name="root_b2")
    primary = ExecutionContractError("INVALID_REQUEST", "multi-release accumulation")
    try:
        close_binding_releasing(b1, primary)
        assert primary.details["cleanup_failure_count"] == 2, primary.details
        close_binding_releasing(b2, primary)
        assert primary.details["cleanup_failure_count"] == 3, primary.details
        assert primary.details["close_cause"] == "RuntimeError: fake handle close boom 12", \
            primary.details
        close_binding_releasing(b2, primary)  # already closed: no-op
        assert primary.details["cleanup_failure_count"] == 3, primary.details
        assert b1._api.attempted == [12, 11]
        assert b2._api.attempted == [21]
    finally:
        tracker.test_only_release_known_handles()


def test_fake_windows_keyboardinterrupt_with_primary_propagates(tmp_path, monkeypatch):
    # Interrupt inside the multi-handle loop.  r3 pinned "attempted ==
    # [403]" — the abort-with-unattempted-remainder behavior reproduced by
    # reviewer r4 §3 (P3) and superseded by the main-agent decision for r5:
    # every remaining handle must still be attempted exactly once, the
    # interrupted handle is never retried, and the true unconfirmed-handle
    # count reaches the primary.  See R5 suite for the full contract.
    tracker = _RecordOS()
    binding = _fake_api_binding(tmp_path, tracker, monkeypatch, [401, 402, 403],
                                kind=KeyboardInterrupt)
    primary = ExecutionContractError("INVALID_REQUEST", "body refusal")
    raised = None
    try:
        try:
            close_binding_releasing(binding, primary)
        except BaseException as exc:
            raised = exc
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        assert _chain_has(raised, primary), "primary must stay reachable on the chain"
        assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.details
        assert binding._api.attempted == [403, 402, 401], binding._api.attempted
        assert primary.details.get("cleanup_failure_count") == 3, primary.details
        _REAL_OS.fstat(tracker.opened[-1][0])  # root fd still unconfirmed-alive
    finally:
        tracker.test_only_release_known_handles()


# ------------------------------------------------------------------ existing boundary (unchanged)

class _FakeCtx:
    def __init__(self, fail):
        self.fail = fail
        self.n = 0

    def set(self, value):
        self.n += 1
        return self.n

    def reset(self, token):
        if self.fail and token == 1:
            raise ValueError("injected contextvar reset fault")


def test_contextvar_reset_fault_skips_release_boundary_unchanged(tmp_path, monkeypatch):
    # Pre-existing r2 boundary, deliberately NOT refactored here: a failing
    # ContextVar reset runs before the release and skips it entirely on the
    # baseline candidate and must stay identical on this candidate.
    root = _project(tmp_path)
    service = _service(tmp_path)
    service._project_root_binding_context = _FakeCtx(fail=True)
    tracker = _RecordOS()
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                with service.project_root_scope(root):
                    pass
            except BaseException as exc:
                raised = exc
        assert isinstance(raised, ValueError), (type(raised).__name__, raised)
        deepest = tracker.opened[-1][0]
        assert tracker.attempts[deepest] == 0, "release never attempted"
        assert tracker.live(), "root descriptor leaked"
    finally:
        tracker.test_only_release_known_handles()
