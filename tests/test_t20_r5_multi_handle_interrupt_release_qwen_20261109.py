"""T20 r5 controls: an interrupt during multi-handle owned-release must not
strand the remaining owned handles unattempted (reviewer r4 §3 P3 leak).

Semantics pinned here (main-agent decision replacing the r4-quantified
boundary; r4 report + r3 pins for the old abort behavior stay archived):
  * Every owned handle is attempted exactly once per release operation.
    The handle whose closer raised the interrupt is never retried.
  * The interrupt still propagates after the remaining attempts: it is
    never converted into an Exception that ordinary ``except`` would
    swallow, and business never continues past it.
  * Priority: the earliest observed interrupt propagates (Python < 3.11
    has no BaseExceptionGroup); remaining interrupts and the classified
    release error ride free __cause__ slots in attempt order.
  * The primary failure, every observed ordinary cleanup failure, and the
    UNCONFIRMED release state (with the true unconfirmed-handle count,
    interrupted handles included) stay visible on the primary/chain.
  * A second close/re-close after the interrupt is a strict no-op: no
    retry, no double count.
  * Positive control: all-clean close attempts every handle once and
    raises nothing.

Synthetic injection only: fake handle APIs and tracker os around real fds.
No COMSOL, daemon, Worker, SSH.  Native Windows not exercised (fake-API
contract only).  The ContextVar-reset boundary is deliberately untouched.
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._project_root import (ProjectRootBinding,
                                      close_binding_releasing)

from tests.test_t20_close_interrupt_count_qwen_20261109 import _SigCloseOS, _project, _service
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


def _cause_chain(exc, limit=10):
    seen, chain, cur = set(), [], exc.__cause__
    while cur is not None and id(cur) not in seen and len(chain) < limit:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__
    return chain


class _ScriptedAPI:
    """Fake Windows handle API: chosen handles raise ordinary Exception
    failures, chosen handles raise non-Exception interrupts (exact
    instances, recorded for chain-identity assertions), the rest close
    cleanly.  Every closer attempt is recorded in attempt order."""

    def __init__(self, *, failures=None, interrupts=None):
        self.failures = dict(failures or {})
        self.interrupts = dict(interrupts or {})
        self.attempted = []

    def close(self, handle):
        self.attempted.append(handle)
        if handle in self.failures:
            raise self.failures[handle]
        if handle in self.interrupts:
            raise self.interrupts[handle]


def _fake_binding(tmp_path, api, handles):
    return ProjectRootBinding(Path(tmp_path), (1, 2), api=api, handles=list(handles))


def _assert_once(api):
    counts = Counter(api.attempted)
    assert all(n == 1 for n in counts.values()), api.attempted


class _KIOnFirstCloseOS(_CloseFaultOS):
    """The very first close attempt raises the chosen interrupt *before*
    the real syscall (signal-like arrival inside the release loop); every
    later close is the real one."""

    def __init__(self, kind, **kwargs):
        super().__init__(**kwargs)
        self.kind = kind
        self.interrupted_fds = []

    def close(self, fd):
        self.attempts[fd] += 1
        if not self.interrupted_fds:
            self.interrupted_fds.append(fd)
            raise self.kind("injected release interrupt")
        return _REAL_OS.close(fd)


# ------------------------------------------------ close() Windows route (fake API)


def test_close_first_attempt_ki_remaining_handles_each_attempted_once(tmp_path):
    api = _ScriptedAPI(interrupts={30: KeyboardInterrupt("ki30")})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r5 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    assert ei.value is api.interrupts[30]
    assert api.attempted == [30, 20, 10], "remaining handles must each be attempted"
    _assert_once(api)
    assert binding._handles == [], "bookkeeping complete: nothing to retry"
    assert binding._closed is True
    assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.details
    # only handle 30 is unconfirmed; 20 and 10 closed cleanly.
    assert primary.details.get("cleanup_failure_count") == 1, primary.details
    assert "ki30" in primary.details.get("close_cause", ""), primary.details
    close_binding_releasing(binding, primary)  # strict no-op: no retry, no recount
    assert api.attempted == [30, 20, 10]
    assert primary.details.get("cleanup_failure_count") == 1, primary.details


def test_close_middle_interrupt_with_prior_ordinary_failure_preserves_all_facts(tmp_path):
    boom30 = OSError(5, "boom 30")
    ki20 = KeyboardInterrupt("ki20")
    api = _ScriptedAPI(failures={30: boom30}, interrupts={20: ki20})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r5 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    assert ei.value is ki20, "the interrupt itself must propagate, not a wrapper"
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.details
    # ordinary failure (30) + interrupted handle (20); 10 confirmed.
    assert primary.details.get("cleanup_failure_count") == 2, primary.details
    assert "boom 30" in primary.details.get("close_cause", ""), primary.details
    carrier = ei.value.__cause__
    assert isinstance(carrier, ExecutionContractError), carrier
    assert carrier.details.get("cleanup_status") == "UNCONFIRMED"
    assert carrier.details.get("cleanup_failure_count") == 2
    assert "boom 30" in carrier.details.get("close_cause", "")
    # no retry / no double count on a second call
    close_binding_releasing(binding, primary)
    assert api.attempted == [30, 20, 10]
    assert primary.details.get("cleanup_failure_count") == 2


def test_close_last_interrupt_after_ordinary_failures_counts_all_unconfirmed(tmp_path):
    boom30, boom20 = OSError(5, "boom 30"), OSError(5, "boom 20")
    ki10 = KeyboardInterrupt("ki10")
    api = _ScriptedAPI(failures={30: boom30, 20: boom20}, interrupts={10: ki10})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r5 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    assert ei.value is ki10
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    assert primary.details.get("cleanup_failure_count") == 3, primary.details
    assert "boom 30" in primary.details.get("close_cause", ""), primary.details


def test_close_first_attempt_systemexit_variant_propagates_and_attempts_all(tmp_path):
    se30 = SystemExit("se30")
    api = _ScriptedAPI(interrupts={30: se30})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r5 primary")
    with pytest.raises(SystemExit) as ei:
        close_binding_releasing(binding, primary)
    assert ei.value is se30
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.details
    assert primary.details.get("cleanup_failure_count") == 1, primary.details


def test_close_all_interrupts_first_wins_and_all_facts_chain(tmp_path):
    ki30, ki20, ki10 = (KeyboardInterrupt("ki30"), KeyboardInterrupt("ki20"),
                        KeyboardInterrupt("ki10"))
    api = _ScriptedAPI(interrupts={30: ki30, 20: ki20, 10: ki10})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r5 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    assert ei.value is ki30, "first observed interrupt (attempt order) wins"
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    chain = _cause_chain(ei.value)
    assert chain[:2] == [ki20, ki10], "remaining interrupts visible on the chain"
    carrier = chain[2]
    assert isinstance(carrier, ExecutionContractError)
    assert carrier.details.get("cleanup_failure_count") == 3, carrier.details
    assert primary.details.get("cleanup_failure_count") == 3, primary.details
    assert "ki30" in primary.details.get("close_cause", ""), primary.details
    assert _chain_has(ei.value, primary)
    assert any("could not be confirmed" in n for n in getattr(ei.value, "__notes__", []))


def test_close_no_primary_interrupt_carries_classified_release_error(tmp_path):
    ki30 = KeyboardInterrupt("ki30")
    api = _ScriptedAPI(interrupts={30: ki30})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, None)
    assert ei.value is ki30
    assert not isinstance(ei.value, Exception), "signal must not become an Exception"
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    carrier = ei.value.__cause__
    assert isinstance(carrier, ExecutionContractError), "facts must not vanish"
    assert carrier.details.get("cleanup_status") == "UNCONFIRMED"
    assert carrier.details.get("cleanup_failure_count") == 1


def test_public_close_direct_interrupt_then_second_close_is_noop_without_retry(tmp_path):
    ki30 = KeyboardInterrupt("ki30")
    api = _ScriptedAPI(interrupts={30: ki30})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    with pytest.raises(KeyboardInterrupt) as ei:
        binding.close()
    assert ei.value is ki30
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    assert binding.close() is None, "second public close: strict no-op"
    assert api.attempted == [30, 20, 10], "interrupted handle never retried"


def test_generatorexit_close_interrupt_treated_symmetrically(tmp_path):
    ge30 = GeneratorExit("gen30")
    api = _ScriptedAPI(interrupts={30: ge30})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r5 primary")
    with pytest.raises(GeneratorExit) as ei:
        close_binding_releasing(binding, primary)
    assert ei.value is ge30
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.details
    assert primary.details.get("cleanup_failure_count") == 1, primary.details


def test_positive_all_handles_clean_attempted_once_no_annotation(tmp_path):
    api = _ScriptedAPI()
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r5 primary")
    close_binding_releasing(binding, primary)
    assert api.attempted == [30, 20, 10]
    _assert_once(api)
    assert "cleanup_status" not in primary.details, primary.details
    assert binding._handles == []


# ------------------------------------------- _finish_cleanup route (real fds / fake API)


def test_snapshot_route_interrupt_on_first_owned_fd_releases_remaining_fds(tmp_path, monkeypatch):
    root = _project(tmp_path)
    (root / "source.dat").write_bytes(b"fixture data payload")
    binding = project_root.open_project_root_binding(root)
    tracker = _KIOnFirstCloseOS(KeyboardInterrupt)
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                binding.read_file(("source.dat",), max_bytes=3)
            except BaseException as exc:
                raised = exc
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        assert len(tracker.interrupted_fds) == 1, tracker.interrupted_fds
        dup_fd, file_fd = tracker.opened[0][0], tracker.opened[1][0]
        assert tracker.attempts[dup_fd] == 1, "remaining owned fd must be released once"
        assert tracker.attempts[file_fd] == 1, "interrupted fd must not be retried"
        try:
            _REAL_OS.fstat(dup_fd)
            stranded = True
        except OSError as exc:
            assert exc.errno == 9, exc  # EBADF: dup descriptor confirmed released
            stranded = False
        assert not stranded, "remaining owned descriptor must not be left unattempted"
    finally:
        tracker.test_only_release_known_handles()
        if not binding._closed:
            binding.close()


def test_read_posix_primary_annotated_when_interrupt_hits_first_close(tmp_path, monkeypatch):
    root = _project(tmp_path)
    (root / "source.dat").write_bytes(b"fixture data payload")
    binding = project_root.open_project_root_binding(root)
    tracker = _KIOnFirstCloseOS(KeyboardInterrupt)
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                binding.read_file(("source.dat",), max_bytes=3)
            except BaseException as exc:
                raised = exc
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        primary = raised.__context__
        assert isinstance(primary, ExecutionContractError), type(primary).__name__
        assert primary.code == "INVALID_REQUEST", primary.as_dict()
        assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.as_dict()
        assert primary.details.get("cleanup_failure_count") == 1, primary.as_dict()
        dup_fd = tracker.opened[0][0]
        assert tracker.attempts[dup_fd] == 1, "remaining owned fd attempted once"
    finally:
        tracker.test_only_release_known_handles()
        if not binding._closed:
            binding.close()


def test_open_path_interrupt_in_parent_close_releases_remaining_descriptors(tmp_path, monkeypatch):
    (tmp_path / "a" / "b").mkdir(parents=True)
    target = tmp_path / "a" / "b"
    tracker = _KIOnFirstCloseOS(KeyboardInterrupt, fail_fstat_component="b")
    raised = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", tracker)
            try:
                project_root.open_project_root_binding(target)
            except BaseException as exc:
                raised = exc
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        for fd, _, _ in tracker.opened:
            assert tracker.attempts[fd] == 1, (fd, dict(tracker.attempts))
        b_fd = tracker.target_fd
        assert b_fd is not None
        # Interpreter auto-context: the raw in-flight OSError (same on both
        # trees); the classified open failure rides as __cause__ carrying
        # the annotated UNCONFIRMED facts.
        assert isinstance(raised.__context__, OSError), type(raised.__context__).__name__
        primary = raised.__cause__
        assert isinstance(primary, ExecutionContractError), type(primary).__name__
        assert primary.code == "ACCESS_VIOLATION", primary.as_dict()
        assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.as_dict()
        assert primary.details.get("cleanup_failure_count") == 1, primary.as_dict()
        live = tracker.live()
        assert live == [b_fd], (live, "only the interrupted descriptor may stay open")
    finally:
        tracker.test_only_release_known_handles()


class _ReadKiAPI:
    def __init__(self, interrupt_exc):
        self.interrupt_exc = interrupt_exc
        self.attempted = []

    def open_child_directory(self, current, component):
        return f"dir:{component}"

    def open_source_for_snapshot(self, current, name):
        return "file"

    def source_fingerprint(self, handle):
        # links=2 -> in-body ACCESS_VIOLATION primary before any read
        return (1, 4, 1000, 1000, 2)

    def close(self, handle):
        self.attempted.append(handle)
        if handle == "file":
            raise self.interrupt_exc


def test_windows_read_finish_cleanup_interrupt_still_attempts_remaining_handles(tmp_path):
    ki = KeyboardInterrupt("ki-file")
    api = _ReadKiAPI(ki)
    binding = ProjectRootBinding(Path(tmp_path), (1, 2), api=api, handles=["drive", "dirA"])
    raised = None
    attempted_after_read = None
    try:
        try:
            binding.read_file(("sub", "x.java"), max_bytes=10)
        except BaseException as exc:
            raised = exc
        attempted_after_read = list(api.attempted)
    finally:
        binding.close()
    assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
    assert raised is ki
    assert attempted_after_read == ["file", "dir:sub"], attempted_after_read
    assert _cause_chain(raised) == []  # _finish_cleanup primary path: no classified carrier
    primary = raised.__context__
    assert isinstance(primary, ExecutionContractError), type(primary).__name__
    assert primary.code == "ACCESS_VIOLATION", primary.as_dict()
    assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.as_dict()
    assert primary.details.get("cleanup_failure_count") == 1, primary.as_dict()


def test_service_scope_interrupt_still_annotates_primary_with_true_count(tmp_path, monkeypatch):
    # Real caller ownership path, regression guard for the carrier change:
    # single-fd POSIX close interrupt keeps count == 1 and propagates.
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
        assert isinstance(raised, KeyboardInterrupt), (type(raised).__name__, raised)
        assert tracker.attempts[tracker.failed_fds[0]] == 1, "no retry"
        assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.as_dict()
        assert primary.details.get("cleanup_failure_count") == 1, primary.as_dict()
        assert "KeyboardInterrupt" in repr(primary.details), primary.as_dict()
    finally:
        tracker.test_only_release_known_handles()
