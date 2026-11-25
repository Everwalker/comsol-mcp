from __future__ import annotations

import ast
import copy
import importlib.machinery
import json
import sqlite3
from pathlib import Path
import sys
from typing import Any
import unittest
import types
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
STORE_SOURCE = (ROOT / "comsol_mcp/_operation_store.py").read_text()
BACKEND_SOURCE = (ROOT / "comsol_mcp/_managed_backend.py").read_text()
STORE_TREE = ast.parse(STORE_SOURCE)
BACKEND_TREE = ast.parse(BACKEND_SOURCE)


class SyntheticContractError(Exception):
    def __init__(self, code, message, *, safe_retry=None, details=None, stage=None):
        super().__init__(message)
        self.code = code
        self.safe_retry = safe_retry
        self.details = details
        self.stage = stage

    def as_dict(self):
        return {
            "code": self.code,
            "message": str(self),
            "safe_retry": self.safe_retry,
            "details": self.details,
            "stage": self.stage,
        }


_SYNTHETIC_PACKAGE = "_t10_public_candidate_synthetic"
_package = types.ModuleType(_SYNTHETIC_PACKAGE)
_package.__path__ = []
_package.__spec__ = importlib.machinery.ModuleSpec(
    _SYNTHETIC_PACKAGE, loader=None, is_package=True,
)
_contracts = types.ModuleType(f"{_SYNTHETIC_PACKAGE}._execution_contract")
_contracts.ExecutionContractError = SyntheticContractError
sys.modules[_SYNTHETIC_PACKAGE] = _package
sys.modules[_contracts.__name__] = _contracts


def class_method(tree: ast.Module, class_name: str, method_name: str):
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    return next(n for n in cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method_name)


def has_call(node: ast.AST, qualified: str) -> bool:
    return any(isinstance(n, ast.Call) and ast.unparse(n.func) == qualified for n in ast.walk(node))


def compile_store_guard(source: str):
    tree = ast.parse(source)
    node = copy.deepcopy(class_method(
        tree, "OperationStore", "_reject_cross_session_project_model_conflicts",
    ))
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = {
        "__name__": _SYNTHETIC_PACKAGE,
        "__package__": _SYNTHETIC_PACKAGE,
        "Any": Any,
        "json": json,
        "ExecutionContractError": SyntheticContractError,
    }
    exec(compile(module, "<public-operation-store-guard>", "exec"), namespace)
    return namespace[node.name]


def persist_guard(node: ast.AST) -> ast.Try:
    return next(n for n in ast.walk(node)
                if isinstance(n, ast.Try) and has_call(n, "self.persist")
                and any(ast.unparse(h.type) == "ExecutionContractError"
                        for h in n.handlers if h.type))


def project_error(handler: ast.ExceptHandler, cause: Exception) -> Exception:
    wrapper = ast.FunctionDef(
        name="project",
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="exc")], vararg=None,
                           kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
        body=copy.deepcopy(handler.body), decorator_list=[],
    )
    for node in ast.walk(wrapper):
        if isinstance(node, ast.Raise) and node.exc is None:
            node.exc = ast.Name(id="exc", ctx=ast.Load())
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace = {"ExecutionContractError": SyntheticContractError}
    exec(compile(module, "<pure-post-worker-projection>", "exec"), namespace)
    try:
        namespace["project"](cause)
    except Exception as projected:
        return projected
    raise AssertionError("projection returned without raising")


class PublicT10CandidateSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            "CREATE TABLE sessions(session_id TEXT PRIMARY KEY, metadata TEXT NOT NULL);"
            "CREATE TABLE revisions(model_key TEXT PRIMARY KEY, metadata TEXT NOT NULL, revision INTEGER);"
        )
        self.addCleanup(self.db.close)
        self.guard = compile_store_guard(STORE_SOURCE)

    @staticmethod
    def model_key(ref):
        return json.dumps(ref, sort_keys=True, separators=(",", ":"))

    def current_snapshot(self, *, address="127.0.0.1", port=2036, project="project-B"):
        ref = {"session_id": "current", "model_tag": "Model1", "revision": 2}
        state = {
            "worker_identity": {
                "observed_peer": {"address": address, "port": port},
                "server_ownership": "shared",
            },
            "models": {"Model1": {"retired": False, "ref": ref}},
        }
        return state, {self.model_key(ref): project}

    def insert_other(self, *, project, address="127.0.0.1", port=2036, retired=False,
                     malformed_models=False):
        ref = {"session_id": "other", "model_tag": "Model1", "revision": 1}
        models = "not-json-object" if malformed_models else {
            "Model1": {"retired": retired, "ref": ref},
        }
        metadata = json.dumps({
            "worker_identity": {"observed_peer": {"address": address, "port": port}},
            "models": models,
        })
        self.db.execute(
            "INSERT INTO sessions(session_id, metadata) VALUES (?, ?)", ("other", metadata),
        )
        self.db.execute(
            "INSERT INTO revisions(model_key, metadata, revision) VALUES (?, ?, ?)",
            (self.model_key(ref), json.dumps({"model_ref": ref, "project_id": project}), 1),
        )

    def test_memory_sqlite_rejects_foreign_project_for_same_peer_tag(self):
        self.insert_other(project="project-A")
        state, bindings = self.current_snapshot()
        with self.assertRaises(SyntheticContractError) as raised:
            self.guard(SimpleNamespace(db=self.db), state, bindings)
        self.assertEqual(raised.exception.code, "PROJECT_IDENTITY_MISMATCH")

    def test_memory_sqlite_allows_same_project_other_peer_and_retired_tag(self):
        cases = (
            {"project": "project-B"},
            {"project": "project-A", "port": 2037},
            {"project": "project-A", "retired": True},
        )
        for options in cases:
            with self.subTest(options=options):
                self.db.execute("DELETE FROM sessions")
                self.db.execute("DELETE FROM revisions")
                self.insert_other(**options)
                state, bindings = self.current_snapshot()
                self.guard(SimpleNamespace(db=self.db), state, bindings)

    def test_memory_sqlite_fails_closed_on_unreadable_peer_lineage(self):
        self.insert_other(project="project-A", malformed_models=True)
        state, bindings = self.current_snapshot()
        with self.assertRaises(SyntheticContractError) as raised:
            self.guard(SimpleNamespace(db=self.db), state, bindings)
        self.assertEqual(raised.exception.code, "PROJECT_IDENTITY_MISMATCH")

    def test_guard_runs_in_snapshot_transaction_before_session_writes(self):
        method = class_method(STORE_TREE, "OperationStore", "persist_runtime_snapshot")
        begin = next(n for n in ast.walk(method)
                     if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.db.execute"
                     and n.args and isinstance(n.args[0], ast.Constant)
                     and n.args[0].value == "BEGIN IMMEDIATE")
        guard = next(n for n in ast.walk(method)
                     if isinstance(n, ast.Call)
                     and ast.unparse(n.func) == "self._reject_cross_session_project_model_conflicts")
        write_loop = next(n for n in ast.walk(method) if isinstance(n, ast.For)
                          and ast.unparse(n.target) == "(table, column, key)"
                          and "sessions" in ast.unparse(n.iter) and "runtimes" in ast.unparse(n.iter))
        self.assertLess(begin.lineno, guard.lineno)
        self.assertLess(guard.lineno, write_loop.lineno)
        transaction_try = next(n for n in ast.walk(method) if isinstance(n, ast.Try)
                               and begin in ast.walk(n) and guard in ast.walk(n))
        self.assertTrue(any(isinstance(h, ast.ExceptHandler) for h in transaction_try.handlers))

    def test_managed_backend_guards_follow_worker_dispatch_and_bindings(self):
        invoke = class_method(BACKEND_TREE, "ManagedBackend", "invoke")
        target = next(n for n in ast.walk(invoke) if isinstance(n, ast.If)
                      and "actual in selections or starter" in ast.unparse(n.test)
                      and "result.get('success') is True" in ast.unparse(n.test))
        condition = ast.unparse(target.test)
        self.assertIn("not ref", condition)
        self.assertIn("self.worker is not None", condition)
        execute = next(n for n in ast.walk(invoke)
                       if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.service.execute_legacy")
        bind = next(n for n in ast.walk(invoke)
                    if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.service.bind_model")
        bind_project = next(n for n in ast.walk(invoke)
                            if isinstance(n, ast.Call) and ast.unparse(n.func) == "self._bind_model_project")
        wrapper = persist_guard(target)
        self.assertLess(execute.lineno, bind.lineno)
        self.assertLess(bind.lineno, bind_project.lineno)
        self.assertLess(bind_project.lineno, wrapper.lineno)
        self.assertEqual(len(wrapper.body), 1)
        self.assertTrue(has_call(wrapper.body[0], "self.persist"))

        adopt = class_method(BACKEND_TREE, "ManagedBackend", "adopt")
        worker_model = next(n for n in ast.walk(adopt)
                           if isinstance(n, ast.Call)
                           and ast.unparse(n.func) == "self.worker.client().model")
        bind_adopt = next(n for n in ast.walk(adopt)
                          if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.service.bind_model")
        bind_project_adopt = next(n for n in ast.walk(adopt)
                                  if isinstance(n, ast.Call)
                                  and ast.unparse(n.func) == "self._bind_model_project")
        adopt_wrapper = persist_guard(adopt)
        self.assertLess(worker_model.lineno, bind_adopt.lineno)
        self.assertLess(bind_adopt.lineno, bind_project_adopt.lineno)
        self.assertLess(bind_project_adopt.lineno, adopt_wrapper.lineno)
        self.assertEqual(len(adopt_wrapper.body), 1)
        self.assertTrue(has_call(adopt_wrapper.body[0], "self.persist"))

    def test_post_worker_projection_is_unknown_no_retry_and_chains_cause(self):
        invoke = class_method(BACKEND_TREE, "ManagedBackend", "invoke")
        target = next(n for n in ast.walk(invoke) if isinstance(n, ast.If)
                      and "actual in selections or starter" in ast.unparse(n.test)
                      and "result.get('success') is True" in ast.unparse(n.test))
        adopt = class_method(BACKEND_TREE, "ManagedBackend", "adopt")
        for node in (target, adopt):
            wrapper = persist_guard(node)
            handler = next(h for h in wrapper.handlers
                           if ast.unparse(h.type) == "ExecutionContractError")
            cause = SyntheticContractError("PROJECT_IDENTITY_MISMATCH", "synthetic conflict")
            projected = project_error(handler, cause)
            self.assertIsInstance(projected, SyntheticContractError)
            self.assertEqual(projected.code, "EXECUTION_STATE_UNKNOWN")
            self.assertEqual(projected.stage, "post_dispatch")
            self.assertIs(projected.safe_retry, False)
            self.assertEqual(projected.details["cause"], cause.as_dict())
            self.assertIs(projected.__cause__, cause)

            other = SyntheticContractError("OTHER_FAILURE", "preserve existing route")
            self.assertIs(project_error(handler, other), other)



if __name__ == "__main__":
    unittest.main()
