"""Exercise only the source exception branch, without daemon or job objects."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = PROJECT_ROOT / "comsol_mcp" / "_control_daemon.py"


def branch_code():
    tree = ast.parse(CANDIDATE.read_text(encoding="utf-8"))
    daemon = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ControlDaemon")
    execute = next(node for node in daemon.body if isinstance(node, ast.FunctionDef) and node.name == "_execute")
    matches = []
    for node in ast.walk(execute):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == "ExecutionContractError":
            text = ast.unparse(node)
            if 'ENGINE_UNRESPONSIVE' in text and 'EXECUTION_STATE_UNKNOWN' in text:
                matches.append(node)
    if len(matches) != 1:
        raise AssertionError(f"expected one target exception handler, found {len(matches)}")
    module = ast.fix_missing_locations(ast.Module(body=matches[0].body, type_ignores=[]))
    return compile(module, str(CANDIDATE), "exec")


class FakeDaemon:
    def _exception(self, exc):
        return {"success": False, "data": {}, "error": {
            "code": exc.code, "stage": exc.stage, "safe_retry": exc.safe_retry,
            "details": {"cause_type": "SyntheticStoreError"},
        }}


def classify(code, stage, safe_retry):
    namespace = {"self": FakeDaemon(), "exc": SimpleNamespace(code=code, stage=stage, safe_retry=safe_retry)}
    exec(branch_code(), namespace)
    return namespace["status"], namespace["result"]


class SavedStateSignalTests(unittest.TestCase):
    def test_post_dispatch_saved_state_is_unknown_and_publicly_flagged(self):
        status, result = classify("SAVED_STATE_UNKNOWN", "post_dispatch", False)
        self.assertEqual(status, "UNKNOWN")
        self.assertIs(result["execution_state_unknown"], True)
        self.assertEqual(result["error"], {
            "code": "SAVED_STATE_UNKNOWN", "stage": "post_dispatch", "safe_retry": False,
            "details": {"cause_type": "SyntheticStoreError"},
        })


    def test_predispatch_and_validation_are_not_upgraded(self):
        for stage in ("pre_dispatch", "validation", None):
            with self.subTest(stage=stage):
                status, result = classify("SAVED_STATE_UNKNOWN", stage, False)
                self.assertEqual(status, "FAILED")
                self.assertNotIn("execution_state_unknown", result)

    def test_retryable_and_other_codes_are_not_upgraded(self):
        for code, retry in (("SAVED_STATE_UNKNOWN", True), ("OTHER_ERROR", False)):
            with self.subTest(code=code, retry=retry):
                status, result = classify(code, "post_dispatch", retry)
                self.assertEqual(status, "FAILED")
                self.assertNotIn("execution_state_unknown", result)

    def test_existing_unknown_retry_flag_is_still_pinned(self):
        status, result = classify("EXECUTION_STATE_UNKNOWN", "post_dispatch", True)
        self.assertEqual(status, "UNKNOWN")
        self.assertIs(result["error"]["safe_retry"], False)

    def test_engine_unresponsive_classification_is_unchanged(self):
        self.assertEqual(classify("ENGINE_UNRESPONSIVE", "pre_dispatch", True)[0], "FAILED")
        self.assertEqual(classify("ENGINE_UNRESPONSIVE", "post_dispatch", False)[0], "UNKNOWN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
