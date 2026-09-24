"""W20 Validation Module: Three-layer validation system (Execution, Numerical, Physical).

Implements B01-B09 for the COMSOL MCP project:
- B01: Structural Pre-Checks
- B02: Numerical Metrics
- B03, B04, B08: Frozen Oracle / Benchmark Checker
- B05: Convergence Study
- B06: Three-Layer Status Model
- B09: Validation Report
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# 1. Three-Layer Status Model (B06)
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_BLOCKED = "BLOCKED"
STATUS_ERROR = "ERROR"
STATUS_UNSUPPORTED = "UNSUPPORTED"


@dataclass
class ValidationStatus:
    """Three independent status axes for validation."""
    execution_status: str = STATUS_UNVERIFIED
    numerical_verification_status: str = STATUS_UNVERIFIED
    physical_validation_status: str = STATUS_UNVERIFIED


# 2. Structural Pre-Check Rules (B01)
@dataclass
class RuleDeclaration:
    applicable_modules: List[str]
    version: str
    dimension: List[int]
    read_permission: bool
    coverage_scope: str
    unsupported_reason: Optional[str] = None


@dataclass
class RuleResult:
    status: str
    declaration: RuleDeclaration
    details: Dict[str, Any] = field(default_factory=dict)


class StructuralPreCheck:
    """Base class for structural validation rules."""
    def __init__(self, declaration: RuleDeclaration):
        self.declaration = declaration
        
    def check_completeness(self, model_data: Dict[str, Any]) -> RuleResult:
        # Unknown getters / unparseable expressions -> UNVERIFIED, NOT PASS
        return RuleResult(STATUS_UNVERIFIED, self.declaration, {"message": "Not implemented"})


# 3. Numerical Metrics (B02)
def finite_check(values: Sequence[float]) -> bool:
    try:
        return all(math.isfinite(v) for v in values)
    except (TypeError, ValueError):
        return False


def range_check(values: Sequence[float], min_val: float, max_val: float) -> bool:
    try:
        return all(min_val <= v <= max_val for v in values)
    except (TypeError, ValueError):
        return False


def weighted_statistics(values: Sequence[float], weights: Sequence[float]) -> Dict[str, float]:
    if not values or not weights or len(values) != len(weights):
        raise ValueError("Invalid values or weights")
    sum_w = sum(weights)
    if sum_w <= 0:
        raise ValueError("Non-positive weights sum")
    
    mean = sum(v * w for v, w in zip(values, weights)) / sum_w
    variance = sum(w * (v - mean)**2 for v, w in zip(values, weights)) / sum_w
    std = math.sqrt(max(0.0, variance))
    rms = math.sqrt(sum(w * (v**2) for v, w in zip(values, weights)) / sum_w)
    
    return {"mean": mean, "std": std, "rms": rms}


def integral_check(values: Sequence[float], weights: Sequence[float]) -> float:
    if not values or not weights or len(values) != len(weights):
        raise ValueError("Invalid inputs")
    return sum(v * w for v, w in zip(values, weights))


def conservation_residual(inflow: float, outflow: float, normalization: float) -> float:
    if abs(normalization) < 1e-12:
        raise ValueError("Small denominator")
    return abs(inflow - outflow) / abs(normalization)


def benchmark_error(observed: float, expected: float, is_relative: bool = False) -> float:
    if is_relative:
        if abs(expected) < 1e-12:
            raise ValueError("Small denominator for relative error")
        return abs(observed - expected) / abs(expected)
    return abs(observed - expected)


# 4. Frozen Oracle / Benchmark Checker (B03, B04, B08)
@dataclass(frozen=True)
class FrozenExpectation:
    name: str
    expected_value: float
    tolerance: float
    is_relative: bool = False


class FrozenOracle:
    def __init__(self) -> None:
        self.expectations: Dict[str, FrozenExpectation] = {}
        self.frozen: bool = False
        
    def set_expectation(self, exp: FrozenExpectation) -> None:
        if self.frozen:
            raise RuntimeError("Cannot modify oracle after freezing")
        self.expectations[exp.name] = exp
        
    def freeze(self) -> None:
        self.frozen = True
        
    def check_observation(self, name: str, observed: float) -> Tuple[str, float]:
        if not self.frozen:
            raise RuntimeError("Oracle must be frozen before checking")
        if name not in self.expectations:
            return STATUS_UNVERIFIED, 0.0
            
        exp = self.expectations[name]
        try:
            err = benchmark_error(observed, exp.expected_value, exp.is_relative)
            if err <= exp.tolerance:
                return STATUS_PASS, err
            return STATUS_FAIL, err
        except ValueError:
            return STATUS_FAIL, float('inf')


def create_steady_state_oracle() -> FrozenOracle:
    """Creates the copper block steady-state oracle."""
    oracle = FrozenOracle()
    oracle.set_expectation(FrozenExpectation("T_0.0125", 312.5, 0.1, False))
    oracle.set_expectation(FrozenExpectation("T_0.025", 325.0, 0.1, False))
    oracle.set_expectation(FrozenExpectation("T_0.0375", 337.5, 0.1, False))
    oracle.set_expectation(FrozenExpectation("HeatFlow", 80.0, 0.01, True))
    oracle.freeze()
    return oracle


def transient_analytical_solution(x: float, t: float) -> float:
    L = 1.0
    alpha = 1.0
    return 300.0 + 10.0 * math.sin(math.pi * x / L) * math.exp(- (math.pi**2) * alpha * t / (L**2))


def create_transient_oracle() -> FrozenOracle:
    """Creates the transient sine decay oracle."""
    oracle = FrozenOracle()
    xs = [0.25, 0.5, 0.75]
    ts = [0.01, 0.03, 0.1]
    for x in xs:
        for t in ts:
            name = f"T_{x}_{t}"
            expected = transient_analytical_solution(x, t)
            oracle.set_expectation(FrozenExpectation(name, expected, 0.1, False))
    oracle.freeze()
    return oracle


# 5. Convergence Study (B05)
@dataclass
class ConvergenceStep:
    level: int
    mesh_size_metric: float
    tolerance: float
    time_step: float
    error: float
    resources: Dict[str, Any]


class ConvergenceStudy:
    def __init__(self) -> None:
        self.steps: List[ConvergenceStep] = []
        
    def add_step(self, step: ConvergenceStep) -> None:
        self.steps.append(step)
        
    def analyze_trend(self) -> Dict[str, Any]:
        if len(self.steps) < 3:
            return {"status": STATUS_UNVERIFIED, "message": "At least 3 steps required"}
        
        sorted_steps = sorted(self.steps, key=lambda s: s.mesh_size_metric, reverse=True)
        errors = [s.error for s in sorted_steps]
        
        improvements = [errors[i] < errors[i-1] for i in range(1, len(errors))]
        monotonic = all(improvements)
        
        return {
            "status": STATUS_PASS if monotonic else STATUS_FAIL,
            "trend": "monotonic" if monotonic else "fluctuating",
            "errors": errors,
            "message": "Convergence trend analyzed"
        }


# 6. Validation Report (B09)
@dataclass
class ValidationReport:
    source_identity: str
    runtime_version: str
    model_ref: Any
    dataset_ref: str
    rule_version: str
    input_assumptions: Dict[str, Any]
    frozen_expectations: Dict[str, Any]
    raw_observations: Dict[str, Any]
    error_tolerance_data: Dict[str, Any]
    coverage_exclusions: Dict[str, Any]
    warnings: List[str]
    evidence_hash: str
    
    def to_json(self) -> str:
        return json.dumps(self.__dict__, default=str)
        
    def to_markdown(self) -> str:
        md = f"# Validation Report: {self.source_identity}\n\n"
        md += f"**Runtime Version:** {self.runtime_version}\n"
        md += f"**Dataset:** {self.dataset_ref}\n\n"
        md += "## Frozen Expectations\n```json\n"
        md += json.dumps(self.frozen_expectations, indent=2)
        md += "\n```\n\n"
        md += "## Observations\n```json\n"
        md += json.dumps(self.raw_observations, indent=2)
        md += "\n```\n\n"
        md += "## Results\n```json\n"
        md += json.dumps(self.error_tolerance_data, indent=2)
        md += "\n```\n\n"
        md += f"**Evidence Hash:** `{self.evidence_hash}`\n"
        return md
