"""T20 reused-interrupt r8 — caller-visible contract tests (Qwen3.8 subagent).

Scope per T20-reused-interrupt-r8-brief: the adjudicated r6 defect is that
one reusable exception instance shared across TWO owned-cleanup operations
made operation B's close_binding_releasing annotation report operation A's
classified carrier (undercounted unconfirmed handles, A's ordinary
close_cause leaking into B).  These tests assert ONLY the caller-visible
facts (the primary's details, raw propagation, one attempt per handle,
strict no-op re-close, foreign attributes never destroyed) through the
real product entries ProjectRootBinding.close() / close_binding_releasing.
They deliberately do NOT read private carrier attributes on the interrupt:
that private direct-exception visibility is pinned separately by the
existing r6/r7 suites, which are run unmodified elsewhere.

Expected: RED on the pinned r6 baseline (935ad2bb…), GREEN on the r8
isolated candidate (operation-local release facts kept on the binding).
Synthetic fake-API injection only; no claim any real OS reuses interrupt
instances; no native Windows, COMSOL/Worker/daemon/SSH, no user data.
"""
from __future__ import annotations

import errno
from collections import Counter
from pathlib import Path

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import (ProjectRootBinding,
                                      close_binding_releasing)

pytestmark = pytest.mark.skipif(__import__("os").name == "nt",
                                reason="POSIX-only scratch tree")

_ATTR = "_cleanup_release_carrier"


class _ScriptedAPI:
    def __init__(self, closers):
        self.closers = dict(closers)
        self.attempted = []

    def close(self, handle):
        self.attempted.append(handle)
        closer = self.closers.get(handle)
        if closer is not None:
            closer(handle)


def _fake_binding(tmp_path, api, handles):
    return ProjectRootBinding(Path(tmp_path) / "root", (1, 2), api=api,
                              handles=list(handles))


def _raise_instance(exc_obj):
    def closer(handle):
        raise exc_obj
    return closer


def _chained_from(exc_obj, message):
    def closer(handle):
        try:
            raise OSError(errno.EAGAIN, message)
        except OSError as inner:
            raise exc_obj from inner
    return closer


def _boom(message):
    def closer(handle):
        raise OSError(5, message)
    return closer


def _run_op(tmp_path, name, api, handles):
    binding = _fake_binding(tmp_path, api, handles)
    primary = ExecutionContractError("INVALID_REQUEST", f"primary-{name}")
    raised = None
    try:
        close_binding_releasing(binding, primary)
    except BaseException as exc:  # noqa: BLE001 - signal expected
        raised = exc
    assert isinstance(raised, BaseException) and not isinstance(raised, Exception), \
        (type(raised).__name__, "interrupt must propagate raw")
    assert binding._handles == [] and binding._closed is True
    return binding, primary, raised


def _assert_once(api):
    assert all(n == 1 for n in Counter(api.attempted).values()), api.attempted


# ------------------------------------------------------------------ R1
# The adjudicated precise repro, asserted through caller-visible facts only.
# RED on baseline: op B primary count 2 and stale boom-20 leak.

def test_r8_reused_instance_second_op_true_count_and_current_cause(tmp_path):
    shared = KeyboardInterrupt("ki-shared")
    _, primary_a, raised_a = _run_op(
        tmp_path, "A", _ScriptedAPI({30: _raise_instance(shared),
                                     20: _boom("boom-20"), 10: None}),
        [10, 20, 30])
    assert raised_a is shared
    assert primary_a.details.get("cleanup_failure_count") == 2, primary_a.details
    assert "boom-20" in primary_a.details.get("close_cause", "")

    api_b = _ScriptedAPI({30: _raise_instance(shared), 21: _raise_instance(shared),
                          20: _raise_instance(shared), 10: None})
    binding_b, primary_b, raised_b = _run_op(tmp_path, "B", api_b, [10, 20, 21, 30])
    assert api_b.attempted == [30, 21, 20, 10]
    _assert_once(api_b)
    assert raised_b is shared
    # CURRENT operation's facts reach the caller:
    assert primary_b.details.get("cleanup_failure_count") == 3, primary_b.details
    close_cause_b = primary_b.details.get("close_cause", "")
    assert "boom-20" not in close_cause_b, primary_b.details
    assert "KeyboardInterrupt" in close_cause_b, primary_b.details
    # Op A's own evidence survived untouched:
    assert primary_a.details.get("cleanup_failure_count") == 2, primary_a.details
    assert "boom-20" in primary_a.details.get("close_cause", "")
    # strict no-op re-close: no retry, no recount
    close_binding_releasing(binding_b, primary_b)
    assert api_b.attempted == [30, 21, 20, 10]
    assert primary_b.details.get("cleanup_failure_count") == 3, primary_b.details


# ------------------------------------------------------------------ R2
# Reused instance, second operation has its own ordinary failure.

def test_r8_reused_instance_second_op_current_ordinary_cause(tmp_path):
    shared = KeyboardInterrupt("ki-cause")
    _, primary_a, _ = _run_op(
        tmp_path, "A", _ScriptedAPI({30: _raise_instance(shared),
                                     20: _boom("boom-20"), 10: None}),
        [10, 20, 30])
    assert "boom-20" in primary_a.details.get("close_cause", "")
    _, primary_b, _ = _run_op(
        tmp_path, "B", _ScriptedAPI({30: _raise_instance(shared),
                                     20: _boom("boom-77"), 10: None}),
        [10, 20, 30])
    assert primary_b.details.get("cleanup_failure_count") == 2, primary_b.details
    close_cause_b = primary_b.details.get("close_cause", "")
    assert "boom-77" in close_cause_b, primary_b.details
    assert "boom-20" not in close_cause_b, primary_b.details


# ------------------------------------------------------------------ R3
# Reused instance + deep all-interrupt second op (9 handles).

def test_r8_reused_instance_deep_multi_handle_true_count(tmp_path):
    shared = KeyboardInterrupt("ki-deep")
    _, primary_a, _ = _run_op(tmp_path, "A",
                              _ScriptedAPI({30: _raise_instance(shared)}), [30])
    assert primary_a.details.get("cleanup_failure_count") == 1, primary_a.details
    api_b = _ScriptedAPI({h: _raise_instance(shared) for h in range(1, 10)})
    _, primary_b, _ = _run_op(tmp_path, "B", api_b, list(range(1, 10)))
    _assert_once(api_b)
    assert primary_b.details.get("cleanup_failure_count") == 9, primary_b.details


# ------------------------------------------------------------------ R4
# SystemExit reuses through the same path: symmetric behaviour.

def test_r8_systemexit_shared_instance_across_ops_true_counts(tmp_path):
    shared = SystemExit("se-shared")
    _, primary_a, _ = _run_op(tmp_path, "A",
                              _ScriptedAPI({30: _raise_instance(shared),
                                            20: None, 10: None}), [10, 20, 30])
    assert primary_a.details.get("cleanup_failure_count") == 1, primary_a.details
    _, primary_b, raised_b = _run_op(
        tmp_path, "B", _ScriptedAPI({30: _raise_instance(shared),
                                     20: _raise_instance(shared), 10: None}),
        [10, 20, 30])
    assert isinstance(raised_b, SystemExit)
    assert primary_b.details.get("cleanup_failure_count") == 2, primary_b.details


# ------------------------------------------------------------------ R5
# Foreign pre-existing legacy attribute: never destroyed, and the current
# operation's facts still reach the caller.  RED on baseline (the foreign
# stale carrier used to annotate this operation).

def test_r8_foreign_attribute_never_destroyed_current_facts_reach_caller(tmp_path):
    ki = KeyboardInterrupt("ki-foreign")
    sentinel = ExecutionContractError("ACCESS_VIOLATION", "foreign prior binding")
    sentinel.details["cleanup_status"] = "UNCONFIRMED"
    sentinel.details["cleanup_failure_count"] = 1
    sentinel.details["close_cause"] = "OSError: foreign-cause"
    setattr(ki, _ATTR, sentinel)
    _, primary, raised = _run_op(
        tmp_path, "foreign", _ScriptedAPI({30: _raise_instance(ki),
                                           20: _boom("boom-9"), 10: None}),
        [10, 20, 30])
    assert raised is ki
    assert getattr(ki, _ATTR, None) is sentinel, "foreign attribute must survive"
    assert sentinel.details.get("cleanup_failure_count") == 1, "foreign carrier not mutated"
    assert primary.details.get("cleanup_failure_count") == 2, primary.details
    close_cause = primary.details.get("close_cause", "")
    assert "boom-9" in close_cause, primary.details
    assert "foreign-cause" not in close_cause, primary.details


# ------------------------------------------------------------------ R6
# Foreign occupied __cause__ (raise shared from OSError) in both ops: the
# closer's own cause stays, each op's count stays true.

def test_r8_foreign_occupied_cause_preserved_and_counts_current(tmp_path):
    shared = KeyboardInterrupt("ki-fc")
    _, primary_a, raised_a = _run_op(
        tmp_path, "A", _ScriptedAPI({30: _chained_from(shared, "preempt-A"),
                                     20: _boom("boom-20"), 10: None}),
        [10, 20, 30])
    assert isinstance(raised_a.__cause__, OSError) and "preempt-A" in str(raised_a.__cause__)
    assert primary_a.details.get("cleanup_failure_count") == 2, primary_a.details
    _, primary_b, raised_b = _run_op(
        tmp_path, "B", _ScriptedAPI({30: _chained_from(shared, "preempt-B"),
                                     21: _raise_instance(shared),
                                     22: _raise_instance(shared), 10: None}),
        [10, 21, 22, 30])
    assert raised_b is shared
    assert isinstance(raised_b.__cause__, OSError) and "preempt-B" in str(raised_b.__cause__), \
        "closer's current cause must be preserved"
    assert primary_b.details.get("cleanup_failure_count") == 3, primary_b.details
    assert "boom-20" not in primary_b.details.get("close_cause", ""), primary_b.details


# ------------------------------------------------------------------ R7
# NEGATIVE CONTROL: fresh instances per op — already truthful on both trees.

def test_r8_fresh_instances_across_ops_control(tmp_path):
    _, primary_a, _ = _run_op(tmp_path, "A",
                              _ScriptedAPI({30: _raise_instance(KeyboardInterrupt("kiA")),
                                            20: None, 10: None}), [10, 20, 30])
    assert primary_a.details.get("cleanup_failure_count") == 1, primary_a.details
    _, primary_b, _ = _run_op(
        tmp_path, "B", _ScriptedAPI({30: _raise_instance(KeyboardInterrupt("kiB")),
                                     20: _raise_instance(KeyboardInterrupt("kiB2")),
                                     10: None}), [10, 20, 30])
    assert primary_b.details.get("cleanup_failure_count") == 2, primary_b.details
    assert "kiA" not in primary_b.details.get("close_cause", ""), primary_b.details


# ------------------------------------------------------------------ R8
# SINGLE-OP reuse: facts belong to that operation; count true on both trees.

def test_r8_single_op_reused_instance_control(tmp_path):
    shared = KeyboardInterrupt("ki-single-op")
    _, primary, raised = _run_op(
        tmp_path, "single", _ScriptedAPI({30: _raise_instance(shared),
                                          20: _raise_instance(shared), 10: None}),
        [10, 20, 30])
    assert raised is shared
    assert primary.details.get("cleanup_failure_count") == 2, primary.details


# ------------------------------------------------------------------ R9
# Marker-write-refusal interrupt subclass through the real product entry:
# the interrupt must propagate raw (parent counterexample 1 shape asserted
# caller-visibly); preservation control, true on both trees.

def test_r8_marker_refusing_interrupt_still_propagates_raw(tmp_path):
    class SelectiveInterrupt(KeyboardInterrupt):
        def __setattr__(self, name, value):
            if name == "_cleanup_release_carrier_bound":
                raise AttributeError("injected marker-attribute refusal")
            return super().__setattr__(name, value)

    ki = SelectiveInterrupt("ki-selective")
    api = _ScriptedAPI({30: _raise_instance(ki), 20: _boom("boom-2"), 10: None})
    _, primary, raised = _run_op(tmp_path, "selective", api, [10, 20, 30])
    assert raised is ki, "no helper may turn an interrupt into a normal return"
    _assert_once(api)
    assert primary.details.get("cleanup_failure_count") == 2, primary.details
    assert "boom-2" in primary.details.get("close_cause", ""), primary.details
