import builtins
import json
import math
import unittest
from pathlib import Path

from comsol_mcp import _g3_w21 as w21

ROOT = Path(__file__).resolve().parents[1]
GRID_POINTS = 10_000
MAX_CASES = 1


class FakeWorker:
    def __init__(self, project_id, response=None):
        self.project_id = project_id
        self.response = response
        self.calls = []

    def evaluate(self, parameters):
        self.calls.append(dict(parameters))
        if self.response is not None:
            return dict(self.response)
        return {"status": "COMPLETED", "objective": parameters["x"] ** 2}


class T24OptimizerBudgetTests(unittest.TestCase):
    def setUp(self):
        self.project = json.loads((ROOT / "fixtures/synthetic-project/project.json").read_text())
        task = json.loads((ROOT / "fixtures/synthetic-project/task.json").read_text())
        self.assertTrue(self.project["synthetic"])
        self.assertTrue(task["synthetic"])
        self.assertEqual(self.project["worker"], "FakeWorker")

    def test_axis_materialization_is_bounded_by_case_budget(self):
        visited_grid_indices = []
        original_range = builtins.range

        class CountedRange:
            def __init__(self, values):
                self.values = values
                self.stop = values.stop

            def __iter__(self):
                for value in self.values:
                    visited_grid_indices.append(value)
                    yield value

            def __len__(self):
                return len(self.values)

        def tracked_range(*args):
            values = original_range(*args)
            return CountedRange(values) if args == (GRID_POINTS,) else values

        had_module_range = "range" in w21.__dict__
        previous_module_range = w21.__dict__.get("range")
        w21.range = tracked_range
        try:
            worker = FakeWorker(self.project["project_id"])
            budget = w21.ComputationBudget(max_cases=MAX_CASES)
            optimizer = w21.BoundedOptimizer(
                "objective", parameter_bounds={"x": (0.0, 1.0)}, budget=budget,
            )
            result = optimizer.run_bounded_search(
                grid_points_per_dim=GRID_POINTS, eval_fn=worker.evaluate,
            )
        finally:
            if had_module_range:
                w21.range = previous_module_range
            else:
                del w21.range

        self.assertEqual(len(worker.calls), MAX_CASES)
        self.assertEqual(result["total_evaluated"], MAX_CASES)
        self.assertLessEqual(
            len(visited_grid_indices), MAX_CASES,
            "grid planning traversed the whole axis before the case budget was checked",
        )

    def test_arbitrarily_large_grid_is_lazy_and_avoids_float_conversion_overflow(self):
        very_large_grid = 10**100
        float_overflow_grid = 10**1000
        with self.assertRaises(OverflowError):
            len(range(very_large_grid))
        with self.assertRaises(OverflowError):
            float(float_overflow_grid)

        for grid_points in (very_large_grid, float_overflow_grid):
            with self.subTest(grid_points_digits=len(str(grid_points))):
                worker = FakeWorker(self.project["project_id"])
                optimizer = w21.BoundedOptimizer(
                    "objective", parameter_bounds={"x": (0.0, 1.0)},
                    budget=w21.ComputationBudget(max_cases=1),
                )
                result = optimizer.run_bounded_search(grid_points, worker.evaluate)
                self.assertEqual(len(worker.calls), 1)
                self.assertEqual(worker.calls[0], {"x": 0.0})
                self.assertEqual(result["total_evaluated"], 1)

    def test_grid_order_and_values_are_preserved(self):
        worker = FakeWorker(self.project["project_id"])
        optimizer = w21.BoundedOptimizer(
            "objective", parameter_bounds={"x": (0.0, 1.0), "y": (0.0, 1.0)},
            budget=w21.ComputationBudget(max_cases=9),
        )
        result = optimizer.run_bounded_search(3, worker.evaluate)
        self.assertEqual(worker.calls, [
            {"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 0.5}, {"x": 0.0, "y": 1.0},
            {"x": 0.5, "y": 0.0}, {"x": 0.5, "y": 0.5}, {"x": 0.5, "y": 1.0},
            {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 0.5}, {"x": 1.0, "y": 1.0},
        ])
        self.assertEqual(result["total_evaluated"], 9)

    def test_reused_optimizer_counts_only_current_grid_for_budget_status(self):
        scenarios = (
            (2, 2, 1, "BUDGET_EXHAUSTED"),
            (3, 3, 2, "BUDGET_EXHAUSTED"),
            (3, 2, 2, "FEASIBLE_FOUND"),
        )
        for max_cases, grid_points, expected_new, expected_status in scenarios:
            with self.subTest(max_cases=max_cases, grid_points=grid_points):
                optimizer = w21.BoundedOptimizer(
                    "objective", parameter_bounds={"x": (0.0, 1.0)},
                    budget=w21.ComputationBudget(max_cases=max_cases),
                )
                optimizer.evaluate_candidate(
                    "preexisting", {"x": 0.25},
                    lambda _params: {"status": "COMPLETED", "objective": -1.0},
                )
                observed = []
                result = optimizer.run_bounded_search(
                    grid_points,
                    lambda params: observed.append(dict(params)) or {
                        "status": "COMPLETED", "objective": params["x"],
                    },
                )

                self.assertEqual(len(observed), expected_new)
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["total_evaluated"], 1 + expected_new)
                self.assertEqual(len(result["all_candidates"]), 1 + expected_new)
                self.assertEqual(result["all_candidates"][0]["candidate_id"], "preexisting")
                self.assertEqual(result["feasible_count"], 1 + expected_new)
                self.assertEqual(result["best_candidate"]["candidate_id"], "preexisting")

    def test_decimal_negative_two_dimensional_grid_uses_exact_endpoints(self):
        observed = []
        optimizer = w21.BoundedOptimizer(
            "objective",
            parameter_bounds={"x": (0.1, 0.3), "y": (-0.3, -0.1)},
            budget=w21.ComputationBudget(max_cases=16),
        )
        result = optimizer.run_bounded_search(4, lambda params: observed.append(dict(params)) or {
            "status": "COMPLETED", "objective": params["x"] + params["y"],
        })

        self.assertEqual(len(observed), 16)
        self.assertEqual(observed[0], {"x": 0.1, "y": -0.3})
        self.assertEqual(observed[3], {"x": 0.1, "y": -0.1})
        self.assertEqual(observed[12], {"x": 0.3, "y": -0.3})
        self.assertEqual(observed[-1], {"x": 0.3, "y": -0.1})
        for point in observed:
            self.assertTrue(0.1 <= point["x"] <= 0.3)
            self.assertTrue(-0.3 <= point["y"] <= -0.1)
        self.assertEqual(result["status"], "FEASIBLE_FOUND")
        self.assertTrue(result["budget"]["exhausted"])

    def test_extreme_finite_float_bounds_keep_all_grid_values_finite(self):
        low, high = -1.7e308, 1.7e308
        observed = []
        optimizer = w21.BoundedOptimizer(
            "objective", parameter_bounds={"x": (low, high)},
            budget=w21.ComputationBudget(max_cases=3),
        )

        result = optimizer.run_bounded_search(3, lambda params: observed.append(params["x"]) or {
            "status": "COMPLETED", "objective": 0.0,
        })

        self.assertEqual(observed, [low, 0.0, high])
        for value in observed:
            self.assertTrue(math.isfinite(value))
            self.assertGreaterEqual(value, low)
            self.assertLessEqual(value, high)
        self.assertEqual(result["status"], "FEASIBLE_FOUND")

    def test_historical_unknown_prevents_reused_search_and_preserves_history_best_and_budget(self):
        optimizer = w21.BoundedOptimizer(
            "objective", parameter_bounds={"x": (0.0, 1.0)},
            budget=w21.ComputationBudget(max_cases=10),
        )
        optimizer.evaluate_candidate(
            "preexisting-best", {"x": 0.25},
            lambda _params: {"status": "COMPLETED", "objective": -1.0},
        )
        first_calls = []
        first = optimizer.run_bounded_search(
            3,
            lambda params: first_calls.append(dict(params)) or {"status": "UNKNOWN"},
        )
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(first["status"], "EXECUTION_STATE_UNKNOWN")

        history_before = [candidate.to_dict() for candidate in optimizer.history]
        best_before = optimizer.best_candidate.to_dict()
        budget_before = optimizer.budget.status()
        second_calls = []
        second = optimizer.run_bounded_search(
            3,
            lambda params: second_calls.append(dict(params)) or {
                "status": "COMPLETED", "objective": 0.0,
            },
        )

        self.assertEqual(second_calls, [])
        self.assertEqual(second["status"], "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(second["total_evaluated"], len(history_before))
        self.assertEqual(second["all_candidates"], history_before)
        self.assertEqual(second["best_candidate"], best_before)
        for counter in ("cases_evaluated", "consecutive_failures", "total_failures"):
            self.assertEqual(second["budget"][counter], budget_before[counter])

    def test_reused_optimizer_keeps_candidate_ids_unique(self):
        optimizer = w21.BoundedOptimizer(
            "objective", parameter_bounds={"x": (0.0, 1.0)},
            budget=w21.ComputationBudget(max_cases=4),
        )

        def evaluate(params):
            return {"status": "COMPLETED", "objective": params["x"]}

        optimizer.run_bounded_search(2, evaluate)
        result = optimizer.run_bounded_search(2, evaluate)
        self.assertEqual(
            [candidate["candidate_id"] for candidate in result["all_candidates"]],
            ["opt-case-001", "opt-case-002", "opt-case-003", "opt-case-004"],
        )

    def test_empty_single_and_degenerate_axes_keep_iteration_protocol(self):
        observed = []
        empty = w21.BoundedOptimizer(
            "objective", parameter_bounds={}, budget=w21.ComputationBudget(max_cases=3),
        )
        empty.run_bounded_search(3, lambda params: observed.append(dict(params)) or {
            "status": "COMPLETED", "objective": 0.0,
        })
        self.assertEqual(observed, [{}])

        observed.clear()
        single = w21.BoundedOptimizer(
            "objective", parameter_bounds={"x": (0.0, 1.0)},
            budget=w21.ComputationBudget(max_cases=3),
        )
        single.run_bounded_search(3, lambda params: observed.append(dict(params)) or {
            "status": "COMPLETED", "objective": params["x"],
        })
        self.assertEqual(observed, [{"x": 0.0}, {"x": 0.5}, {"x": 1.0}])

        observed.clear()
        degenerate = w21.BoundedOptimizer(
            "objective", parameter_bounds={"x": (2.0, 2.0), "y": (0.0, 1.0)},
            budget=w21.ComputationBudget(max_cases=3),
        )
        degenerate.run_bounded_search(3, lambda params: observed.append(dict(params)) or {
            "status": "COMPLETED", "objective": params["y"],
        })
        self.assertEqual(observed, [
            {"x": 2.0, "y": 0.0}, {"x": 2.0, "y": 0.5}, {"x": 2.0, "y": 1.0},
        ])

    def test_zero_case_and_expired_time_budgets_dispatch_nothing(self):
        for budget in (
            w21.ComputationBudget(max_cases=0),
            w21.ComputationBudget(max_cases=5, max_wall_time_s=0),
        ):
            with self.subTest(budget=budget.max_cases, wall=budget.max_wall_time_s):
                calls = []
                optimizer = w21.BoundedOptimizer(
                    "objective", parameter_bounds={"x": (0.0, 1.0)}, budget=budget,
                )
                result = optimizer.run_bounded_search(10, lambda params: calls.append(params) or {
                    "status": "COMPLETED", "objective": 0.0,
                })
                self.assertEqual(calls, [])
                self.assertEqual(result["total_evaluated"], 0)

    def test_unknown_case_stops_without_replay_or_next_case(self):
        worker = FakeWorker(self.project["project_id"], {
            "status": "UNKNOWN", "execution_state_unknown": True,
        })
        optimizer = w21.BoundedOptimizer(
            "objective", parameter_bounds={"x": (0.0, 1.0)},
            budget=w21.ComputationBudget(max_cases=10),
        )
        result = optimizer.run_bounded_search(100, worker.evaluate)
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(result["status"], "EXECUTION_STATE_UNKNOWN")

    def test_cache_key_changes_with_model_inputs_parameters_definition_and_runtime(self):
        cache = w21.ResultCache()
        base_args = {
            "model_digest": "sha256:synthetic-model-v1",
            "parameters": {"p": 1.0},
            "study_config": {"study": "std1", "rtol": 1e-6},
            "runtime_version": "synthetic-comsol-6.4",
        }
        key = cache.compute_key(**base_args)
        cache.store(key, {"status": "COMPLETED", "objective": 1.0})
        self.assertIsNotNone(cache.get(key))
        changes = [
            {**base_args, "model_digest": "sha256:synthetic-model-v2"},
            {**base_args, "parameters": {"p": 2.0}},
            {**base_args, "study_config": {"study": "std1", "rtol": 1e-7}},
            {**base_args, "runtime_version": "synthetic-comsol-6.3"},
        ]
        for changed in changes:
            with self.subTest(changed=changed):
                self.assertNotEqual(cache.compute_key(**changed), key)
                self.assertIsNone(cache.get(cache.compute_key(**changed)))


if __name__ == "__main__":
    unittest.main()
