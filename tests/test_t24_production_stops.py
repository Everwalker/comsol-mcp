import unittest
from unittest.mock import patch

from comsol_mcp import _g3_w21 as w21
from comsol_mcp import _w21_execution as execution


class T24ProductionStopTests(unittest.TestCase):
    def setUp(self):
        self.arguments = {
            "definition": {"synthetic": True},
            "study": "std1",
            "parameter_bounds": {"x": (0.0, 1.0)},
            "constraints": [{"expression": "guard", "max_value": 1.0}],
            "objective_name": "objective",
            "max_cases": 10,
            "max_wall_time_s": 300,
            "grid_points_per_dim": 100,
        }

    def run_production_route(self, fake_execute_case):
        calls = []

        def counted_execute_case(worker, tag, study, definition, params, budget, case_id):
            calls.append((case_id, dict(params)))
            return fake_execute_case(budget, params, case_id)

        with patch.object(execution, "validate_definition", lambda *args: None), \
             patch.object(execution, "execute_case", counted_execute_case):
            result = execution.op_optimization_bounded_run(
                object(), "synthetic-model", dict(self.arguments),
            )
        return result, calls

    def test_failed_case_stops_and_preserves_possible_partial_model_change(self):
        failure = {
            "status": "FAILED",
            "error": "synthetic failure after parameter write",
            "model_change_observation": "parameter write completed before failure",
        }

        def execute(budget, params, case_id):
            budget.cases_evaluated += 1
            budget.cases_failed += 1
            budget.total_failures += 1
            return {**failure, "case_id": case_id, "parameters": dict(params)}

        result, calls = self.run_production_route(execute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["search_status"], "FAILED")
        self.assertEqual(result["completion_status"], "CASE_FAILED")
        self.assertEqual(result["stopped_case"]["model_change_observation"], failure["model_change_observation"])
        self.assertEqual(result["all_candidates"][0]["raw_results"]["status"], "FAILED")
        self.assertEqual(result["compute_budget"]["consecutive_failures"], 1)

    def test_unknown_case_stops_and_remains_unknown(self):
        def execute(budget, params, case_id):
            budget.cases_evaluated += 1
            budget.cases_failed += 1
            budget.total_failures += 1
            return {
                "status": "UNKNOWN",
                "execution_state_unknown": True,
                "case_id": case_id,
                "parameters": dict(params),
            }

        result, calls = self.run_production_route(execute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(result["completion_status"], "EXECUTION_STATE_UNKNOWN")
        self.assertTrue(result["execution_state_unknown"])
        self.assertEqual(result["stopped_case"]["status"], "UNKNOWN")

    def test_not_run_case_stops_and_preserves_budget_exhaustion(self):
        def execute(budget, params, case_id):
            budget.start_time_s -= budget.max_wall_time_s + 1
            return {
                "status": "NOT_RUN",
                "reason": "BUDGET_EXHAUSTED",
                "case_id": case_id,
                "parameters": dict(params),
            }

        result, calls = self.run_production_route(execute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["search_status"], "BUDGET_EXHAUSTED")
        self.assertEqual(result["completion_status"], "BUDGET_EXHAUSTED")
        self.assertEqual(result["stopped_case"]["status"], "NOT_RUN")
        self.assertEqual(result["stopped_case"]["reason"], "BUDGET_EXHAUSTED")
        self.assertEqual(result["compute_budget"]["exhaustion_reason"], "WALL_TIME_EXCEEDED")

    def test_not_run_without_exact_budget_reason_is_not_reported_as_complete(self):
        for reason in (None, "WORKER_NOT_READY"):
            with self.subTest(reason=reason):
                def execute(budget, params, case_id):
                    result = {"status": "NOT_RUN", "case_id": case_id,
                              "parameters": dict(params)}
                    if reason is not None:
                        result["reason"] = reason
                    return result

                result, calls = self.run_production_route(execute)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["status"], "NOT_RUN")
                self.assertEqual(result["search_status"], "NOT_RUN")
                self.assertEqual(result["completion_status"], reason or "NOT_RUN")
                self.assertEqual(result["stopped_case"]["status"], "NOT_RUN")
                if reason is None:
                    self.assertNotIn("reason", result["stopped_case"])
                else:
                    self.assertEqual(result["stopped_case"]["reason"], reason)

    def test_execute_case_exception_is_captured_and_stops(self):
        def execute(budget, params, case_id):
            budget.cases_evaluated += 1
            budget.cases_failed += 1
            budget.total_failures += 1
            raise RuntimeError("synthetic failure after possible engine mutation")

        result, calls = self.run_production_route(execute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["completion_status"], "CASE_FAILED")
        self.assertIn("possible engine mutation", result["stopped_case"]["error"])
        self.assertEqual(result["stopped_case"]["parameters"], {"x": 0.0})

    def test_unknown_execute_case_exception_stays_unknown(self):
        def execute(budget, params, case_id):
            error = RuntimeError("synthetic worker timeout")
            error.code = "WORKER_TIMEOUT"
            raise error

        result, calls = self.run_production_route(execute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "EXECUTION_STATE_UNKNOWN")
        self.assertTrue(result["execution_state_unknown"])
        self.assertEqual(result["stopped_case"]["error_code"], "WORKER_TIMEOUT")

    def test_optimizer_budget_exhaustion_is_reported(self):
        def execute(budget, params, case_id):
            budget.cases_evaluated += 1
            return {
                "status": "COMPLETED",
                "objective": params["x"] ** 2,
                "guard": 0.0,
                "case_id": case_id,
            }

        self.arguments["max_cases"] = 1
        self.arguments["grid_points_per_dim"] = 5
        result, calls = self.run_production_route(execute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["search_status"], "BUDGET_EXHAUSTED")
        self.assertEqual(result["completion_status"], "BUDGET_EXHAUSTED")
        self.assertTrue(result["budget"]["exhausted"])
        self.assertIsNone(result["stopped_case"])

    def test_optimizer_default_retains_consecutive_failure_budget(self):
        calls = []

        def fail(params):
            calls.append(dict(params))
            raise RuntimeError("synthetic generic optimizer failure")

        optimizer = w21.BoundedOptimizer(
            "objective",
            parameter_bounds={"x": (0.0, 1.0)},
            budget=w21.ComputationBudget(max_cases=10),
        )
        result = optimizer.run_bounded_search(100, fail)
        self.assertEqual(len(calls), 5)
        self.assertEqual(result["budget"]["consecutive_failures"], 5)
        self.assertEqual(result["budget"]["exhaustion_reason"], "CONSECUTIVE_FAILURES_EXCEEDED")

    def test_successful_production_route_keeps_complete_status(self):
        def execute(budget, params, case_id):
            budget.cases_evaluated += 1
            return {
                "status": "COMPLETED",
                "objective": params["x"] ** 2,
                "guard": 0.0,
                "case_id": case_id,
            }

        self.arguments["max_cases"] = 2
        self.arguments["grid_points_per_dim"] = 2
        result, calls = self.run_production_route(execute)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["search_status"], "FEASIBLE_FOUND")
        self.assertEqual(result["completion_status"], "ALL_CANDIDATES_EVALUATED")
        self.assertTrue(result["budget"]["exhausted"])


if __name__ == "__main__":
    unittest.main()
