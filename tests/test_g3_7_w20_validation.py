"""Tests for W20 three-layer validation module (B01-B09).

Covers:
- B06: Three-layer status independence
- B02: Numerical metrics (finite/range/weighted/integral/conservation/benchmark)
- B03/B04: Frozen oracle correctness (steady-state copper block, transient sine decay)
- B08: Oracle immutability after freeze, negative control
- B05: Convergence study with 3+ levels
- B09: Report structure
"""
from __future__ import annotations

import json
import math
import pytest

from comsol_mcp._g3_w20_validation import (
    STATUS_PASS, STATUS_FAIL, STATUS_UNVERIFIED,
    ValidationStatus,
    finite_check, range_check, weighted_statistics, integral_check,
    conservation_residual, benchmark_error,
    FrozenExpectation, FrozenOracle,
    create_steady_state_oracle, create_transient_oracle,
    transient_analytical_solution,
    ConvergenceStep, ConvergenceStudy,
    ValidationReport,
)


# ──── B06: Three-Layer Status Independence ────


class TestValidationStatus:
    def test_default_is_unverified(self):
        vs = ValidationStatus()
        assert vs.execution_status == STATUS_UNVERIFIED
        assert vs.numerical_verification_status == STATUS_UNVERIFIED
        assert vs.physical_validation_status == STATUS_UNVERIFIED

    def test_axes_are_independent(self):
        """Execution PASS must NOT imply numerical PASS."""
        vs = ValidationStatus(
            execution_status=STATUS_PASS,
            numerical_verification_status=STATUS_FAIL,
            physical_validation_status=STATUS_UNVERIFIED,
        )
        assert vs.execution_status == STATUS_PASS
        assert vs.numerical_verification_status == STATUS_FAIL
        assert vs.physical_validation_status == STATUS_UNVERIFIED

    def test_no_physical_without_experiment(self):
        """Without independent experimental evidence, physical must be UNVERIFIED."""
        vs = ValidationStatus(
            execution_status=STATUS_PASS,
            numerical_verification_status=STATUS_PASS,
        )
        assert vs.physical_validation_status == STATUS_UNVERIFIED


# ──── B02: Numerical Metrics ────


class TestNumericalMetrics:
    def test_finite_check_pass(self):
        assert finite_check([1.0, 2.0, 3.0]) is True

    def test_finite_check_nan(self):
        assert finite_check([1.0, float('nan'), 3.0]) is False

    def test_finite_check_inf(self):
        assert finite_check([1.0, float('inf')]) is False

    def test_finite_check_empty(self):
        assert finite_check([]) is True

    def test_range_check_within(self):
        assert range_check([300.0, 325.0, 350.0], 200.0, 400.0) is True

    def test_range_check_outside(self):
        assert range_check([300.0, 500.0], 200.0, 400.0) is False

    def test_weighted_statistics_uniform(self):
        result = weighted_statistics([1.0, 2.0, 3.0], [1.0, 1.0, 1.0])
        assert abs(result["mean"] - 2.0) < 1e-10
        assert abs(result["rms"] - math.sqrt(14 / 3)) < 1e-10

    def test_weighted_statistics_mismatched_lengths(self):
        with pytest.raises(ValueError):
            weighted_statistics([1.0, 2.0], [1.0])

    def test_integral_check(self):
        # Trapezoidal-style: sum(v*w)
        result = integral_check([10.0, 20.0], [0.5, 0.5])
        assert abs(result - 15.0) < 1e-10

    def test_conservation_residual_exact(self):
        assert abs(conservation_residual(80.0, 80.0, 80.0)) < 1e-12

    def test_conservation_residual_small_denominator(self):
        with pytest.raises(ValueError, match="Small denominator"):
            conservation_residual(80.0, 79.0, 0.0)

    def test_benchmark_error_absolute(self):
        assert abs(benchmark_error(312.6, 312.5) - 0.1) < 1e-10

    def test_benchmark_error_relative(self):
        # |80.5 - 80| / 80 = 0.00625
        assert abs(benchmark_error(80.5, 80.0, is_relative=True) - 0.00625) < 1e-10

    def test_benchmark_error_small_denominator_relative(self):
        with pytest.raises(ValueError, match="Small denominator"):
            benchmark_error(0.01, 0.0, is_relative=True)


# ──── B03/B04: Frozen Oracle ────


class TestFrozenOracle:
    def test_steady_state_oracle_correct_values(self):
        """B03: Copper block steady-state oracle with correct analytical values."""
        oracle = create_steady_state_oracle()
        # T(0.0125) = 312.5 K exactly
        status, err = oracle.check_observation("T_0.0125", 312.5)
        assert status == STATUS_PASS
        assert err < 1e-10

        status, err = oracle.check_observation("T_0.025", 325.0)
        assert status == STATUS_PASS

        status, err = oracle.check_observation("T_0.0375", 337.5)
        assert status == STATUS_PASS

    def test_steady_state_oracle_marginal(self):
        """Value at tolerance boundary."""
        oracle = create_steady_state_oracle()
        # T_0.0125 expected=312.5, tolerance=0.1K
        status, err = oracle.check_observation("T_0.0125", 312.59)
        assert status == STATUS_PASS  # 0.09 < 0.1
        assert err < 0.1

    def test_steady_state_oracle_fails_outside_tolerance(self):
        """B08 negative control: value outside tolerance must FAIL."""
        oracle = create_steady_state_oracle()
        status, err = oracle.check_observation("T_0.0125", 313.0)
        assert status == STATUS_FAIL
        assert err > 0.1

    def test_steady_state_heat_flow(self):
        """Heat flow check: 80W expected, 1% relative tolerance."""
        oracle = create_steady_state_oracle()
        # 80.5W → relative error 0.5/80 = 0.625% < 1%
        status, err = oracle.check_observation("HeatFlow", 80.5)
        assert status == STATUS_PASS

        # 82W → relative error 2/80 = 2.5% > 1%
        status, err = oracle.check_observation("HeatFlow", 82.0)
        assert status == STATUS_FAIL

    def test_transient_oracle_known_points(self):
        """B04: Transient sine decay oracle with analytical solution."""
        oracle = create_transient_oracle()

        # Check all 9 points (3 x-positions × 3 times)
        for x in [0.25, 0.5, 0.75]:
            for t in [0.01, 0.03, 0.1]:
                expected = transient_analytical_solution(x, t)
                name = f"T_{x}_{t}"
                status, err = oracle.check_observation(name, expected)
                assert status == STATUS_PASS, f"{name}: expected PASS, got {status} (err={err})"

    def test_transient_analytical_solution_correctness(self):
        """Verify the analytical solution formula itself."""
        # At t=0, x=0.5: T = 300 + 10*sin(π*0.5) = 310
        assert abs(transient_analytical_solution(0.5, 0.0) - 310.0) < 1e-10
        # At t→∞: T → 300 (decay term vanishes)
        assert abs(transient_analytical_solution(0.5, 100.0) - 300.0) < 1e-10
        # At x=0 or x=L: T = 300 (boundary condition, sin(0)=0, sin(π)≈0)
        assert abs(transient_analytical_solution(0.0, 0.05) - 300.0) < 1e-10

    def test_oracle_immutability(self):
        """B08: Cannot modify oracle after freezing."""
        oracle = create_steady_state_oracle()
        assert oracle.frozen is True
        with pytest.raises(RuntimeError, match="Cannot modify oracle after freezing"):
            oracle.set_expectation(FrozenExpectation("T_hack", 999.0, 100.0))

    def test_oracle_must_be_frozen_to_check(self):
        """Cannot check observations before freezing."""
        oracle = FrozenOracle()
        oracle.set_expectation(FrozenExpectation("T_test", 100.0, 0.1))
        with pytest.raises(RuntimeError, match="must be frozen"):
            oracle.check_observation("T_test", 100.0)

    def test_oracle_unknown_name_returns_unverified(self):
        """Checking an unknown observation returns UNVERIFIED."""
        oracle = create_steady_state_oracle()
        status, _ = oracle.check_observation("nonexistent", 42.0)
        assert status == STATUS_UNVERIFIED


# ──── B05: Convergence Study ────


class TestConvergenceStudy:
    def test_monotonic_convergence(self):
        study = ConvergenceStudy()
        study.add_step(ConvergenceStep(1, 0.1, 1e-3, 0.01, 0.5, {"time_s": 1}))
        study.add_step(ConvergenceStep(2, 0.05, 1e-4, 0.005, 0.1, {"time_s": 5}))
        study.add_step(ConvergenceStep(3, 0.025, 1e-5, 0.0025, 0.01, {"time_s": 20}))
        result = study.analyze_trend()
        assert result["status"] == STATUS_PASS
        assert result["trend"] == "monotonic"

    def test_non_monotonic_convergence(self):
        study = ConvergenceStudy()
        study.add_step(ConvergenceStep(1, 0.1, 1e-3, 0.01, 0.5, {}))
        study.add_step(ConvergenceStep(2, 0.05, 1e-4, 0.005, 0.8, {}))  # Worse
        study.add_step(ConvergenceStep(3, 0.025, 1e-5, 0.0025, 0.01, {}))
        result = study.analyze_trend()
        assert result["status"] == STATUS_FAIL
        assert result["trend"] == "fluctuating"

    def test_insufficient_steps(self):
        study = ConvergenceStudy()
        study.add_step(ConvergenceStep(1, 0.1, 1e-3, 0.01, 0.5, {}))
        study.add_step(ConvergenceStep(2, 0.05, 1e-4, 0.005, 0.1, {}))
        result = study.analyze_trend()
        assert result["status"] == STATUS_UNVERIFIED


# ──── B09: Validation Report ────


class TestValidationReport:
    def test_report_json_roundtrip(self):
        report = ValidationReport(
            source_identity="be5bfc8",
            runtime_version="6.4.0.293",
            model_ref="copper_block.mph",
            dataset_ref="dset1",
            rule_version="w20.1",
            input_assumptions={"k": 400, "L": 0.05},
            frozen_expectations={"T_0.025": 325.0},
            raw_observations={"T_0.025": 325.001},
            error_tolerance_data={"T_0.025": {"error": 0.001, "tolerance": 0.1}},
            coverage_exclusions={"excluded_regions": []},
            warnings=[],
            evidence_hash="abc123",
        )
        text = report.to_json()
        data = json.loads(text)
        assert data["source_identity"] == "be5bfc8"
        assert data["frozen_expectations"]["T_0.025"] == 325.0

    def test_report_markdown_contains_sections(self):
        report = ValidationReport(
            source_identity="be5bfc8",
            runtime_version="6.4.0.293",
            model_ref="test.mph",
            dataset_ref="dset1",
            rule_version="w20.1",
            input_assumptions={},
            frozen_expectations={},
            raw_observations={},
            error_tolerance_data={},
            coverage_exclusions={},
            warnings=["low mesh quality"],
            evidence_hash="xyz789",
        )
        md = report.to_markdown()
        assert "Validation Report" in md
        assert "Evidence Hash" in md
        assert "xyz789" in md
