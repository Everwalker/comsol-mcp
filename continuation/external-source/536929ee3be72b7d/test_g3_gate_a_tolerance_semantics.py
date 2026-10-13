"""Tolerance semantics of the reopen checker (D20).

The delivered checker read ``tolerance``/``rel_tolerance`` with a 1e-3 default and
passed when *either* window held, so a receipt that stated a 1e-3 absolute tolerance
against a 338 K value was silently verified against 0.338 K -- the stated window and
the applied window were different numbers, and nothing in the report said which one
decided.  These tests pin the corrected contract:

* a receipt that states neither window is refused (no substituted default),
* the applied window is exactly the stated one,
* the effective absolute window and which rule decided are recorded per comparison.
"""
from __future__ import annotations

import pytest

from comsol_mcp._gate_a_reopen import ReopenVerificationError, verify_reopen
from tests.test_g3_gate_a2_f02_reopen import FakeReopenModel


def _receipt(sha: str, expected: float, **extra: object) -> dict:
    return {
        "model_sha256": sha,
        "dataset": "dset1",
        "expectations": {"T_sample": {"expected": expected, **extra}},
    }


def test_missing_tolerance_is_refused_not_defaulted() -> None:
    model = FakeReopenModel(sha256="ok_sha", stored_solutions={"T_sample": [[308.15]]})
    with pytest.raises(ReopenVerificationError) as excinfo:
        verify_reopen(model, _receipt("ok_sha", 308.15))
    assert excinfo.value.code == "MISSING_TOLERANCE"
    assert excinfo.value.details["actual"] == 308.15


def test_stated_absolute_tolerance_is_the_window_that_applies() -> None:
    model = FakeReopenModel(sha256="ok_sha", stored_solutions={"T_sample": [[308.1500001]]})
    report = verify_reopen(model, _receipt("ok_sha", 308.15, tolerance=1e-3))
    comparison = report["comparisons"]["T_sample"]
    assert comparison["effective_abs_window"] == 1e-3
    assert comparison["decided_by"] == "absolute"
    assert comparison["abs_diff"] < 1e-3
    assert "no substituted default" in report["tolerance_policy"] or "must state" in report["tolerance_policy"]


def test_defaulted_relative_window_no_longer_widens_the_check() -> None:
    # A 0.33 K deviation at 338 K: the old default rel_tolerance=1e-3 accepted it.
    model = FakeReopenModel(sha256="ok_sha", stored_solutions={"T_sample": [[338.48]]})
    with pytest.raises(ReopenVerificationError) as excinfo:
        verify_reopen(model, _receipt("ok_sha", 338.15, tolerance=1e-3))
    assert excinfo.value.code == "STORED_VALUE_MISMATCH"
    assert excinfo.value.details["effective_abs_window"] == 1e-3
    assert excinfo.value.details["rel_tolerance"] is None


def test_relative_tolerance_alone_defines_its_own_window() -> None:
    # 1e-3 relative at 338.15 K is 0.338 K, and the report says so explicitly.
    model = FakeReopenModel(sha256="ok_sha", stored_solutions={"T_sample": [[338.4]]})
    report = verify_reopen(model, _receipt("ok_sha", 338.15, rel_tolerance=1e-2))
    comparison = report["comparisons"]["T_sample"]
    assert comparison["rel_tolerance"] == 1e-2
    assert comparison["effective_abs_window"] == pytest.approx(1e-2 * 338.15)
    assert comparison["decided_by"] == "relative"
    assert comparison["tolerance"] is None
