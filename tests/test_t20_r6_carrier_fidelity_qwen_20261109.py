"""T20 r6 carrier-fidelity expectation tests (2026-11-09, Qwen subagent).

Expectation (NOT characterization) tests for the two diagnostic-fidelity
defects the r5 independent review measured and the main agent adjudicated
as real under the running synthetic fault model (fake-API injection; no
claim that a real OS signal produced them):

  1. an interrupt whose __cause__ slot was already occupied (closer body
     did ``raise KI from OSError``) made the classified release carrier
     unreachable -> primary undercounted, ordinary cleanup facts vanished,
     close_cause misled; with no primary every observed release fact
     vanished with it;
  2. with >=9 all-interrupt handles the chain-tail classified carrier sat
     beyond the bounded _release_carrier(limit=8) walk -> N unconfirmed
     handles annotated as 1.

These tests assert the CORRECTED contract: the classified carrier must be
reachable as a structured object bound directly on the propagating
interrupt (attribute ``_cleanup_release_carrier``, read via the product
reader ``_attached_release_carrier``), counts must be true, and the
interrupt's original __cause__, the primary and every pre-existing slot
must survive untouched.  Same real code entries as the r5 review probes:
ProjectRootBinding.close(), close_binding_releasing, the _finish_cleanup
route through read_file, and the real-fd open/close routes.  Expected:
RED on the r5 fixed tree, GREEN on the r6 fixed tree.  The r5 reviewer
characterization probes are preserved verbatim as separate files; they
are not edited here.

Synthetic fake-API/POSIX injection only; no COMSOL/Worker/daemon/SSH.
"""
from __future__ import annotations

import errno
import os
from collections import Counter
from pathlib import Path

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import (ProjectRootBinding,
                                      close_binding_releasing)

from tests.test_t20_project_root_cleanup_faults_parent_20261109 import (
    _CloseFaultOS, _REAL_OS)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixtures")

_ATTR = "_cleanup_release_carrier"


def _cause_chain(exc, limit=16):
    seen, chain, cur = set(), [], exc.__cause__
    while cur is not None and id(cur) not in seen and len(chain) < limit:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__
    return chain


def _has_classified_carrier(exc):
    return (isinstance(exc, ExecutionContractError)
            and isinstance(exc.details.get("cleanup_failure_count"), int))


def _direct_carrier(exc):
    """Query the structured carrier bound on the propagating exception
    itself, independent of the __cause__ chain (r6 contract)."""
    carrier = getattr(exc, _ATTR, None)
    if carrier is not None and _has_classified_carrier(carrier):
        return carrier
    return None


class _ScriptedAPI:
    """Fake Windows handle API; each handle maps to a closer body that
    raises (or returns cleanly).  Attempt order recorded."""

    def __init__(self, closers):
        self.closers = dict(closers)
        self.attempted = []

    def close(self, handle):
        self.attempted.append(handle)
        closer = self.closers.get(handle)
        if closer is not None:
            closer(handle)


def _chained(exc_type, payload, preempt):
    """Closer raising an interrupt explicitly chained from an inner
    OSError: the interrupt arrives with __cause__ already occupied."""
    def closer(handle):
        try:
            raise OSError(errno.EAGAIN, preempt)
        except OSError as inner:
            raise exc_type(payload) from inner
    return closer


def _free(exc_type, payload):
    def closer(handle):
        raise exc_type(payload)
    return closer


def _boom(message):
    def closer(handle):
        raise OSError(5, message)
    return closer


def _raise(exc):
    def closer(handle):
        raise exc
    return closer


def _fake_binding(tmp_path, api, handles):
    return ProjectRootBinding(Path(tmp_path), (1, 2), api=api,
                              handles=list(handles))


def _once(api):
    assert all(n == 1 for n in Counter(api.attempted).values()), api.attempted


def _multi_interrupt(tmp_path, n):
    api = _ScriptedAPI({h: _free(KeyboardInterrupt, f"ki{h}")
                        for h in range(1, n + 1)})
    binding = _fake_binding(tmp_path, api, list(range(1, n + 1)))
    primary = ExecutionContractError("INVALID_REQUEST", "r6 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    assert len(api.attempted) == n
    _once(api)
    assert primary.details.get("cleanup_status") == "UNCONFIRMED"
    return primary, ei.value


# ---------------------------------------------------------------- T1
# r5 P1 shape (occupied slot + primary): carrier must stay reachable as a
# structured object on the propagating interrupt; true count; ordinary
# failure fact visible; original closer cause untouched.


def test_r6_occupied_slot_primary_carrier_direct_and_true_count(tmp_path):
    api = _ScriptedAPI({30: _chained(KeyboardInterrupt, "ki30", "preempt-30"),
                        20: _boom("boom-20"), 10: None})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r6 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    # r5 fix surface preserved: exactly one attempt per handle, complete
    # bookkeeping, raw interrupt propagates.
    assert api.attempted == [30, 20, 10]
    _once(api)
    assert binding._handles == [] and binding._closed is True
    # Original closer cause is never overwritten.
    preempt = ei.value.__cause__
    assert isinstance(preempt, OSError) and "preempt-30" in str(preempt)
    # r6 contract: structured classified carrier reachable directly on the
    # propagated interrupt, with the true facts (RED on the r5 tree).
    carrier = _direct_carrier(ei.value)
    assert carrier is not None, "classified carrier vanished with occupied slot"
    assert carrier.details.get("cleanup_status") == "UNCONFIRMED"
    assert carrier.details.get("cleanup_failure_count") == 2, carrier.details
    assert "boom-20" in carrier.details.get("close_cause", ""), carrier.details
    # Primary keeps truthful facts: two unconfirmed handles (30 interrupted
    # + 20 ordinary), the ordinary failure visible, no misleading cause.
    assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.details
    assert primary.details.get("cleanup_failure_count") == 2, primary.details
    close_cause = primary.details.get("close_cause", "")
    assert "boom-20" in close_cause, primary.details
    assert "preempt-30" not in close_cause, primary.details
    # Strict no-op re-entry: no retry, no recount.
    close_binding_releasing(binding, primary)
    assert api.attempted == [30, 20, 10]
    assert primary.details.get("cleanup_failure_count") == 2, primary.details


# ---------------------------------------------------------------- T2
# r5 P3 shape (no primary, occupied slot): the propagated exception itself
# must expose every observed release-failure fact, queryably.


def test_r6_no_primary_occupied_slot_propagated_exception_exposes_facts(tmp_path):
    api = _ScriptedAPI({30: _chained(KeyboardInterrupt, "ki30", "preempt-30"),
                        20: _boom("boom-20"), 10: None})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, None)
    assert not isinstance(ei.value, Exception), "signal must not become Exception"
    assert api.attempted == [30, 20, 10]
    _once(api)
    assert isinstance(ei.value.__cause__, OSError) and "preempt-30" in str(ei.value.__cause__)
    carrier = _direct_carrier(ei.value)
    assert carrier is not None, (
        "no-primary occupied-slot shape must keep all observed facts "
        "queryable on the propagated exception")
    assert carrier.details.get("cleanup_status") == "UNCONFIRMED"
    assert carrier.details.get("cleanup_failure_count") == 2, carrier.details
    assert "boom-20" in carrier.details.get("close_cause", ""), carrier.details


# ---------------------------------------------------------------- T3
# r5 P4 shape (mid-chain occupied slot): the later free-slot interrupt
# attached the carrier on the chain, but the raised interrupt's occupied
# slot hid everything; the direct binding must cover the raised one.


def test_r6_midchain_occupied_slot_counts_both_interrupts(tmp_path):
    api = _ScriptedAPI({30: _chained(KeyboardInterrupt, "ki30", "preempt-30"),
                        20: _free(KeyboardInterrupt, "ki20"), 10: None})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r6 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    assert api.attempted == [30, 20, 10]
    _once(api)
    preempt = ei.value.__cause__
    assert isinstance(preempt, OSError) and "preempt-30" in str(preempt)
    carrier = _direct_carrier(ei.value)
    assert carrier is not None, "mid-chain occupied slot hid the carrier"
    assert carrier.details.get("cleanup_failure_count") == 2, carrier.details
    assert primary.details.get("cleanup_failure_count") == 2, (
        "two interrupted handles annotated as one")


# ---------------------------------------------------------------- T4/T5
# r5 P2b/P2c shapes (>=9 all-interrupt handles): carrier beyond the
# bounded chain walk, but the direct binding is depth-independent.


def test_r6_nine_interrupts_true_count_not_undercounted(tmp_path):
    primary, raised = _multi_interrupt(tmp_path, 9)
    chain = _cause_chain(raised)
    # The chain-tail carrier stays exactly where r5 put it (link 9; the
    # bounded product walk cannot reach it), but the direct binding does.
    idx = next(i for i, e in enumerate(chain) if _has_classified_carrier(e))
    assert idx == 8, chain
    carrier = _direct_carrier(raised)
    assert carrier is not None, "carrier unreachable at depth 9"
    assert carrier.details.get("cleanup_failure_count") == 9, carrier.details
    assert primary.details.get("cleanup_failure_count") == 9, primary.details


def test_r6_ten_interrupts_true_count_not_undercounted(tmp_path):
    primary, raised = _multi_interrupt(tmp_path, 10)
    carrier = _direct_carrier(raised)
    assert carrier is not None, "carrier unreachable at depth 10"
    assert carrier.details.get("cleanup_failure_count") == 10, carrier.details
    assert primary.details.get("cleanup_failure_count") == 10, primary.details


# ---------------------------------------------------------------- T6
# r5 P2a shape: the already-reachable boundary must NOT regress.


def test_r6_eight_interrupts_reachable_shape_still_holds(tmp_path):
    primary, raised = _multi_interrupt(tmp_path, 8)
    assert any(_has_classified_carrier(e) for e in _cause_chain(raised))
    carrier = _direct_carrier(raised)
    assert carrier is not None
    assert primary.details.get("cleanup_failure_count") == 8, primary.details


# ---------------------------------------------------------------- T7
# r5 P6 shape (single real fd, chained interrupt): count already correct,
# but close_cause must report the real interrupt, not the closer's inner
# preempting OSError, while the preempt cause stays on the chain.


class _ChainedCloseOS(_CloseFaultOS):
    def close(self, fd):
        self.attempts[fd] += 1
        try:
            raise OSError(errno.EAGAIN, "preempt-fd")
        except OSError as inner:
            raise KeyboardInterrupt("ki-fd") from inner


def test_r6_single_fd_chained_interrupt_cause_not_misleading(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    binding = project_root.open_project_root_binding(root)
    tracker = _ChainedCloseOS()
    primary = ExecutionContractError("INVALID_REQUEST", "r6 primary")
    raised = None
    try:
        import unittest.mock as mock
        with mock.patch.object(project_root, "os", tracker):
            try:
                close_binding_releasing(binding, primary)
            except BaseException as exc:
                raised = exc
        assert isinstance(raised, KeyboardInterrupt), type(raised).__name__
        assert all(n == 1 for n in tracker.attempts.values()), dict(tracker.attempts)
        # original cause survives on the chain
        assert isinstance(raised.__cause__, OSError) and "preempt-fd" in str(raised.__cause__)
        carrier = _direct_carrier(raised)
        assert carrier is not None, "single-fd occupied slot dropped the carrier"
        assert carrier.details.get("cleanup_failure_count") == 1, carrier.details
        assert primary.details.get("cleanup_failure_count") == 1, primary.details
        close_cause = primary.details.get("close_cause", "")
        assert "ki-fd" in close_cause, primary.details
        assert "preempt-fd" not in close_cause, primary.details
    finally:
        tracker.test_only_release_known_handles()


# ---------------------------------------------------------------- T8
# Original cause / ordinary-failure pins (no interrupt): behaviour must
# stay identical on both trees (pin against over-reach).


def test_r6_ordinary_only_release_without_primary_classified_raises(tmp_path):
    api = _ScriptedAPI({30: _boom("boom-30"), 20: None, 10: None})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    with pytest.raises(ExecutionContractError) as ei:
        close_binding_releasing(binding, None)
    assert api.attempted == [30, 20, 10]
    _once(api)
    assert isinstance(ei.value, OSError), "classified release stays OSError-catching"
    assert ei.value.details.get("cleanup_status") == "UNCONFIRMED"
    assert ei.value.details.get("cleanup_failure_count") == 1, ei.value.details
    assert isinstance(ei.value.__cause__, OSError) and "boom-30" in str(ei.value.__cause__)


def test_r6_ordinary_only_release_with_primary_annotates_without_raise(tmp_path):
    api = _ScriptedAPI({30: _boom("boom-30"), 20: None, 10: None})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r6 primary")
    close_binding_releasing(binding, primary)  # ordinary close failure: annotated, not raised
    assert api.attempted == [30, 20, 10]
    _once(api)
    assert primary.details.get("cleanup_status") == "UNCONFIRMED", primary.details
    assert primary.details.get("cleanup_failure_count") == 1, primary.details
    assert "boom-30" in primary.details.get("close_cause", ""), primary.details


# ---------------------------------------------------------------- T9
# Guard contracts on the product binding function itself: never
# overwrite existing information, no cycle, non-Exception variants
# symmetric, chaining surface of the r5 fix preserved.


def test_r6_direct_binding_never_overwrites_existing_info(tmp_path):
    sentinel = ExecutionContractError("ACCESS_VIOLATION", "prior binding")
    sentinel.details["cleanup_status"] = "UNCONFIRMED"
    sentinel.details["cleanup_failure_count"] = 1
    ki = KeyboardInterrupt("ki-guard")
    setattr(ki, _ATTR, sentinel)
    later = ExecutionContractError("ACCESS_VIOLATION", "later carrier")
    later.details["cleanup_failure_count"] = 7
    with pytest.raises(KeyboardInterrupt) as ei:
        project_root._propagate_cleanup_interrupts([ki], classified=later)
    assert ei.value is ki
    assert getattr(ki, _ATTR, None) is sentinel, "existing binding must survive"


def test_r6_carrier_cycle_guard_and_fallback_note_untouched(tmp_path):
    ki = KeyboardInterrupt("ki-cycle")
    classified = ExecutionContractError("ACCESS_VIOLATION", "classified")
    classified.details["cleanup_failure_count"] = 1
    classified.__cause__ = ki  # carrier pointing at the interrupt
    with pytest.raises(KeyboardInterrupt) as ei:
        project_root._propagate_cleanup_interrupts([ki], classified=classified)
    assert classified.__cause__ is None, "anti-cycle guard must clear the back edge"
    assert ei.value.__cause__ is classified or _direct_carrier(ei.value) is classified
    assert _direct_carrier(ei.value) is classified


def test_r6_systemexit_occupied_slot_symmetric(tmp_path):
    api = _ScriptedAPI({30: _chained(SystemExit, "se30", "preempt-30"),
                        20: _boom("boom-20"), 10: None})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r6 primary")
    with pytest.raises(SystemExit) as ei:
        close_binding_releasing(binding, primary)
    assert api.attempted == [30, 20, 10]
    _once(api)
    carrier = _direct_carrier(ei.value)
    assert carrier is not None
    assert carrier.details.get("cleanup_failure_count") == 2, carrier.details
    assert primary.details.get("cleanup_failure_count") == 2, primary.details


def test_r6_free_slot_shape_chains_and_direct_binding_agree(tmp_path):
    boom30 = OSError(5, "boom 30")
    ki20 = KeyboardInterrupt("ki20")
    api = _ScriptedAPI({30: _raise(boom30),
                        20: _free(KeyboardInterrupt, "ki20"), 10: None})
    binding = _fake_binding(tmp_path, api, [10, 20, 30])
    primary = ExecutionContractError("INVALID_REQUEST", "r6 primary")
    with pytest.raises(KeyboardInterrupt) as ei:
        close_binding_releasing(binding, primary)
    # r5 chaining surface preserved: the carrier rides the free __cause__
    # slot of the raised interrupt.
    chain = _cause_chain(ei.value)
    assert _has_classified_carrier(ei.value.__cause__), chain
    # and the direct binding points at the same classified facts.
    carrier = _direct_carrier(ei.value)
    assert carrier is not None, "free-slot shape must also expose the binding"
    assert carrier is ei.value.__cause__
    assert carrier.details.get("cleanup_failure_count") == 2, carrier.details
