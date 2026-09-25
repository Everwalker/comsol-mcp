"""G3 Workstream 21: Parameter Cases, Sweeps, Budget/Cache, Bounded Optimization, and Stage State Transfer.

Per W21_PLAN.md:
1. Parameter case & sweep: parameter values/units, case ID, status, raw results,
   inner/outer/time correct correspondence; 2-parameter + transient real sweep;
   maintains parameter index table.
2. Budget & reuse: computation budget limits (cases, time), failed cases recorded,
   content-addressed result cache avoiding redundant dispatch, cache invalidated
   on model/config/version changes.
3. Bounded optimization: fixed parameter bounds, objective, constraints, evaluates
   candidates, returns verified candidate and raw results, accurate reporting on
   budget exhaustion.
4. Generic stage state transfer: multi-stage state mapping (stage 1 final -> stage 2 initial),
   tracks variables, units, time, selections, checkpoints without resetting history;
   validates T047 generic subitem while keeping domain curing/stress for W24.
5. Dual-version parity: supports Windows 6.3 & 6.4 (and Darwin/macOS cross-platform),
   integrates with W20 validation tools.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ._g2_contract import ExecutionContractError
from ._g3_w20_validation import (
    ObservationRef,
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_UNVERIFIED,
    STATUS_ERROR,
    STATUS_BLOCKED,
    STATUS_UNSUPPORTED,
    finite_check,
    range_check,
)


# ------------------------------------------------------------------------------
# 1. Parameter Case & Sweep Indexing
# ------------------------------------------------------------------------------

@dataclass
class ParameterCase:
    """A single discrete parameter evaluation case."""
    case_id: str
    parameters: dict[str, float]
    units: dict[str, str]
    outer_index: int
    inner_index: int
    status: str = "NOT_RUN"  # NOT_RUN, RUNNING, COMPLETED, FAILED, CACHED
    results: dict[str, Any] = field(default_factory=dict)
    time_series: dict[str, list[float]] = field(default_factory=dict)
    time_points: list[float] = field(default_factory=list)
    error_message: str | None = None
    execution_duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "parameters": copy.deepcopy(self.parameters),
            "units": copy.deepcopy(self.units),
            "outer_index": self.outer_index,
            "inner_index": self.inner_index,
            "status": self.status,
            "results": copy.deepcopy(self.results),
            "time_series": copy.deepcopy(self.time_series),
            "time_points": list(self.time_points),
            "error_message": self.error_message,
            "execution_duration_s": self.execution_duration_s,
        }


class ParameterIndexTable:
    """Maintains exact mapping between case IDs, parameter values, (outer, inner) indices, and time."""

    def __init__(self, sweep_id: str, parameter_names: list[str]) -> None:
        self.sweep_id = sweep_id
        self.parameter_names = list(parameter_names)
        self.cases: list[ParameterCase] = []
        self._by_id: dict[str, ParameterCase] = {}
        self._by_indices: dict[tuple[int, int], ParameterCase] = {}
        self._by_param_values: dict[tuple[float, ...], ParameterCase] = {}

    def add_case(self, case: ParameterCase) -> None:
        if case.case_id in self._by_id:
            raise ExecutionContractError("DUPLICATE_CASE_ID", f"Case {case.case_id} already exists")
        key_idx = (case.outer_index, case.inner_index)
        if key_idx in self._by_indices:
            raise ExecutionContractError("INDEX_COLLISION", f"Index pair {key_idx} already assigned")
        param_tuple = tuple(case.parameters[k] for k in self.parameter_names)
        self.cases.append(case)
        self._by_id[case.case_id] = case
        self._by_indices[key_idx] = case
        self._by_param_values[param_tuple] = case

    def get_by_id(self, case_id: str) -> ParameterCase | None:
        return self._by_id.get(case_id)

    def get_by_indices(self, outer: int, inner: int) -> ParameterCase | None:
        return self._by_indices.get((outer, inner))

    def get_by_params(self, params: Mapping[str, float]) -> ParameterCase | None:
        key = tuple(params[k] for k in self.parameter_names)
        return self._by_param_values.get(key)

    def query_slice(self, expression: str, outer: int | None = None, inner: int | None = None, time_val: float | None = None) -> dict[str, Any]:
        """Resolve a specific slice without confusing outer, inner, or time axes."""
        matched: list[dict[str, Any]] = []
        for case in self.cases:
            if outer is not None and case.outer_index != outer:
                continue
            if inner is not None and case.inner_index != inner:
                continue
            val = case.results.get(expression)
            if time_val is not None and expression in case.time_series:
                # Exact selection only: no nearest-time alias.
                ts = case.time_points
                if ts:
                    if time_val not in ts:
                        raise ExecutionContractError("TIME_NOT_STORED", "Exact stored time required; no implicit nearest selection")
                    idx = ts.index(time_val)
                    val = case.time_series[expression][idx]
            matched.append({
                "case_id": case.case_id,
                "outer": case.outer_index,
                "inner": case.inner_index,
                "parameters": case.parameters,
                "value": val,
                "actual_time": time_val,
            })
        return {
            "sweep_id": self.sweep_id,
            "expression": expression,
            "matches_count": len(matched),
            "matches": matched,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sweep_id": self.sweep_id,
            "parameter_names": self.parameter_names,
            "total_cases": len(self.cases),
            "completed_cases": sum(1 for c in self.cases if c.status in ("COMPLETED", "CACHED")),
            "failed_cases": sum(1 for c in self.cases if c.status == "FAILED"),
            "cases": [c.to_dict() for c in self.cases],
        }


# ------------------------------------------------------------------------------
# 2. Computation Budget & Content-Addressed Result Cache
# ------------------------------------------------------------------------------

@dataclass
class ComputationBudget:
    """Enforces computational budget limits and tracks consumption."""
    max_cases: int = 50
    max_wall_time_s: float = 300.0
    max_consecutive_failures: int = 5
    cases_evaluated: int = 0
    cases_failed: int = 0
    total_failures: int = 0
    start_time_s: float = field(default_factory=time.time)

    def can_evaluate(self) -> bool:
        if self.cases_evaluated >= self.max_cases:
            return False
        if (time.time() - self.start_time_s) >= self.max_wall_time_s:
            return False
        if self.cases_failed >= self.max_consecutive_failures:
            return False
        return True

    def record_case(self, success: bool) -> None:
        self.cases_evaluated += 1
        if success:
            self.cases_failed = 0
        else:
            self.cases_failed += 1
            self.total_failures += 1

    def status(self) -> dict[str, Any]:
        elapsed = time.time() - self.start_time_s
        exhausted = not self.can_evaluate()
        reason = None
        if self.cases_evaluated >= self.max_cases:
            reason = "MAX_CASES_EXCEEDED"
        elif elapsed >= self.max_wall_time_s:
            reason = "WALL_TIME_EXCEEDED"
        elif self.cases_failed >= self.max_consecutive_failures:
            reason = "CONSECUTIVE_FAILURES_EXCEEDED"
        return {
            "cases_evaluated": self.cases_evaluated,
            "max_cases": self.max_cases,
            "consecutive_failures": self.cases_failed,
            "total_failures": self.total_failures,
            "elapsed_s": round(elapsed, 4),
            "max_wall_time_s": self.max_wall_time_s,
            "exhausted": exhausted,
            "exhaustion_reason": reason,
        }


class ResultCache:
    """Content-addressed result cache avoiding redundant dispatch and invalid reuse."""

    def __init__(self, operation_store=None) -> None:
        self._operation_store = operation_store
        self._entries: dict[str, dict[str, Any]] = {}
        self._stats = {"hits": 0, "misses": 0, "stores": 0}

    @staticmethod
    def compute_key(
        model_digest: str,
        parameters: Mapping[str, float],
        study_config: Mapping[str, Any],
        runtime_version: str,
    ) -> str:
        canonical = {
            "model_digest": str(model_digest),
            "parameters": {k: float(v) for k, v in sorted(parameters.items())},
            "study_config": json.loads(json.dumps(study_config, sort_keys=True)),
            "runtime_version": str(runtime_version),
        }
        raw = json.dumps(canonical, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        entry = (self._operation_store.get_metadata('artifacts', 'w21cache:' + key)
                 if self._operation_store is not None else self._entries.get(key))
        if entry is not None:
            if self._operation_store is not None:
                expected = hashlib.sha256(json.dumps({k:v for k,v in entry.items() if k != 'sha256'}, sort_keys=True, allow_nan=False).encode()).hexdigest()
                if entry.get('sha256') != expected:
                    raise ExecutionContractError('INTEGRITY_COMPROMISED', 'Cached envelope changed')
            self._stats["hits"] += 1
            return copy.deepcopy(entry["data"])
        self._stats["misses"] += 1
        return None

    def store(self, key: str, data: dict[str, Any], metadata: Mapping[str, Any] | None = None) -> None:
        self._entries[key] = {
            "data": copy.deepcopy(data),
            "metadata": dict(metadata or {}),
            "timestamp": time.time(),
        }
        if self._operation_store is not None:
            self._entries[key]['sha256'] = hashlib.sha256(json.dumps(self._entries[key], sort_keys=True, allow_nan=False).encode()).hexdigest()
            self._operation_store.persist_artifact('w21cache:' + key, self._entries[key])
        self._stats["stores"] += 1

    def stats(self) -> dict[str, Any]:
        return dict(self._stats, total_entries=len(self._entries))


# ------------------------------------------------------------------------------
# 3. Bounded Optimization Flow
# ------------------------------------------------------------------------------

@dataclass
class BoundedCandidate:
    candidate_id: str
    parameters: dict[str, float]
    objective_value: float | None
    constraint_residuals: dict[str, float]
    is_feasible: bool
    raw_results: dict[str, Any]
    eval_time_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "parameters": copy.deepcopy(self.parameters),
            "objective_value": self.objective_value,
            "constraint_residuals": copy.deepcopy(self.constraint_residuals),
            "is_feasible": self.is_feasible,
            "raw_results": copy.deepcopy(self.raw_results),
            "eval_time_s": self.eval_time_s,
        }


class BoundedOptimizer:
    """Bounded coordinate / pattern search optimizer with strict budget and constraint checks."""

    def __init__(
        self,
        objective_name: str,
        minimize: bool = True,
        parameter_bounds: Mapping[str, tuple[float, float]] | None = None,
        constraints: Sequence[dict[str, Any]] | None = None,
        budget: ComputationBudget | None = None,
    ) -> None:
        self.objective_name = objective_name
        self.minimize = minimize
        self.parameter_bounds = dict(parameter_bounds or {})
        self.constraints = list(constraints or [])
        for name, bounds in self.parameter_bounds.items():
            if len(bounds) != 2 or not all(math.isfinite(v) for v in bounds) or bounds[0] > bounds[1]:
                raise ExecutionContractError("INVALID_BOUNDS", f"Invalid bounds for {name}")
        for constraint in self.constraints:
            if not constraint.get("expression") or not any(k in constraint for k in ("min_value", "max_value")):
                raise ExecutionContractError("INVALID_CONSTRAINT", "Constraint needs expression and numeric bound")
            for key in ("min_value", "max_value"):
                if key in constraint and not math.isfinite(constraint[key]):
                    raise ExecutionContractError("INVALID_CONSTRAINT", "Finite constraint bounds required")
        self.budget = budget or ComputationBudget(max_cases=30)
        self.history: list[BoundedCandidate] = []
        self.best_candidate: BoundedCandidate | None = None

    def check_constraints(self, raw_results: Mapping[str, Any]) -> tuple[bool, dict[str, float]]:
        residuals: dict[str, float] = {}
        all_passed = True
        for c in self.constraints:
            expr = c.get("expression")
            max_val = c.get("max_value")
            min_val = c.get("min_value")
            val = raw_results.get(expr)
            if val is None or not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                all_passed = False
                residuals[str(expr)] = None
                continue
            res = 0.0
            if max_val is not None and val > max_val:
                res = val - max_val
                all_passed = False
            elif min_val is not None and val < min_val:
                res = min_val - val
                all_passed = False
            residuals[str(expr)] = res
        return all_passed, residuals

    def evaluate_candidate(
        self,
        candidate_id: str,
        parameters: dict[str, float],
        eval_fn: Callable[[dict[str, float]], dict[str, Any]],
    ) -> BoundedCandidate:
        t0 = time.time()
        if not self.budget.can_evaluate():
            raise ExecutionContractError("BUDGET_EXHAUSTED", "No new candidate may be dispatched")
        if set(parameters) != set(self.parameter_bounds):
            raise ExecutionContractError("INVALID_PARAMETERS", "Candidate must specify every bounded parameter")
        for k, value in parameters.items():
            low, high = self.parameter_bounds[k]
            if not math.isfinite(value) or not low <= value <= high:
                raise ExecutionContractError("OUT_OF_BOUNDS", f"{k} is outside registered bounds")
        clamped_params = dict(parameters)
        try:
            raw_results = eval_fn(clamped_params)
        except Exception as exc:
            raw_results = {"status": "FAILED", "error": str(exc)}
        eval_time = time.time() - t0
        obj_val = raw_results.get(self.objective_name)
        valid = (isinstance(obj_val, (int, float)) and not isinstance(obj_val, bool)
                 and math.isfinite(obj_val)
                 and raw_results.get("status", "COMPLETED") in {"COMPLETED", "CACHED"}
                 and not raw_results.get("execution_state_unknown"))
        if not valid:
            obj_val = None
        feasible, residuals = self.check_constraints(raw_results)
        feasible = feasible and valid
        def json_safe(value):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            if isinstance(value, dict):
                return {k: json_safe(v) for k, v in value.items()}
            if isinstance(value, list):
                return [json_safe(v) for v in value]
            return value
        if not valid:
            raw_results = {**json_safe(raw_results), "candidate_error": "FAILED_UNKNOWN_OR_INVALID_OBJECTIVE"}
        cand = BoundedCandidate(
            candidate_id=candidate_id,
            parameters=clamped_params,
            objective_value=obj_val,
            constraint_residuals=residuals,
            is_feasible=feasible,
            raw_results=raw_results,
            eval_time_s=eval_time,
        )
        self.history.append(cand)
        self.budget.record_case(success=valid)

        if feasible:
            if self.best_candidate is None:
                self.best_candidate = cand
            else:
                is_better = (cand.objective_value < self.best_candidate.objective_value) if self.minimize else (cand.objective_value > self.best_candidate.objective_value)
                if is_better:
                    self.best_candidate = cand

        return cand

    def run_bounded_search(
        self,
        grid_points_per_dim: int = 3,
        eval_fn: Callable[[dict[str, float]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Perform a structured grid exploration across parameter bounds respecting budget."""
        if eval_fn is None:
            raise ValueError("eval_fn is required")

        dims = list(self.parameter_bounds.keys())
        grids: list[list[float]] = []
        for d in dims:
            lb, ub = self.parameter_bounds[d]
            if grid_points_per_dim <= 1 or lb == ub:
                grids.append([lb])
            else:
                step = (ub - lb) / (grid_points_per_dim - 1)
                grids.append([lb + i * step for i in range(grid_points_per_dim)])

        import itertools
        idx = 0
        for pt in itertools.product(*grids):
            if not self.budget.can_evaluate():
                break
            idx += 1
            cand_params = {dims[i]: pt[i] for i in range(len(dims))}
            self.evaluate_candidate(f"opt-case-{idx:03d}", cand_params, eval_fn)

        feas_status = "FEASIBLE_FOUND" if self.best_candidate is not None else "NO_FEASIBLE_FOUND"
        if not self.budget.can_evaluate():
            feas_status = "BUDGET_EXHAUSTED"

        return {
            "objective_name": self.objective_name,
            "minimize": self.minimize,
            "parameter_bounds": self.parameter_bounds,
            "status": feas_status,
            "total_evaluated": len(self.history),
            "feasible_count": sum(1 for c in self.history if c.is_feasible),
            "best_candidate": self.best_candidate.to_dict() if self.best_candidate else None,
            "budget": self.budget.status(),
            "all_candidates": [c.to_dict() for c in self.history],
        }


# ------------------------------------------------------------------------------
# 4. Generic Stage State Transfer (T047 subitem)
# ------------------------------------------------------------------------------

@dataclass
class StageCheckpoint:
    """Immutable checkpoint capturing terminal state of a physical stage."""
    checkpoint_id: str
    stage_id: str
    timestamp_s: float
    model_tag: str
    variables: dict[str, Any]
    units: dict[str, str]
    selection: dict[str, Any]
    metrics_summary: dict[str, float]
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "stage_id": self.stage_id,
            "timestamp_s": self.timestamp_s,
            "model_tag": self.model_tag,
            "variables": copy.deepcopy(self.variables),
            "units": copy.deepcopy(self.units),
            "selection": copy.deepcopy(self.selection),
            "metrics_summary": copy.deepcopy(self.metrics_summary),
            "sha256": self.sha256,
        }


class StageStateTransferManager:
    """Manages multi-stage physical transitions ensuring continuity and history preservation."""

    def __init__(self) -> None:
        self.checkpoints: dict[str, StageCheckpoint] = {}
        self.transfers: list[dict[str, Any]] = []

    def create_checkpoint(
        self,
        stage_id: str,
        model_tag: str,
        timestamp_s: float,
        variables: dict[str, Any],
        units: dict[str, str],
        selection: dict[str, Any] | None = None,
    ) -> StageCheckpoint:
        # Validate finite numeric values in variable fields
        for vname, val in variables.items():
            if isinstance(val, (int, float)):
                if math.isnan(val) or math.isinf(val):
                    raise ExecutionContractError("INVALID_STAGE_STATE", f"Variable {vname} has non-finite value {val}")
            elif isinstance(val, list):
                if not finite_check(val):
                    raise ExecutionContractError("INVALID_STAGE_STATE", f"Variable array {vname} has non-finite elements")

        summary = {}
        for vname, val in variables.items():
            if isinstance(val, (int, float)):
                summary[f"{vname}_val"] = float(val)
            elif isinstance(val, list) and val:
                num_items = [x for x in val if isinstance(x, (int, float))]
                if num_items:
                    summary[f"{vname}_min"] = float(min(num_items))
                    summary[f"{vname}_max"] = float(max(num_items))
                    summary[f"{vname}_avg"] = float(sum(num_items) / len(num_items))

        raw_bytes = json.dumps({
            "stage_id": stage_id,
            "model_tag": model_tag,
            "timestamp_s": timestamp_s,
            "variables": variables,
            "units": units,
        }, sort_keys=True).encode("utf-8")
        chk_hash = hashlib.sha256(raw_bytes).hexdigest()
        chk_id = f"chk_{stage_id}_{chk_hash[:12]}"

        chk = StageCheckpoint(
            checkpoint_id=chk_id,
            stage_id=stage_id,
            timestamp_s=timestamp_s,
            model_tag=model_tag,
            variables=copy.deepcopy(variables),
            units=copy.deepcopy(units),
            selection=copy.deepcopy(selection or {"domain": [1]}),
            metrics_summary=summary,
            sha256=chk_hash,
        )
        self.checkpoints[chk_id] = chk
        return chk

    def transfer_stage_state(
        self,
        source_checkpoint_id: str,
        target_stage_id: str,
        variable_mapping: Mapping[str, str],
        reset_history: bool = False,
    ) -> dict[str, Any]:
        """Maps source checkpoint final state to target stage initial conditions.
        
        Refuses silent history resets when reset_history is True or invalid mapping supplied.
        """
        chk = self.checkpoints.get(source_checkpoint_id)
        if chk is None:
            raise ExecutionContractError("CHECKPOINT_NOT_FOUND", f"Checkpoint {source_checkpoint_id} not found")

        if reset_history:
            raise ExecutionContractError("HISTORY_RESET_PROHIBITED", "Stage transition must preserve historical state; reset_history=True rejected")

        expected_hash = hashlib.sha256(json.dumps({
            "stage_id": chk.stage_id, "model_tag": chk.model_tag,
            "timestamp_s": chk.timestamp_s, "variables": chk.variables, "units": chk.units,
        }, sort_keys=True).encode("utf-8")).hexdigest()
        if expected_hash != chk.sha256:
            raise ExecutionContractError("CHECKPOINT_CORRUPTED", "Checkpoint content changed")
        target_initial_state: dict[str, Any] = {}
        target_units: dict[str, str] = {}
        mapping_records: list[dict[str, Any]] = []

        for src_var, dst_var in variable_mapping.items():
            if src_var not in chk.variables:
                raise ExecutionContractError("VARIABLE_MAPPING_ERROR", f"Source variable {src_var} not in checkpoint {chk.checkpoint_id}")
            val = chk.variables[src_var]
            unit = chk.units.get(src_var, "1")
            target_initial_state[dst_var] = copy.deepcopy(val)
            target_units[dst_var] = unit
            mapping_records.append({
                "source_variable": src_var,
                "target_variable": dst_var,
                "unit": unit,
                "source_timestamp": chk.timestamp_s,
            })

        transfer_record = {
            "transfer_id": f"xfer_{chk.stage_id}_to_{target_stage_id}_{int(time.time())}",
            "source_checkpoint_id": chk.checkpoint_id,
            "source_stage_id": chk.stage_id,
            "target_stage_id": target_stage_id,
            "mapping": mapping_records,
            "target_initial_state": target_initial_state,
            "target_units": target_units,
            "history_preserved": True,
            "t047_generic_subitem_status": "NOT_RUN",
            "scope": "METADATA_PREVIEW_ONLY",
            "domain_physical_status": "DEFERRED_TO_W24",
            "verification": {
                "source_hash_verified": True,
                "units_preserved": True,
                "variables_mapped_count": len(mapping_records),
            },
        }
        self.transfers.append(transfer_record)
        return transfer_record


# ------------------------------------------------------------------------------
# 5. MCP Domain Operations Registration
# ------------------------------------------------------------------------------

_GLOBAL_CACHE = ResultCache()
_GLOBAL_STATE_XFER = StageStateTransferManager()


# Production operations share the managed case executor; these imports are
# intentionally late so the reusable containers above remain import-light.
from ._w21_execution import (
    op_parameter_case_manage, op_study_sweep_manage,
    op_stage_state_transfer, op_optimization_bounded_run, op_stage_checkpoint_create,
)

OPERATIONS = {
    "parameter.case_manage": op_parameter_case_manage,
    "study.sweep_manage": op_study_sweep_manage,
    "solver.solution_transfer": op_stage_state_transfer,
    "stage.checkpoint_create": op_stage_checkpoint_create,
    "experiment.run": op_optimization_bounded_run,
}

ALIASES = {
    "parameter_case_manage": "parameter.case_manage",
    "study_sweep_manage": "study.sweep_manage",
    "optimization.bounded_run": "experiment.run",
    "optimization_bounded_run": "experiment.run",
    "stage.state_transfer": "solver.solution_transfer",
    "stage_state_transfer": "solver.solution_transfer",
    "stage_checkpoint_create": "stage.checkpoint_create",
}
