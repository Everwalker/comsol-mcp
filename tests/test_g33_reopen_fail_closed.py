"""Control tests only: ambiguous scalar reads and missing identities must fail."""
import pytest
from comsol_mcp._gate_a_reopen import verify_reopen, ReopenVerificationError
from tests.test_g3_gate_a2_f02_reopen import FakeReopenModel


def receipt(**extra):
    return {"model_sha256": "control-only", "dataset": "dset1",
            "expectations": {"T": {"expected": 300., "tolerance": .001}}, **extra}


def test_scalar_expectation_cannot_silently_drop_other_points():
    model = FakeReopenModel(sha256="control-only", stored_solutions={})
    with pytest.raises(ReopenVerificationError, match="multiple values"):
        verify_reopen(model, receipt(), evaluator=lambda m, e: [[[300., 999.]]])


def test_empty_solution_tags_never_pass_required_solution():
    model = FakeReopenModel(sha256="control-only", stored_solutions={})
    class EmptySolutions:
        def tags(self):
            return []
    model.sol = lambda: EmptySolutions()
    with pytest.raises(ReopenVerificationError) as raised:
        verify_reopen(model, receipt(solution="sol1"), evaluator=lambda m, e: 300.)
    assert raised.value.code == "SOLUTION_NOT_FOUND"


def test_missing_solver_readback_never_skips_required_check():
    model = FakeReopenModel(sha256="control-only", stored_solutions={})
    model.solver_settings = lambda: None
    with pytest.raises(ReopenVerificationError) as raised:
        verify_reopen(model, receipt(solver_settings={"rtol": 1e-6}), evaluator=lambda m, e: 300.)
    assert raised.value.code == "SOLVER_SETTINGS_UNREADABLE"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "300"])
def test_nonnumeric_or_nonfinite_scalar_is_not_an_empty_solution(value):
    model = FakeReopenModel(sha256="control-only", stored_solutions={})
    with pytest.raises(ReopenVerificationError) as raised:
        verify_reopen(model, receipt(), evaluator=lambda m, e: [[value]])
    assert raised.value.code == "UNEXPECTED_DATA_SHAPE"
