import ast
import hashlib
import importlib.util
import itertools
import json
import re
import sys
import unittest
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "comsol_mcp"
W21_SOURCE = CANDIDATE / "_w21_execution.py"
ENGINE_SOURCE = CANDIDATE / "_g2_engine.py"
DISPATCH_SOURCE = CANDIDATE / "_managed_backend.py"
DOMAIN_SOURCE = CANDIDATE / "_domain_outcome.py"
VIRTUAL_FILES = {}


class SyntheticExecutionContractError(Exception):
    def __init__(self, code, message, *, stage=None, details=None, safe_retry=False):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.details = dict(details or {})
        self.safe_retry = safe_retry


class SyntheticPreWriteRefusal(SyntheticExecutionContractError):
    def __init__(self, code, message, *, stage="validation", details=None, safe_retry=False):
        super().__init__(code, message, stage=stage, details=details, safe_retry=safe_retry)


class SyntheticBudget:
    def __init__(self, max_cases=1, max_wall_time_s=1):
        self.max_cases = max_cases
        self.max_wall_time_s = max_wall_time_s
        self.cases_evaluated = 0
        self.cases_failed = 0
        self.total_failures = 0

    def can_evaluate(self):
        return self.cases_evaluated < self.max_cases

    def status(self):
        return {"cases_evaluated": self.cases_evaluated, "max_cases": self.max_cases}


class SyntheticResultCache:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def compute_key(*args):
        return "synthetic-cache-key"

    def get(self, key):
        return None


class SyntheticOptimizer:
    def __init__(self, *args):
        self.history = []

    def run_bounded_search(self, grid_points, evaluate, *, stop_on_terminal_case_status):
        case = evaluate({"p": 0.5})
        return {"status": case.get("status", "COMPLETE"), "best_candidate": None}


class VirtualPath:
    def __init__(self, value):
        self.value = str(value)

    @property
    def parent(self):
        return VirtualPath(self.value.rsplit("/", 1)[0])

    def mkdir(self, **kwargs):
        return None

    def read_text(self, encoding=None):
        return VIRTUAL_FILES[self.value]

    def read_bytes(self):
        return VIRTUAL_FILES[self.value].encode("utf-8")

    def __str__(self):
        return self.value


class SyntheticArtifactStore:
    def __init__(self, root):
        self.root = root

    def resolve_safe_path(self, relative, allow_overwrite=False):
        return VirtualPath(relative)


class SyntheticInterpolation:
    def getType(self):
        return "Interpolation"

    def getString(self, name):
        assert name == "filename"
        return "synthetic-input.csv"


class SyntheticStep:
    def __init__(self, mode, error=None):
        self.mode = mode
        self.error = error

    def getString(self, name):
        if name == "useinitsol":
            return "on" if self.mode == "on" else "off"
        if name == "initstudy":
            if self.error is not None:
                raise self.error
            return "missing-study"
        raise AssertionError(name)


class SyntheticStudy:
    def __init__(self, step):
        self.step = step

    def feature(self, *args):
        return self.step if args else ["step1"]


class SyntheticModel:
    def __init__(self, source, *, export_error=None, step_mode="off", step_error=None):
        self.source = source
        self.export_error = export_error
        self.step = SyntheticStep(step_mode, step_error)

    def resetHist(self):
        return None

    def save(self, path, file_type):
        assert file_type == "java"
        VIRTUAL_FILES[str(path)] = self.source

    def func(self, tag):
        assert tag == "f"
        if self.export_error is not None:
            raise self.export_error
        return SyntheticInterpolation()

    def study(self, tag):
        assert tag == "study1"
        return SyntheticStudy(self.step)

    def sol(self, *args):
        return []


def compile_nodes(source, names, namespace):
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    selected_names = set(names)
    selected = [node for node in tree.body if getattr(node, "name", None) in selected_names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)


def load_worker_failure_unknown():
    tree = ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8"), filename=str(ENGINE_SOURCE))
    node = next(node for node in tree.body if getattr(node, "name", None) == "_worker_failure_unknown")
    namespace = {"Mapping": Mapping, "BaseException": BaseException}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ENGINE_SOURCE), "exec"), namespace)
    return namespace["_worker_failure_unknown"]


def load_domain_outcome(package_name):
    spec = importlib.util.spec_from_file_location(package_name + "._domain_outcome", DOMAIN_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module


def load_dispatcher(package_name):
    tree = ast.parse(DISPATCH_SOURCE.read_text(encoding="utf-8"), filename=str(DISPATCH_SOURCE))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "ManagedBackend")
    function = next(node for node in owner.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_dispatch_with_witness")
    function.decorator_list = []
    namespace = {
        "Any": Any,
        "Mapping": Mapping,
        "ExecutionContractError": SyntheticExecutionContractError,
        "PreWriteRefusal": SyntheticPreWriteRefusal,
        "__package__": package_name,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(DISPATCH_SOURCE), "exec"), namespace)
    return namespace["_dispatch_with_witness"]


def raised_from(outer, inner):
    try:
        raise outer from inner
    except Exception as caught:
        return caught


def hidden_context_from_none(ordinary, hidden):
    try:
        raise hidden
    except Exception:
        try:
            raise ordinary from None
        except Exception as caught:
            return caught


def implicit_context_from_unknown(hidden):
    try:
        raise hidden
    except Exception:
        try:
            raise RuntimeError("ordinary implicit wrapper")
        except Exception as caught:
            return caught


class CacheUnknownRepairTests(unittest.TestCase):
    def setUp(self):
        VIRTUAL_FILES.clear()

    def make_harness(self, model=None):
        package_name = "_t24_cache_repair_" + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(CANDIDATE)]
        sys.modules[package_name] = package
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in tuple(sys.modules)
                                 if name == package_name or name.startswith(package_name + ".")])
        domain = load_domain_outcome(package_name)
        worker_failure_unknown = load_worker_failure_unknown()
        engine_identity_calls = []

        runtime = ModuleType(package_name + "._g3_runtime")
        runtime._engine_identity = lambda worker: engine_identity_calls.append(worker) or {
            "comsol_version": "synthetic", "source": "synthetic", "channel": "fake",
        }
        java_worker = ModuleType(package_name + "._java_worker")
        java_worker.RemoteJava = type("RemoteJava", (), {})
        observation = ModuleType(package_name + "._observation_store")
        observation.solution_identity = lambda worker, tag, sol: {"solution": sol}
        g3_w21 = ModuleType(package_name + "._g3_w21")
        g3_w21.ResultCache = SyntheticResultCache
        g3_w21.ComputationBudget = SyntheticBudget
        g3_w21.BoundedOptimizer = SyntheticOptimizer
        validation = ModuleType(package_name + "._g3_w20_validation")
        validation.validate_solution = lambda *args, **kwargs: None
        for module in (runtime, java_worker, observation, g3_w21, validation):
            sys.modules[module.__name__] = module

        if model is None:
            model = SyntheticModel("// synthetic export\n")
        namespace = {
            "ArtifactStore": SyntheticArtifactStore,
            "BaseException": BaseException,
            "Mapping": Mapping,
            "Path": VirtualPath,
            "PreWriteRefusal": SyntheticPreWriteRefusal,
            "_call": lambda receiver, method, *args: getattr(receiver, method)(*args),
            "_worker_failure_unknown": worker_failure_unknown,
            "bound_model": lambda worker, tag: model,
            "current_context": lambda: {
                "model_ref": {"model_tag": "synthetic-model"},
                "producer": "synthetic-producer",
                "store": object(),
            },
            "hashlib": hashlib,
            "itertools": itertools,
            "json": json,
            "re": re,
            "tag_list": list,
            "trusted_project_root": lambda worker: None,
            "uuid": uuid,
            "__package__": package_name,
            "__name__": package_name + "._w21_execution",
        }
        compile_nodes(W21_SOURCE, {
            "_exception_chain", "_exception_is_unknown", "digest", "require",
            "exported_input_identity", "model_identity", "execute_case",
            "op_study_sweep_manage", "op_optimization_bounded_run",
        }, namespace)
        namespace["validate_parameters"] = lambda *args, **kwargs: None
        namespace["validate_definition"] = lambda *args, **kwargs: None
        namespace["apply_parameters"] = lambda *args, **kwargs: None
        namespace["persist"] = lambda *args, **kwargs: None
        namespace["key_for"] = lambda *args, **kwargs: "synthetic-result-key"
        dispatcher = load_dispatcher(package_name)
        return namespace, domain, dispatcher, engine_identity_calls

    def identity(self, module):
        return module["model_identity"](
            object(), "model1", [], {"input_artifacts": []}, "study1"
        )

    def test_classifier_uses_structured_signals_explicit_causes_and_legacy_codes(self):
        module, _, _, _ = self.make_harness()
        cases = [
            SyntheticExecutionContractError(
                "API_UNSUPPORTED", "safe outer text", details={"execution_state_unknown": True}
            ),
            SyntheticExecutionContractError("ENGINE_UNRESPONSIVE", "synthetic engine failure"),
            SyntheticExecutionContractError("WORKER_TIMEOUT", "synthetic Worker timeout"),
            SyntheticExecutionContractError("EXECUTION_STATE_UNKNOWN", "synthetic explicit unknown"),
            raised_from(
                SyntheticExecutionContractError("API_UNSUPPORTED", "outer wrapper"),
                SyntheticExecutionContractError(
                    "API_UNSUPPORTED", "inner wrapper", details={"execution_state_unknown": True}
                ),
            ),
            raised_from(
                SyntheticExecutionContractError("API_UNSUPPORTED", "outer wrapper"),
                SyntheticExecutionContractError("WORKER_TIMEOUT", "nested timeout"),
            ),
        ]
        for error in cases:
            with self.subTest(code=error.code, cause=error.__cause__ is not None):
                self.assertTrue(module["_exception_is_unknown"](error))

        cycle = RuntimeError("synthetic cause cycle")
        cycle.__cause__ = cycle
        self.assertFalse(module["_exception_is_unknown"](cycle))

    def test_from_none_ordinary_exception_does_not_inherit_hidden_unknown_context(self):
        module, _, _, _ = self.make_harness()
        hidden = SyntheticExecutionContractError(
            "API_UNSUPPORTED", "hidden", details={"execution_state_unknown": True}
        )
        ordinary = hidden_context_from_none(RuntimeError("ordinary failure"), hidden)
        self.assertIs(ordinary.__context__, hidden)
        self.assertTrue(ordinary.__suppress_context__)
        self.assertFalse(module["_exception_is_unknown"](ordinary))
        implicit = implicit_context_from_unknown(hidden)
        self.assertIs(implicit.__context__, hidden)
        self.assertFalse(implicit.__suppress_context__)
        self.assertFalse(module["_exception_is_unknown"](implicit))

    def test_both_cache_identity_fallbacks_bare_raise_confirmed_unknowns(self):
        source = 'model.func("f").set("filename", "synthetic-input.csv");'
        errors = (
            SyntheticExecutionContractError(
                "API_UNSUPPORTED", "synthetic structured unknown",
                details={"execution_state_unknown": True},
            ),
            SyntheticExecutionContractError("ENGINE_UNRESPONSIVE", "synthetic engine failure"),
            SyntheticExecutionContractError("WORKER_TIMEOUT", "synthetic Worker timeout"),
        )
        for location in ("export", "initial"):
            for error in errors:
                with self.subTest(location=location, code=error.code):
                    if location == "export":
                        model = SyntheticModel(source, export_error=error)
                        module, _, _, engine_calls = self.make_harness(model)
                        operation = lambda: module["exported_input_identity"](source, model)
                    else:
                        model = SyntheticModel(
                            "// synthetic export\n", step_mode="on", step_error=error
                        )
                        module, _, _, engine_calls = self.make_harness(model)
                        operation = lambda: self.identity(module)
                    with self.assertRaises(SyntheticExecutionContractError) as caught:
                        operation()
                    self.assertIs(caught.exception, error)
                    self.assertEqual(engine_calls, [])

    def test_ordinary_cache_failures_keep_both_nonreusable_fallbacks(self):
        hidden = SyntheticExecutionContractError(
            "API_UNSUPPORTED", "hidden prior unknown", details={"execution_state_unknown": True}
        )
        ordinary = hidden_context_from_none(RuntimeError("ordinary input failure"), hidden)
        source = 'model.func("f").set("filename", "synthetic-input.csv");'
        model = SyntheticModel(source, export_error=ordinary)
        module, _, _, _ = self.make_harness(model)
        transformed, bindings = module["exported_input_identity"](source, model)
        self.assertEqual(transformed, source)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(set(bindings[0]), {"unresolved_import"})

        model = SyntheticModel("// synthetic export\n", step_mode="on", step_error=ordinary)
        module, _, _, engine_calls = self.make_harness(model)
        identity = self.identity(module)
        self.assertTrue(identity["cache_policy"].startswith("BYPASS_UNRESOLVED_INITIAL_DEPENDENCY: "))
        self.assertEqual(set(identity["initial_sources"][0]), {"non_reusable_request"})
        self.assertEqual(len(engine_calls), 1)

    def test_optimizer_keeps_nested_unknown_as_unknown_without_cause_details(self):
        module, _, _, _ = self.make_harness()
        inner = SyntheticExecutionContractError(
            "API_UNSUPPORTED", "SENSITIVE_SYNTHETIC_CAUSE_MESSAGE",
            details={"execution_state_unknown": True, "private": "SENSITIVE_SYNTHETIC_DETAIL"},
        )
        outer = raised_from(
            SyntheticExecutionContractError("API_UNSUPPORTED", "safe optimizer wrapper"), inner
        )
        module["execute_case"] = lambda *args, **kwargs: (_ for _ in ()).throw(outer)
        result = module["op_optimization_bounded_run"](object(), "model1", {
            "definition": {"units": {"p": "1"}},
            "study": "study1",
            "parameter_bounds": {"p": [0.0, 1.0]},
            "objective_name": "synthetic_objective",
            "constraints": [{}],
            "max_cases": 1,
            "max_wall_time_s": 1,
        })
        self.assertEqual(result["status"], "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(result["completion_status"], "EXECUTION_STATE_UNKNOWN")
        self.assertEqual(result["stopped_case"]["status"], "UNKNOWN")
        self.assertNotIn("cause_chain", result["stopped_case"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("SENSITIVE_SYNTHETIC_CAUSE_MESSAGE", rendered)
        self.assertNotIn("SENSITIVE_SYNTHETIC_DETAIL", rendered)

    def test_sweep_uses_existing_managed_dispatcher_classification_without_detail_leak(self):
        module, domain, dispatcher, _ = self.make_harness()
        module["model_identity"] = lambda *args, **kwargs: {
            "engine": {"comsol_version": "synthetic"},
            "cache_policy": "REUSE_VERIFIED_CONFIGURATION",
        }
        inner = SyntheticExecutionContractError(
            "API_UNSUPPORTED", "SENSITIVE_SYNTHETIC_CAUSE_MESSAGE",
            details={"execution_state_unknown": True, "private": "SENSITIVE_SYNTHETIC_DETAIL"},
        )
        outer = raised_from(
            SyntheticExecutionContractError("API_UNSUPPORTED", "safe case wrapper"), inner
        )
        module["apply_parameters"] = lambda *args, **kwargs: (_ for _ in ()).throw(outer)
        sweep = module["op_study_sweep_manage"](object(), "model1", {
            "action": "run",
            "definition": {"units": {"p": "1"}, "parameters": {"p": [0.0, 1.0]}},
            "study": "study1",
            "max_cases": 2,
            "max_wall_time_s": 1,
        })
        self.assertEqual(sweep["status"], "EXECUTION_STATE_UNKNOWN")
        self.assertTrue(sweep["execution_state_unknown"])
        self.assertEqual(len(sweep["cases"]), 1)
        self.assertEqual(sweep["cases"][0]["status"], "UNKNOWN")
        self.assertNotIn("cause_chain", sweep["cases"][0])
        self.assertNotIn("details", sweep["cases"][0])

        public_sweep = dispatcher("study.sweep_manage", lambda: sweep, effect="compute")
        self.assertEqual(domain.classify_envelope(public_sweep).state, "unknown")
        self.assertTrue(public_sweep["execution_state_unknown"])
        rendered = json.dumps(public_sweep, sort_keys=True)
        self.assertNotIn("SENSITIVE_SYNTHETIC_CAUSE_MESSAGE", rendered)
        self.assertNotIn("SENSITIVE_SYNTHETIC_DETAIL", rendered)


if __name__ == "__main__":
    unittest.main()
