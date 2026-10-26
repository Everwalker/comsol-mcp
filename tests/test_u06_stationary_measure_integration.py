"""Portable generic-route regressions for weighted stationary measures."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from comsol_mcp import _g2_public_api as public_api
from comsol_mcp import _g3_results as results
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_engine import _UNKNOWN_DISPATCH_RETAINED
from comsol_mcp._g3_ops import dispatch
from comsol_mcp._java_worker import JavaWorkerError, RemoteJava
from test_g3_w17 import FWiredTree
from u06_stationary_measure_inert import InertRemoteWorker, MODEL_PARAM, NUMBER_UNIT


def _numbers(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _numbers(child)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield float(value)


def _counter_tree(
    *,
    primary_value: float,
    numerator_value: float,
    measure_value: float,
    primary_unit: str,
    numerator_unit: str,
    measure_unit: str,
    centered_integral: float | None = None,
    centered_evaluator: Any = None,
    complex_field: bool = False,
    primary_imag_value: float | None = None,
    numerator_imag_value: float | None = None,
):
    tree = FWiredTree(sdim=1, numerical_real=0.0, numerical_imag=numerator_imag_value,
                      is_complex=complex_field)
    tree.solution_info.props["getMaxInner"] = 1
    tree.solver.props["getPVals"] = [0.0]
    original_factory = tree.numerical_list.factory
    created: list[Any] = []
    payload_reads: list[dict[str, Any]] = []

    def role_for(tag: str) -> str:
        if tag.endswith("_meas"):
            return "denominator"
        if tag.endswith("_num"):
            return "numerator"
        if "weight" in tag or "wmin" in tag:
            return "weight_validation"
        return "primary"

    def factory(tag: str, *args: Any):
        node = original_factory(tag, *args)
        role = role_for(tag)
        selection_node = getattr(node, "selection_node", None)
        if selection_node is not None and hasattr(selection_node, "_entities"):
            # This inert SDK leaf represents one selected entity for the
            # fixture's all-selection call. The production code still reads
            # its selection receiver, geometry, dimension, and membership via
            # the real RemoteJava dispatch path.
            selection_node.all = lambda selection=selection_node: setattr(selection, "_entities", [1])
        node.is_complex_flag = complex_field and role in {"primary", "numerator"}
        base_set = node.set
        base_get_real = node.getReal
        base_get_data = node.getData
        base_get_string_array = node.getStringArray

        def expressions_now() -> list[str]:
            value = node.props.get("expr", [])
            if isinstance(value, (list, tuple)):
                expressions = [str(item) for item in value]
            else:
                expressions = [str(value)] if value is not None else []
            node.is_complex_flag = (
                complex_field and role in {"primary", "numerator"}
                and not any(any(token in item for token in ("abs(", "real(", "imag("))
                            for item in expressions)
            )
            return expressions

        def set_property(name: str, *values: Any):
            result = base_set(name, *values)
            if name == "expr":
                expressions_now()
            return result

        def unit_value() -> str:
            if role == "denominator":
                return measure_unit
            if role == "numerator":
                expressions = expressions_now()
                if ((centered_integral is not None or centered_evaluator is not None)
                        and any("abs(" in item for item in expressions)):
                    return "K^2*m"
                return numerator_unit
            if role == "weight_validation":
                return "1"
            configured = node.props.get("unit")
            if isinstance(configured, (list, tuple)) and configured:
                return str(configured[0])
            return primary_unit

        def current_value() -> list[float]:
            if role == "denominator":
                node.is_complex_flag = False
                node.imag_data = None
                return [measure_value]
            if role == "numerator":
                expressions = expressions_now()
                if centered_integral is not None and any("abs(" in item for item in expressions):
                    node.imag_data = None
                    return [centered_integral]
                if centered_evaluator is not None and any("abs(" in item for item in expressions):
                    node.imag_data = None
                    return [float(centered_evaluator(expressions))]
                if complex_field and not any(
                    any(token in item for token in ("abs(", "real(", "imag("))
                    for item in expressions
                ):
                    node.imag_data = [numerator_imag_value if numerator_imag_value is not None else 0.0]
                else:
                    node.imag_data = None
                return [numerator_value]
            if role == "weight_validation":
                node.is_complex_flag = False
                node.imag_data = None
                return [1.0]
            expressions = expressions_now()
            if complex_field and any("imag(" in item for item in expressions):
                # The effective NumericalFeature expression is already projected.
                # Return the source imaginary component as a real scalar payload.
                node.imag_data = None
                return [primary_imag_value if primary_imag_value is not None else 0.0]
            if complex_field and not any(
                any(token in item for token in ("abs(", "real(", "imag("))
                for item in expressions
            ):
                node.imag_data = [primary_imag_value if primary_imag_value is not None else 0.0]
            else:
                node.imag_data = None
            return [primary_value]

        def record_getter(method: str, base_getter: Any, *getter_args: Any):
            node.real_data = current_value()
            value = base_getter(*getter_args)
            payload_reads.append({"role": role, "feature_tag": tag, "method": method,
                                  "expressions": expressions_now(), "is_complex": node.is_complex_flag,
                                  "unit_readback": unit_value(), "returned_payload": value})
            return value

        def get_string_array(name: str):
            if name == "unit":
                node._guard("getStringArray", (name,))
                return [unit_value()]
            return base_get_string_array(name)

        node.set = set_property
        node.getReal = lambda *getter_args: record_getter("getReal", base_get_real, *getter_args)
        node.getData = lambda: record_getter("getData", base_get_data)
        node.getStringArray = get_string_array
        created.append(node)
        return node

    tree.numerical_list.factory = factory
    return tree, created, payload_reads


def _write_case(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_counter(
    name: str,
    spec: dict[str, Any],
    tree: Any,
    created: list[Any],
    payload_reads: list[dict[str, Any]],
    tmp_path: Path,
    *,
    counter_reference: dict[str, Any],
):
    worker = InertRemoteWorker(tree)
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    binding = result.get("native_result_binding_evidence", {})
    rows = binding.get("readbacks", [])
    record = {
        "case": name,
        "request_spec": spec,
        "result_status": result.get("status"),
        "output_values": result.get("values"),
        "output_expression_units": result.get("expression_units"),
        "native_binding_status": binding.get("status"),
        "actual_native_readbacks": rows,
        "native_payload_getter_returns": payload_reads,
        "cleanup": result.get("cleanup"),
        "typed_evaluations": worker.typed_evaluations,
        "unit_conversion_evidence": result.get("unit_conversion_evidence"),
        "software_only": True,
        "counter_reference": counter_reference,
    }
    _write_case(tmp_path, name, record)
    assert result.get("status", {}).get("ok") is True
    assert rows, "generic result.evaluate must include actual feature readbacks"
    assert created and tree.numerical_list.items == {}
    return result, rows, worker


def _weighted_spec(*, aggregate: str = "average", complex_mode: str = "real",
                   output_unit: str = "cm") -> dict[str, Any]:
    return {"expressions": ["T"], "solution": {"dataset": "dset1"},
            "aggregate": aggregate, "complex_mode": complex_mode,
            "weight_expression": "w", "units": [output_unit]}


def test_generic_weighted_mean_uses_actual_units_and_exact_typed_dispatch(tmp_path: Path):
    tree, created, payload_reads = _counter_tree(
        primary_value=200.0, numerator_value=4.0, measure_value=2.0,
        primary_unit="cm", numerator_unit="m^2", measure_unit="m",
    )
    result, rows, worker = _run_counter(
        "weighted_mean_actual_units",
        _weighted_spec(), tree, created, payload_reads, tmp_path,
        counter_reference={"numerator": {"value": 4.0, "unit": "m^2"},
                           "measure": {"value": 2.0, "unit": "m"},
                           "expected_output": {"value": 200.0, "unit": "cm"}},
    )
    actual = list(_numbers(result["values"]))
    assert actual and all(math.isclose(value, 200.0, rel_tol=0.0, abs_tol=1e-12) for value in actual)
    assert result["expression_units"]["T"] == "cm"
    role_units = {role: next(row["unit_readback"] for row in reversed(rows) if row["role"] == role)
                  for role in ("primary", "numerator", "denominator")}
    assert role_units == {"primary": {"real((T))": "cm"},
                          "numerator": {"(w)*(real((T)))": "m^2"},
                          "denominator": {"w": "m"}}
    evidence = result["unit_conversion_evidence"]
    assert evidence["status"] == "TYPED_PARAMBASE_EVALUATE_READBACKS_RETURNED"
    assert evidence["sdk_dispatches"] == len(worker.typed_evaluations) == 1
    assert worker.descriptor_reads == ["com.comsol.model.Model", MODEL_PARAM]
    assert worker.typed_evaluations[0]["expression"]
    assert worker.typed_evaluations[0]["target_unit"] == "cm"
    assert [arg["java_signature"] for arg in worker.typed_evaluations[0]["arguments"]] == [
        "java.lang.String", "java.lang.String"]
    assert any(row["method"] == "evaluate" for row in worker.dispatches)
    assert not any(row["method"] in {"evaluateUnit", "evaluateComplex"} for row in worker.dispatches)


def test_missing_measure_unit_refuses_before_typed_conversion(tmp_path: Path):
    tree, created, payload_reads = _counter_tree(
        primary_value=200.0, numerator_value=4.0, measure_value=2.0,
        primary_unit="cm", numerator_unit="m^2", measure_unit="",
    )
    worker = InertRemoteWorker(tree)
    with pytest.raises(ExecutionContractError) as caught:
        dispatch("result.evaluate", worker, "Model", {"spec": _weighted_spec()})
    denominators = [row for row in payload_reads if row["role"] == "denominator"]
    evidence = {"error_code": caught.value.code, "measure_unit_readbacks": [row["unit_readback"] for row in denominators],
                "typed_evaluations": worker.typed_evaluations, "software_only": True}
    _write_case(tmp_path, "missing_measure_unit_refusal", evidence)
    assert caught.value.code == "RESULT_UNIT_UNVERIFIED"
    assert worker.typed_evaluations == []
    assert denominators and all(row["unit_readback"] == "" for row in denominators)
    assert created and tree.numerical_list.items == {}


def test_private_typed_adapter_preserves_absolute_and_differential_temperature_units(tmp_path: Path):
    tree = FWiredTree(numerical_real=1.0)
    worker = InertRemoteWorker(tree)
    model = worker.client().model("Model")
    evaluator = results._result_unit_evaluator(worker, model)
    quotient = evaluator.quotient(4.0, 2.0, "m^2", "m", "cm")
    absolute = evaluator.quotient(300.0, 1.0, "K", "1", "degC")
    differential = evaluator.quotient(1.0, 1.0, "K", "1", "degC", differential=True)
    with pytest.raises(ExecutionContractError) as affine:
        evaluator.quotient(1.0, 1.0, "K", "1", "degC", absolute_magnitude=True)
    _write_case(tmp_path, "private_typed_adapter", {
        "values": [quotient, absolute, differential], "refusal": affine.value.code,
        "descriptor_reads": worker.descriptor_reads,
        "typed_calls": worker.typed_evaluations, "software_only": True,
    })
    assert quotient["value"] == 200.0
    assert absolute["value"] == pytest.approx(26.85)
    assert differential["value"] == pytest.approx(1.0)
    assert affine.value.code == "API_UNSUPPORTED"
    assert worker.descriptor_reads == ["com.comsol.model.Model", MODEL_PARAM]
    assert all([arg["java_signature"] for arg in row["arguments"]]
               == ["java.lang.String", "java.lang.String"] for row in worker.typed_evaluations)
    assert not any(row["method"] in {"evaluateUnit", "evaluateComplex"} for row in worker.dispatches)
    assert "evaluate" not in {cap.method for cap in public_api.CAPABILITIES}


def test_typed_evaluate_first_unknown_stops_dispatch_and_retains_original_owners(tmp_path: Path):
    class StructuredUnknown(RuntimeError):
        code = "EXECUTION_STATE_UNKNOWN"
        execution_state_unknown = True

    tree, _created, _payload_reads = _counter_tree(
        primary_value=200.0, numerator_value=4.0, measure_value=2.0,
        primary_unit="cm", numerator_unit="m^2", measure_unit="m",
    )
    parameter_node = type("ParameterLeaf", (), {"calls": []})()
    tree.parameter_node = parameter_node
    tree.model.param = lambda: parameter_node
    worker = InertRemoteWorker(tree)
    first_unknown = StructuredUnknown("typed parameter evaluation first UNKNOWN")
    worker.typed_evaluation_failure = first_unknown
    retained_before = len(_UNKNOWN_DISPATCH_RETAINED)
    with pytest.raises(JavaWorkerError) as caught:
        dispatch("result.evaluate", worker, "Model", {"spec": _weighted_spec()})
    attempts = _UNKNOWN_DISPATCH_RETAINED[retained_before:]
    _write_case(tmp_path, "typed_first_unknown", {
        "error_code": caught.value.reply.get("code"),
        "dispatch_methods": [row["method"] for row in worker.dispatches],
        "skipped_methods": [row["method"] for row in attempts[-1].skipped_dispatches],
        "owner_types": [type(owner).__name__ for owner in attempts[-1].owners],
        "typed_arguments": worker.typed_evaluations[0]["arguments"],
        "software_only": True,
    })
    assert caught.value.reply.get("code") == "EXECUTION_STATE_UNKNOWN"
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.first_unknown is caught.value
    assert attempt.first_call["method"] == "evaluate"
    assert isinstance(attempt.first_call["receiver"], RemoteJava)
    assert attempt.first_call["receiver"].java_type == MODEL_PARAM
    assert attempt.retained is True
    assert worker in attempt.owners
    assert any(isinstance(owner, RemoteJava) and owner.java_type == "com.comsol.model.Model"
               for owner in attempt.owners)
    assert any(isinstance(owner, RemoteJava) and owner.java_type == MODEL_PARAM for owner in attempt.owners)
    assert worker.dispatches[-1]["method"] == "evaluate"
    assert not any(row["method"] in {"remove", "restore", "tags", "query"}
                   for row in worker.dispatches[worker.dispatches.index(worker.dispatches[-1]) + 1:])
    assert any(row["method"] == "remove" for row in attempt.skipped_dispatches)
    assert [arg["java_signature"] for arg in worker.typed_evaluations[0]["arguments"]] == [
        "java.lang.String", "java.lang.String"]


@pytest.mark.parametrize("bad_response", ["bool", "nan", "descriptor"])
def test_adapter_local_invalid_response_latches_before_cleanup_and_retains_owners(
    tmp_path: Path, bad_response: str,
):
    tree, _created, _payload_reads = _counter_tree(
        primary_value=200.0, numerator_value=4.0, measure_value=2.0,
        primary_unit="cm", numerator_unit="m^2", measure_unit="m",
    )
    worker = InertRemoteWorker(tree)
    if bad_response in {"bool", "nan"}:
        invalid: Any = True if bad_response == "bool" else math.nan
        worker._evaluate_typed_constants = lambda _args, value=invalid: value
        expected_first_call = expected_dispatch = "evaluate"
    else:
        original_describe = worker.describe_public

        def malformed_descriptor(receiver: Any) -> dict[str, Any]:
            descriptor = original_describe(receiver)
            if getattr(receiver, "java_type", None) == MODEL_PARAM:
                descriptor["interfaces"] = [42]
            return descriptor

        worker.describe_public = malformed_descriptor
        expected_first_call, expected_dispatch = "describe_public", "param"
    retained_before = len(_UNKNOWN_DISPATCH_RETAINED)
    with pytest.raises(ExecutionContractError) as caught:
        dispatch("result.evaluate", worker, "Model", {"spec": _weighted_spec()})
    attempts = _UNKNOWN_DISPATCH_RETAINED[retained_before:]
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.first_unknown is caught.value
    assert attempt.first_call["method"] == expected_first_call
    assert attempt.retained is True and worker in attempt.owners
    assert any(isinstance(owner, RemoteJava) and owner.java_type == "com.comsol.model.Model"
               for owner in attempt.owners)
    assert any(isinstance(owner, RemoteJava) and owner.java_type == MODEL_PARAM for owner in attempt.owners)
    assert worker.dispatches[-1]["method"] == expected_dispatch
    assert attempt.skipped_dispatches
    assert any(row["method"] == "remove" for row in attempt.skipped_dispatches)
    assert any(isinstance(owner, list) and any(
        isinstance(record, dict) and record.get("cleanup_status") == "SKIPPED_AFTER_FIRST_UNKNOWN"
        for record in owner) for owner in attempt.owners)
    if bad_response == "descriptor":
        assert worker.typed_evaluations == []
    else:
        assert len(worker.typed_evaluations) == 1
    _write_case(tmp_path, f"adapter_invalid_{bad_response}", {
        "error_code": caught.value.code, "original_error": str(caught.value),
        "first_call_method": attempt.first_call["method"],
        "dispatch_methods": [row["method"] for row in worker.dispatches],
        "skipped_methods": [row["method"] for row in attempt.skipped_dispatches],
        "typed_call_count": len(worker.typed_evaluations), "software_only": True,
    })


def test_weighted_rms_converts_actual_square_integral_units_to_requested_length(tmp_path: Path):
    tree, created, payload_reads = _counter_tree(
        primary_value=200.0, numerator_value=8.0, measure_value=2.0,
        primary_unit="cm", numerator_unit="m^3", measure_unit="m",
    )
    result, rows, worker = _run_counter(
        "rms_actual_square_integral_units",
        _weighted_spec(aggregate="rms"), tree, created, payload_reads, tmp_path,
        counter_reference={"square_integral": {"value": 8.0, "unit": "m^3"},
                           "measure": {"value": 2.0, "unit": "m"},
                           "expected_output": {"value": 200.0, "unit": "cm"}},
    )
    actual = list(_numbers(result["values"]))
    assert actual and all(math.isclose(value, 200.0, rel_tol=0.0, abs_tol=1e-12) for value in actual)
    assert result["expression_units"]["T"] == "cm"
    evidence = result["unit_conversion_evidence"]
    assert evidence["operation"] == "rms"
    assert evidence["sdk_dispatches"] == 2
    assert [row["expression"] for row in worker.typed_evaluations] == [
        "sqrt((8[m^3])/(2[m]))", "sqrt((0[m^3])/(1[m]))"]
    assert all([arg["java_signature"] for arg in row["arguments"]]
               == ["java.lang.String", "java.lang.String"] for row in worker.typed_evaluations)
    units = evidence["native_unit_readbacks"]
    assert units["primary"]["rows"][-1]["unit_readback"] == {"real((T))": "cm"}
    assert units["numerator_or_variance"]["rows"][-1]["unit_readback"] == {
        "(w)*abs((real((T))))^2": "m^3"}
    assert units["denominator"]["rows"][-1]["unit_readback"] == {"w": "m"}
    assert rows


@pytest.mark.parametrize(
    ("measure_unit", "primary_unit", "expected_error", "expected_typed_calls"),
    [("", "cm", "RESULT_UNIT_UNVERIFIED", 0),
     ("m", "degC", "API_UNSUPPORTED", 2)],
)
def test_weighted_rms_refuses_missing_measure_or_affine_temperature_units(
    tmp_path: Path, measure_unit: str, primary_unit: str, expected_error: str,
    expected_typed_calls: int,
):
    tree, created, payload_reads = _counter_tree(
        primary_value=26.85 if primary_unit == "degC" else 200.0,
        numerator_value=8.0, measure_value=2.0,
        primary_unit=primary_unit, numerator_unit="K^2*m" if primary_unit == "degC" else "m^3",
        measure_unit=measure_unit,
    )
    worker = InertRemoteWorker(tree)
    with pytest.raises(ExecutionContractError) as caught:
        dispatch("result.evaluate", worker, "Model", {"spec": _weighted_spec(aggregate="rms",
                                                                                output_unit=primary_unit)})
    denominator_reads = [row["unit_readback"] for row in payload_reads if row["role"] == "denominator"]
    _write_case(tmp_path, f"rms_refusal_{expected_error.lower()}", {
        "error_code": caught.value.code, "denominator_unit_readbacks": denominator_reads,
        "typed_expressions": [row["expression"] for row in worker.typed_evaluations],
        "software_only": True,
    })
    assert caught.value.code == expected_error
    assert len(worker.typed_evaluations) == expected_typed_calls
    assert denominator_reads and denominator_reads[-1] == measure_unit
    assert created and tree.numerical_list.items == {}


def test_real_std_center_remains_absolute_and_uses_typed_converted_mean(tmp_path: Path):
    calculations: list[dict[str, Any]] = []

    def centered_leaf(expressions: list[str]) -> float:
        emitted = " ".join(expressions)
        literals = NUMBER_UNIT.findall(emitted)
        assert len(literals) == 1, f"real scalar center must remain one absolute literal: {emitted!r}"
        assert literals[0][1] == "degC"
        center_kelvin = InertRemoteWorker._convert(float(literals[0][0]), literals[0][1], "K")
        variance = 2.0 * (300.0 - center_kelvin) ** 2
        calculations.append({"emitted_center": emitted, "center_kelvin": center_kelvin,
                             "field_kelvin": 300.0, "calculated_variance_integral": variance})
        return variance

    tree, created, payload_reads = _counter_tree(
        primary_value=26.85, numerator_value=600.0, measure_value=2.0,
        primary_unit="degC", numerator_unit="K*m", measure_unit="m",
        centered_evaluator=centered_leaf,
    )
    result, rows, worker = _run_counter(
        "real_std_absolute_center",
        _weighted_spec(aggregate="std", output_unit="degC"), tree, created, payload_reads, tmp_path,
        counter_reference={"field": {"value": 300.0, "unit": "K"},
                           "expected_absolute_center": {"value": 26.85, "unit": "degC"},
                           "expected_std": 0.0},
    )
    numerator = next(node for node in created if node.tag_.endswith("_num"))
    expression = numerator.getStringArray("expr")[0]
    center = re.search(r"\(([+-]?[0-9.eE]+)\[degC\]\)", expression)
    actual = list(_numbers(result["values"]))
    assert center and math.isclose(float(center.group(1)), 26.85, rel_tol=0.0, abs_tol=1e-10)
    assert "300[degC]" not in expression
    assert calculations and all(math.isclose(row["center_kelvin"], 300.0, abs_tol=1e-10)
                                and math.isclose(row["calculated_variance_integral"], 0.0, abs_tol=1e-18)
                                for row in calculations)
    assert actual and all(math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-9) for value in actual)
    assert worker.typed_evaluations and rows
    assert any("sqrt(" not in row["expression"] and "K*m" in row["expression"]
               for row in worker.typed_evaluations)
    _write_case(tmp_path, "real_std_emitted_absolute_center_calculation", {
        "calculations": calculations, "output_values": result["values"], "software_only": True,
    })


def test_one_kelvin_std_uses_affine_center_and_differential_output_scale(tmp_path: Path):
    mean_kelvin = 301.0
    sample_kelvin = (300.0, 302.0)
    center_fahrenheit = (mean_kelvin - 273.15) * 9.0 / 5.0 + 32.0
    calculations: list[dict[str, Any]] = []

    def centered_leaf(expressions: list[str]) -> float:
        emitted = " ".join(expressions)
        literals = NUMBER_UNIT.findall(emitted)
        assert len(literals) == 1, f"real scalar center must remain one absolute literal: {emitted!r}"
        assert literals[0][1] == "degF"
        center_kelvin = InertRemoteWorker._convert(float(literals[0][0]), literals[0][1], "K")
        variance = 2.0 * sum((value - center_kelvin) ** 2 for value in sample_kelvin) / len(sample_kelvin)
        calculations.append({"emitted_center": emitted, "center_kelvin": center_kelvin,
                             "samples_kelvin": sample_kelvin,
                             "calculated_variance_integral": variance})
        return variance

    tree, created, payload_reads = _counter_tree(
        primary_value=center_fahrenheit, numerator_value=602.0, measure_value=2.0,
        primary_unit="degF", numerator_unit="K*m", measure_unit="m",
        centered_evaluator=centered_leaf,
    )
    result, rows, worker = _run_counter(
        "one_kelvin_std_to_degf",
        _weighted_spec(aggregate="std", output_unit="degF"), tree, created, payload_reads, tmp_path,
        counter_reference={"samples": [{"value": 300.0, "unit": "K"}, {"value": 302.0, "unit": "K"}],
                           "expected_center": {"value": center_fahrenheit, "unit": "degF"},
                           "expected_std": {"value": 1.8, "unit": "delta_degF"}},
    )
    numerator = next(node for node in created if node.tag_.endswith("_num"))
    expression = numerator.getStringArray("expr")[0]
    center = re.search(r"\(([+-]?[0-9.eE]+)\[degF\]\)", expression)
    actual = list(_numbers(result["values"]))
    assert center and math.isclose(float(center.group(1)), center_fahrenheit, rel_tol=0.0, abs_tol=1e-10)
    assert actual and all(math.isclose(value, 1.8, rel_tol=0.0, abs_tol=1e-9) for value in actual)
    assert calculations and all(math.isclose(row["center_kelvin"], mean_kelvin, abs_tol=1e-10)
                                and math.isclose(row["calculated_variance_integral"], 2.0, abs_tol=1e-12)
                                for row in calculations)
    assert len(worker.typed_evaluations) == 3
    evidence = result["unit_conversion_evidence"]
    assert evidence["sdk_dispatches"] == 3
    stages = {row["purpose"]: row for row in evidence["conversion_stages"]}
    assert stages["standard_deviation_center"]["sdk_dispatches"] == 1
    assert stages["standard_deviation_output"]["sdk_dispatches"] == 2
    assert rows
    _write_case(tmp_path, "one_kelvin_std_emitted_center_calculation", {
        "calculations": calculations, "output_values": result["values"], "software_only": True,
    })


def test_scalar_imag_std_computes_variance_from_emitted_differential_center(tmp_path: Path):
    source_field = {
        "expression": "T",
        "real": {"value": 300.0, "unit": "K"},
        "imag": {"value": 1.0, "unit": "K", "semantics": "differential"},
    }
    calculations: list[dict[str, Any]] = []

    def centered_leaf(expressions: list[str]) -> float:
        emitted = " ".join(expressions)
        literals = NUMBER_UNIT.findall(emitted)
        assert len(literals) == 2, f"imaginary scalar center must emit a value and zero reference: {emitted!r}"
        assert [unit for _value, unit in literals] == ["degC", "degC"]
        center_delta = (InertRemoteWorker._convert(float(literals[0][0]), literals[0][1], "K")
                        - InertRemoteWorker._convert(float(literals[1][0]), literals[1][1], "K"))
        field_imaginary_kelvin = float(source_field["imag"]["value"])
        variance = 2.0 * (field_imaginary_kelvin - center_delta) ** 2
        calculations.append({"source_field": source_field, "emitted_center": emitted,
                             "center_delta_kelvin": center_delta,
                             "field_imaginary_kelvin": field_imaginary_kelvin, "measure": 2.0,
                             "calculated_variance_integral": variance})
        return variance

    tree, created, payload_reads = _counter_tree(
        primary_value=300.0, primary_imag_value=1.0, numerator_value=2.0,
        numerator_imag_value=2.0, measure_value=2.0, primary_unit="degC",
        numerator_unit="K*m", measure_unit="m", complex_field=True,
        centered_evaluator=centered_leaf,
    )
    result, rows, worker = _run_counter(
        "scalar_imag_std_differential_center",
        _weighted_spec(aggregate="std", complex_mode="imag", output_unit="degC"),
        tree, created, payload_reads, tmp_path,
        counter_reference={"field": source_field,
                           "numerator": {"value": 2.0, "unit": "K*m"},
                           "measure": {"value": 2.0, "unit": "m"}, "expected_std": 0.0},
    )
    assert calculations and all(math.isclose(row["center_delta_kelvin"], 1.0, abs_tol=1e-12)
                                and math.isclose(row["calculated_variance_integral"], 0.0, abs_tol=1e-18)
                                for row in calculations)
    assert all("imag(" in expression for row in payload_reads if row["role"] in {"primary", "numerator"}
               for expression in row["expressions"])
    assert all(row["is_complex"] is False for row in payload_reads
               if row["role"] in {"primary", "numerator"})
    assert source_field == {
        "expression": "T",
        "real": {"value": 300.0, "unit": "K"},
        "imag": {"value": 1.0, "unit": "K", "semantics": "differential"},
    }
    primary_rows = [row for row in payload_reads if row["role"] == "primary"]
    projected_primary = [value for row in primary_rows for value in _numbers(row["returned_payload"])]
    assert primary_rows and all(row["unit_readback"] == "degC" for row in primary_rows)
    assert projected_primary and all(math.isclose(value, source_field["imag"]["value"],
                                                    rel_tol=0.0, abs_tol=1e-12)
                                     for value in projected_primary)
    assert all(not math.isclose(value, source_field["real"]["value"], rel_tol=0.0, abs_tol=1e-12)
               for value in projected_primary)
    assert [row["role"] for row in payload_reads if row["role"] == "denominator"]
    assert worker.typed_evaluations
    assert any("K*m" in row["expression"] and row["target_unit"] == "degC"
               for row in worker.typed_evaluations)
    assert any(row["expression"].startswith("0*") for row in worker.typed_evaluations)
    assert all([arg["java_signature"] for arg in row["arguments"]]
               == ["java.lang.String", "java.lang.String"] for row in worker.typed_evaluations)
    assert rows and created and tree.numerical_list.items == {}
    actual = list(_numbers(result["values"]))
    assert actual and all(math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-9) for value in actual)
    _write_case(tmp_path, "scalar_imag_emitted_center_calculation", {
        "source_field": source_field,
        "projected_primary_getter_payloads": projected_primary,
        "calculations": calculations, "typed_calls": worker.typed_evaluations,
        "output_values": result["values"], "software_only": True,
    })


def test_preserve_complex_std_keeps_real_absolute_and_imaginary_differential_centers(tmp_path: Path):
    calculations: list[dict[str, Any]] = []

    def centered_leaf(expressions: list[str]) -> float:
        emitted = " ".join(expressions)
        literals = NUMBER_UNIT.findall(emitted)
        assert len(literals) == 3, f"complex center must emit real, imaginary, and zero-reference literals: {emitted!r}"
        assert [unit for _value, unit in literals] == ["degC", "degC", "degC"]
        real_center = InertRemoteWorker._convert(float(literals[0][0]), literals[0][1], "K")
        imag_center = (InertRemoteWorker._convert(float(literals[1][0]), literals[1][1], "K")
                       - InertRemoteWorker._convert(float(literals[2][0]), literals[2][1], "K"))
        variance = 2.0 * ((300.0 - real_center) ** 2 + (1.0 - imag_center) ** 2)
        calculations.append({"emitted_center": emitted, "real_center_kelvin": real_center,
                             "imag_center_delta_kelvin": imag_center, "calculated_variance_integral": variance})
        return variance

    tree, created, payload_reads = _counter_tree(
        primary_value=26.85, primary_imag_value=1.0, numerator_value=600.0,
        numerator_imag_value=2.0, measure_value=2.0, primary_unit="degC",
        numerator_unit="K*m", measure_unit="m", complex_field=True,
        centered_evaluator=centered_leaf,
    )
    result, rows, worker = _run_counter(
        "preserve_complex_std_center", _weighted_spec(aggregate="std", complex_mode="preserve",
                                                       output_unit="degC"),
        tree, created, payload_reads, tmp_path,
        counter_reference={"field": {"real": {"value": 300.0, "unit": "K"},
                                     "imag": {"value": 1.0, "unit": "K", "semantics": "differential"}},
                           "expected_std": 0.0},
    )
    assert calculations and all(math.isclose(row["real_center_kelvin"], 300.0, abs_tol=1e-10)
                                and math.isclose(row["imag_center_delta_kelvin"], 1.0, abs_tol=1e-10)
                                and math.isclose(row["calculated_variance_integral"], 0.0, abs_tol=1e-18)
                                for row in calculations)
    source_reads = [row for row in payload_reads if row["role"] in {"primary", "numerator"}
                    and any("abs(" not in expr for expr in row["expressions"])]
    centered_reads = [row for row in payload_reads if row["role"] == "numerator"
                      and any("abs(" in expr for expr in row["expressions"])]
    assert source_reads and all(row["is_complex"] is True for row in source_reads)
    assert centered_reads and all(row["is_complex"] is False for row in centered_reads)
    assert all(re.search(r"\([+-]?[0-9.eE]+\[degC\]\)-\(0(?:\.0+)?\[degC\]\)",
                         row["emitted_center"]) for row in calculations)
    actual = list(_numbers(result["values"]))
    assert actual and all(math.isclose(value, 0.0, rel_tol=0.0, abs_tol=1e-9) for value in actual)
    assert rows and created and tree.numerical_list.items == {}
    _write_case(tmp_path, "preserve_complex_emitted_center_calculation", {
        "calculations": calculations, "output_values": result["values"], "software_only": True,
    })
