"""Pure AST/value regression for external-authority UNKNOWN propagation.

This imports no product package and creates no Worker, job, database, or model.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
import sys
import traceback
from types import ModuleType, SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "comsol_mcp"


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def compile_node(node: ast.AST, namespace: dict[str, Any]) -> Any:
    cloned = ast.fix_missing_locations(node)
    code = compile(ast.Module(body=[cloned], type_ignores=[]), "<source-ast>", "exec")
    exec(code, namespace)
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return namespace[node.name]
    raise TypeError(type(node).__name__)


def function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"missing class {name}")


def class_method(tree: ast.Module, name: str) -> ast.FunctionDef:
    for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        for member in cls.body:
            if isinstance(member, ast.FunctionDef) and member.name == name:
                member.decorator_list = []
                return member
    raise AssertionError(f"missing method {name}")


common: dict[str, Any] = {"Any": Any, "Mapping": Mapping}
contract_tree = parse(SOURCE / "_execution_contract.py")
ExecutionContractError = compile_node(class_node(contract_tree, "ExecutionContractError"), common)
java_tree = parse(SOURCE / "_java_worker.py")
compile_node(function_node(java_tree, "_structured_true"), common)
JavaWorkerError = compile_node(class_node(java_tree, "JavaWorkerError"), common)
engine_tree = parse(SOURCE / "_g2_engine.py")
worker_failure_unknown = compile_node(function_node(engine_tree, "_worker_failure_unknown"), common)
engine_call = compile_node(function_node(engine_tree, "_call"), {
    **common,
    "ExecutionContractError": ExecutionContractError,
    "_active_unknown_dispatch_attempt": lambda: None,
    "_worker_failure_unknown": worker_failure_unknown,
})

probe_package = "authority_probe"
probe_package_module = ModuleType(probe_package)
probe_package_module.__path__ = []
sys.modules[probe_package] = probe_package_module
helper_namespace = {
    **common,
    "ExecutionContractError": ExecutionContractError,
    "__package__": probe_package,
}
managed_tree = parse(SOURCE / "_managed_backend.py")
external_constant_names = {
    "_EXTERNAL_EXECUTING_LEGACY_OPERATIONS",
    "_EXTERNAL_EXECUTING_G3_OPERATIONS",
    "_EXTERNAL_EXECUTION_FUNCTION_TYPES",
    "_EXTERNAL_EVALUATOR_METHODS",
    "_EXTERNAL_G3_READBACK_EVALUATORS",
    "_EXTERNAL_SAFE_GEOMETRY_EVALUATIONS",
    "_EXTERNAL_G3_OPERATION_DOMAINS",
}
external_constant_nodes = [
    node for node in managed_tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id in external_constant_names
            for target in node.targets)
]
assert len(external_constant_nodes) == len(external_constant_names)
exec(compile(ast.fix_missing_locations(ast.Module(body=external_constant_nodes,
                                                  type_ignores=[])),
             "<external-authority-constants>", "exec"), helper_namespace)
g3_ops_stub = ModuleType(f"{probe_package}._g3_ops")
g3_ops_stub.EFFECTS = {
    "result.evaluate": "EVALUATE",
    "study.run": "COMPUTE",
    "geometry.export": "FILE_WRITE",
    "selection.list": "READ",
    "selection.measure": "EVALUATE",
    "selection.query_spatial": "EVALUATE",
    "selection.validate": "EVALUATE",
}
sys.modules[f"{probe_package}._g3_ops"] = g3_ops_stub
external_execution_required = compile_node(
    class_method(managed_tree, "_external_execution_required"), helper_namespace
)
external_function_present = compile_node(
    class_method(managed_tree, "_external_function_present"), helper_namespace
)
unknown_normalizer = compile_node(
    class_method(managed_tree, "_external_inventory_unknown_error"), helper_namespace
)
require_authority = compile_node(
    class_method(managed_tree, "_require_external_execution_authority"), helper_namespace
)

engine_stub = ModuleType(f"{probe_package}._g2_engine")
engine_stub._worker_failure_unknown = worker_failure_unknown
engine_stub._call = lambda target, name, *args: getattr(target, name)(*args)
server_stub = ModuleType(f"{probe_package}._server")
sys.modules[f"{probe_package}._g2_engine"] = engine_stub
g3_common_stub = ModuleType(f"{probe_package}._g3_common")
g3_common_stub.bound_model = lambda worker, _tag: worker.model
g3_common_stub.collection_spec = lambda _name: object()
g3_common_stub.tag_list = lambda collection, _spec: collection.tags()
sys.modules[f"{probe_package}._g3_common"] = g3_common_stub


class Harness:
    _external_execution_required = staticmethod(external_execution_required)
    _external_inventory_unknown_error = staticmethod(unknown_normalizer)
    _require_external_execution_authority = require_authority

    def __init__(self, current_model: Any = None, inventory: Any = None,
                 permissions: Any = (), authorizer: Any = None) -> None:
        self.service = SimpleNamespace(
            ledger=SimpleNamespace(permissions=frozenset(permissions))
        )
        self.project_authorizer = authorizer
        self.current_model = current_model
        self.inventory_calls = 0
        self.inventory = inventory
        self._external_execution_required = lambda *args, **kwargs: True

    def _external_function_present(self, model_tag: str) -> bool:
        self.inventory_calls += 1
        if self.inventory is not None:
            return self.inventory(model_tag)
        return False


def structured_worker_error(*, fields: bool = True) -> BaseException:
    reply: dict[str, Any] = {
        "code": "RPC_FAILED",
        "execution_state_unknown": True,
        "failure": {"code": "RPC_FAILED", "execution_state_unknown": True},
    }
    if fields:
        reply.update({"request_id": "worker-req-17", "transport_stage": "reply_read",
                      "stage": "post_dispatch"})
        reply["failure"].update({"errno": 61})
    return JavaWorkerError("RAW_WORKER_SECRET_SENTINEL", reply=reply)


class RaisingNode:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def tag(self) -> None:
        self.calls += 1
        raise self.error


def install_server_model(model: Any) -> tuple[Any, Any]:
    previous_server = sys.modules.get(f"{probe_package}._server")
    previous_engine = sys.modules.get(f"{probe_package}._g2_engine")
    server_stub.session_server = SimpleNamespace(_current_model=model)
    sys.modules[f"{probe_package}._server"] = server_stub
    sys.modules[f"{probe_package}._g2_engine"] = engine_stub
    return previous_server, previous_engine


def restore_modules(previous: tuple[Any, Any]) -> None:
    for name, module in zip((f"{probe_package}._server", f"{probe_package}._g2_engine"), previous):
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def assert_safe_unknown(error: BaseException, *, request_id: str | None,
                        transport_stage: str | None, errno: int | None,
                        stage: str = "post_dispatch") -> None:
    assert isinstance(error, ExecutionContractError)
    result = error.as_dict()
    assert result["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["safe_retry"] is False
    assert result["stage"] == stage
    assert result["details"].get("request_id") == request_id
    assert result["details"].get("transport_stage") == transport_stage
    assert result["details"].get("errno") == errno
    assert "RAW_WORKER_SECRET_SENTINEL" not in str(result)


def test_real_engine_call_wrapper() -> None:
    worker_error = structured_worker_error()
    node = RaisingNode(worker_error)
    wrapped_error: BaseException | None = None
    try:
        engine_call(node, "tag")
    except ExecutionContractError as error:
        wrapped_error = error
        assert error.code == "ENGINE_CALL_FAILED"
        assert error.__cause__ is worker_error
        assert "RAW_WORKER_SECRET_SENTINEL" in str(error)
    else:
        raise AssertionError("the extracted _g2_engine._call did not wrap the Worker error")

    assert wrapped_error is not None
    normalized = unknown_normalizer("metric.evaluate", wrapped_error)
    assert normalized is not None
    assert normalized.details["cause_code"] == "ENGINE_CALL_FAILED"
    assert_safe_unknown(normalized, request_id="worker-req-17",
                        transport_stage="reply_read", errno=61)

    backend = Harness(inventory=lambda _tag: engine_call(RaisingNode(worker_error), "tag"))
    target_calls = 0
    previous = install_server_model(None)
    try:
        try:
            backend._require_external_execution_authority(
                "metric.evaluate", {}, model_tag="model-1", project_id="project-1"
            )
            target_calls += 1
        except ExecutionContractError as error:
            assert_safe_unknown(error, request_id="worker-req-17",
                                transport_stage="reply_read", errno=61)
            assert error.__suppress_context__ is True
            assert "RAW_WORKER_SECRET_SENTINEL" not in "".join(traceback.format_exception(error))
    finally:
        restore_modules(previous)
    assert backend.inventory_calls == 1
    assert target_calls == 0


def test_deepest_signal_owner_and_cause_cycle() -> None:
    worker_error = structured_worker_error()
    middle = ExecutionContractError(
        "EXECUTION_STATE_UNKNOWN", "middle wrapper",
        details={"request_id": "middle-request", "transport_stage": "middle", "errno": 99},
        stage="validation",
    )
    outer = ExecutionContractError(
        "ENGINE_CALL_FAILED", "outer wrapper",
        details={"request_id": "outer-request", "transport_stage": "outer"},
        stage="pre_dispatch",
    )
    outer.__cause__ = middle
    middle.__cause__ = worker_error
    worker_error.__cause__ = outer

    normalized = unknown_normalizer("model.rebuild_target", outer)
    assert normalized is not None
    assert_safe_unknown(normalized, request_id="worker-req-17",
                        transport_stage="reply_read", errno=61)


def test_missing_worker_identity_is_not_invented() -> None:
    worker_error = structured_worker_error(fields=False)
    outer = ExecutionContractError(
        "ENGINE_CALL_FAILED", "outer wrapper",
        details={"request_id": "unrelated-request", "transport_stage": "wrapper",
                 "errno": 87},
        stage="validation",
    )
    outer.__cause__ = worker_error
    normalized = unknown_normalizer("metric.evaluate", outer)
    assert normalized is not None
    assert_safe_unknown(normalized, request_id=None, transport_stage=None, errno=None)


def test_legacy_current_model_tag_getter_unknown() -> None:
    worker_error = structured_worker_error()

    class RemoteModelValue:
        @property
        def java(self) -> "RemoteModelValue":
            return self

        def __getattr__(self, name: str):
            if name != "tag":
                raise AttributeError(name)

            def call(*_args: Any, **_kwargs: Any) -> Any:
                raise worker_error

            return call

    current_model = RemoteModelValue()
    backend = Harness(current_model=current_model)
    target_calls = 0
    previous = install_server_model(current_model)
    try:
        try:
            backend._require_external_execution_authority(
                "study.run", {}, model_tag=None, project_id="project-1"
            )
            target_calls += 1
        except ExecutionContractError as error:
            assert_safe_unknown(error, request_id="worker-req-17",
                                transport_stage="reply_read", errno=61)
            assert error.__suppress_context__ is True
    finally:
        restore_modules(previous)
    assert backend.inventory_calls == 0
    assert target_calls == 0


def test_double_trusted_code_authority_and_ordinary_model() -> None:
    def denied(backend: Harness) -> None:
        try:
            backend._require_external_execution_authority(
                "metric.evaluate", {}, model_tag="model-1", project_id="project-1"
            )
        except ExecutionContractError as error:
            assert error.code == "PERMISSION_DENIED"
        else:
            raise AssertionError("External model passed without both trusted_code grants")

    denied(Harness(inventory=lambda _tag: True))
    denied(Harness(inventory=lambda _tag: True, permissions={"trusted_code"}))

    project_calls: list[tuple[str, str]] = []

    def project_authorizer(project_id: str, permission: str) -> None:
        project_calls.append((project_id, permission))

    project_only = Harness(inventory=lambda _tag: True, authorizer=project_authorizer)
    denied(project_only)
    assert project_calls == []

    both = Harness(permissions={"trusted_code"}, authorizer=project_authorizer,
                   inventory=lambda _tag: (_ for _ in ()).throw(
                       AssertionError("authorized route should not inventory")))
    both._require_external_execution_authority(
        "metric.evaluate", {}, model_tag="model-1", project_id="project-1"
    )
    assert project_calls == [("project-1", "trusted_code")]
    assert both.inventory_calls == 0

    ordinary = Harness(inventory=lambda _tag: False)
    ordinary._require_external_execution_authority(
        "study.run", {}, model_tag="model-1", project_id="project-1"
    )
    assert ordinary.inventory_calls == 1


def test_inventory_detects_external_and_matlab_functions() -> None:
    assert helper_namespace["_EXTERNAL_EXECUTION_FUNCTION_TYPES"] == frozenset(
        {"External", "MATLAB"}
    )

    class FunctionNode:
        def __init__(self, type_id: str) -> None:
            self.type_id = type_id

        def getType(self) -> str:
            return self.type_id

    class FunctionCollection:
        def __init__(self, types: dict[str, str]) -> None:
            self.types = types

        def tags(self) -> list[str]:
            return list(self.types)

        def function(self, tag: str) -> FunctionNode:
            return FunctionNode(self.types[tag])

    class Component:
        def __init__(self, types: dict[str, str]) -> None:
            self.functions = FunctionCollection(types)

        def func(self, tag: str | None = None):
            return self.functions if tag is None else self.functions.function(tag)

    class ComponentCollection:
        def tags(self) -> list[str]:
            return ["component-1"]

    class Model:
        def __init__(self, model_types: dict[str, str], component_types: dict[str, str]) -> None:
            self.functions = FunctionCollection(model_types)
            self.component_value = Component(component_types)

        def func(self, tag: str | None = None):
            return self.functions if tag is None else self.functions.function(tag)

        def component(self, tag: str | None = None):
            return ComponentCollection() if tag is None else self.component_value

    class Worker:
        def __init__(self, model: Model) -> None:
            self.model = model

    for model_types, component_types, expected in [
        ({"external-1": "External"}, {}, True),
        ({}, {"matlab-1": "MATLAB"}, True),
        ({"analytic-1": "Analytic"}, {}, False),
    ]:
        backend = SimpleNamespace(worker=Worker(Model(model_types, component_types)))
        assert external_function_present(backend, "model-1") is expected


def test_protected_and_unprotected_route_classification() -> None:
    legacy = helper_namespace["_EXTERNAL_EXECUTING_LEGACY_OPERATIONS"]
    for operation in legacy:
        assert external_execution_required(operation, {}) is True

    protected = [
        ("metric.evaluate", {}),
        ("model.rebuild_target", {}),
        ("api.invoke", {"method": "evaluate"}),
        ("api.invoke", {"method": "evaluateComplex"}),
        ("api.invoke", {"method": "evaluateUnit"}),
        ("api.invoke", {"method": "getNode"}, "COMPUTE"),
        ("plot.render", {}),
        ("result.field_export", {}),
        ("parameter.list", {}),
        ("parameter.get", {}),
        ("parameter.set", {}),
        ("variable.list", {}),
        ("variable.get", {}),
        ("result.numerical_manage", {"action": "run"}),
        ("result.numerical_manage", {"action": "evaluate"}),
        ("result.evaluate", {}),
        ("study.run", {}),
    ]
    for case in protected:
        operation, arguments = case[:2]
        resolved_effect = case[2] if len(case) == 3 else None
        assert external_execution_required(
            operation, arguments, resolved_effect=resolved_effect
        ) is True, (operation, arguments, resolved_effect)

    unprotected = [
        ("selection.measure", {}),
        ("selection.query_spatial", {}),
        ("selection.validate", {}),
        ("geometry.export", {}),  # retained T38 FILE_WRITE path
        ("selection.list", {}),
        ("api.invoke", {"method": "getNode"}, "INSPECT"),
        ("result.numerical_manage", {"action": "list"}),
        ("code.execute_java", {}),  # still governed by its existing effect policy
    ]
    for case in unprotected:
        operation, arguments = case[:2]
        resolved_effect = case[2] if len(case) == 3 else None
        assert external_execution_required(
            operation, arguments, resolved_effect=resolved_effect
        ) is False, (operation, arguments, resolved_effect)


def run() -> None:
    test_real_engine_call_wrapper()
    test_deepest_signal_owner_and_cause_cycle()
    test_missing_worker_identity_is_not_invented()
    test_legacy_current_model_tag_getter_unknown()
    test_double_trusted_code_authority_and_ordinary_model()
    test_inventory_detects_external_and_matlab_functions()
    test_protected_and_unprotected_route_classification()
    print("PASS: T19 authority, route coverage, and UNKNOWN propagation (pure AST/value)")


if __name__ == "__main__":
    run()
