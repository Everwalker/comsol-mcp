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


# ──── B01-B09 Operations Dispatch via G3 Registry ────


class TestG3ValidationOperations:
    def test_g3_ops_registration(self):
        from comsol_mcp import _g3_ops
        for op in (
            "validate.preflight",
            "validate.structure",
            "validate.expressions",
            "validate.boundary_conditions",
            "validate.solution",
            "validate.conservation",
            "validate.convergence",
            "validate.report",
        ):
            assert _g3_ops.is_implemented(op), f"{op} must be implemented in G3 operations"

    def test_dispatch_validate_structure(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        data = _g3_ops.dispatch("validate.structure", mock_worker, "model1", {
            "model_data": {
                "components": ["comp1"],
                "geometries": ["geom1"],
                "physics": ["ht"],
                "meshes": ["mesh1"],
                "studies": ["std1"],
            }
        })
        assert data["execution_status"] == STATUS_PASS
        assert data["numerical_verification_status"] == "NOT_APPLICABLE"
        assert data["physical_validation_status"] == STATUS_UNVERIFIED
        assert data["status"] == STATUS_PASS

    def test_dispatch_validate_preflight(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        # Incomplete structure cannot be ready to solve (F02)
        incomplete_data = _g3_ops.dispatch("validate.preflight", mock_worker, "model1", {
            "model_data": {
                "components": ["comp1"],
                "studies": ["std1"],
            }
        })
        assert incomplete_data["ready_to_solve"] is False

        # Complete structure with components, studies, physics, mesh is ready
        data = _g3_ops.dispatch("validate.preflight", mock_worker, "model1", {
            "model_data": {
                "components": ["comp1"],
                "geometries": ["geom1"],
                "physics": ["ht"],
                "meshes": ["mesh1"],
                "studies": ["std1"],
            }
        })
        assert data["execution_status"] == STATUS_PASS
        assert data["ready_to_solve"] is True

    def test_dispatch_validate_expressions(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        data = _g3_ops.dispatch("validate.expressions", mock_worker, "model1", {
            "expressions": [
                {"name": "k_cu", "expr": "400[W/(m*K)]", "value": 400.0},
                {"name": "T0", "expr": "300[K]", "value": 300.0},
            ]
        })
        assert data["status"] == STATUS_PASS
        assert data["numerical_verification_status"] == STATUS_PASS

        # Negative control: syntax error and string expression handling
        bad_data = _g3_ops.dispatch("validate.expressions", mock_worker, "model1", {
            "expressions": ["undefined_variable", "T + )"]
        })
        assert bad_data["status"] == STATUS_FAIL
        assert bad_data["numerical_verification_status"] == STATUS_FAIL

    def test_dispatch_validate_boundary_conditions(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        # Uninspected boundaries without engine or data are UNVERIFIED
        unverified_data = _g3_ops.dispatch("validate.boundary_conditions", mock_worker, "model1", {
            "rules": ["bc.thermal_insulation", "bc.temperature_inflow"]
        })
        assert unverified_data["status"] == STATUS_UNVERIFIED

        # Provided boundary data with distinct entities passes
        data = _g3_ops.dispatch("validate.boundary_conditions", mock_worker, "model1", {
            "rules": ["bc.thermal_insulation", "bc.temperature_inflow"],
            "boundary_data": {
                "boundaries": [
                    {"tag": "temp1", "entities": [1]},
                    {"tag": "temp2", "entities": [6]},
                ]
            }
        })
        assert data["status"] == STATUS_PASS
        assert data["execution_status"] == STATUS_PASS

        # Negative control: conflicting temperature boundaries on entity 1
        conflict_data = _g3_ops.dispatch("validate.boundary_conditions", mock_worker, "model1", {
            "rules": ["conflicting_temperature_boundaries"],
            "boundary_data": {
                "boundaries": [
                    {"tag": "temp1", "entities": [1]},
                    {"tag": "temp2", "entities": [1]},
                ]
            }
        })
        assert conflict_data["status"] == STATUS_FAIL

    def test_dispatch_validate_solution_with_copper_block_oracle(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        data = _g3_ops.dispatch("validate.solution", mock_worker, "model1", {
            "solution": {"tag": "sol1"},
            "criteria": {
                "values": [312.5, 325.0, 337.5],
                "range": [300.0, 350.0],
                "oracle": "steady_state_copper_block",
                "observations": {
                    "T_0.0125": 312.505,
                    "T_0.025": 325.01,
                    "T_0.0375": 337.495,
                    "HeatFlow": 80.2,
                }
            }
        })
        assert data["execution_status"] == STATUS_PASS
        assert data["numerical_verification_status"] == STATUS_PASS
        assert data["physical_validation_status"] == STATUS_UNVERIFIED
        assert data["status"] == STATUS_PASS

    def test_dispatch_validate_solution_negative_control(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        # Empty solution / criteria cannot pass
        empty_res = _g3_ops.dispatch("validate.solution", mock_worker, "model1", {})
        assert empty_res["status"] == STATUS_FAIL

        # Missing required observations for oracle cannot pass
        missing_res = _g3_ops.dispatch("validate.solution", mock_worker, "model1", {
            "solution": {"tag": "sol1"},
            "criteria": {
                "oracle": "steady_state_copper_block",
                "observations": {"T_0.025": 325.0}  # missing other 3
            }
        })
        assert missing_res["status"] == STATUS_FAIL

        # Value out of tolerance fails
        data = _g3_ops.dispatch("validate.solution", mock_worker, "model1", {
            "solution": {"tag": "sol1"},
            "criteria": {
                "oracle": "steady_state_copper_block",
                "observations": {
                    "T_0.0125": 320.0,  # 7.5K error >> 0.1K tolerance
                    "T_0.025": 325.0,
                    "T_0.0375": 337.5,
                    "HeatFlow": 80.0,
                }
            }
        })
        assert data["execution_status"] == STATUS_PASS
        assert data["numerical_verification_status"] == STATUS_FAIL
        assert data["status"] == STATUS_FAIL

    def test_dispatch_validate_conservation(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        # Empty conservation fails
        assert _g3_ops.dispatch("validate.conservation", mock_worker, "model1", {})["status"] == STATUS_FAIL

        data = _g3_ops.dispatch("validate.conservation", mock_worker, "model1", {
            "definition": {
                "inflow": 80.0,
                "outflow": 79.9,
                "normalization": 80.0,
                "tolerance": 0.01,
            }
        })
        assert data["execution_status"] == STATUS_PASS
        assert data["passed"] is True
        assert data["status"] == STATUS_PASS

    def test_dispatch_validate_convergence(self):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        # Identical mesh size fails
        bad_conv = _g3_ops.dispatch("validate.convergence", mock_worker, "model1", {
            "cases": [
                {"level": 1, "mesh_size_metric": 1.0, "error": 100.0},
                {"level": 2, "mesh_size_metric": 1.0, "error": 90.0},
                {"level": 3, "mesh_size_metric": 1.0, "error": 80.0},
            ],
            "criteria": {"absolute_error_max": 0.001}
        })
        assert bad_conv["status"] == STATUS_FAIL

        # Valid monotonic convergence meeting target
        data = _g3_ops.dispatch("validate.convergence", mock_worker, "model1", {
            "cases": [
                {"level": 1, "mesh_size_metric": 0.02, "error": 0.25},
                {"level": 2, "mesh_size_metric": 0.01, "error": 0.08},
                {"level": 3, "mesh_size_metric": 0.005, "error": 0.015},
            ],
            "metrics": ["T_error"],
            "criteria": {"absolute_error_max": 0.05}
        })
        assert data["execution_status"] == STATUS_PASS
        assert data["status"] == STATUS_PASS
        assert data["analysis"]["trend"] == "monotonic"

    def test_dispatch_validate_report(self, tmp_path):
        from comsol_mcp import _g3_ops
        mock_worker = type("MockWorker", (), {"client": lambda self: None})()
        dest = str(tmp_path / "report.md")
        data = _g3_ops.dispatch("validate.report", mock_worker, "model1", {
            "destination": dest,
            "data": {
                "source_identity": "be5bfc8",
                "runtime_version": "6.4.0.293",
                "frozen_expectations": {"T_mid": 325.0},
                "raw_observations": {"T_mid": 325.002},
                "error_tolerance_data": {
                    "status": "PASS",
                    "numerical_verification_status": "PASS"
                }
            }
        })
        assert data["status"] == STATUS_PASS
        assert "evidence_hash" in data["report_summary"]
        report_file = tmp_path / "report.md"
        assert report_file.is_file()
        content = report_file.read_text(encoding="utf-8")
        assert "Validation Report: be5bfc8" in content

        # Negative control: destination is a directory fails
        dir_dest = tmp_path / "somedir"
        dir_dest.mkdir()
        fail_dir = _g3_ops.dispatch("validate.report", mock_worker, "model1", {
            "destination": str(dir_dest),
            "data": {"status": "PASS"}
        })
        assert fail_dir["status"] == STATUS_FAIL

        # Negative control: child verification FAIL does not get upgraded to PASS
        fail_up = _g3_ops.dispatch("validate.report", mock_worker, "model1", {
            "data": {
                "error_tolerance_data": {"numerical_verification_status": "FAIL"}
            }
        })
        assert fail_up["status"] == STATUS_FAIL

