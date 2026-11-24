"""Narrow AST-extracted fake probe for the isolated model-close fix."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
import unittest

import comsol_mcp


PACKAGE = Path(comsol_mcp.__file__).resolve().parent


class ProbeExecutionContractError(Exception):
    def __init__(self, code, message, *, safe_retry=False, details=None, stage=None):
        super().__init__(message)
        self.code = code
        self.safe_retry = safe_retry
        self.details = dict(details or {})
        self.stage = stage


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def named_node(tree: ast.AST, name: str, kind=ast.FunctionDef):
    return next(node for node in ast.walk(tree)
                if isinstance(node, kind) and node.name == name)


def close_function(tree: ast.AST):
    return named_node(tree, "close_bound_model")


def shared_probe_block(function: ast.FunctionDef):
    for index, node in enumerate(function.body):
        if not isinstance(node, ast.Try):
            continue
        calls_probe = any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            and child.func.id == "shared_probe"
            for child in ast.walk(node)
        )
        if calls_probe:
            guards = [child for child in function.body[index + 1:]
                      if isinstance(child, ast.If)
                      and "shared_tags" in ast.unparse(child)]
            if len(guards) != 2:
                raise AssertionError(f"expected two shared-tag guards, found {len(guards)}")
            return node, guards
    raise AssertionError("shared_probe try block not found")


def make_probe(tree: ast.AST, model_tag: str):
    function = close_function(tree)
    try_node, guards = shared_probe_block(function)
    helper = ast.FunctionDef(
        name="run_shared_probe",
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="shared_probe")],
                           vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
                           defaults=[]),
        body=[copy.deepcopy(try_node), *(copy.deepcopy(guard) for guard in guards),
              ast.Return(value=ast.Name(id="shared_tags", ctx=ast.Load()))],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
    namespace = {"ExecutionContractError": ProbeExecutionContractError,
                 "model_tag": model_tag}
    exec(compile(module, "<extracted-shared-probe>", "exec"), namespace)
    return namespace["run_shared_probe"]


def make_refusal_envelope(tree: ast.AST):
    function = copy.deepcopy(named_node(tree, "_refusal_envelope"))
    function.body = [node for node in function.body if not isinstance(node, ast.ImportFrom)]
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))

    def domain_envelope(operation, data, *, dispatch_stage, witness):
        return {"operation": operation, "data": data, "dispatch_stage": dispatch_stage}

    namespace = {"ExecutionContractError": ProbeExecutionContractError,
                 "STAGE_VALIDATION": "validation", "Any": object,
                 "domain_envelope": domain_envelope}
    exec(compile(module, "<extracted-refusal-envelope>", "exec"), namespace)
    return namespace["_refusal_envelope"]


def make_worker_error_class(tree: ast.AST):
    helper = copy.deepcopy(named_node(tree, "_structured_true"))
    error_class = copy.deepcopy(named_node(tree, "JavaWorkerError", ast.ClassDef))
    module = ast.fix_missing_locations(ast.Module(body=[helper, error_class], type_ignores=[]))
    from collections.abc import Mapping
    namespace = {"Mapping": Mapping, "Any": object, "_structured_true": None}
    exec(compile(module, "<extracted-worker-error>", "exec"), namespace)
    return namespace["JavaWorkerError"]


class StructuredUnknown(RuntimeError):
    execution_state_unknown = True
    reply = {"status": "UNKNOWN", "execution_state_unknown": True,
             "safe_retry": False}


class SharedProbeUnknownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.copied_tree = parse(PACKAGE / "_managed_backend.py")
        cls.baseline_tree = parse(PACKAGE / "_managed_backend.py")
        cls.worker_tree = parse(PACKAGE / "_java_worker.py")
        cls.service_tree = parse(PACKAGE / "_execution_service.py")

    def test_structured_unknown_stays_unknown_and_non_retryable(self):
        probe = make_probe(self.copied_tree, "owned-model")

        def unknown_read():
            raise StructuredUnknown("structured Worker UNKNOWN")

        with self.assertRaises(ProbeExecutionContractError) as caught:
            probe(unknown_read)
        error = caught.exception
        self.assertEqual(error.code, "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(error.stage, "validation")
        self.assertIs(error.safe_retry, False)
        self.assertIs(error.details.get("safe_retry"), False)
        self.assertEqual(error.details.get("cause_type"), "StructuredUnknown")

    def test_ordinary_getter_error_keeps_existing_refusal(self):
        probe = make_probe(self.copied_tree, "owned-model")

        def ordinary_read():
            raise RuntimeError("deterministic getter failure")

        with self.assertRaises(ProbeExecutionContractError) as caught:
            probe(ordinary_read)
        error = caught.exception
        self.assertEqual(error.code, "MODEL_SHARED_STATE_UNKNOWN")
        self.assertEqual(error.stage, "validation")
        self.assertIs(error.safe_retry, False)
        self.assertEqual(error.details.get("cause_type"), "RuntimeError")

    def test_shared_tag_keeps_current_refusal(self):
        probe = make_probe(self.copied_tree, "owned-model")
        with self.assertRaises(ProbeExecutionContractError) as caught:
            probe(lambda: ["owned-model"])
        self.assertEqual(caught.exception.code, "MODEL_SHARED_WITH_OTHER_CLIENT")
        self.assertEqual(caught.exception.stage, "validation")
        self.assertEqual(caught.exception.details.get("model_tag"), "owned-model")

    def test_probe_failures_raise_before_the_only_remove_call(self):
        function = close_function(self.copied_tree)
        probe_try, _ = shared_probe_block(function)
        remove_calls = [node for node in ast.walk(function)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "client"
                        and node.func.attr == "remove"]
        self.assertEqual(len(remove_calls), 1)
        self.assertGreater(remove_calls[0].lineno, probe_try.end_lineno)
        self.assertTrue(all(isinstance(handler, ast.ExceptHandler)
                            for handler in probe_try.handlers))
        self.assertFalse(any(isinstance(node, ast.Call)
                             and isinstance(node.func, ast.Attribute)
                             and node.func.attr == "remove"
                             for handler in probe_try.handlers
                             for node in ast.walk(handler)))

    def test_production_classifier_recognizes_the_preserved_unknown_code(self):
        refusal = named_node(self.baseline_tree, "_refusal_envelope")
        refusal_text = ast.unparse(refusal)
        self.assertIn("EXECUTION_STATE_UNKNOWN", refusal_text)
        self.assertIn("execution_state_unknown", refusal_text)

        dispatcher = named_node(self.baseline_tree, "_dispatch_with_witness")
        dispatcher_text = ast.unparse(dispatcher)
        self.assertIn("witness.mutation_issued", dispatcher_text)
        self.assertIn("_refusal_envelope", dispatcher_text)

        publish_refusal = make_refusal_envelope(self.baseline_tree)
        error = ProbeExecutionContractError("EXECUTION_STATE_UNKNOWN", "probe unknown",
                                            stage="validation", details={"safe_retry": False})
        envelope = publish_refusal("model.close", error,
                                   type("Witness", (), {"as_dict": lambda self: {"mutation_issued": False}})(),
                                   effect="host_control")
        self.assertEqual(envelope["error"]["code"], "EXECUTION_STATE_UNKNOWN")
        self.assertIs(envelope["error"]["safe_retry"], False)
        self.assertIs(envelope["data"]["execution_state_unknown"], True)

        service = named_node(self.service_tree, "execute_legacy")
        service_text = ast.unparse(service)
        self.assertIn("returned_state_unknown", service_text)
        self.assertIn("EXECUTION_STATE_UNKNOWN", service_text)
        finishes_unknown = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "finish"
            and any(keyword.arg == "outcome" and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value == "unknown" for keyword in node.keywords)
            for node in ast.walk(service)
        )
        self.assertTrue(finishes_unknown)

    def test_worker_error_exposes_structured_unknown_to_the_close_catch(self):
        worker_error = make_worker_error_class(self.worker_tree)
        error = worker_error("correlated Worker UNKNOWN", reply={
            "status": "UNKNOWN", "execution_state_unknown": True,
            "safe_retry": False,
        })
        self.assertIs(error.execution_state_unknown, True)

        util = named_node(self.worker_tree, "modelsUsedByOtherClients")
        util_text = ast.unparse(util)
        self.assertIn("_decode_reply", util_text)
        self.assertIn("modelutil", util_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
