"""T13 portable regression: a validation-stage UNKNOWN raise must not project
as a definite refusal at the live managed callback boundary.

Version v2 (task-owned): corrects one characterization assertion in
``test_no_stage_unknown_never_relabeled_pre_dispatch_fail_closed_raise`` —
the existing live handling re-raises the SAME exception with its ``stage``
attribute left as ``None`` and records the post-dispatch facts in
``details``; it never rewrites the stage attribute.  All other bytes are as
in v1; v1 and its red-v1 receipts remain preserved.

Scope (docs/universal_mcp_execution/evidence/full/T13-managed-validation-live-brief-20261110.md):
this exercises only the real static ``ManagedBackend._dispatch_with_witness``
path of ``comsol_mcp/_managed_backend.py`` plus the shared
``comsol_mcp/_domain_outcome`` classifier, against a synthetic caller-raised
``ExecutionContractError``.  No service, ledger, store, Worker, daemon, model
or engine is constructed or called, and nothing here tests fallback, cursor or
returned-UNKNOWN behavior.  The synthetic exception shape is constructible; no
real producer is claimed.  A no-mutation witness proves only that no
mutation-class engine method was dispatched — never that no read was
dispatched, and never Worker quiescence.

The test is portable: it carries no task-identity environment requirement.
Process identity binding (live module paths + SHAs) lives in a separate
task-owned guard plugin configured by the task runner.
"""
from __future__ import annotations

import pytest

from comsol_mcp._domain_outcome import classify_envelope
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._managed_backend import ManagedBackend

UNKNOWN_CODE = "EXECUTION_STATE_UNKNOWN"
DEFINITE_CODE = "INVALID_REQUEST"
UNKNOWN_MESSAGE = "synthetic validation probe could not establish the engine state"
DEFINITE_MESSAGE = "synthetic validation gate rejected the request"
OPERATION = "variable.group_create"


def _dispatch_raising(exc, *, effect="project_write"):
    """Run the real live static dispatcher; the callback executes exactly once."""
    calls: list[str] = []

    def call():
        calls.append(OPERATION)
        raise exc

    envelope = ManagedBackend._dispatch_with_witness(OPERATION, call, effect=effect)
    return envelope, calls


def _assert_unknown_projection(envelope, calls, exc, *, declared_safe_retry, details, effect):
    assert calls == [OPERATION], "the callback must really execute exactly once"
    outer = envelope["error"]
    # Safety core: an explicit UNKNOWN code must never advertise safe_retry=True
    # at a declared outer error or data-refusal position.  This fails on the
    # pre-fix live backend, which forwards the exception's declared True.
    assert outer["safe_retry"] is False
    refusal = envelope["data"]["refusal"]
    assert refusal["safe_retry"] is False
    # The existing classifier must be told this is an unresolved state, via the
    # existing UNKNOWN signal contract key — not via any new shared-classifier
    # interpretation of the error code.
    outcome = classify_envelope(envelope)
    assert outcome.state == "unknown"
    assert outcome.execution_state_unknown is True
    assert envelope["domain_state"] == "unknown"
    assert envelope["execution_state_unknown"] is True
    assert outcome.success is False
    # Declared code/message/stage facts are preserved at both positions.
    for record in (outer, refusal):
        assert record["code"] == UNKNOWN_CODE
        assert record["message"] == UNKNOWN_MESSAGE
        assert record["stage"] == "validation"
    assert envelope["data"]["status"] == "REFUSED"
    assert envelope["data"]["refused"] is True
    assert envelope["data"]["dispatch_stage"] == "validation"
    # The witness stays published, but only ever proves the mutation negative.
    assert envelope["data"]["witness"]["mutation_issued"] is False
    assert envelope["effect"] == effect
    # The caller's exception object and its details are never mutated.
    assert exc.code == UNKNOWN_CODE
    assert exc.safe_retry is declared_safe_retry
    assert exc.stage == "validation"
    assert exc.details == details
    assert exc.details is not envelope["data"]["refusal"].get("details")


def test_validation_stage_unknown_safe_retry_true_projects_unknown_and_suppresses_retry():
    details = {"probe": "t13-managed-boundary", "entities": [1, 2]}
    exc = ExecutionContractError(UNKNOWN_CODE, UNKNOWN_MESSAGE,
                                 safe_retry=True, stage="validation", details=details)
    envelope, calls = _dispatch_raising(exc, effect="project_write")
    _assert_unknown_projection(envelope, calls, exc, declared_safe_retry=True,
                               details=details, effect="project_write")


def test_validation_stage_unknown_safe_retry_false_projects_unknown():
    details = {"probe": "t13-managed-boundary-false"}
    exc = ExecutionContractError(UNKNOWN_CODE, UNKNOWN_MESSAGE,
                                 safe_retry=False, stage="validation", details=details)
    envelope, calls = _dispatch_raising(exc, effect="project_write")
    _assert_unknown_projection(envelope, calls, exc, declared_safe_retry=False,
                               details=details, effect="project_write")


def test_validation_stage_unknown_inspect_effect_projects_unknown():
    details = {"probe": "t13-managed-boundary-inspect"}
    exc = ExecutionContractError(UNKNOWN_CODE, UNKNOWN_MESSAGE,
                                 safe_retry=True, stage="validation", details=details)
    envelope, calls = _dispatch_raising(exc, effect="inspect")
    _assert_unknown_projection(envelope, calls, exc, declared_safe_retry=True,
                               details=details, effect="inspect")


def test_definite_validation_refusal_safe_retry_true_keeps_failed_and_retry_fact():
    details = {"gate": "component-mismatch"}
    exc = ExecutionContractError(DEFINITE_CODE, DEFINITE_MESSAGE,
                                 safe_retry=True, stage="validation", details=details)
    envelope, calls = _dispatch_raising(exc, effect="project_write")
    assert calls == [OPERATION]
    outcome = classify_envelope(envelope)
    # A definite refusal keeps its old classification and its own retry fact.
    assert outcome.state == "failed"
    assert envelope["domain_state"] == "failed"
    assert envelope["execution_state_unknown"] is False
    assert outcome.execution_state_unknown is False
    outer = envelope["error"]
    assert outer["code"] == DEFINITE_CODE
    assert outer["message"] == DEFINITE_MESSAGE
    assert outer["stage"] == "validation"
    assert outer["safe_retry"] is True
    assert envelope["data"]["refusal"]["safe_retry"] is True
    assert envelope["data"]["status"] == "REFUSED"
    assert envelope["data"]["dispatch_stage"] == "validation"
    assert envelope["effect"] == "project_write"
    assert exc.details == details
    assert exc.safe_retry is True


def test_definite_validation_refusal_safe_retry_false_keeps_failed_and_retry_fact():
    details = {"gate": "component-mismatch"}
    exc = ExecutionContractError(DEFINITE_CODE, DEFINITE_MESSAGE,
                                 safe_retry=False, stage="validation", details=details)
    envelope, calls = _dispatch_raising(exc, effect="inspect")
    assert calls == [OPERATION]
    outcome = classify_envelope(envelope)
    assert outcome.state == "failed"
    assert envelope["domain_state"] == "failed"
    assert envelope["execution_state_unknown"] is False
    outer = envelope["error"]
    assert outer["code"] == DEFINITE_CODE
    assert outer["safe_retry"] is False
    assert envelope["data"]["refusal"]["safe_retry"] is False
    assert envelope["effect"] == "inspect"
    assert exc.details == details


def test_no_stage_unknown_never_relabeled_pre_dispatch_fail_closed_raise():
    # Existing handling (read before writing): a raise without the explicit
    # validation stage is never routed to the refusal envelope; the dispatcher
    # re-raises the SAME exception fail-closed, leaving its stage attribute
    # None and recording the post-dispatch facts in details only.
    details = {"probe": "t13-no-stage"}
    exc = ExecutionContractError(UNKNOWN_CODE, UNKNOWN_MESSAGE,
                                 safe_retry=True, details=details)
    calls: list[str] = []

    def call():
        calls.append(OPERATION)
        raise exc

    with pytest.raises(ExecutionContractError) as info:
        ManagedBackend._dispatch_with_witness(OPERATION, call, effect="project_write")
    assert calls == [OPERATION]
    assert info.value is exc  # same exception re-raised, never a refusal envelope
    assert info.value.stage is None  # the attribute is not rewritten pre-dispatch
    assert info.value.details["dispatch_stage"] == "post_dispatch"
    assert info.value.details["unproven_pre_dispatch"] is False
    assert info.value.details["cause_code"] == UNKNOWN_CODE
    assert info.value.details["witness"]["mutation_issued"] is False
    assert info.value.details["probe"] == "t13-no-stage"


def test_no_stage_definite_code_fail_closed_wrapped_unknown_post_dispatch():
    exc = ExecutionContractError(DEFINITE_CODE, DEFINITE_MESSAGE,
                                 safe_retry=True, details={"gate": "no-stage"})
    calls: list[str] = []

    def call():
        calls.append(OPERATION)
        raise exc

    with pytest.raises(ExecutionContractError) as info:
        ManagedBackend._dispatch_with_witness(OPERATION, call, effect="project_write")
    assert calls == [OPERATION]
    assert info.value.code == UNKNOWN_CODE
    assert info.value.stage == "post_dispatch"
    assert info.value.__cause__ is exc
    assert info.value.details["cause_code"] == DEFINITE_CODE
    assert info.value.details["unproven_pre_dispatch"] is False
