"""G3 R04 targeted regression: huge built-in integers in ``property_tolerance``.

This file is the authored, source-bound regression for one narrow preflight
defect hypothesis: the ``property_tolerance`` validator in
``comsol_mcp/_g2_transactions.py`` classifies ``value``/``tolerance`` with a
direct ``math.isfinite(float(number))`` call, and ``float()`` raises
``OverflowError`` on a standard Python integer of magnitude ``10**400`` before
the intended ``INVALID_INVARIANT`` classification can run.  The declared
intent - pinned here against the real exported entrypoints - is that such a
declaration is a malformed invariant: ``supported=False`` with
``code="INVALID_INVARIANT"`` and a finite-number reason, rejected before any
action when required, and NOT_RUN (never a pass) when optional.

Author note: this test file was authored but never executed by the author.
The parent runs the source-bound RED/GREEN judgment.

Fault-input provenance: the principal failure inputs are huge *signed built-in
integers* (``10**400`` and ``-(10**400)``), which are ordinary Python literals
and perfectly JSON-representable as numbers.  The synthetic ``float('nan')`` /
``float('inf')`` controls below are Python-only fault inputs (JSON cannot carry
NaN/Infinity); they are secondary classification controls, not the principal
failure, and are labelled as such in their case IDs.
"""
from __future__ import annotations

from typing import Any, Mapping

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_transactions import (
    parse_invariants,
    preview_transaction,
    run_transaction,
    validate_invariants,
)

# A valid empty NodePath and a registry-backed fake action (same operation
# shape already exercised in tests/test_g3_r04_invariants.py).  No COMSOL,
# backend, store or verifier-of-truth is ever touched; every runner/verifier
# below is an explicit local spy.
_EMPTY_PATH = {"segments": []}
_ACTION = {"operation_id": "node.inspect", "arguments": {"path": _EMPTY_PATH}}

_HUGE = 10**400


def _tolerance_decl(**overrides) -> dict:
    """A ``property_tolerance`` declaration with otherwise normal numeric fields."""
    declaration = {
        "type": "property_tolerance",
        "path": _EMPTY_PATH,
        "name": "height",
        "value": 2.0,
        "tolerance": 0.1,
    }
    declaration.update(overrides)
    return declaration


# One huge signed built-in integer per field, each in its own case so a single
# field is ever the sole malformed part.
_HUGE_CASES = [
    pytest.param("value", _HUGE, id="value-positive-huge-int-10pow400"),
    pytest.param("value", -_HUGE, id="value-negative-huge-int-neg-10pow400"),
    pytest.param("tolerance", _HUGE, id="tolerance-positive-huge-int-10pow400"),
    pytest.param("tolerance", -_HUGE, id="tolerance-negative-huge-int-neg-10pow400"),
]


def _huge_decl(field: str, number: int) -> dict:
    return _tolerance_decl(**{field: number})


class _RunnerSpy:
    """Fake transaction runner: records every invocation, succeeds, mutates nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int]] = []

    def __call__(self, operation: str, arguments: dict, index: int) -> dict:
        self.calls.append((operation, dict(arguments), index))
        return {"success": True}


class _PassVerifierSpy:
    """Fake invariant verifier: records invocations and would return PASS."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], int]] = []

    def __call__(self, declaration: Mapping[str, Any], index: int) -> Mapping[str, Any]:
        self.calls.append((dict(declaration), index))
        return {"status": "PASS", "observed": {"value": declaration.get("value")},
                "expected": {"value": declaration.get("value")}}


# ---------------------------------------------------------------------------
# parse_invariants: the huge integer must classify as a malformed invariant.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "number"), _HUGE_CASES)
def test_parse_invariants_classifies_huge_integer_as_invalid_invariant(field, number):
    rows = parse_invariants([_huge_decl(field, number)])

    assert len(rows) == 1
    row = rows[0]
    assert row["supported"] is False
    assert row["code"] == "INVALID_INVARIANT"
    assert row["error"] is not None
    assert f"property_tolerance {field} must be a finite number" in row["error"]
    # The declaration was required by default and stays visible as required.
    assert row["required"] is True


# ---------------------------------------------------------------------------
# Required declarations: every preflight surface must raise the typed
# ExecutionContractError before any action, never an OverflowError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "number"), _HUGE_CASES)
def test_validate_invariants_raises_contract_error_for_required_huge_integer(field, number):
    with pytest.raises(ExecutionContractError) as exc:
        validate_invariants([_huge_decl(field, number)])

    assert exc.value.code == "INVALID_INVARIANT"
    assert f"property_tolerance {field} must be a finite number" in str(exc.value)


@pytest.mark.parametrize(("field", "number"), _HUGE_CASES)
def test_preview_transaction_raises_contract_error_for_required_huge_integer(field, number):
    with pytest.raises(ExecutionContractError) as exc:
        preview_transaction([_ACTION], [_huge_decl(field, number)])

    assert exc.value.code == "INVALID_INVARIANT"
    assert f"property_tolerance {field} must be a finite number" in str(exc.value)


@pytest.mark.parametrize(("field", "number"), _HUGE_CASES)
def test_run_transaction_rejects_required_huge_integer_before_invoking_runner(field, number):
    runner = _RunnerSpy()

    with pytest.raises(ExecutionContractError) as exc:
        run_transaction([_ACTION], runner=runner, invariants=[_huge_decl(field, number)])

    assert exc.value.code == "INVALID_INVARIANT"
    # Side-effect witness: the preflight must abort before the first action.
    assert runner.calls == []


# ---------------------------------------------------------------------------
# Optional declarations: the action must really run, the malformed check is
# NOT_RUN (never a pass), and no verifier invocation may occur.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "number"), _HUGE_CASES)
def test_run_transaction_optional_huge_integer_runs_action_and_marks_check_not_run(field, number):
    runner = _RunnerSpy()
    verifier = _PassVerifierSpy()
    declaration = {**_huge_decl(field, number), "required": False}

    record = run_transaction([_ACTION], runner=runner, invariants=[declaration],
                             invariant_verifier=verifier)

    # The action really reached the fake runner exactly once.
    assert len(runner.calls) == 1
    assert runner.calls[0][2] == 0
    assert len(record.applied) == 1 and not record.failed
    assert record.execution_status == "SUCCEEDED"
    assert record.verification_status == "PARTIAL"

    checks = record.invariant_results["checks"]
    assert len(checks) == 1
    assert checks[0]["status"] == "NOT_RUN"
    assert checks[0]["required"] is False
    assert record.invariant_results["counts"]["not_run"] == 1
    # The unsupported check short-circuits: the verifier is never invoked.
    assert verifier.calls == []


# ---------------------------------------------------------------------------
# Non-vacuous finite positive controls: the same entrypoints must still accept
# ordinary built-in ints and floats and really execute/run/verify.
# ---------------------------------------------------------------------------


def test_finite_integer_property_tolerance_is_supported_and_fully_runs():
    declaration = _tolerance_decl(value=42, tolerance=1)

    rows = parse_invariants([declaration])
    assert rows[0]["supported"] is True
    assert rows[0]["code"] is None

    assert validate_invariants([declaration])[0]["supported"] is True

    runner = _RunnerSpy()
    verifier = _PassVerifierSpy()
    record = run_transaction([_ACTION], runner=runner, invariants=[declaration],
                             invariant_verifier=verifier)

    assert len(runner.calls) == 1
    assert len(verifier.calls) == 1
    assert record.execution_status == "SUCCEEDED"
    assert record.verification_status == "VERIFIED"
    assert record.invariant_results["checks"][0]["status"] == "PASS"


def test_finite_float_property_tolerance_is_supported_and_fully_runs():
    declaration = _tolerance_decl(value=2.5, tolerance=0.25)

    rows = parse_invariants([declaration])
    assert rows[0]["supported"] is True
    assert rows[0]["code"] is None

    preview = preview_transaction([_ACTION], [declaration])
    assert preview["status"] == "PREVIEW"
    assert preview["invariant_checks"][0]["supported"] is True

    runner = _RunnerSpy()
    verifier = _PassVerifierSpy()
    record = run_transaction([_ACTION], runner=runner, invariants=[declaration],
                             invariant_verifier=verifier)

    assert len(runner.calls) == 1
    assert len(verifier.calls) == 1
    assert record.execution_status == "SUCCEEDED"
    assert record.verification_status == "VERIFIED"
    assert record.invariant_results["checks"][0]["status"] == "PASS"


# ---------------------------------------------------------------------------
# Existing classification controls (already-green behaviour kept pinned so a
# numeric-preflight fix cannot regress them).  NaN/inf cases are labelled
# python-only fault inputs.
# ---------------------------------------------------------------------------

_CLASSIFICATION_CONTROLS = [
    pytest.param("tolerance", -0.5, "must not be negative", id="control-negative-tolerance"),
    pytest.param("value", True, "must be a finite number", id="control-bool-value"),
    pytest.param("tolerance", False, "must be a finite number", id="control-bool-tolerance"),
    pytest.param("value", float("nan"), "must be a finite number",
                 id="control-python-only-nan-value"),
    pytest.param("tolerance", float("inf"), "must be a finite number",
                 id="control-python-only-inf-tolerance"),
]


@pytest.mark.parametrize(("field", "number", "expected_reason"), _CLASSIFICATION_CONTROLS)
def test_existing_malformed_numeric_classifications_stay_invalid(field, number, expected_reason):
    rows = parse_invariants([_tolerance_decl(**{field: number})])

    assert rows[0]["supported"] is False
    assert rows[0]["code"] == "INVALID_INVARIANT"
    assert expected_reason in rows[0]["error"]
