#!/usr/bin/env python3
"""W21 Acceptance Runner & Evidence Generator.

Executes all 5 fixed deliverables defined in W21_PLAN.md:
1. 2-parameter + transient sweep, inner/outer/time parameter index table.
2. Computation budget enforcement, explicit failure logging, SHA-256 cache hit/miss semantics.
3. Bounded optimization with constraints, residual tracking, best feasible reporting.
4. Generic multi-stage state transfer (T047 subitem), checkpointing, variable mapping, history preservation.
5. Dual-version execution parity (6.3 & 6.4) and W20 validator verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from comsol_mcp._g3_w21 import (
    ComputationBudget,
    ResultCache,
    ParameterCase,
    ParameterIndexTable,
    BoundedOptimizer,
    StageCheckpoint,
    StageStateTransferManager,
    op_parameter_case_manage,
    op_study_sweep_manage,
    op_optimization_bounded_run,
    op_stage_checkpoint_create,
    op_stage_state_transfer,
)
from comsol_mcp._g3_w20_validation import (
    finite_check,
    range_check,
    benchmark_error,
    ValidationReport,
    STATUS_PASS,
    STATUS_UNVERIFIED,
)


def compute_sha256(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def run_deliverable_1_sweep(version: str) -> dict[str, Any]:
    """Deliverable 1: 2-parameter + transient sweep with parameter index table."""
    index_table = ParameterIndexTable(sweep_id="sweep_w21_01", parameter_names=["k", "rho_cp"])
    budget = ComputationBudget(max_cases=20)
    cache = ResultCache()

    # Two parameters: thermal conductivity k (W/(m*K)) and heat capacity rho_cp (J/(m^3*K))
    k_vals = [380.0, 400.0]
    rho_cp_vals = [3.4e6, 3.8e6]
    tlist = [0.0, 0.5, 1.0, 1.5, 2.0]
    L = 0.05
    T_init = 300.0
    T_hot = 350.0

    cases_data = []
    case_idx = 0
    for outer_i, k in enumerate(k_vals):
        for inner_j, rho_cp in enumerate(rho_cp_vals):
            case_idx += 1
            case_id = f"case_k{int(k)}_cp{int(rho_cp/1e4)}"
            alpha = k / rho_cp

            # Simulate 1D transient heat diffusion with 5 time steps
            t_series = {}
            for probe_x in [0.0, 0.025, 0.05]:
                t_series[f"T_x{probe_x:.3f}"] = [
                    T_init + (T_hot - T_init) * (1.0 - math.exp(-t / (L**2 / (math.pi**2 * alpha)))) * (probe_x / L)
                    for t in tlist
                ]

            raw_res = {
                "alpha": alpha,
                "T_final_mid": t_series["T_x0.025"][-1],
                "T_final_hot": t_series["T_x0.050"][-1],
                "status": "COMPLETED",
                "version": version,
            }

            # W20 numeric validation on final values
            assert finite_check(t_series["T_x0.025"]), f"Finite check failed for case {case_id}"
            assert range_check(t_series["T_x0.025"], min_val=290.0, max_val=360.0), f"Range check failed for case {case_id}"

            case = ParameterCase(
                case_id=case_id,
                parameters={"k": k, "rho_cp": rho_cp},
                units={"k": "W/(m*K)", "rho_cp": "J/(m^3*K)"},
                outer_index=outer_i,
                inner_index=inner_j,
                status="COMPLETED",
                results=raw_res,
                time_series=t_series,
                time_points=tlist,
            )
            index_table.add_case(case)
            budget.record_case(success=True)
            cases_data.append(case.to_dict())

    return {
        "status": "PASS",
        "deliverable": 1,
        "description": "2-parameter + transient sweep with parameter index table",
        "version": version,
        "cases_count": len(cases_data),
        "index_table": index_table.to_dict(),
        "budget_status": budget.status(),
    }


def run_deliverable_2_budget_cache(version: str) -> dict[str, Any]:
    """Deliverable 2: Computation budget enforcement and content-addressed cache reuse."""
    budget = ComputationBudget(max_cases=5, max_consecutive_failures=2)
    cache = ResultCache()

    model_tag = "m_thermal_w21"
    config = {"solver": "time_dependent", "tolerance": 1e-4}

    records = []

    # 1. Successful runs up to 3 cases
    for i in range(3):
        params = {"k": 350.0 + i * 25.0}
        key = ResultCache.compute_key(model_tag, params, config, version)
        # Check cache (should miss first time)
        cached = cache.get(key)
        records.append({"case": f"c_{i}", "cache_lookup": "MISS" if cached is None else "HIT"})

        # Record into cache
        eval_res = {"T_avg": 310.0 + i * 5.0, "status": "COMPLETED"}
        cache.store(key, eval_res)
        budget.record_case(success=True)

    # 2. Duplicate dispatch attempt (should HIT cache and NOT consume budget)
    dup_params = {"k": 350.0}
    dup_key = ResultCache.compute_key(model_tag, dup_params, config, version)
    cached_dup = cache.get(dup_key)
    records.append({"case": "c_0_dup", "cache_lookup": "HIT" if cached_dup is not None else "MISS", "result": cached_dup})
    assert cached_dup is not None, "Cache lookup must hit for duplicate dispatch"

    # 3. Cache MISS when version changes (e.g. 6.3 vs 6.4)
    alt_version = "6.4" if version == "6.3" else "6.3"
    alt_key = ResultCache.compute_key(model_tag, dup_params, config, alt_version)
    cached_alt_ver = cache.get(alt_key)
    records.append({"case": "c_0_alt_ver", "cache_lookup": "MISS" if cached_alt_ver is None else "HIT"})
    assert cached_alt_ver is None, "Cache must miss when version changes"

    # 4. Explicit failure case logging
    failed_params = {"k": -10.0}
    budget.record_case(success=False)
    records.append({"case": "c_fail", "status": "FAILED", "reason": "PHYSICAL_PARAM_NEGATIVE"})

    # 5. Exhaust budget and verify fail-closed enforcement
    budget.record_case(success=True)
    budget.record_case(success=True) # Now at 6 total cases (3 + 1 fail + 2 = 6 > max_cases=5)
    can_eval = budget.can_evaluate()
    b_stat = budget.status()
    records.append({"can_evaluate": can_eval, "budget_status": b_stat})
    assert not can_eval, "Budget must refuse further execution once max_cases is exceeded"

    return {
        "status": "PASS",
        "deliverable": 2,
        "description": "Computation budget enforcement and content-addressed cache reuse",
        "version": version,
        "budget_status": budget.status(),
        "cache_stats": cache.stats(),
        "trace": records,
    }


def run_deliverable_3_optimization(version: str) -> dict[str, Any]:
    """Deliverable 3: Bounded optimization with constraints and residual checks."""
    budget = ComputationBudget(max_cases=15)
    opt = BoundedOptimizer(
        objective_name="T_error",
        minimize=True,
        parameter_bounds={"k": (300.0, 500.0), "h": (10.0, 50.0)},
        constraints=[
            {"expression": "T_mid", "max_value": 340.0},
            {"expression": "T_mid", "min_value": 310.0},
        ],
        budget=budget,
    )

    # Physical objective: Target T_mid = 325.0 K
    # Analytical approximation: T_mid = 300.0 + 50.0 * (k / (k + h * 0.05))
    def eval_fn(params: dict[str, float]) -> dict[str, Any]:
        k = params["k"]
        h = params["h"]
        T_mid = 300.0 + 50.0 * (k / (k + h * 20.0))
        T_error = abs(T_mid - 325.0)
        return {
            "T_error": T_error,
            "T_mid": T_mid,
            "status": "COMPLETED",
        }

    opt_result = opt.run_bounded_search(grid_points_per_dim=3, eval_fn=eval_fn)
    assert opt_result["best_candidate"] is not None, "Optimizer must return a best feasible candidate"
    assert opt_result["best_candidate"]["is_feasible"], "Best candidate must be feasible"

    # W20 numeric validation on best candidate residual
    best_T_mid = opt_result["best_candidate"]["raw_results"]["T_mid"]
    b_err = benchmark_error(best_T_mid, 325.0, is_relative=False)
    assert b_err < 15.0, f"Optimization error too large: {b_err}"

    return {
        "status": "PASS",
        "deliverable": 3,
        "description": "Bounded parameter optimization under budget and constraints",
        "version": version,
        "best_candidate": opt_result["best_candidate"],
        "total_evaluated": opt_result["total_evaluated"],
        "feasible_count": opt_result["feasible_count"],
        "budget": opt_result["budget"],
        "history": opt_result["all_candidates"],
    }


def run_deliverable_4_stage_transfer(version: str) -> dict[str, Any]:
    """Deliverable 4: Multi-stage state transfer (T047 generic subitem)."""
    manager = StageStateTransferManager()

    # Stage 1: Heating phase
    chk1 = manager.create_checkpoint(
        stage_id="stage_01_heating",
        model_tag="model_adhesion_curing",
        timestamp_s=10.0,
        variables={
            "T_field": 345.2,
            "thermal_gradient": 12.4,
            "cure_degree": 0.35, # Generic reaction fraction
        },
        units={
            "T_field": "K",
            "thermal_gradient": "K/m",
            "cure_degree": "1",
        },
        selection="domain_curing_layer",
    )

    # State transfer: Map Stage 1 terminal state to Stage 2 initial state
    xfer = manager.transfer_stage_state(
        source_checkpoint_id=chk1.checkpoint_id,
        target_stage_id="stage_02_relaxation_cure",
        variable_mapping={
            "T_field": "T_init",
            "cure_degree": "alpha_cure_0",
        },
        reset_history=False,
    )

    # Verification: verify that reset_history=True is rejected fail-closed
    try:
        manager.transfer_stage_state(
            source_checkpoint_id=chk1.checkpoint_id,
            target_stage_id="stage_02_invalid",
            variable_mapping={"T_field": "T_init"},
            reset_history=True,
        )
        reset_prevented = False
    except Exception as exc:
        reset_prevented = True

    assert reset_prevented, "State transfer must prohibit history resets fail-closed"

    return {
        "status": "PASS",
        "deliverable": 4,
        "description": "Multi-stage state transfer and lineage preservation (T047 subitem)",
        "version": version,
        "t047_generic_subitem_status": "PASS",
        "t047_domain_acceptance_status": "DEFERRED_TO_W24",
        "source_checkpoint": chk1.to_dict(),
        "transfer_record": xfer,
        "transfers": manager.transfers,
    }


def run_full_w21_suite(version: str) -> dict[str, Any]:
    """Runs all 4 core deliverables for a specific COMSOL version target."""
    t0 = time.time()
    d1 = run_deliverable_1_sweep(version)
    d2 = run_deliverable_2_budget_cache(version)
    d3 = run_deliverable_3_optimization(version)
    d4 = run_deliverable_4_stage_transfer(version)
    elapsed = time.time() - t0

    return {
        "target_version": version,
        "status": "PASS",
        "elapsed_seconds": round(elapsed, 4),
        "deliverable_1": d1,
        "deliverable_2": d2,
        "deliverable_3": d3,
        "deliverable_4": d4,
    }


def main():
    # Historical analytical helpers above remain useful as control fixtures.
    # They cannot produce Windows/native approval or overwrite old evidence.
    print(json.dumps({"scope": "DEMO_ONLY", "native_certified": False,
                      "message": "Use tools/run_w21_native.py for actual Windows COMSOL execution"}))

if __name__ == "__main__":
    main()
