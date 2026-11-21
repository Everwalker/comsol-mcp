"""T20 r8 POSIX reused-interrupt regression (Qwen3.8 subagent, integration).

Single collected pytest case for the r8-modified POSIX ``os.close``
interrupt branch of ProjectRootBinding.close() (the fake-API r8 suite
never reaches it; the shape mirrors the independent reviewer's guarded
P2 probe, restructured as an honest collected test).

A reused KeyboardInterrupt instance already carries a foreign
``_cleanup_release_carrier`` sentinel with a STALE count.  The injected
``os.close`` raises that shared instance for this test's tracked fd
before the real close ever sees that fd, which therefore stays open and
owned by this test until the finally releases it exactly once.

Corrected contract asserted here (only for operations WITH a primary that
take the close_binding_releasing annotation branch): the consumer
annotates the primary with THIS operation's truth — count 1, the current
KeyboardInterrupt cause, no foreign-cause leak — while the foreign
sentinel attribute and its own facts are never destroyed or mutated.
The pinned r6 baseline annotates the stale foreign count/cause instead
(RED on count, not on setup).

Fixture-safety invariants: ownership tracking starts before ``os.open``
is ever invoked, the fake close counts/intercepts only the exact tracked
fd and delegates every other fd argument to the real ``os.close``, the
passthrough fd number is relinquished BEFORE its close is attempted so
no later exception can make the cleanup blind-retry an already-attempted
(possibly reused) descriptor number, that number is never probed with
``fstat`` after its close (an open fstat answer on a closed number could
belong to a foreign resource), and cleanup attempts every still-owned
test fd exactly once — a failed or ambiguous close is surfaced, never
retried, and never skips the attempt on another owned fd.
Synthetic guarded injection on test-owned real fds only; skip on native
Windows; no claim any real OS reuses interrupt instances; no COMSOL,
daemon, Worker, SSH or network.
"""
from __future__ import annotations

import os

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import (ProjectRootBinding,
                                      close_binding_releasing)

pytestmark = pytest.mark.skipif(os.name == "nt",
                                reason="POSIX fd injection only")

_ATTR = "_cleanup_release_carrier"


def test_posix_reused_interrupt_annotates_current_op_count(tmp_path, monkeypatch):
    real_close = os.close
    shared = KeyboardInterrupt("ki-r8-posix-reused")
    sentinel = ExecutionContractError("ACCESS_VIOLATION", "foreign-posix-prior")
    sentinel.details["cleanup_status"] = "UNCONFIRMED"
    sentinel.details["cleanup_failure_count"] = 7
    sentinel.details["close_cause"] = "OSError: foreign-posix-cause"
    setattr(shared, _ATTR, sentinel)

    tracked_fd: int | None = None
    passthrough_fd: int | None = None
    tracked_close_calls = 0
    delegated: list[int] = []

    def fake_close(fd: int) -> None:
        nonlocal tracked_close_calls
        # Intercept ONLY this test's tracked fd: the shared interrupt is
        # raised before the real close ever sees that fd, which stays
        # open and owned by this test until the finally below.  Any
        # other fd argument is delegated straight to the real
        # ``os.close`` — this fake is never a process-global close-blocker.
        if fd == tracked_fd:
            tracked_close_calls += 1
            raise shared
        delegated.append(fd)
        return real_close(fd)

    # Ownership protection starts BEFORE os.open is invoked: if the open
    # itself or any later setup step fails, the finally releases exactly
    # the fd this test actually acquired (and nothing else), once.
    try:
        tracked_fd = os.open(str(tmp_path), os.O_RDONLY)
        binding = ProjectRootBinding(tmp_path / "root", (1, 2), fd=tracked_fd)
        primary = ExecutionContractError("INVALID_REQUEST", "primary-posix-reuse")
        raised = None
        with monkeypatch.context() as patch:
            patch.setattr(os, "close", fake_close)
            try:
                close_binding_releasing(binding, primary)
            except BaseException as exc:
                raised = exc
            # Passthrough check on an explicitly test-owned separate
            # synthetic fd: through the patched ``os.close`` it must
            # reach the real close instead of being swallowed.  The fd
            # number is relinquished from the tracked variables BEFORE
            # its close is attempted, so no subsequent exception —
            # ambiguous close, assertion failure or interrupt — can
            # make the finally below retry a close that has already been
            # attempted.  The number is never probed afterwards: a
            # closed descriptor number may be reused by unrelated code,
            # and an ``fstat`` on it would touch a potentially foreign
            # resource.  Only fds owned by this test are ever passed.
            passthrough_fd = os.open(str(tmp_path), os.O_RDONLY)
            owned_passthrough, passthrough_fd = passthrough_fd, None
            os.close(owned_passthrough)
            assert delegated == [owned_passthrough], delegated
        # The injected fault reached the tracked fd exactly once, and
        # nothing else was intercepted, before any success is claimed:
        assert tracked_close_calls == 1, tracked_close_calls
        # Propagation shape holds on both trees (passes on base too):
        assert raised is shared, "raw reused interrupt must propagate"
        assert not isinstance(raised, Exception), type(raised).__name__
        assert binding._closed is True and binding._fd is None
        # THIS operation's truth reaches the caller — the pinned r6 base
        # fails exactly here with the stale foreign count (7), not setup:
        assert primary.details.get("cleanup_failure_count") == 1, primary.details
        close_cause = primary.details.get("close_cause", "")
        assert "ki-r8-posix-reused" in close_cause, primary.details
        assert "foreign-posix-cause" not in close_cause, primary.details
        # The binding recorded this attempt's classified facts locally:
        recorded = getattr(binding, "_release_error", None)
        assert (recorded is not None
                and recorded.details.get("cleanup_failure_count") == 1), \
            "POSIX recording branch must have run for this attempt"
        # Foreign carrier and its facts are never destroyed or mutated:
        assert getattr(shared, _ATTR, None) is sentinel
        assert sentinel.details.get("cleanup_failure_count") == 7, sentinel.details
    finally:
        # The monkeypatch context above has exited, so ``os.close`` is
        # restored by it — no manual global write.  Every fd still owned
        # by this test is attempted exactly once through the real
        # closer: a close already attempted (the passthrough fd, whose
        # number was relinquished above before its close) is never
        # retried, and one failing release neither swallows its own
        # outcome nor skips the attempt on another owned fd — the first
        # such failure is surfaced after every remaining owned fd has
        # been attempted.
        cleanup_failure = None
        for owned_fd in (passthrough_fd, tracked_fd):
            if owned_fd is None:
                continue
            try:
                real_close(owned_fd)
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        if cleanup_failure is not None:
            raise cleanup_failure
