"""Tests for G3 Workstream 21 (W21): Sweeps, Budget/Cache, Bounded Optimization, and Stage State Transfer."""
from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock

from comsol_mcp._g2_contract import ExecutionContractError
from comsol_mcp._g3_ops import DISPATCH, EFFECTS, REQUIRES_ISOLATION
from comsol_mcp._g3_w21 import (
    BoundedOptimizer,
    ComputationBudget,
    ParameterCase,
    ParameterIndexTable,
    ResultCache,
    StageStateTransferManager,
)
from comsol_mcp._tools_w21 import (
    optimization_bounded_run,
    parameter_case_manage,
    stage_checkpoint_create,
    stage_state_transfer,
    study_sweep_manage,
)


class W21DeliverablesTests(unittest.TestCase):
    """Verifies all five W21 deliverables per W21_PLAN.md."""

    # --------------------------------------------------------------------------
    # 1. Parameter Case & Sweep Indexing (T021 Mapping)
    # --------------------------------------------------------------------------
    def test_parameter_case_creation_and_indexing(self):
        """Verifies parameter case creation with units, indices, and status."""
        table = ParameterIndexTable(sweep_id="sweep_test", parameter_names=["L", "k"])
        case1 = ParameterCase(
            case_id="case_001",
            parameters={"L": 0.05, "k": 400.0},
            units={"L": "m", "k": "W/(m*K)"},
            outer_index=1,
            inner_index=1,
            status="COMPLETED",
            results={"T_mid": 325.0, "flux": 80.0},
            time_series={"T_mid": [300.0, 312.0, 325.0]},
            time_points=[0.0, 0.05, 0.1],
        )
        table.add_case(case1)

        self.assertEqual(table.get_by_id("case_001"), case1)
        self.assertEqual(table.get_by_indices(1, 1), case1)
        self.assertEqual(table.get_by_params({"L": 0.05, "k": 400.0}), case1)

    def test_duplicate_case_or_index_collision_rejected(self):
        """Ensures index and ID collisions are rejected fail-closed."""
        table = ParameterIndexTable(sweep_id="sweep_test", parameter_names=["L", "k"])
        case1 = ParameterCase(
            case_id="case_001",
            parameters={"L": 0.05, "k": 400.0},
            units={"L": "m", "k": "W/(m*K)"},
            outer_index=1,
            inner_index=1,
        )
        table.add_case(case1)

        # Duplicate ID
        dup_id_case = ParameterCase(
            case_id="case_001",
            parameters={"L": 0.1, "k": 300.0},
            units={"L": "m", "k": "W/(m*K)"},
            outer_index=1,
            inner_index=2,
        )
        with self.assertRaises(ExecutionContractError):
            table.add_case(dup_id_case)

        # Duplicate indices
        dup_idx_case = ParameterCase(
            case_id="case_002",
            parameters={"L": 0.1, "k": 300.0},
            units={"L": "m", "k": "W/(m*K)"},
            outer_index=1,
            inner_index=1,
        )
        with self.assertRaises(ExecutionContractError):
            table.add_case(dup_idx_case)

    def test_two_parameter_transient_sweep_query_slice(self):
        """Two-parameter sweep with transient steps verifies separation of outer, inner, and time."""
        table = ParameterIndexTable(sweep_id="sweep_2param", parameter_names=["width", "power"])
        for outer, w in enumerate([0.01, 0.02], start=1):
            for inner, p in enumerate([50.0, 100.0], start=1):
                case = ParameterCase(
                    case_id=f"c_{outer}_{inner}",
                    parameters={"width": w, "power": p},
                    units={"width": "m", "power": "W"},
                    outer_index=outer,
                    inner_index=inner,
                    status="COMPLETED",
                    results={"T_peak": 300.0 + p * w * 100},
                    time_series={"T_probe": [300.0, 300.0 + p * 0.5, 300.0 + p]},
                    time_points=[0.01, 0.05, 0.1],
                )
                table.add_case(case)

        # Query outer=1, inner=2 slice
        res = table.query_slice(expression="T_probe", outer=1, inner=2, time_val=0.05)
        self.assertEqual(res["matches_count"], 1)
        self.assertEqual(res["matches"][0]["case_id"], "c_1_2")
        self.assertEqual(res["matches"][0]["value"], 350.0)

    # --------------------------------------------------------------------------
    # 2. Computation Budget & Cache Invalidation
    # --------------------------------------------------------------------------
    def test_computation_budget_enforcement(self):
        """Verifies that computation budget halts execution when max cases reached."""
        budget = ComputationBudget(max_cases=3, max_wall_time_s=10.0)
        self.assertTrue(budget.can_evaluate())

        budget.record_case(success=True)
        budget.record_case(success=True)
        self.assertTrue(budget.can_evaluate())

        budget.record_case(success=True)
        self.assertFalse(budget.can_evaluate())
        self.assertEqual(budget.status()["exhaustion_reason"], "MAX_CASES_EXCEEDED")

    def test_result_cache_hit_and_invalidation(self):
        """Content-addressed cache returns hits for identical inputs and misses on changes."""
        cache = ResultCache()
        key1 = cache.compute_key(
            model_digest="sha_model_v1",
            parameters={"k": 400.0, "L": 0.05},
            study_config={"study": "std1", "tlist": [0.01, 0.1]},
            runtime_version="6.4.0.293",
        )
        # Initially miss
        self.assertIsNone(cache.get(key1))

        # Store result
        cache.store(key1, {"T_mid": 325.0, "power": 80.0})
        cached = cache.get(key1)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["T_mid"], 325.0)

        # Invalidate by parameter change
        key_param_changed = cache.compute_key(
            model_digest="sha_model_v1",
            parameters={"k": 450.0, "L": 0.05},
            study_config={"study": "std1", "tlist": [0.01, 0.1]},
            runtime_version="6.4.0.293",
        )
        self.assertIsNone(cache.get(key_param_changed))

        # Invalidate by COMSOL version change (e.g. 6.3 vs 6.4)
        key_ver_changed = cache.compute_key(
            model_digest="sha_model_v1",
            parameters={"k": 400.0, "L": 0.05},
            study_config={"study": "std1", "tlist": [0.01, 0.1]},
            runtime_version="6.3.0.290",
        )
        self.assertIsNone(cache.get(key_ver_changed))

    # --------------------------------------------------------------------------
    # 3. Bounded Optimization
    # --------------------------------------------------------------------------
    def test_bounded_optimization_finds_constrained_minimum(self):
        """Optimizes parameter to minimize objective subject to temperature constraint."""
        budget = ComputationBudget(max_cases=20)
        optimizer = BoundedOptimizer(
            objective_name="heat_loss",
            minimize=True,
            parameter_bounds={"k": (200.0, 500.0), "thickness": (0.01, 0.05)},
            constraints=[
                {"expression": "T_surface", "max_value": 340.0},
            ],
            budget=budget,
        )

        def eval_heat_transfer(params: dict[str, float]) -> dict[str, Any]:
            k = params["k"]
            th = params["thickness"]
            # Physical response mock:
            # T_surface = 300 + 100 * (th / 0.05)
            # heat_loss = k * 0.01 / th
            t_surf = 300.0 + 80.0 * (th / 0.05)
            loss = k * 0.01 / th
            return {
                "T_surface": t_surf,
                "heat_loss": loss,
            }

        res = optimizer.run_bounded_search(grid_points_per_dim=3, eval_fn=eval_heat_transfer)
        self.assertEqual(res["status"], "FEASIBLE_FOUND")
        self.assertIsNotNone(res["best_candidate"])
        best = res["best_candidate"]
        self.assertTrue(best["is_feasible"])
        self.assertLessEqual(best["raw_results"]["T_surface"], 340.0)

    def test_bounded_optimization_budget_exhaustion(self):
        """When budget is small, optimizer terminates gracefully and reports budget metrics."""
        budget = ComputationBudget(max_cases=2)
        optimizer = BoundedOptimizer(
            objective_name="cost",
            minimize=True,
            parameter_bounds={"x": (0.0, 10.0)},
            budget=budget,
        )

        def eval_fn(params: dict[str, float]) -> dict[str, Any]:
            return {"cost": params["x"] ** 2}

        res = optimizer.run_bounded_search(grid_points_per_dim=5, eval_fn=eval_fn)
        self.assertEqual(res["total_evaluated"], 2)
        self.assertEqual(res["status"], "BUDGET_EXHAUSTED")
        self.assertTrue(res["budget"]["exhausted"])

    # --------------------------------------------------------------------------
    # 4. Stage State Transfer (T047 Subitem)
    # --------------------------------------------------------------------------
    def test_stage_state_transfer_preserves_history(self):
        """Validates checkpoint creation and state mapping without resetting history."""
        mgr = StageStateTransferManager()
        chk = mgr.create_checkpoint(
            stage_id="stage_curing",
            model_tag="model_adhesion_v1",
            timestamp_s=10.0,
            variables={"T": 350.0, "conversion_alpha": 0.85, "stress_von_mises": 1.2e6},
            units={"T": "K", "conversion_alpha": "1", "stress_von_mises": "Pa"},
            selection={"domain": [1, 2]},
        )
        self.assertTrue(chk.checkpoint_id.startswith("chk_stage_curing_"))

        # Transfer state into next cooling stage
        xfer = mgr.transfer_stage_state(
            source_checkpoint_id=chk.checkpoint_id,
            target_stage_id="stage_cooling",
            variable_mapping={
                "T": "T_init",
                "conversion_alpha": "alpha_pregel",
                "stress_von_mises": "stress_initial",
            },
            reset_history=False,
        )
        self.assertEqual(xfer["t047_generic_subitem_status"], "NOT_RUN")
        self.assertEqual(xfer["scope"], "METADATA_PREVIEW_ONLY")
        self.assertEqual(xfer["domain_physical_status"], "DEFERRED_TO_W24")
        self.assertTrue(xfer["history_preserved"])
        self.assertEqual(xfer["target_initial_state"]["T_init"], 350.0)
        self.assertEqual(xfer["target_initial_state"]["alpha_pregel"], 0.85)

    def test_stage_state_transfer_rejects_history_reset(self):
        """Explicitly rejects reset_history=True to prevent silent state wiping."""
        mgr = StageStateTransferManager()
        chk = mgr.create_checkpoint(
            stage_id="stage_1",
            model_tag="m1",
            timestamp_s=5.0,
            variables={"T": 320.0},
            units={"T": "K"},
        )
        with self.assertRaises(ExecutionContractError) as ctx:
            mgr.transfer_stage_state(
                source_checkpoint_id=chk.checkpoint_id,
                target_stage_id="stage_2",
                variable_mapping={"T": "T_init"},
                reset_history=True,
            )
        self.assertEqual(ctx.exception.code, "HISTORY_RESET_PROHIBITED")

    def test_stage_checkpoint_rejects_non_finite_values(self):
        """Non-finite floats in state variables are rejected fail-closed."""
        mgr = StageStateTransferManager()
        with self.assertRaises(ExecutionContractError) as ctx:
            mgr.create_checkpoint(
                stage_id="stage_bad",
                model_tag="m1",
                timestamp_s=1.0,
                variables={"T": float("nan")},
                units={"T": "K"},
            )
        self.assertEqual(ctx.exception.code, "INVALID_STAGE_STATE")

    # --------------------------------------------------------------------------
    # 5. G3 Operations Dispatch & Tool Schemas
    # --------------------------------------------------------------------------
    def test_g3_ops_dispatch_w21_operations(self):
        """Verifies that all W21 operations are published in DISPATCH and EFFECTS."""
        for op in ("parameter.case_manage", "study.sweep_manage", "solver.solution_transfer", "stage.checkpoint_create", "experiment.run"):
            self.assertIn(op, DISPATCH)
            self.assertIn(op, EFFECTS)
            self.assertIn(op, REQUIRES_ISOLATION)

    def test_w21_tools_argument_wrapping(self):
        """Verifies tool entry points wrap arguments into consistent payloads."""
        p_res = parameter_case_manage(action="create", group="grp1", case_tag="c1", values={"p": 1.0})
        self.assertEqual(p_res["action"], "create")
        self.assertEqual(p_res["values"], {"p": 1.0})

        s_res = study_sweep_manage(study="std1", max_cases=15)
        self.assertEqual(s_res["study"], "std1")
        self.assertEqual(s_res["max_cases"], 15)

        o_res = optimization_bounded_run(objective_name="f", parameter_bounds={"x": [0.0, 1.0]})
        self.assertEqual(o_res["objective_name"], "f")
        self.assertEqual(o_res["parameter_bounds"], {"x": (0.0, 1.0)})

        c_res = stage_checkpoint_create(stage_id="s1", timestamp_s=0.5, variables={"T": 300.0}, units={"T": "K"})
        self.assertEqual(c_res["action"], "create_checkpoint")

        x_res = stage_state_transfer(checkpoint_id="chk1", target_stage_id="s2", variable_mapping={"T": "T_init"})
        self.assertEqual(x_res["action"], "transfer")


if __name__ == "__main__":
    unittest.main()
