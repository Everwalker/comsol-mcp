from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from comsol_mcp import _g3_ops
from comsol_mcp._domain_outcome import STATE_FAILED, STATE_SUCCEEDED, classify
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g3_w13 import function_evaluate
from comsol_mcp._java_worker import JavaWorkerError, JavaWorkerTimeout, RemoteJava, RemoteModel
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._mcp_gateway import mcp_result
from comsol_mcp._operation_store import OperationStore


class FunctionNode:
    def __init__(self, type_id: str, names: list[str], properties: Mapping[str, tuple[str, Any]], *, fail_reads=()):
        self.type_id = type_id
        self.names = list(names)
        self.properties = dict(properties)
        self.fail_reads = set(fail_reads)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.mutations = 0

    def getType(self):
        self.calls.append(("getType", ()))
        return self.type_id

    def functionNames(self):
        self.calls.append(("functionNames", ()))
        return list(self.names)

    def hasProperty(self, name):
        self.calls.append(("hasProperty", (name,)))
        return name in self.properties

    def getValueType(self, name):
        self.calls.append(("getValueType", (name,)))
        return self.properties[name][0]

    def _read(self, getter, name):
        self.calls.append((getter, (name,)))
        if getter in self.fail_reads:
            raise RuntimeError(f"{getter} unavailable")
        return self.properties[name][1]

    def getString(self, name):
        return self._read("getString", name)

    def getStringArray(self, name):
        return self._read("getStringArray", name)

    def getInt(self, name):
        return self._read("getInt", name)

    def getDoubleMatrix(self, name):
        return self._read("getDoubleMatrix", name)

    def getStringMatrix(self, name):
        return self._read("getStringMatrix", name)

    def set(self, *args):
        self.mutations += 1
        raise AssertionError("function.evaluate must not mutate a function node")

    def importData(self, *args):
        self.mutations += 1
        raise AssertionError("function.evaluate must not import function data")

    def refresh(self, *args):
        self.mutations += 1
        raise AssertionError("function.evaluate must not refresh function data")

    def run(self, *args):
        self.mutations += 1
        raise AssertionError("function.evaluate must not run or train a function")

    def unverifiedMethod(self):
        return None


class ParamNode:
    def __init__(self, *, values=None, failures=(), unit_failures=()):
        self.values = dict(values or {})
        self.failures = set(failures)
        self.unit_failures = set(unit_failures)
        self.complex_calls: list[str] = []
        self.unit_calls: list[str] = []
        self.other_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.mutations = 0

    def evaluateComplex(self, expression):
        self.complex_calls.append(expression)
        if expression in self.failures:
            raise RuntimeError("evaluation failed")
        return self.values.get(expression, [2.5, -1.25])

    def evaluateUnit(self, expression):
        self.unit_calls.append(expression)
        if expression in self.unit_failures:
            raise RuntimeError("unit evaluation failed")
        if expression.startswith("1[") and expression.endswith("]"):
            return expression[2:-1]
        return "W"

    def evaluate(self, *args):
        self.other_calls.append(("evaluate", args))
        raise AssertionError("real-valued fallback is forbidden")

    def set(self, *args):
        self.mutations += 1
        raise AssertionError("param() must not be mutated")


class WorkerOutcomeParamNode(ParamNode):
    def __init__(self, outcome):
        super().__init__()
        self.outcome = outcome

    def evaluateComplex(self, expression):
        self.complex_calls.append(expression)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class ComponentNode:
    def __init__(self, functions):
        self.functions = dict(functions)

    def func(self, tag):
        return self.functions[tag]


class ModelNode:
    def __init__(self, global_functions=None, component_functions=None, param=None):
        self.global_functions = dict(global_functions or {})
        self.components = {"comp1": ComponentNode(component_functions or {})}
        self.param_node = param or ParamNode()

    def func(self, tag):
        return self.global_functions[tag]

    def component(self, tag):
        return self.components[tag]

    def param(self):
        return self.param_node


class Client:
    def __init__(self, model):
        self.model_node = model

    def model(self, tag):
        assert tag == "Model"
        return self.model_node


class Worker:
    def __init__(self, model):
        self.client_node = Client(model)

    def client(self):
        return self.client_node


class JavaHandleTestWorker:
    """Exercise production RemoteJava._call dispatch and its witness funnel."""

    def __init__(self, model, *, inject_method=None, on_dispatch=None):
        self.generation = 1
        self.objects = {"h-model": model}
        self.handle_by_identity = {id(model): "h-model"}
        self.dispatches = []
        self.inject_method = inject_method
        self.on_dispatch = on_dispatch

    def client(self):
        return self

    def model(self, tag):
        assert tag == "Model"
        return RemoteModel(self, "h-model", self.generation, "Model")

    def submit(self, kind, payload, **_kwargs):
        assert kind == "call"
        method = payload["method"]
        args = payload["args"]
        self.dispatches.append({"method": method, "args": list(args), "handle": payload["handle"]})
        receiver = self.objects[payload["handle"]]
        result = getattr(receiver, method)(*args)
        if self.on_dispatch is not None:
            self.on_dispatch(method)
        if method == "functionNames" and self.inject_method is not None:
            injected, self.inject_method = self.inject_method, None
            RemoteJava(self, payload["handle"], self.generation, "FunctionFeature").__getattr__(injected)()
        if isinstance(result, (ModelNode, ComponentNode, FunctionNode, ParamNode)):
            handle = self.handle_by_identity.get(id(result))
            if handle is None:
                handle = f"h-{len(self.objects)}"
                self.objects[handle] = result
                self.handle_by_identity[id(result)] = handle
            result = {"$worker_handle": handle, "generation": self.generation,
                      "java_type": type(result).__name__}
        return {"ok": True, "status": "SUCCEEDED", "result": result}


def analytic(*, name="actual_f", args=("x",), argunit=("m",), extra=None):
    props = {
        "args": ("StringArray", list(args)),
        "argunit": ("StringArray", list(argunit)),
    }
    props.update(extra or {})
    return FunctionNode("Analytic", [name], props)


def interpolation(*, name="table_f", arity=1, argunit=("m",), extra=None):
    props = {
        "nargs": ("Int", arity),
        "source": ("String", "table"),
        "argunit": ("StringArray", list(argunit)),
        "argrange": ("DoubleMatrix", [[0.0, 1.0]]),
        "extrap": ("String", "none"),
    }
    props.update(extra or {})
    return FunctionNode("Interpolation", [name], props)


def path(tag="f1", component=None):
    segments = []
    if component is not None:
        segments.append({"collection": "component", "tag": component})
    segments.append({"collection": "func", "tag": tag})
    return {"segments": segments}


def run(node, payload, *, param=None, component=None, tag="f1"):
    model = ModelNode(
        global_functions={} if component else {tag: node},
        component_functions={tag: node} if component else {},
        param=param,
    )
    return function_evaluate(Worker(model), "Model", {"path": path(tag, component), **payload}), model


def test_unary_complex_sampling_uses_actual_name_argunit_and_reports_unit():
    node = analytic()
    param = ParamNode(values={"root.actual_f(1.25[m])": [3.0, -4.0]})
    result, model = run(node, {"arguments": [{"value": 1.25}]}, param=param)

    row = result["results"][0]
    assert result["schema_version"] == "comsol-mcp.function-evaluate/1.0.0"
    assert result["status"] == "OBSERVED" and result["ok"] is True
    assert result["sample_completion"] == "SUCCEEDED"
    assert row["expression"] == "root.actual_f(1.25[m])"
    assert row["argument_units"] == ["m"]
    assert row["argument_units_source"] == "function_argunit_readback"
    assert row["value"] == {"real": 3.0, "imag": -4.0}
    assert row["unit"] == "W" and row["unit_status"] == "OK"
    assert row["range_status"] == "UNKNOWN"
    outcome = classify("function.evaluate", result)
    assert outcome.state == STATE_SUCCEEDED
    assert outcome.envelope_fields()["success"] is True
    assert "1[m]" in param.unit_calls
    assert node.mutations == model.param_node.mutations == 0
    assert param.other_calls == []


def test_component_scope_is_bound_from_resolved_path_not_global_shadow():
    global_node = analytic(name="shared")
    local_node = analytic(name="shared")
    param = ParamNode()
    model = ModelNode(global_functions={"global_tag": global_node}, component_functions={"local_tag": local_node}, param=param)
    worker = Worker(model)

    global_result = function_evaluate(worker, "Model", {"path": path("global_tag"), "arguments": [{"value": 1, "unit": ""}]})
    local_result = function_evaluate(worker, "Model", {"path": path("local_tag", "comp1"), "arguments": [{"value": 1, "unit": ""}]})

    assert global_result["results"][0]["expression"] == "root.shared(1.0)"
    assert local_result["results"][0]["expression"] == "root.comp1.shared(1.0)"
    assert param.complex_calls == ["root.shared(1.0)", "root.comp1.shared(1.0)"]


def test_multivariate_uses_per_argument_units_and_rejects_scalar_unit():
    node = analytic(args=("x", "y"), argunit=("m", "s"))
    result, _ = run(node, {"arguments": [{"coordinate": [2, 3], "units": ["m", "s"]}]})
    assert result["results"][0]["expression"] == "root.actual_f(2.0[m],3.0[s])"
    assert result["results"][0]["argument_units_source"] == "sample.units"

    with pytest.raises(ExecutionContractError) as error:
        run(node, {"arguments": [{"coordinate": [2, 3], "unit": "m"}]})
    assert error.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("unit", ["m^10", "m^+10", "m^-12"])
def test_unit_integer_exponents_use_longest_numeric_token_match(unit):
    result, _ = run(analytic(argunit=("",)), {"arguments": [{"value": 1, "unit": unit}]})
    assert result["results"][0]["argument_units"] == [unit]
    assert result["results"][0]["expression"] == f"root.actual_f(1.0[{unit}])"


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_request_id"),
    [("explicit_unknown", "FAILED", "function-evaluate-worker-unknown"),
     ("unknown_code", "FAILED", "function-evaluate-worker-unknown-code"),
     ("timeout", None, None),
     ("pending_reply", "RUNNING", "function-evaluate-worker-pending")],
)
def test_function_evaluate_preserves_unobserved_worker_failures(outcome, expected_status, expected_request_id):
    if outcome in {"explicit_unknown", "unknown_code"}:
        failure_detail = {"code": "EXECUTION_STATE_UNKNOWN", "message": "call outcome unresolved"}
        if outcome == "explicit_unknown":
            failure_detail["execution_state_unknown"] = True
        failure = JavaWorkerError(
            "worker reported unresolved call",
            reply={"ok": False, "status": "FAILED", "request_id": expected_request_id,
                   "failure": failure_detail},
        )
    elif outcome == "timeout":
        failure = JavaWorkerTimeout("RPC timeout; original request may still be running")
    else:
        failure = {"ok": True, "status": expected_status, "request_id": expected_request_id}

    param = WorkerOutcomeParamNode(failure)
    with pytest.raises(ExecutionContractError) as error:
        run(analytic(argunit=("",)), {"arguments": [{"value": 1, "unit": ""}]}, param=param)

    assert error.value.code == "EXECUTION_STATE_UNKNOWN"
    assert param.complex_calls == ["root.actual_f(1.0)"]
    assert param.unit_calls == []  # no output-unit read follows an unobserved value call
    if expected_status is not None:
        assert error.value.details["worker_status"] == expected_status
    if expected_request_id is not None:
        assert error.value.details["worker_request_id"] == expected_request_id
    if outcome == "timeout":
        assert error.value.details["worker_reason"] == "rpc_timeout_may_still_be_running"


def test_completed_worker_evaluation_error_is_a_sample_failure():
    failure = JavaWorkerError(
        "COMSOL rejected the function expression",
        reply={"ok": False, "status": "FAILED", "request_id": "function-evaluate-complete-failure",
               "failure": {"code": "FUNCTION_EVALUATION_ERROR", "message": "invalid function expression"}},
    )
    param = WorkerOutcomeParamNode(failure)
    result, _ = run(analytic(argunit=("",)), {"arguments": [{"value": 1, "unit": ""}]}, param=param)

    assert result["status"] == "FAILED"
    assert result["sample_completion"] == "FAILED"
    assert result["execution_state_unknown"] is False
    assert result["results"][0]["value_status"] == "EVALUATION_ERROR"
    assert result["results"][0]["errors"][0]["code"] == "FUNCTION_EVALUATION_ERROR"


def test_coordinate_value_and_unit_fields_are_strictly_exclusive_and_finite():
    node = analytic()
    invalid_payloads = [
        {"coordinate": [1], "value": 1},
        {"coordinate": [float("nan")]},
        {"coordinate": [float("inf")]},
        {"coordinate": [True]},
        {"coordinate": [10**1000]},
        {"value": "1+sin(2)"},
        {"value": 1, "unit": "m]+root.other(1)[m"},
        {"value": 1, "unit": "m;run"},
    ]
    for sample in invalid_payloads:
        param = ParamNode()
        with pytest.raises(ExecutionContractError) as error:
            run(node, {"arguments": [sample]}, param=param)
        assert error.value.code == "INVALID_REQUEST"
        assert param.complex_calls == []


def test_multiple_function_names_fail_as_ambiguous_without_evaluation():
    node = FunctionNode("Interpolation", ["first", "second"], {"nargs": ("Int", 1)})
    param = ParamNode()
    with pytest.raises(ExecutionContractError) as error:
        run(node, {"arguments": [{"value": 1, "unit": ""}]}, param=param)
    assert error.value.code == "AMBIGUOUS_FUNCTION"
    assert param.complex_calls == []


def test_zero_function_names_are_not_misreported_as_ambiguity():
    node = FunctionNode("Analytic", [], {"args": ("StringArray", ["x"])})
    param = ParamNode()
    with pytest.raises(ExecutionContractError) as error:
        run(node, {"arguments": [{"value": 1, "unit": ""}]}, param=param)
    assert error.value.code == "API_UNSUPPORTED"
    assert param.complex_calls == []


def test_function_name_is_metadata_only_and_top_level_override_is_rejected():
    node = analytic()
    param = ParamNode()
    with pytest.raises(ExecutionContractError) as error:
        function_evaluate(Worker(ModelNode(global_functions={"f1": node}, param=param)), "Model", {
            "path": path(), "arguments": [{"value": 1, "unit": ""}], "function_name": "root.injected(1)"
        })
    assert error.value.code == "INVALID_REQUEST"
    assert param.complex_calls == []

    unsafe = FunctionNode("Analytic", ["f(x)"], {"args": ("StringArray", ["x"])})
    with pytest.raises(ExecutionContractError) as error:
        run(unsafe, {"arguments": [{"value": 1, "unit": ""}]}, param=param)
    assert error.value.code == "API_UNSUPPORTED"
    assert param.complex_calls == []


def test_table_interpolation_uses_only_readback_argunit_when_nargs_is_absent():
    node = interpolation()
    node.properties.pop("nargs")
    param = ParamNode(values={"root.table_f(0.5[m])": [5.0, 0.0]})
    result, _ = run(node, {"arguments": [{"value": 0.5}]}, param=param)

    assert result["arity"] == 1
    assert result["results"][0]["expression"] == "root.table_f(0.5[m])"
    assert result["results"][0]["value"] == {"real": 5.0, "imag": 0.0}
    assert param.complex_calls == ["root.table_f(0.5[m])"]


def test_interpolation_missing_nargs_refuses_unknown_source_or_nonunary_table_metadata():
    unknown_source = interpolation(extra={"source": ("String", "function")})
    unknown_source.properties.pop("nargs")
    with pytest.raises(ExecutionContractError) as error:
        run(unknown_source, {"arguments": [{"value": 0.5, "unit": "m"}]})
    assert error.value.code == "API_UNSUPPORTED"
    assert "source=table fallback" in str(error.value)
    assert error.value.stage == "validation"

    nonunary_table = interpolation(argunit=("m", "s"))
    nonunary_table.properties.pop("nargs")
    with pytest.raises(ExecutionContractError) as error:
        run(nonunary_table, {"arguments": [{"coordinate": [0.5, 1.0], "units": ["m", "s"]}]})
    assert error.value.code == "API_UNSUPPORTED"
    assert "exactly one readable StringArray argunit" in str(error.value)


def test_interpolation_multiname_is_rejected_before_arity_fallback_or_evaluation():
    node = FunctionNode("Interpolation", ["first", "second"], {
        "source": ("String", "table"), "argunit": ("StringArray", ["m"]),
    })
    param = ParamNode()
    with pytest.raises(ExecutionContractError) as error:
        run(node, {"arguments": [{"value": 0.5}]}, param=param)
    assert error.value.code == "AMBIGUOUS_FUNCTION"
    assert error.value.stage == "validation"
    assert param.complex_calls == []


def test_interpolation_range_is_raw_evidence_only_and_never_claims_domain():
    node = interpolation(extra={"argrange": ("DoubleMatrix", [[0.0, 1.0]]), "extrap": ("String", "linear")})
    result, _ = run(node, {"arguments": [{"value": 2, "unit": ""}]})
    assert result["range_status"] == "UNKNOWN"
    assert result["results"][0]["range_status"] == "UNKNOWN"
    assert result["range_evidence"]["status"] == "RAW_METADATA_NOT_NATIVELY_VERIFIED"
    assert result["range_evidence"]["properties"]["extrap"]["value"] == "linear"


def test_failed_complex_sample_is_a_failed_operation_with_partial_sample_completion():
    node = analytic(argunit=("",))
    failed_expression = "root.actual_f(2.0)"
    param = ParamNode(failures={failed_expression})
    result, _ = run(node, {"arguments": [{"value": 1}, {"value": 2}]}, param=param)

    assert result["status"] == "FAILED" and result["ok"] is False
    assert result["sample_completion"] == "PARTIAL_FAILURE"
    assert result["partial_change"] is False
    assert result["results"][0]["value"] == {"real": 2.5, "imag": -1.25}
    assert result["results"][1]["value_status"] == "EVALUATION_ERROR"
    assert result["results"][1]["range_status"] == "UNKNOWN"
    assert result["sample_failure_count"] == 1
    assert param.complex_calls == ["root.actual_f(1.0)", failed_expression]
    assert param.other_calls == []
    assert failed_expression in param.unit_calls  # output-unit read is independent of value failure
    outcome = classify("function.evaluate", result)
    assert outcome.state == STATE_FAILED and outcome.success is False
    assert outcome.status_token == "FAILED"
    assert outcome.envelope_fields()["success"] is False
    assert len(result["results"]) == 2


def test_all_failed_samples_report_failed_and_public_envelope_remains_failure():
    expression = "root.actual_f(2.0)"
    node = analytic(argunit=("",))
    param = ParamNode(failures={expression})
    result, _ = run(node, {"arguments": [{"value": 2}]}, param=param)

    assert result["status"] == "FAILED"
    assert result["sample_completion"] == "FAILED"
    assert result["successful_count"] == 0
    assert result["sample_failure_count"] == 1
    assert result["results"][0]["value_status"] == "EVALUATION_ERROR"
    outcome = classify("function.evaluate", result)
    assert outcome.state == STATE_FAILED
    assert outcome.envelope_fields()["success"] is False


def test_invalid_complex_pair_is_an_evaluation_error_not_coerced():
    node = analytic(argunit=("",))
    param = ParamNode(values={"root.actual_f(1.0)": [7.0]})
    result, _ = run(node, {"arguments": [{"value": 1}]}, param=param)
    row = result["results"][0]
    assert row["value_status"] == "EVALUATION_ERROR"
    assert row["value"] is None
    assert row["errors"][0]["code"] == "INVALID_COMPLEX_RESULT"


@pytest.mark.parametrize("derivative", [{"order": 1, "argument_index": 0}, {"order": 2, "argument_index": 0}])
def test_valid_derivative_shape_remains_explicitly_unsupported(derivative):
    node = analytic(argunit=("",))
    param = ParamNode()
    with pytest.raises(ExecutionContractError) as error:
        run(node, {"arguments": [{"value": 1}], "derivative": derivative}, param=param)
    assert error.value.code == "API_UNSUPPORTED"
    assert "derivative order" in str(error.value) and "unsupported" in str(error.value)
    assert param.complex_calls == []


def test_output_unit_failure_keeps_complex_value_and_marks_single_sample_failed():
    expression = "root.actual_f(1.0)"
    node = analytic(argunit=("",))
    param = ParamNode(unit_failures={expression})
    result, _ = run(node, {"arguments": [{"value": 1}]}, param=param)
    row = result["results"][0]
    assert row["value_status"] == "OK"
    assert row["value"] == {"real": 2.5, "imag": -1.25}
    assert row["unit_status"] == "EVALUATION_ERROR"
    assert result["status"] == "FAILED"


def test_function_with_unknown_or_unreadable_argunit_fails_closed_when_units_omitted():
    node = analytic(argunit=("m",))
    node.properties["argunit"] = ("StringArray", None)
    param = ParamNode()
    with pytest.raises(ExecutionContractError) as error:
        run(node, {"arguments": [{"value": 1}]}, param=param)
    assert error.value.code == "API_UNSUPPORTED"
    assert "argunit readback" in str(error.value)
    assert param.complex_calls == []


class SnapshotAdapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": 0, "fingerprint": "stable-model-fingerprint"}


def execution_service(tmp_path):
    service = ExecutionService(SessionLedger("session", "server"), SnapshotAdapter(), project_root=tmp_path)
    bound = service.bind_model("Model")
    return service, model_ref_from_mapping(bound["execution"]["model_ref"])


def ledger_state(service, model_ref):
    state = service.ledger._state_for(model_ref)
    return state.revision, state.dirty, state.fingerprint


def function_evaluate_failure_batch(*, all_failed: bool):
    node = analytic(argunit=("",))
    failures = {"root.actual_f(2.0)"}
    if all_failed:
        samples = [{"value": 2}]
    else:
        samples = [{"value": 1}, {"value": 2}]
    param = ParamNode(failures=failures)
    result, _ = run(node, {"arguments": samples}, param=param)
    return result


@pytest.mark.parametrize(
    ("all_failed", "completion", "successful_count", "row_count"),
    [(False, "PARTIAL_FAILURE", 1, 2), (True, "FAILED", 0, 1)],
)
def test_execution_service_read_effect_keeps_failed_sample_batches_and_ledger_clean(
    tmp_path, all_failed, completion, successful_count, row_count
):
    data = function_evaluate_failure_batch(all_failed=all_failed)
    service, model_ref = execution_service(tmp_path)
    before = ledger_state(service, model_ref)

    result = service.execute_legacy(
        "get_parameters",
        lambda _args: {"success": data["ok"], "data": data},
        {}, model_ref=model_ref,
    )

    assert result["success"] is False
    assert result["data"]["status"] == "FAILED"
    assert result["data"]["sample_completion"] == completion
    assert result["data"]["successful_count"] == successful_count
    assert len(result["data"]["results"]) == row_count
    if not all_failed:
        assert result["data"]["results"][0]["value_status"] == "OK"
        assert result["data"]["results"][1]["value_status"] == "EVALUATION_ERROR"
    assert mcp_result(result).isError is True
    assert ledger_state(service, model_ref) == before


def test_g3_function_evaluate_route_preserves_mixed_rows_and_clean_failed_outcome(tmp_path, monkeypatch):
    data = function_evaluate_failure_batch(all_failed=False)
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=object(), registry={}, project_root=tmp_path,
    )
    monkeypatch.setitem(_g3_ops.DISPATCH, "function.evaluate", lambda _worker, _tag, _body: data)
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before = ledger_state(service, model_ref)

    result = backend._invoke_g3_model(
        "function.evaluate", model_ref, {"arguments": [{"value": 1}, {"value": 2}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-mixed"},
        "function-evaluate-mixed", "session",
    )

    assert result["success"] is False
    assert result["data"]["status"] == "FAILED"
    assert result["data"]["sample_completion"] == "PARTIAL_FAILURE"
    assert result["data"]["successful_count"] == 1
    assert [row["value_status"] for row in result["data"]["results"]] == ["OK", "EVALUATION_ERROR"]
    assert mcp_result(result).isError is True
    assert ledger_state(service, model_ref) == before
    backend.close()


def test_missing_interpolation_arity_is_a_read_only_validation_refusal(tmp_path, monkeypatch):
    node = interpolation(extra={"source": ("String", "function")})
    node.properties.pop("nargs")
    worker = Worker(ModelNode(global_functions={"f1": node}))
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before = ledger_state(service, model_ref)

    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"value": 0.5, "unit": "m"}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-no-arity"},
        "function-evaluate-no-arity", "session",
    )

    assert result["success"] is False
    assert result["error"]["code"] == "API_UNSUPPORTED"
    assert result["error"]["stage"] == "validation"
    assert result["data"]["status"] == "REFUSED"
    assert result["data"]["witness"]["mutation_issued"] is False
    assert ledger_state(service, model_ref) == before
    assert node.calls[:3] == [("getType", ()), ("functionNames", ()), ("hasProperty", ("nargs",))]
    backend.close()


def test_unsupported_derivative_is_a_real_remote_validation_refusal(tmp_path, monkeypatch):
    node = analytic(name="q", args=("x", "y"), argunit=("1", "1"))
    worker = JavaHandleTestWorker(ModelNode(global_functions={"f1": node}, param=ParamNode()))
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before = ledger_state(service, model_ref)

    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"coordinate": [2, 3], "units": ["1", "1"]}],
         "derivative": {"order": 1, "argument_index": 0}},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-derivative-refusal"},
        "function-evaluate-derivative-refusal", "session",
    )

    assert result["success"] is False
    assert result["error"]["code"] == "API_UNSUPPORTED"
    assert result["error"]["stage"] == "validation"
    assert result["data"]["status"] == "REFUSED"
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["partial_change"] is False
    assert result["execution"]["revision"] == before[0], result["data"].get("domain_outcome")
    assert result["execution"]["dirty"] is False
    assert ledger_state(service, model_ref) == before
    witness = result["data"]["witness"]
    assert witness["mutation_issued"] is False
    assert [row["method"] for row in worker.dispatches] == [
        "func", "getType", "functionNames", "hasProperty", "getValueType", "getStringArray",
    ]
    assert all(row["is_mutation"] is False for row in witness["dispatches"])
    backend.close()


def test_full_remote_java_handle_sample_is_observed_without_revision_change(tmp_path, monkeypatch):
    node = analytic(argunit=("1",))
    param = ParamNode(values={"root.actual_f(1.0)": [7.0, -2.0]})
    model = ModelNode(global_functions={"f1": node}, param=param)
    worker = JavaHandleTestWorker(model)
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before = ledger_state(service, model_ref)

    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"coordinate": [1], "units": ["1"]}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-java-handles"},
        "function-evaluate-java-handles", "session",
    )

    assert result["success"] is True, result.get("error")
    assert result["data"]["status"] == "OBSERVED"
    assert result["data"]["results"][0]["value"] == {"real": 7.0, "imag": -2.0}
    assert result["execution"]["revision"] == before[0], result["data"].get("domain_outcome")
    assert result["execution"]["dirty"] is False
    assert ledger_state(service, model_ref) == before
    witness = result["data"]["domain_outcome"]["witness"]
    assert witness["mutation_issued"] is False
    assert [row["method"] for row in worker.dispatches] == [
        "func", "getType", "functionNames", "hasProperty", "getValueType",
        "getStringArray", "param", "evaluateComplex", "evaluateUnit",
    ]
    assert all(row["is_mutation"] is False for row in witness["dispatches"])
    backend.close()


@pytest.mark.parametrize(
    ("matrix_type", "matrix_value", "matrix_getter"),
    [
        ("DoubleMatrix", [[0.0, 1.0]], "getDoubleMatrix"),
        ("StringMatrix", [["0", "1"]], "getStringMatrix"),
    ],
)
def test_table_interpolation_readback_fallback_survives_managed_java_route(
    tmp_path, monkeypatch, matrix_type, matrix_value, matrix_getter,
):
    node = interpolation()
    node.properties.pop("nargs")
    node.properties["argrange"] = (matrix_type, matrix_value)
    param = ParamNode(values={"root.table_f(0.5[m])": [5.0, 0.0]})
    worker = JavaHandleTestWorker(ModelNode(global_functions={"f1": node}, param=param))
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before = ledger_state(service, model_ref)

    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"value": 0.5}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-table-arity-fallback"},
        "function-evaluate-table-arity-fallback", "session",
    )

    assert result["success"] is True, result.get("error")
    assert result["data"]["arity"] == 1
    assert result["data"]["results"][0]["expression"] == "root.table_f(0.5[m])"
    assert result["data"]["results"][0]["value"] == {"real": 5.0, "imag": 0.0}
    assert result["execution"]["revision"] == before[0], result["data"].get("domain_outcome")
    assert result["execution"]["dirty"] is False
    assert ledger_state(service, model_ref) == before
    witness = result["data"]["domain_outcome"]["witness"]
    assert witness["mutation_issued"] is False
    assert matrix_getter in witness["methods"]
    assert any(
        row["method"] == matrix_getter and row["args_count"] == 1 and row["is_mutation"] is False
        for row in witness["dispatches"]
    )
    assert all(row["is_mutation"] is False for row in witness["dispatches"])
    backend.close()


def test_completed_interpolation_range_error_is_failed_without_ledger_mutation(tmp_path, monkeypatch):
    node = interpolation()
    node.properties["argrange"] = ("StringMatrix", [["0", "1"]])
    failure = JavaWorkerError(
        "COMSOL rejected an out-of-range interpolation argument",
        reply={
            "ok": False,
            "status": "FAILED",
            "request_id": "function-evaluate-interpolation-range-error",
            "failure": {
                "code": "FUNCTION_EVALUATION_ERROR",
                "message": "ModelParam.evaluateComplex(String) completed with a recognized interpolation range error",
                "classification": "COMSOL_INTERPOLATION_RANGE",
                "terminal_sample_error": True,
                "execution_state_unknown": False,
                "post_dispatch": True,
                "serialization_failed": False,
                "read_only_source": "com.comsol.model.ModelParam.evaluateComplex(String)",
                "method": "evaluateComplex",
                "target_contract": "com.comsol.model.ModelParam",
                "target_implementation_type": "com.comsol.model.impl.ModelParamImpl",
                "exception_type": "com.comsol.util.exceptions.FlException",
                "error_tag": "Interpolation_function_is_out_of_range",
                "exception_message": "Interpolation_function_is_out_of_range",
                "native_request_id": "native-function-evaluate-17",
                "cause_type": "com.comsol.util.exceptions.FlException",
                "cause_message": "Interpolation_function_is_out_of_range",
            },
        },
    )
    param = WorkerOutcomeParamNode(failure)
    worker = JavaHandleTestWorker(ModelNode(global_functions={"f1": node}, param=param))
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before = ledger_state(service, model_ref)

    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"value": -0.1, "unit": "m"}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-interpolation-range-error"},
        "function-evaluate-interpolation-range-error", "session",
    )

    assert result["success"] is False
    assert result["error"]["code"] == "OPERATION_FAILED"
    assert result["data"]["status"] == "FAILED"
    assert result["data"]["execution_state_unknown"] is False
    assert result["data"]["results"][0]["value_status"] == "EVALUATION_ERROR"
    row_error = result["data"]["results"][0]["errors"][0]
    assert row_error["code"] == "FUNCTION_EVALUATION_ERROR"
    assert row_error["worker_request_id"] == "function-evaluate-interpolation-range-error"
    raw_failure = row_error["worker_failure_raw"]
    assert raw_failure["classification"] == "COMSOL_INTERPOLATION_RANGE"
    assert raw_failure["terminal_sample_error"] is True
    assert raw_failure["execution_state_unknown"] is False
    assert raw_failure["post_dispatch"] is True
    assert raw_failure["read_only_source"] == "com.comsol.model.ModelParam.evaluateComplex(String)"
    assert raw_failure["exception_type"] == "com.comsol.util.exceptions.FlException"
    assert raw_failure["error_tag"] == "Interpolation_function_is_out_of_range"
    assert raw_failure["native_request_id"] == "native-function-evaluate-17"
    assert result["execution"]["revision"] == before[0]
    assert result["execution"]["dirty"] is False
    assert ledger_state(service, model_ref) == before
    witness = result["data"]["domain_outcome"]["witness"]
    assert witness["mutation_issued"] is False
    assert any(
        row["method"] == "getStringMatrix" and row["args_count"] == 1 and row["is_mutation"] is False
        for row in witness["dispatches"]
    )
    backend.close()


def test_explicit_unknown_interpolation_evaluation_still_freezes_read_ticket(tmp_path, monkeypatch):
    node = interpolation()
    node.properties["argrange"] = ("StringMatrix", [["0", "1"]])
    failure = JavaWorkerError(
        "worker could not establish interpolation completion",
        reply={
            "ok": False,
            "status": "FAILED",
            "request_id": "function-evaluate-interpolation-unknown",
            "failure": {
                "code": "ENGINE_CALL_FAILED",
                "message": "COMSOL call outcome unresolved",
                "execution_state_unknown": True,
            },
        },
    )
    worker = JavaHandleTestWorker(
        ModelNode(global_functions={"f1": node}, param=WorkerOutcomeParamNode(failure))
    )
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before_revision, _before_dirty, before_fingerprint = ledger_state(service, model_ref)

    with pytest.raises(ExecutionContractError) as error:
        backend._invoke_g3_model(
            "function.evaluate", model_ref,
            {"path": path(), "arguments": [{"value": -0.1, "unit": "m"}]},
            {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
             "request_id": "function-evaluate-interpolation-unknown"},
            "function-evaluate-interpolation-unknown", "session",
        )

    assert error.value.code == "EXECUTION_STATE_UNKNOWN"
    witness = error.value.details["witness"]
    assert witness["mutation_issued"] is False
    assert any(
        row["method"] == "getStringMatrix" and row["args_count"] == 1 and row["is_mutation"] is False
        for row in witness["dispatches"]
    )
    state = service.ledger._state_for(model_ref)
    assert state.revision == before_revision + 1
    assert state.dirty is True
    assert state.fingerprint is None
    assert state.fingerprint != before_fingerprint
    backend.close()


def test_multiname_refusal_is_read_only_through_managed_java_route(tmp_path, monkeypatch):
    node = FunctionNode("Interpolation", ["first", "second"], {
        "source": ("String", "table"), "argunit": ("StringArray", ["m"]),
    })
    worker = JavaHandleTestWorker(ModelNode(global_functions={"f1": node}, param=ParamNode()))
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before = ledger_state(service, model_ref)

    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"value": 0.5, "unit": "m"}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-multiname-refusal"},
        "function-evaluate-multiname-refusal", "session",
    )

    assert result["success"] is False
    assert result["error"]["code"] == "AMBIGUOUS_FUNCTION"
    assert result["error"]["stage"] == "validation"
    assert result["data"]["status"] == "REFUSED"
    assert result["data"]["witness"]["mutation_issued"] is False
    assert [row["method"] for row in worker.dispatches] == ["func", "getType", "functionNames"]
    assert ledger_state(service, model_ref) == before
    backend.close()


def test_unverified_java_handle_dispatch_prevents_revision_suppression(tmp_path, monkeypatch):
    node = analytic(argunit=("1",))
    model = ModelNode(global_functions={"f1": node}, param=ParamNode())
    worker = JavaHandleTestWorker(model, inject_method="unverifiedMethod")
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"coordinate": [1], "units": ["1"]}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-unknown-dispatch"},
        "function-evaluate-unknown-dispatch", "session",
    )
    witness = result["data"]["domain_outcome"]["witness"]
    assert result["success"] is True
    assert [row["method"] for row in worker.dispatches].count("unverifiedMethod") == 1
    assert witness["mutation_issued"] is True
    assert result["execution"]["revision"] == 1
    assert result["execution"]["dirty"] is False
    backend.close()


def test_function_evaluate_external_snapshot_change_freezes_ticket(tmp_path, monkeypatch):
    class ChangingSnapshot:
        counter = 0

        def model_snapshot(self, tag):
            return {"model_tag": tag, "server_instance_id": "server",
                    "external_event_counter": self.counter, "fingerprint": f"fingerprint-{self.counter}"}

    adapter = ChangingSnapshot()
    service = ExecutionService(SessionLedger("session", "server"), adapter, project_root=tmp_path)
    bound = service.bind_model("Model")
    model_ref = model_ref_from_mapping(bound["execution"]["model_ref"])
    node = analytic(argunit=("1",))
    worker = JavaHandleTestWorker(
        ModelNode(global_functions={"f1": node}, param=ParamNode()),
        on_dispatch=lambda method: setattr(adapter, "counter", 1) if method == "evaluateComplex" else None,
    )
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    result = backend._invoke_g3_model(
        "function.evaluate", model_ref,
        {"path": path(), "arguments": [{"coordinate": [1], "units": ["1"]}]},
        {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
         "request_id": "function-evaluate-external-change"},
        "function-evaluate-external-change", "session",
    )
    assert result["success"] is False
    state = service.ledger._state_for(model_ref)
    assert state.revision == 1 and state.dirty is True
    assert result["execution"]["dirty"] is True
    assert result["data"]["execution_state_unknown"] is True
    backend.close()


def test_other_evaluate_effect_does_not_receive_function_evaluate_exception(tmp_path):
    service, model_ref = execution_service(tmp_path)
    result = service.execute_legacy(
        "result_at_points", lambda _args: {"success": True, "data": {"status": "OBSERVED"}},
        {}, model_ref=model_ref, expected_revision=0, effect="evaluate",
    )
    assert result["success"] is True
    assert result["execution"]["revision"] == 1


def test_function_evaluate_wrong_revision_and_missing_permission_are_rejected(tmp_path, monkeypatch):
    node = analytic(argunit=("1",))
    worker = JavaHandleTestWorker(ModelNode(global_functions={"f1": node}, param=ParamNode()))
    service, model_ref = execution_service(tmp_path)
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    arguments = {"path": path(), "arguments": [{"coordinate": [1], "units": ["1"]}]}
    base = {"session_id": "session", "model_ref": model_ref.as_dict(), "request_id": "function-evaluate-preflight"}
    with pytest.raises(ExecutionContractError) as conflict:
        backend._invoke_g3_model("function.evaluate", model_ref, arguments,
                                 {**base, "expected_revision": 1}, "function-evaluate-preflight", "session")
    assert conflict.value.code == "REVISION_CONFLICT"
    service.ledger.permissions = {"inspect"}
    with pytest.raises(ExecutionContractError) as denied:
        backend._invoke_g3_model("function.evaluate", model_ref, arguments,
                                 {**base, "expected_revision": 0}, "function-evaluate-preflight", "session")
    assert denied.value.code == "PERMISSION_DENIED"
    assert worker.dispatches == []
    assert ledger_state(service, model_ref) == (0, False, "stable-model-fingerprint")
    backend.close()


@pytest.mark.parametrize("worker_outcome", ["java_worker_error", "timeout_exception", "running_reply"])
def test_real_function_evaluate_worker_unknown_flows_through_managed_gate(tmp_path, monkeypatch, worker_outcome):
    service, model_ref = execution_service(tmp_path)
    node = analytic(argunit=("",))
    if worker_outcome == "java_worker_error":
        outcome = JavaWorkerError(
            "worker could not establish evaluation completion",
            reply={"ok": False, "status": "FAILED", "request_id": "function-evaluate-live-unknown",
                   "failure": {"code": "ENGINE_CALL_FAILED", "message": "Java call outcome unresolved",
                               "execution_state_unknown": True}},
        )
    elif worker_outcome == "timeout_exception":
        outcome = JavaWorkerTimeout("RPC timeout; original request may still be running")
    else:
        outcome = {"ok": True, "status": "RUNNING", "request_id": "function-evaluate-live-unknown"}
    param = WorkerOutcomeParamNode(outcome)
    worker = Worker(ModelNode(global_functions={"f1": node}, param=param))
    backend = ManagedBackend(
        tmp_path / "home", OperationStore(tmp_path / "operations.sqlite3"),
        service=service, worker=worker, registry={}, project_root=tmp_path,
    )
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    before_revision, _before_dirty, before_fingerprint = ledger_state(service, model_ref)

    try:
        with pytest.raises(ExecutionContractError) as error:
            backend._invoke_g3_model(
                "function.evaluate", model_ref, {"path": path(), "arguments": [{"value": 1, "unit": ""}]},
                {"session_id": "session", "model_ref": model_ref.as_dict(), "expected_revision": 0,
                 "request_id": "function-evaluate-live-unknown"},
                "function-evaluate-live-unknown", "session",
            )

        assert error.value.code == "EXECUTION_STATE_UNKNOWN"
        if worker_outcome != "timeout_exception":
            assert error.value.details["worker_status"] == ("FAILED" if worker_outcome == "java_worker_error" else "RUNNING")
            assert error.value.details["worker_request_id"] == "function-evaluate-live-unknown"
        else:
            assert error.value.details["worker_reason"] == "rpc_timeout_may_still_be_running"
        state = service.ledger._state_for(model_ref)
        assert state.revision == before_revision + 1
        assert state.dirty is True
        assert state.fingerprint is None
        assert state.fingerprint != before_fingerprint
    finally:
        backend.close()


def test_execution_service_read_effect_still_freezes_unverified_cleanup(tmp_path):
    service, model_ref = execution_service(tmp_path)
    before_revision, _before_dirty, before_fingerprint = ledger_state(service, model_ref)
    unresolved = {
        "status": "OBSERVED",
        "execution_state_unknown": True,
        "cleanup": {"created": True, "cleanup_failed": True,
                    "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "cleanup was not verified"}},
    }

    result = service.execute_legacy(
        "get_parameters", lambda _args: {"success": True, "data": unresolved},
        {}, model_ref=model_ref,
    )

    state = service.ledger._state_for(model_ref)
    assert result["success"] is False
    assert state.revision == before_revision
    assert state.dirty is True
    assert state.fingerprint is None
    assert state.fingerprint != before_fingerprint
    assert mcp_result(result).isError is True
