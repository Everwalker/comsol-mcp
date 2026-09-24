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

import hashlib
import json
import math
from pathlib import Path
import re
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ._execution_contract import ExecutionContractError
from ._g2_engine import _call


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
    unit: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Expectation name must be a non-empty string")
        if not math.isfinite(self.expected_value):
            raise ValueError(f"Expectation {self.name} expected_value must be finite, got {self.expected_value}")
        if not math.isfinite(self.tolerance) or self.tolerance <= 0:
            raise ValueError(f"Expectation {self.name} tolerance must be positive and finite, got {self.tolerance}")


class FrozenOracle:
    def __init__(self, name: str = "", required_observations: Optional[Sequence[str]] = None) -> None:
        self.name = name
        self._expectations: dict[str, FrozenExpectation] = {}
        self.frozen: bool = False
        self._required_observations: set[str] = set(required_observations or [])
        self._digest: Optional[str] = None

    @property
    def expectations(self) -> Mapping[str, FrozenExpectation]:
        """Read-only view of expectations preventing mutation via public attribute."""
        return types.MappingProxyType(self._expectations)

    @property
    def required_observations(self) -> frozenset[str]:
        return frozenset(self._required_observations)

    def set_expectation(self, exp: FrozenExpectation) -> None:
        if self.frozen:
            raise RuntimeError("Cannot modify oracle after freezing")
        self._expectations[exp.name] = exp
        self._required_observations.add(exp.name)

    def freeze(self) -> None:
        self.frozen = True
        payload = []
        for k in sorted(self._expectations.keys()):
            e = self._expectations[k]
            payload.append(f"{e.name}:{e.expected_value}:{e.tolerance}:{e.is_relative}:{e.unit}")
        self._digest = hashlib.sha256(";".join(payload).encode("utf-8")).hexdigest()

    @property
    def digest(self) -> str:
        if not self.frozen or self._digest is None:
            raise RuntimeError("Oracle must be frozen to obtain digest")
        return self._digest

    def check_observation(self, name: str, observed: Any) -> Tuple[str, float]:
        if not self.frozen:
            raise RuntimeError("Oracle must be frozen before checking")
        if name not in self._expectations:
            return STATUS_UNVERIFIED, 0.0

        exp = self._expectations[name]
        if observed is None or isinstance(observed, bool) or not isinstance(observed, (int, float)):
            return STATUS_FAIL, float("inf")
        obs_float = float(observed)
        if not math.isfinite(obs_float):
            return STATUS_FAIL, float("inf")
        try:
            err = benchmark_error(obs_float, exp.expected_value, exp.is_relative)
            if err <= exp.tolerance:
                return STATUS_PASS, err
            return STATUS_FAIL, err
        except (ValueError, TypeError):
            return STATUS_FAIL, float("inf")


def create_steady_state_oracle() -> FrozenOracle:
    """Creates the copper block steady-state oracle with 4 required observations."""
    oracle = FrozenOracle(
        name="steady_state_copper_block",
        required_observations=["T_0.0125", "T_0.025", "T_0.0375", "HeatFlow"],
    )
    oracle.set_expectation(FrozenExpectation("T_0.0125", 312.5, 0.1, False, unit="K", description="Temperature at x=0.0125m"))
    oracle.set_expectation(FrozenExpectation("T_0.025", 325.0, 0.1, False, unit="K", description="Temperature at x=0.025m"))
    oracle.set_expectation(FrozenExpectation("T_0.0375", 337.5, 0.1, False, unit="K", description="Temperature at x=0.0375m"))
    oracle.set_expectation(FrozenExpectation("HeatFlow", 80.0, 0.01, True, unit="W", description="Boundary heat flow rate"))
    oracle.freeze()
    return oracle


def transient_analytical_solution(x: float, t: float) -> float:
    L = 1.0
    alpha = 1.0
    return 300.0 + 10.0 * math.sin(math.pi * x / L) * math.exp(- (math.pi**2) * alpha * t / (L**2))


def create_transient_oracle() -> FrozenOracle:
    """Creates the transient sine decay oracle with 9 required observations."""
    xs = [0.25, 0.5, 0.75]
    ts = [0.01, 0.03, 0.1]
    reqs = [f"T_{x}_{t}" for x in xs for t in ts]
    oracle = FrozenOracle(name="transient_diffusion", required_observations=reqs)
    for x in xs:
        for t in ts:
            name = f"T_{x}_{t}"
            expected = transient_analytical_solution(x, t)
            oracle.set_expectation(FrozenExpectation(name, expected, 0.1, False, unit="K", description=f"Temperature at x={x}m, t={t}s"))
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
    def __init__(self, criteria: Optional[Dict[str, Any]] = None) -> None:
        self.steps: List[ConvergenceStep] = []
        self.criteria: Dict[str, Any] = criteria or {}

    def add_step(self, step: ConvergenceStep) -> None:
        self.steps.append(step)

    def analyze_trend(self) -> Dict[str, Any]:
        if len(self.steps) < 3:
            return {"status": STATUS_UNVERIFIED, "message": "At least 3 steps required"}

        # Check if mesh metrics actually vary
        mesh_sizes = [s.mesh_size_metric for s in self.steps]
        if len(set(mesh_sizes)) < len(self.steps):
            time_steps = [s.time_step for s in self.steps]
            tolerances = [s.tolerance for s in self.steps]
            if len(set(time_steps)) < len(self.steps) and len(set(tolerances)) < len(self.steps):
                return {
                    "status": STATUS_FAIL,
                    "trend": "static_parameter",
                    "errors": [s.error for s in self.steps],
                    "message": "Convergence parameters (mesh size, time step, tolerance) must strictly vary across levels",
                }

        # Sort by level
        sorted_steps = sorted(self.steps, key=lambda s: s.level)
        errors = [s.error for s in sorted_steps]

        if any(not math.isfinite(e) for e in errors):
            return {
                "status": STATUS_FAIL,
                "trend": "non_finite",
                "errors": errors,
                "message": "Non-finite errors encountered in convergence study",
            }

        improvements = [errors[i] <= errors[i - 1] for i in range(1, len(errors))]
        monotonic = all(improvements)

        max_allowed = self.criteria.get("absolute_error_max")
        if max_allowed is None:
            max_allowed = self.criteria.get("target_error", self.criteria.get("threshold"))
        finest_error = errors[-1]
        target_met = True
        if max_allowed is not None:
            max_allowed_val = float(max_allowed)
            if max_allowed_val < 0:
                return {
                    "status": STATUS_FAIL,
                    "trend": "invalid_criteria",
                    "errors": errors,
                    "message": f"Negative error threshold {max_allowed} is invalid",
                }
            if finest_error > max_allowed_val:
                target_met = False

        if not target_met:
            return {
                "status": STATUS_FAIL,
                "trend": "monotonic" if monotonic else "fluctuating",
                "errors": errors,
                "finest_error": finest_error,
                "target_error": max_allowed,
                "message": f"Finest level error {finest_error} exceeds target {max_allowed}",
            }

        status = STATUS_PASS if (monotonic and target_met) else STATUS_FAIL
        return {
            "status": status,
            "trend": "monotonic" if monotonic else "fluctuating",
            "errors": errors,
            "finest_error": finest_error,
            "message": "Convergence trend analyzed and target verified" if status == STATUS_PASS else "Convergence criteria not satisfied",
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


# ---------------------------------------------------------------------------
# G3 operations dispatch implementations for validate.*
# ---------------------------------------------------------------------------


def check_expression_syntax(expr_str: str) -> tuple[bool, str]:
    if not isinstance(expr_str, str) or not expr_str.strip():
        return False, "Empty expression"
    s = expr_str.strip()
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return False, f"Mismatched bracket '{ch}'"
            stack.pop()
    if stack:
        return False, f"Unclosed bracket '{stack[-1]}'"
    if re.search(r'[+\-*/^]\s*[)\]}]', s):
        return False, "Dangling operator before closing bracket"
    if re.search(r'[+\-*/^]\s*$', s):
        return False, "Dangling operator at end of expression"
    if re.search(r'[+\-*/^]\s*[*/^]', s) or re.search(r'[+\-*/^]\s*[+\-]\s*[+\-*/^]', s) or re.search(r'[+\-]\s*[+\-]', s):
        return False, "Consecutive operators"
    return True, "Valid syntax"


def validate_structure(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.structure: inspect model structure for components, geometries, selections, physics, mesh, study."""
    args = dict(arguments)
    if "arguments" in args and isinstance(args["arguments"], Mapping):
        inner = dict(args.pop("arguments"))
        conflicts = [k for k in inner if k in args and args[k] is not None and args[k] != inner[k]]
        if conflicts:
            raise ValueError(f"Conflicting parameter values in wrapped arguments: {conflicts}")
        for k, v in inner.items():
            args.setdefault(k, v)
    arguments = args

    findings: list[dict[str, Any]] = []
    rules_evaluated: list[str] = []
    unsupported_reasons: list[str] = []

    components: list[str] = []
    geometries: list[str] = []
    physics_list: list[str] = []
    meshes: list[str] = []
    studies: list[str] = []
    selections: list[str] = []
    materials: list[str] = []

    model = None
    try:
        client = getattr(worker, "client", lambda: worker)()
        if client is not None:
            model = _call(client, "model", model_tag)
    except Exception as exc:
        unsupported_reasons.append(f"Model handle unreachable through engine: {exc}")

    if model is not None:
        try:
            comp_node = _call(model, "component")
            comp_tags = _call(comp_node, "tags") if hasattr(comp_node, "tags") else []
            components = list(comp_tags) if comp_tags else []
            rules_evaluated.append("component.existence")
            if not components:
                findings.append({"rule": "component.existence", "status": STATUS_FAIL, "message": "No components found in model"})
            else:
                findings.append({"rule": "component.existence", "status": STATUS_PASS, "components": components})
        except Exception as exc:
            rules_evaluated.append("component.existence")
            findings.append({"rule": "component.existence", "status": STATUS_UNVERIFIED, "message": f"Could not inspect components: {exc}"})

        try:
            geom_node = _call(model, "geom")
            geom_tags = _call(geom_node, "tags") if hasattr(geom_node, "tags") else []
            geometries = list(geom_tags) if geom_tags else []
            rules_evaluated.append("geometry.existence")
            if not geometries and components:
                findings.append({"rule": "geometry.existence", "status": STATUS_FAIL, "message": "No geometry sequence found"})
            else:
                findings.append({"rule": "geometry.existence", "status": STATUS_PASS, "geometries": geometries})
        except Exception as exc:
            rules_evaluated.append("geometry.existence")
            findings.append({"rule": "geometry.existence", "status": STATUS_UNVERIFIED, "message": f"Could not inspect geometries: {exc}"})

        try:
            phys_node = _call(model, "physics")
            phys_tags = _call(phys_node, "tags") if hasattr(phys_node, "tags") else []
            physics_list = list(phys_tags) if phys_tags else []
            rules_evaluated.append("physics.activation")
            if not physics_list:
                findings.append({"rule": "physics.activation", "status": STATUS_FAIL, "message": "No physics interfaces active"})
            else:
                findings.append({"rule": "physics.activation", "status": STATUS_PASS, "physics": physics_list})
        except Exception as exc:
            rules_evaluated.append("physics.activation")
            findings.append({"rule": "physics.activation", "status": STATUS_UNVERIFIED, "message": f"Could not inspect physics: {exc}"})

        try:
            mesh_node = _call(model, "mesh")
            mesh_tags = _call(mesh_node, "tags") if hasattr(mesh_node, "tags") else []
            meshes = list(mesh_tags) if mesh_tags else []
            rules_evaluated.append("mesh.existence")
            if not meshes:
                findings.append({"rule": "mesh.existence", "status": STATUS_FAIL, "message": "No mesh sequences defined"})
            else:
                findings.append({"rule": "mesh.existence", "status": STATUS_PASS, "meshes": meshes})
        except Exception as exc:
            rules_evaluated.append("mesh.existence")
            findings.append({"rule": "mesh.existence", "status": STATUS_UNVERIFIED, "message": f"Could not inspect mesh: {exc}"})

        try:
            study_node = _call(model, "study")
            study_tags = _call(study_node, "tags") if hasattr(study_node, "tags") else []
            studies = list(study_tags) if study_tags else []
            rules_evaluated.append("study.connection")
            if not studies:
                findings.append({"rule": "study.connection", "status": STATUS_FAIL, "message": "No studies defined"})
            else:
                findings.append({"rule": "study.connection", "status": STATUS_PASS, "studies": studies})
        except Exception as exc:
            rules_evaluated.append("study.connection")
            findings.append({"rule": "study.connection", "status": STATUS_UNVERIFIED, "message": f"Could not inspect study: {exc}"})

        try:
            sel_node = _call(model, "selection")
            sel_tags = _call(sel_node, "tags") if hasattr(sel_node, "tags") else []
            selections = list(sel_tags) if sel_tags else []
            rules_evaluated.append("selection.existence")
            findings.append({"rule": "selection.existence", "status": STATUS_PASS, "selections": selections})
        except Exception as exc:
            rules_evaluated.append("selection.existence")
            findings.append({"rule": "selection.existence", "status": STATUS_UNVERIFIED, "message": f"Could not inspect selections: {exc}"})

        try:
            mat_node = _call(model, "material")
            mat_tags = _call(mat_node, "tags") if hasattr(mat_node, "tags") else []
            materials = list(mat_tags) if mat_tags else []
            rules_evaluated.append("material.existence")
            if not materials:
                findings.append({"rule": "material.existence", "status": STATUS_FAIL, "message": "No materials defined in model"})
            else:
                findings.append({"rule": "material.existence", "status": STATUS_PASS, "materials": materials})
        except Exception as exc:
            rules_evaluated.append("material.existence")
            findings.append({"rule": "material.existence", "status": STATUS_UNVERIFIED, "message": f"Could not inspect materials: {exc}"})
    else:
        mock_data = arguments.get("model_data") or arguments.get("scope") or {}
        if isinstance(mock_data, dict) and mock_data:
            rules_evaluated.append("model_data.provided")
            components = mock_data.get("components", [])
            geometries = mock_data.get("geometries", [])
            physics_list = mock_data.get("physics", [])
            meshes = mock_data.get("meshes", [])
            studies = mock_data.get("studies", [])
            selections = mock_data.get("selections", [])
            materials = mock_data.get("materials", [])
            if not components or not studies:
                findings.append({"rule": "model_data.provided", "status": STATUS_FAIL, "message": "Missing required components or studies"})
            else:
                findings.append({"rule": "model_data.provided", "status": STATUS_PASS})
        else:
            rules_evaluated.append("unsupported_inspection")
            unsupported_reasons.append("Model handle is unavailable for engine read; rule results marked UNVERIFIED")

    has_fail = any(f.get("status") == STATUS_FAIL for f in findings)
    has_unverified = any(f.get("status") == STATUS_UNVERIFIED for f in findings)

    if has_fail:
        overall_status = STATUS_FAIL
    elif unsupported_reasons or has_unverified or not rules_evaluated:
        overall_status = STATUS_UNVERIFIED
    else:
        overall_status = STATUS_PASS

    return {
        "status": overall_status,
        "execution_status": STATUS_PASS,
        "numerical_verification_status": STATUS_NOT_APPLICABLE,
        "physical_validation_status": STATUS_UNVERIFIED,
        "rules_evaluated": rules_evaluated,
        "findings": findings,
        "inventory": {
            "components": components,
            "geometries": geometries,
            "physics": physics_list,
            "meshes": meshes,
            "studies": studies,
            "selections": selections,
            "materials": materials,
        },
        "unsupported_reasons": unsupported_reasons,
    }


def validate_preflight(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.preflight: pre-solve check for structural completeness, materials, mesh, study."""
    args = dict(arguments)
    if "arguments" in args and isinstance(args["arguments"], Mapping):
        inner = dict(args.pop("arguments"))
        conflicts = [k for k in inner if k in args and args[k] is not None and args[k] != inner[k]]
        if conflicts:
            raise ValueError(f"Conflicting parameter values in wrapped arguments: {conflicts}")
        for k, v in inner.items():
            args.setdefault(k, v)
    arguments = args

    struct_res = validate_structure(worker, model_tag, arguments)
    findings = list(struct_res.get("findings", []))
    inventory = struct_res.get("inventory", {})
    unsupported = list(struct_res.get("unsupported_reasons", []))

    ready_to_solve = False
    if struct_res["status"] == STATUS_PASS:
        if inventory.get("components") and inventory.get("studies") and (inventory.get("physics") or inventory.get("meshes")):
            mat_failed = any(f.get("rule") == "material.existence" and f.get("status") == STATUS_FAIL for f in findings)
            if not mat_failed:
                ready_to_solve = True
                findings.append({"check": "preflight.ready", "status": STATUS_PASS, "message": "Model ready for solve"})
            else:
                findings.append({"check": "preflight.ready", "status": STATUS_FAIL, "message": "Missing required materials"})
        else:
            findings.append({"check": "preflight.ready", "status": STATUS_FAIL, "message": "Missing required physics, components, or studies"})
    elif struct_res["status"] == STATUS_UNVERIFIED or unsupported:
        findings.append({"check": "preflight.ready", "status": STATUS_UNVERIFIED, "message": "Cannot confirm readiness without verified model structure"})
    else:
        findings.append({"check": "preflight.ready", "status": STATUS_FAIL, "message": "Structural defects prevent solving"})

    overall_status = STATUS_PASS if ready_to_solve else (STATUS_FAIL if any(f.get("status") == STATUS_FAIL for f in findings) else STATUS_UNVERIFIED)

    return {
        "status": overall_status,
        "execution_status": STATUS_PASS,
        "numerical_verification_status": STATUS_NOT_APPLICABLE,
        "physical_validation_status": STATUS_UNVERIFIED,
        "ready_to_solve": ready_to_solve,
        "checks_evaluated": list(struct_res.get("rules_evaluated", [])),
        "findings": findings,
        "inventory": inventory,
        "unsupported_reasons": unsupported,
    }


def validate_expressions(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.expressions: syntax, finite check, and known unit consistency."""
    args = dict(arguments)
    if "arguments" in args and isinstance(args["arguments"], Mapping):
        inner = dict(args.pop("arguments"))
        conflicts = [k for k in inner if k in args and args[k] is not None and args[k] != inner[k]]
        if conflicts:
            raise ValueError(f"Conflicting parameter values in wrapped arguments: {conflicts}")
        for k, v in inner.items():
            args.setdefault(k, v)
    arguments = args

    raw_expressions = arguments.get("expressions", [])
    if not raw_expressions:
        return {
            "status": STATUS_UNVERIFIED,
            "execution_status": STATUS_PASS,
            "numerical_verification_status": STATUS_UNVERIFIED,
            "physical_validation_status": STATUS_UNVERIFIED,
            "expressions_checked": 0,
            "findings": [],
            "message": "No expressions provided for validation",
        }

    expressions = []
    for item in raw_expressions:
        if isinstance(item, str):
            expressions.append({"name": item, "expr": item})
        elif isinstance(item, dict):
            expressions.append(item)
        else:
            expressions.append({"name": str(item), "expr": "", "invalid_type": True})

    findings: list[dict[str, Any]] = []

    model = None
    try:
        client = getattr(worker, "client", lambda: worker)()
        if client is not None:
            model = _call(client, "model", model_tag)
    except Exception:
        pass

    for item in expressions:
        if item.get("invalid_type"):
            findings.append({"name": item.get("name", "unknown"), "status": STATUS_FAIL, "reason": "Invalid expression specification type"})
            continue
        expr = item.get("expr") or item.get("expression") or ""
        name = item.get("name") or "unnamed"
        val = item.get("value")

        if not expr and val is None:
            findings.append({"name": name, "status": STATUS_FAIL, "reason": "Empty expression and value"})
            continue

        valid_syntax, syntax_msg = check_expression_syntax(expr) if expr else (True, "No expr string")
        if not valid_syntax:
            findings.append({"name": name, "expr": expr, "status": STATUS_FAIL, "reason": syntax_msg})
            continue

        if val is not None and isinstance(val, (int, float)):
            if not math.isfinite(val):
                findings.append({"name": name, "expr": expr, "status": STATUS_FAIL, "reason": "Non-finite value (NaN/Inf)"})
                continue

        if model is not None and expr:
            try:
                param_node = _call(model, "param")
                eval_val = _call(param_node, "evaluate", expr)
                if eval_val is not None and isinstance(eval_val, (int, float)) and not math.isfinite(eval_val):
                    findings.append({"name": name, "expr": expr, "status": STATUS_FAIL, "reason": "Engine evaluated to NaN/Inf"})
                    continue
            except Exception as exc:
                findings.append({"name": name, "expr": expr, "status": STATUS_FAIL, "reason": f"Engine evaluate failed: {exc}"})
                continue

        findings.append({"name": name, "expr": expr, "status": STATUS_PASS})

    has_fail = any(f.get("status") == STATUS_FAIL for f in findings)
    has_unverified = any(f.get("status") == STATUS_UNVERIFIED for f in findings)
    if has_fail:
        status = STATUS_FAIL
    elif has_unverified or not findings:
        status = STATUS_UNVERIFIED
    else:
        status = STATUS_PASS

    return {
        "status": status,
        "execution_status": STATUS_PASS,
        "numerical_verification_status": status,
        "physical_validation_status": STATUS_UNVERIFIED,
        "expressions_checked": len(expressions),
        "findings": findings,
    }


def validate_boundary_conditions(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.boundary_conditions: diagnose missing, conflicting, or unassigned boundary conditions."""
    args = dict(arguments)
    if "arguments" in args and isinstance(args["arguments"], Mapping):
        inner = dict(args.pop("arguments"))
        conflicts = [k for k in inner if k in args and args[k] is not None and args[k] != inner[k]]
        if conflicts:
            raise ValueError(f"Conflicting parameter values in wrapped arguments: {conflicts}")
        for k, v in inner.items():
            args.setdefault(k, v)
    arguments = args

    rules = arguments.get("rules", [])
    if not rules:
        return {
            "status": STATUS_UNVERIFIED,
            "execution_status": STATUS_PASS,
            "numerical_verification_status": STATUS_NOT_APPLICABLE,
            "physical_validation_status": STATUS_UNVERIFIED,
            "rules_evaluated": [],
            "findings": [],
            "message": "No boundary condition rules requested",
        }

    findings: list[dict[str, Any]] = []
    boundary_data = arguments.get("boundary_data") or arguments.get("model_data")

    # Priority 1: inspect live model if worker is provided
    model = None
    try:
        client = getattr(worker, "client", lambda: worker)()
        if client is not None:
            model = _call(client, "model", model_tag)
    except Exception:
        pass

    if model is not None:
        try:
            phys_node = _call(model, "physics")
            phys_tags = list(_call(phys_node, "tags") or []) if hasattr(phys_node, "tags") else []
            features_by_phys: dict[str, list[dict[str, Any]]] = {}
            for ptag in phys_tags:
                p_node = _call(model, "physics", ptag)
                ftags: list[str] = []
                try:
                    feat_container = _call(p_node, "feature")
                    if feat_container is not None:
                        ftags = list(_call(feat_container, "tags") or [])
                except Exception:
                    pass
                if not ftags:
                    try:
                        ftags = list(_call(p_node, "tags") or [])
                    except Exception:
                        pass
                feat_list = []
                for ftag in ftags:
                    try:
                        f = _call(p_node, "feature", ftag)
                        ftype = _call(f, "getType") if hasattr(f, "getType") else ftag
                        sel = []
                        read_error = None
                        try:
                            sel_node = _call(f, "selection")
                            # Pure read-only inspection; NEVER call selection.all() or any setter!
                            read_fns = [
                                lambda: _call(sel_node, "entities", 2),
                                lambda: _call(sel_node, "entities", 1),
                                lambda: _call(sel_node, "entities", 0),
                                lambda: _call(sel_node, "entities"),
                                lambda: _call(sel_node, "getIntArray", "entities"),
                                lambda: _call(sel_node, "getIntArray"),
                                lambda: _call(sel_node, "get"),
                            ]
                            for extract_fn in read_fns:
                                try:
                                    res = extract_fn()
                                    if res is not None:
                                        sel = list(res)
                                        if sel:
                                            break
                                except Exception as e:
                                    read_error = e
                                    continue
                        except Exception as e:
                            read_error = e
                        feat_list.append({"tag": ftag, "type": ftype, "entities": sel, "read_error": read_error})
                    except Exception:
                        pass
                features_by_phys[ptag] = feat_list

            for r in rules:
                r_str = str(r)
                if r_str in ("conflicting_temperature_boundaries", "bc.temperature_inflow", "bc.thermal_insulation", "bc.temperature"):
                    all_ht_feats = [f for feats in features_by_phys.values() for f in feats if "temp" in f["tag"].lower() or "temp" in str(f["type"]).lower()]
                    unreadable = [f["tag"] for f in all_ht_feats if f.get("read_error") is not None and not f["entities"]]
                    if unreadable:
                        findings.append({"rule": r_str, "status": STATUS_UNVERIFIED, "message": f"Selection getter unavailable for features: {unreadable}"})
                        continue
                    entity_map = {}
                    for f in all_ht_feats:
                        for ent in f["entities"]:
                            entity_map.setdefault(ent, []).append(f["tag"])
                    conflicts = {ent: tags for ent, tags in entity_map.items() if len(tags) > 1}
                    if conflicts:
                        findings.append({"rule": r_str, "status": STATUS_FAIL, "message": f"Conflicting boundary conditions on entities: {conflicts}"})
                    else:
                        findings.append({"rule": r_str, "status": STATUS_PASS, "features_checked": [f["tag"] for f in all_ht_feats]})
                else:
                    findings.append({"rule": r_str, "status": STATUS_UNVERIFIED, "message": f"Rule '{r_str}' not implemented in boundary inspector"})
        except Exception as exc:
            for r in rules:
                findings.append({"rule": str(r), "status": STATUS_UNVERIFIED, "message": f"Engine inspection error: {exc}"})
    elif boundary_data and isinstance(boundary_data, dict):
        bc_list = boundary_data.get("boundaries", [])
        entity_map: dict[Any, list[str]] = {}
        for b in bc_list:
            tag = b.get("tag", "bc")
            for ent in b.get("entities", []):
                entity_map.setdefault(ent, []).append(tag)
        conflicts = {ent: tags for ent, tags in entity_map.items() if len(tags) > 1}
        for r in rules:
            r_str = str(r)
            if r_str in ("conflicting_temperature_boundaries", "bc.temperature_inflow", "bc.thermal_insulation", "bc.temperature"):
                if conflicts and "conflict" in r_str.lower():
                    findings.append({"rule": r_str, "status": STATUS_FAIL, "conflicts": conflicts})
                elif conflicts:
                    findings.append({"rule": r_str, "status": STATUS_FAIL, "message": f"Conflicting boundary conditions: {conflicts}"})
                else:
                    findings.append({"rule": r_str, "status": STATUS_PASS, "boundaries": bc_list})
            else:
                findings.append({"rule": r_str, "status": STATUS_UNVERIFIED, "message": f"Rule '{r_str}' not implemented in offline boundary inspector"})
    else:
        for r in rules:
            findings.append({"rule": str(r), "status": STATUS_UNVERIFIED, "reason": "No engine or boundary data available for inspection"})

    has_fail = any(f.get("status") == STATUS_FAIL for f in findings)
    has_unverified = any(f.get("status") == STATUS_UNVERIFIED for f in findings)
    if has_fail:
        status = STATUS_FAIL
    elif has_unverified or not findings:
        status = STATUS_UNVERIFIED
    else:
        status = STATUS_PASS

    return {
        "status": status,
        "execution_status": STATUS_PASS,
        "numerical_verification_status": STATUS_NOT_APPLICABLE,
        "physical_validation_status": STATUS_UNVERIFIED,
        "rules_evaluated": rules,
        "findings": findings,
    }


def validate_solution(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.solution: verify solution existence, finite check, range, benchmark error."""
    args = dict(arguments)
    if "arguments" in args and isinstance(args["arguments"], Mapping):
        inner = dict(args.pop("arguments"))
        conflicts = [k for k in inner if k in args and args[k] is not None and args[k] != inner[k]]
        if conflicts:
            raise ValueError(f"Conflicting parameter values in wrapped arguments: {conflicts}")
        for k, v in inner.items():
            args.setdefault(k, v)
    arguments = args

    solution = dict(arguments.get("solution") or {})
    if not solution and ("dataset" in arguments or "tag" in arguments):
        solution = {"dataset": arguments.get("dataset") or arguments.get("tag")}

    criteria = dict(arguments.get("criteria") or {})
    if "oracle" in arguments and "oracle" not in criteria:
        criteria["oracle"] = arguments["oracle"]
    if "observations" in arguments and "observations" not in criteria:
        criteria["observations"] = arguments["observations"]
    if "values" in arguments and "values" not in criteria:
        criteria["values"] = arguments["values"]
    if "range" in arguments and "range" not in criteria:
        criteria["range"] = arguments["range"]

    if not solution and not criteria:
        return {
            "status": STATUS_FAIL,
            "execution_status": STATUS_PASS,
            "numerical_verification_status": STATUS_FAIL,
            "physical_validation_status": STATUS_UNVERIFIED,
            "solution": {},
            "checks": {},
            "metrics": {},
            "oracle_results": {},
            "message": "Empty solution specification and criteria",
        }

    dataset_name = solution.get("dataset") or solution.get("tag")
    checks: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    oracle_results: dict[str, Any] = {}
    passed = True
    observation_origin = "CALLER_SUPPLIED"

    model = None
    try:
        client = getattr(worker, "client", lambda: worker)()
        if client is not None:
            model = _call(client, "model", model_tag)
    except Exception:
        pass

    if model is not None:
        try:
            res_node = _call(model, "result")
            dsets = []
            try:
                dset_container = _call(res_node, "dataset")
                if dset_container is not None and hasattr(dset_container, "tags"):
                    dsets = list(_call(dset_container, "tags") or [])
            except Exception:
                pass
            if not dsets:
                try:
                    if hasattr(res_node, "tags"):
                        dsets = list(_call(res_node, "tags") or [])
                except Exception:
                    pass

            if dataset_name:
                if dataset_name not in dsets:
                    return {
                        "status": STATUS_FAIL,
                        "execution_status": STATUS_PASS,
                        "numerical_verification_status": STATUS_FAIL,
                        "physical_validation_status": STATUS_UNVERIFIED,
                        "observation_origin": "CALLER_SUPPLIED",
                        "solution": solution,
                        "checks": {"dataset_exists": False},
                        "metrics": {},
                        "oracle_results": {},
                        "message": f"Dataset '{dataset_name}' does not exist in model. Available: {dsets}",
                    }
                checks["dataset_exists"] = True
        except Exception:
            pass

    values = criteria.get("values", [])
    if values and isinstance(values, Sequence):
        is_finite = finite_check(values)
        checks["finite"] = is_finite
        if not is_finite:
            passed = False

        if "range" in criteria and isinstance(criteria["range"], (list, tuple)) and len(criteria["range"]) == 2:
            min_v, max_v = criteria["range"]
            in_range = range_check(values, float(min_v), float(max_v))
            checks["range"] = in_range
            if not in_range:
                passed = False

        if is_finite and values:
            try:
                metrics["statistics"] = weighted_statistics(values, [1.0] * len(values))
            except Exception:
                pass

    oracle_name = criteria.get("oracle")
    if oracle_name:
        registered_oracles = {
            "steady_state_copper_block": create_steady_state_oracle,
            "transient_diffusion": create_transient_oracle,
        }
        if oracle_name not in registered_oracles:
            passed = False
            checks["oracle_registered"] = False
            return {
                "status": STATUS_FAIL,
                "execution_status": STATUS_PASS,
                "numerical_verification_status": STATUS_FAIL,
                "physical_validation_status": STATUS_UNVERIFIED,
                "solution": solution,
                "checks": checks,
                "metrics": metrics,
                "oracle_results": {},
                "message": f"Unknown or unregistered oracle '{oracle_name}'",
            }

        oracle = registered_oracles[oracle_name]()
        obs = criteria.get("observations", {})
        required = oracle.required_observations
        provided_keys = set(obs.keys())
        missing_keys = required - provided_keys

        if missing_keys:
            passed = False
            checks["required_observations_complete"] = False
            return {
                "status": STATUS_FAIL,
                "execution_status": STATUS_PASS,
                "numerical_verification_status": STATUS_FAIL,
                "physical_validation_status": STATUS_UNVERIFIED,
                "solution": solution,
                "checks": {"required_observations_complete": False, "missing": sorted(missing_keys)},
                "metrics": metrics,
                "oracle_results": {k: {"status": STATUS_FAIL, "error": float("inf"), "reason": "Missing required observation"} for k in missing_keys},
                "message": f"Missing required observations for oracle '{oracle_name}': {sorted(missing_keys)}",
            }

        checks["required_observations_complete"] = True
        for k, v in obs.items():
            status, err = oracle.check_observation(k, v)
            oracle_results[k] = {"status": status, "error": err}
            if status != STATUS_PASS:
                passed = False

    if not values and not oracle_name:
        passed = False

    return {
        "status": STATUS_PASS if passed else STATUS_FAIL,
        "execution_status": STATUS_PASS,
        "numerical_verification_status": STATUS_PASS if passed else STATUS_FAIL,
        "physical_validation_status": STATUS_UNVERIFIED,
        "observation_origin": observation_origin,
        "solution": solution,
        "checks": checks,
        "metrics": metrics,
        "oracle_results": oracle_results,
    }


def validate_conservation(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.conservation: energy, mass, or flux conservation residual validation."""
    args = dict(arguments)
    if "arguments" in args and isinstance(args["arguments"], Mapping):
        inner = dict(args.pop("arguments"))
        conflicts = [k for k in inner if k in args and args[k] is not None and args[k] != inner[k]]
        if conflicts:
            raise ValueError(f"Conflicting parameter values in wrapped arguments: {conflicts}")
        for k, v in inner.items():
            args.setdefault(k, v)
    arguments = args

    definition = dict(arguments.get("definition") or {})
    if not definition:
        definition = {k: v for k, v in arguments.items() if k != "definition"}

    has_terms = any(k in definition for k in ("inflow", "outflow", "power", "source_term", "source_rate", "storage_rate"))
    if not definition or not has_terms:
        return {
            "status": STATUS_FAIL,
            "execution_status": STATUS_PASS,
            "numerical_verification_status": STATUS_FAIL,
            "physical_validation_status": STATUS_UNVERIFIED,
            "passed": False,
            "residual": float("inf"),
            "message": "Empty or missing conservation definition; actual inflow/outflow/power measurements required",
        }

    inflow = float(definition.get("inflow", 0.0))
    outflow = float(definition.get("outflow", 0.0))
    storage_rate = float(definition.get("storage_rate", 0.0))

    if "source_term" in definition or "source_rate" in definition:
        source_term = float(definition.get("source_term", definition.get("source_rate", 0.0)))
    elif "power" in definition:
        source_term = float(definition["power"])
    else:
        source_term = 0.0

    tolerance = float(definition.get("tolerance", 0.01))

    norm_candidate = definition.get("normalization")
    if norm_candidate is not None:
        normalization = float(norm_candidate)
    else:
        flux_sum = abs(inflow) + abs(outflow) + abs(source_term) + abs(storage_rate)
        normalization = flux_sum if flux_sum > 0 else 1.0

    if abs(normalization) < 1e-12:
        return {
            "status": STATUS_FAIL,
            "execution_status": STATUS_PASS,
            "numerical_verification_status": STATUS_FAIL,
            "physical_validation_status": STATUS_UNVERIFIED,
            "residual": float("inf"),
            "passed": False,
            "message": "Near-zero normalization denominator for conservation residual",
        }

    imbalance = abs((inflow + source_term) - (outflow + storage_rate))
    res = imbalance / abs(normalization)
    passed = (res <= tolerance)

    return {
        "status": STATUS_PASS if passed else STATUS_FAIL,
        "execution_status": STATUS_PASS,
        "numerical_verification_status": STATUS_PASS if passed else STATUS_FAIL,
        "physical_validation_status": STATUS_UNVERIFIED,
        "inflow": inflow,
        "outflow": outflow,
        "storage_rate": storage_rate,
        "source_term": source_term,
        "normalization": normalization,
        "residual": res,
        "tolerance": tolerance,
        "passed": passed,
    }


def validate_convergence(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.convergence: mesh, time-step, or domain sensitivity convergence study."""
    cases = arguments.get("cases") or []
    metrics = arguments.get("metrics") or []
    criteria = arguments.get("criteria") or {}

    study = ConvergenceStudy(criteria=criteria)
    for c in cases:
        if isinstance(c, dict):
            step = ConvergenceStep(
                level=int(c.get("level", 1)),
                mesh_size_metric=float(c.get("mesh_size_metric", c.get("mesh_size", 1.0))),
                tolerance=float(c.get("tolerance", 1e-3)),
                time_step=float(c.get("time_step", c.get("dt", 0.01))),
                error=float(c.get("error", 0.1)),
                resources=c.get("resources", {}),
            )
            study.add_step(step)

    analysis = study.analyze_trend()
    trend_status = analysis.get("status", STATUS_UNVERIFIED)

    return {
        "status": trend_status,
        "execution_status": STATUS_PASS,
        "numerical_verification_status": trend_status,
        "physical_validation_status": STATUS_UNVERIFIED,
        "cases_count": len(cases),
        "analysis": analysis,
        "metrics": metrics,
    }


def validate_report(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """validate.report: aggregate validation results and generate JSON and Markdown reports."""
    args = dict(arguments)
    if "arguments" in args and isinstance(args["arguments"], Mapping):
        inner = dict(args.pop("arguments"))
        conflicts = [k for k in inner if k in args and args[k] is not None and args[k] != inner[k]]
        if conflicts:
            raise ValueError(f"Conflicting parameter values in wrapped arguments: {conflicts}")
        for k, v in inner.items():
            args.setdefault(k, v)
    arguments = args

    destination = arguments.get("destination")
    overwrite = bool(arguments.get("overwrite", False))
    data = arguments.get("data") or {}
    if not isinstance(data, dict) and isinstance(data, Mapping):
        data = dict(data)
    elif not isinstance(data, dict):
        data = {}

    source_id = data.get("source_identity", "g3_8_pinned")
    runtime_ver = data.get("runtime_version", "UNKNOWN")
    dataset_ref = data.get("dataset_ref", "dset1")
    rule_ver = data.get("rule_version", "W20.2")

    evidence_content = json.dumps(data, sort_keys=True)
    evidence_hash = hashlib.sha256(evidence_content.encode("utf-8")).hexdigest()

    child_statuses = []
    if "status" in data:
        child_statuses.append(data["status"])
    if "numerical_verification_status" in data:
        child_statuses.append(data["numerical_verification_status"])

    err_tol = data.get("error_tolerance_data") or {}
    if isinstance(err_tol, dict):
        if "numerical_verification_status" in err_tol:
            child_statuses.append(err_tol["numerical_verification_status"])
        if "status" in err_tol:
            child_statuses.append(err_tol["status"])

    for k, v in data.items():
        if isinstance(v, dict):
            if "status" in v:
                child_statuses.append(v["status"])
            if "numerical_verification_status" in v:
                child_statuses.append(v["numerical_verification_status"])

    write_ok = True
    write_error = None
    dest_path = None
    sibling_path = None
    if destination:
        dest_path = Path(destination)
        if dest_path.is_dir():
            write_ok = False
            write_error = f"Destination path '{destination}' is an existing directory, not a file"
        else:
            if dest_path.suffix.lower() == ".json":
                sibling_path = dest_path.with_suffix(".md")
            elif dest_path.suffix.lower() == ".md":
                sibling_path = dest_path.with_suffix(".json")

            if not overwrite:
                if dest_path.exists():
                    write_ok = False
                    write_error = f"Destination file '{dest_path}' already exists and overwrite is False"
                elif sibling_path and sibling_path.exists():
                    write_ok = False
                    write_error = f"Sibling report file '{sibling_path}' already exists and overwrite is False"

    if not write_ok:
        overall_status = STATUS_FAIL
        exec_status = STATUS_FAIL
        num_status = STATUS_FAIL
    elif any(s == STATUS_FAIL for s in child_statuses):
        overall_status = STATUS_FAIL
        exec_status = STATUS_PASS
        num_status = STATUS_FAIL
    elif any(s == STATUS_ERROR for s in child_statuses):
        overall_status = STATUS_ERROR
        exec_status = STATUS_ERROR
        num_status = STATUS_ERROR
    elif any(s == STATUS_BLOCKED for s in child_statuses):
        overall_status = STATUS_BLOCKED
        exec_status = STATUS_BLOCKED
        num_status = STATUS_BLOCKED
    elif any(s == STATUS_UNSUPPORTED for s in child_statuses):
        overall_status = STATUS_UNSUPPORTED
        exec_status = STATUS_PASS
        num_status = STATUS_UNSUPPORTED
    elif any(s == STATUS_UNVERIFIED for s in child_statuses) or not child_statuses:
        overall_status = STATUS_UNVERIFIED
        exec_status = STATUS_PASS
        num_status = STATUS_UNVERIFIED
    else:
        overall_status = STATUS_PASS
        exec_status = STATUS_PASS
        num_status = STATUS_PASS

    report = ValidationReport(
        source_identity=source_id,
        runtime_version=runtime_ver,
        model_ref=model_tag,
        dataset_ref=dataset_ref,
        rule_version=rule_ver,
        input_assumptions=data.get("input_assumptions", {}),
        frozen_expectations=data.get("frozen_expectations", {}),
        raw_observations=data.get("raw_observations", {}),
        error_tolerance_data=err_tol,
        coverage_exclusions=data.get("coverage_exclusions", {}),
        warnings=data.get("warnings", []),
        evidence_hash=evidence_hash,
    )

    report_json = report.to_json()
    report_md = report.to_markdown()

    if destination and write_ok and dest_path:
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if dest_path.suffix.lower() == ".json":
                dest_path.write_text(report_json, encoding="utf-8")
                if sibling_path:
                    sibling_path.write_text(report_md, encoding="utf-8")
            elif dest_path.suffix.lower() == ".md":
                dest_path.write_text(report_md, encoding="utf-8")
                if sibling_path:
                    sibling_path.write_text(report_json, encoding="utf-8")
            else:
                dest_path.write_text(report_md, encoding="utf-8")
        except Exception as exc:
            overall_status = STATUS_FAIL
            exec_status = STATUS_FAIL
            write_error = str(exc)

    res = {
        "status": overall_status,
        "execution_status": exec_status,
        "numerical_verification_status": num_status,
        "physical_validation_status": STATUS_UNVERIFIED,
        "report_summary": {
            "source_identity": source_id,
            "runtime_version": runtime_ver,
            "rule_version": rule_ver,
            "evidence_hash": evidence_hash,
        },
        "report_markdown": report_md,
        "report_json": report_json,
    }
    if write_error:
        res["write_error"] = write_error
    return res


OPERATIONS: dict[str, Callable[[Any, str, dict], dict]] = {
    "validate.preflight": validate_preflight,
    "validate.structure": validate_structure,
    "validate.expressions": validate_expressions,
    "validate.boundary_conditions": validate_boundary_conditions,
    "validate.solution": validate_solution,
    "validate.conservation": validate_conservation,
    "validate.convergence": validate_convergence,
    "validate.report": validate_report,
}
