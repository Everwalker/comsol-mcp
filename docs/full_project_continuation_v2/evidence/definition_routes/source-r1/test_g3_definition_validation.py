"""Focused software tests for definition validation and semantic rebinding.

The fakes model the already-bound COMSOL API boundary.  They never start an
engine; result.at_points' native ephemeral-feature behavior remains covered by
its production adapter tests and native acceptance is a separate gate.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from comsol_mcp import _g3_definition_validation as validation
from comsol_mcp._g2_contract import ExecutionContractError


class _Worker:
    def __init__(self, model):
        self._model = model

    def client(self):
        return self

    def model(self, _tag):
        return self._model


class _Geometry:
    def __init__(self, unit="m", sdim=3):
        self._unit = unit
        self._sdim = sdim

    def lengthUnit(self):
        return self._unit

    def getSDim(self):
        return self._sdim


class _NamedSelection:
    def named(self):
        return "source_selection"


class _GeneralExtrusion:
    def __init__(self, *, srcframe="spatial", dstmap=("x+1", "y-0.5", "z")):
        self.srcframe = srcframe
        self.dstmap = list(dstmap)
        self._selection = _NamedSelection()

    def getType(self):
        return "GeneralExtrusion"

    def selection(self):
        return self._selection


class _SpatialSelectionNode:
    def __init__(self, geometry="geom1", dimension=1, entities=(7,)):
        self._geometry = geometry
        self._dimension = dimension
        self._entities = list(entities)
        self.set_calls = []

    def getType(self):
        return "Box"

    def geom(self, *args):
        if args:
            self._geometry = str(args[0])
        return self._geometry

    def entities(self):
        return list(self._entities)

    def dim(self):
        return self._dimension


class _SelectionList:
    def __init__(self, node):
        self.node = node

    def tags(self):
        return ["box1"]

    def get(self, tag):
        if tag != "box1":
            raise KeyError(tag)
        return self.node


class _Component:
    def __init__(self, *, coupling=None, selection_node=None, geometry=None):
        self._coupling = coupling or _GeneralExtrusion()
        self._selection_node = selection_node or _SpatialSelectionNode()
        self._geometry = geometry or _Geometry()

    def cpl(self, tag):
        if tag != "gen1":
            raise KeyError(tag)
        return self._coupling

    def selection(self, tag=None):
        if tag is not None:
            if tag != "box1":
                raise KeyError(tag)
            return self._selection_node
        return _SelectionList(self._selection_node)

    def geom(self, tag):
        if tag != "geom1":
            raise KeyError(tag)
        return self._geometry


class _Model:
    def __init__(self, component=None):
        self._component = component or _Component()

    def component(self, tag):
        if tag != "comp1":
            raise KeyError(tag)
        return self._component


def _mapping_args(*, evaluation=None, test_points=None, checks=None):
    return {
        "path": {"segments": [
            {"collection": "component", "tag": "comp1"},
            {"collection": "cpl", "tag": "gen1"},
        ]},
        "test_points": test_points or [{
            "destination": [1.0, 2.0, 3.0],
            "destination_unit": "m",
            "expected_source": [2.0, 1.5, 3.0],
            "source_unit": "m",
        }],
        "checks": {"evaluation": evaluation, **(checks or {})},
    }


def _patch_coupling_properties(monkeypatch):
    def read(node, name):
        props = {"usesrcmap": "off", "srcframe": node.srcframe, "dstmap": node.dstmap}
        return {"ok": True, "value": props[name]}

    monkeypatch.setattr(validation, "_read_property", read)


def _operator_response(arguments, *, values=None, cleanup=None, expression_units=None,
                       status_ok=True, pair_mapping_complete=True):
    spec = arguments["spec"]
    points = arguments["points"]
    solution = spec["solution"]
    expressions = spec["expressions"]
    if values is None:
        rows = []
        for expression in expressions:
            axis = expression.rsplit("(", 1)[1][0]
            values_for_axis = []
            axis_index = "xyz".index(axis)
            for point in points:
                value = point[axis_index] + (1 if axis == "x" else (-0.5 if axis == "y" else 0))
                values_for_axis.append(value)
            rows.append(values_for_axis)
        # Canonical FieldArray shape is [expression, outer, inner, point].
        values = [[[row]] for row in rows]
    selected_values = deepcopy(values)
    shape = [len(expressions), 1, 1, len(points)]
    if expression_units is None:
        expression_units = {expression: "m" for expression in expressions}
    return {
        "values": selected_values,
        "dataset": solution["dataset"],
        "solution": solution["solution"],
        "frame": arguments["frame"],
        "coordinate_unit": arguments["coordinate_unit"],
        "dataset_binding": {
            "binding_complete": True,
            "solution": solution["solution"],
            "dataset": solution["dataset"],
            "component": "comp1",
            "geometry": "geom1",
        },
        "feature_readback": {"data": solution["dataset"], "data_status": "VERIFIED"},
        "coordinate_readback": {"status": "VERIFIED", "coordinates": [[p[i] for p in points] for i in range(3)], "unit": "m"},
        "field_array": {
            "axes": ["expression", "outer", "inner", "point"],
            "shape": shape,
            "values": selected_values,
            "coords": {"outer": [solution["outer"]], "inner": [solution["inner"]]},
            "units": {"expression": dict(expression_units)},
            "metadata": {
                "pair_mapping_complete": pair_mapping_complete,
                "solution_pairs": [{"outer": solution["outer"], "inner": solution["inner"], "solnum": 4}],
            },
            "is_complex": False,
        },
        "cleanup": cleanup or {"created": True, "removed": True, "cleanup_failed": False, "tag": "mcp_tmp"},
        "status": {"ok": status_ok, "engine_error": None if status_ok else "synthetic domain error", "cleanup_failed": False},
    }


def _evaluation():
    return {
        "solution": {"dataset": "dset1", "solution": "sol1", "outer": 2, "inner": 4},
        "coordinate_frame": "spatial",
        "coordinate_unit": "m",
        "source_unit": "m",
    }


def test_mapping_arithmetic_is_only_partial_and_never_overall_pass(monkeypatch):
    _patch_coupling_properties(monkeypatch)
    worker = _Worker(_Model())
    result = validation.definition_mapping_validate(worker, "m", _mapping_args())
    assert result["source_frame"] == {"value": "spatial", "status": "OBSERVED"}
    assert result["destination_frame"]["value"] is None
    assert result["checks"]["arithmetic_preview_match"] == "PASS"
    assert result["checks"]["sampled_operator_values_match"] == "UNVERIFIED"
    assert result["checks"]["source_mesh_coverage"] == "UNVERIFIED"
    assert result["ok"] is False
    assert result["status"] == "PARTIAL_UNVERIFIED"


def test_mapping_calls_real_at_points_contract_with_explicit_binding_and_units(monkeypatch):
    _patch_coupling_properties(monkeypatch)
    worker = _Worker(_Model(_Component(coupling=_GeneralExtrusion(srcframe="mesh"))))
    captured = {}

    def fake_at_points(_worker, _model_tag, arguments):
        captured.update(deepcopy(arguments))
        return _operator_response(arguments)

    monkeypatch.setattr("comsol_mcp._g3_results.result_at_points", fake_at_points)
    result = validation.definition_mapping_validate(worker, "m", _mapping_args(evaluation=_evaluation()))
    assert captured["spec"] == {
        "expressions": ["gen1(x)", "gen1(y)", "gen1(z)"],
        "solution": {"dataset": "dset1", "solution": "sol1", "outer": 2, "inner": 4},
        "complex_mode": "real",
    }
    assert captured["points"] == [[1.0, 2.0, 3.0]]
    assert captured["coordinate_unit"] == "m"
    assert captured["frame"] == "spatial"
    assert result["source_frame"]["value"] == "mesh"
    assert result["operator_evaluation"]["status"] == "PASS"
    assert result["checks"]["sampled_operator_values_match"] == "PASS"
    assert result["sampled_operator_values_ok"] is True
    assert result["checks"]["source_mesh_coverage"] == "UNVERIFIED"
    assert result["ok"] is False
    assert result["status"] == "PARTIAL_UNVERIFIED"


@pytest.mark.parametrize("response_kwargs, expected_status", [
    ({"expression_units": {"gen1(x)": "cm", "gen1(y)": "cm", "gen1(z)": "cm"}}, "UNVERIFIED"),
    ({"cleanup": {"created": True, "removed": False, "cleanup_failed": True}}, "EXECUTION_STATE_UNKNOWN"),
    ({"status_ok": False}, "UNVERIFIED"),
    ({"pair_mapping_complete": False}, "UNVERIFIED"),
])
def test_mapping_refuses_units_cleanup_engine_or_axis_gaps(monkeypatch, response_kwargs, expected_status):
    _patch_coupling_properties(monkeypatch)
    worker = _Worker(_Model())

    def fake_at_points(_worker, _model_tag, arguments):
        return _operator_response(arguments, **response_kwargs)

    monkeypatch.setattr("comsol_mcp._g3_results.result_at_points", fake_at_points)
    result = validation.definition_mapping_validate(worker, "m", _mapping_args(evaluation=_evaluation()))
    assert result["operator_evaluation"]["status"] == expected_status
    assert result["checks"]["sampled_operator_values_match"] == "UNVERIFIED"
    assert result["ok"] is False


def test_mapping_rejects_implicit_time_or_partial_solution_binding(monkeypatch):
    _patch_coupling_properties(monkeypatch)
    worker = _Worker(_Model())
    invalid = _evaluation()
    invalid["solution"].pop("outer")
    with pytest.raises(ExecutionContractError) as exc:
        validation.definition_mapping_validate(worker, "m", _mapping_args(evaluation=invalid))
    assert exc.value.code == "INVALID_REQUEST"

    invalid = _evaluation()
    invalid["coordinate_frame"] = "material"
    with pytest.raises(ExecutionContractError) as exc:
        validation.definition_mapping_validate(worker, "m", _mapping_args(evaluation=invalid))
    assert exc.value.code == "API_UNSUPPORTED"


def test_mapping_operator_samples_are_authoritative_over_unsupported_python_preview(monkeypatch):
    worker = _Worker(_Model(_Component(coupling=_GeneralExtrusion(dstmap=("cos(x)", "2*y", "z")))))
    _patch_coupling_properties(monkeypatch)
    args = _mapping_args(
        evaluation=_evaluation(),
        test_points=[{"destination": [0.0, 2.0, 3.0], "destination_unit": "m",
                      "expected_source": [1.0, 4.0, 3.0], "source_unit": "m"}],
    )
    # This witness comes from the result.at_points adapter boundary.  Its
    # actual coupling values can be sampled even though our optional Python
    # arithmetic preview cannot parse cos(x).
    def fake_at_points(_worker, _model_tag, arguments):
        return _operator_response(arguments, values=[[[[1.0]]], [[[4.0]]], [[[3.0]]]])

    monkeypatch.setattr("comsol_mcp._g3_results.result_at_points", fake_at_points)
    result = validation.definition_mapping_validate(worker, "m", args)
    assert result["map"]["arithmetic_preview"]["status"] == "UNAVAILABLE"
    assert result["operator_evaluation"]["status"] == "PASS"
    assert result["checks"]["sampled_operator_values_match"] == "PASS"
    assert result["ok"] is False


@pytest.mark.parametrize("response_kwargs, expected", [
    ({"values": [[[['NaN']]], [[[4.0]]], [[[3.0]]]]}, "VALIDATION_FAILED"),
    ({"values": [[[[9.0]]], [[[4.0]]], [[[3.0]]]]}, "VALIDATION_FAILED"),
])
def test_mapping_preserves_real_domain_or_coordinate_mismatch(response_kwargs, expected, monkeypatch):
    _patch_coupling_properties(monkeypatch)

    def fake_at_points(_worker, _model_tag, arguments):
        return _operator_response(arguments, **response_kwargs)

    monkeypatch.setattr("comsol_mcp._g3_results.result_at_points", fake_at_points)
    result = validation.definition_mapping_validate(_Worker(_Model()), "m", _mapping_args(evaluation=_evaluation()))
    assert result["status"] == expected
    assert result["checks"]["sampled_operator_values_match"] == "FAIL"
    assert result["ok"] is False


def test_mapping_rejects_readback_of_a_different_solution_pair(monkeypatch):
    _patch_coupling_properties(monkeypatch)

    def fake_at_points(_worker, _model_tag, arguments):
        response = _operator_response(arguments)
        response["field_array"]["coords"]["inner"] = [5]
        return response

    monkeypatch.setattr("comsol_mcp._g3_results.result_at_points", fake_at_points)
    result = validation.definition_mapping_validate(_Worker(_Model()), "m", _mapping_args(evaluation=_evaluation()))
    assert result["operator_evaluation"]["status"] == "UNVERIFIED"
    assert "outer/inner" in result["operator_evaluation"]["reason"]
    assert result["ok"] is False


class _FunctionNode:
    def getType(self):
        return "Interpolation"


class _FunctionModel(_Model):
    def __init__(self):
        super().__init__()
        self._functions = {"interp1": _FunctionNode()}

    def func(self, tag):
        return self._functions[tag]


def _function_path():
    return {"segments": [{"collection": "func", "tag": "interp1"}]}


def _patch_function_reader(monkeypatch, *, interp="piecewisecubic", extrap="none", values=None):
    props = {
        "argrange": [["0", "1"]],
        "argunit": ["m"],
        "extrap": extrap,
        "interp": interp,
        "fununit": "Pa",
    }
    monkeypatch.setattr(validation, "_read_property", lambda _node, name: {"ok": True, "value": props[name]})
    monkeypatch.setattr(validation._w13, "_function_evaluate_arity", lambda *_args: 1)
    monkeypatch.setattr(validation._w13, "_function_evaluate_arg_units", lambda *_args: ["m"])

    def evaluate(_worker, _model_tag, arguments):
        rows = []
        for index, argument in enumerate(arguments["arguments"]):
            x = argument["value"]
            val = values(x) if values else 2.0 * x
            rows.append({"index": index, "coordinates": [x], "value": {"real": val, "imag": 0.0}, "unit": "Pa",
                         "value_status": "OK", "unit_status": "OK"})
        return {"results": rows, "ok": True, "status": "OBSERVED"}

    monkeypatch.setattr(validation._w13, "function_evaluate", evaluate)


def test_function_method_order_is_not_global_continuity_and_boundary_is_sampled(monkeypatch):
    _patch_function_reader(monkeypatch, interp="piecewisecubic", extrap="linear")
    worker = _Worker(_FunctionModel())
    result = validation.function_validate(worker, "m", {"path": _function_path(), "checks": {
        "continuity": "C1", "extrapolation": ["linear"],
        "domain": {"lower": 0, "upper": 1, "unit": "m"},
    }})
    assert result["continuity"]["guaranteed_minimum"] == "C1"
    assert result["continuity"]["global_data_continuity"] == "UNVERIFIED"
    assert result["checks"][0]["status"] == "PASS"
    assert result["checks"][1]["status"] == "PASS"
    assert result["checks"][2]["status"] == "PASS"
    assert result["checks"][4]["check"] == "actual_boundary_neighbor_behavior"
    assert result["checks"][4]["status"] == "OBSERVED"
    assert result["ok"] is False
    assert result["status"] == "PARTIAL_UNVERIFIED"


def test_function_explicit_discontinuity_probe_finds_data_jump(monkeypatch):
    _patch_function_reader(monkeypatch, interp="piecewisecubic", values=lambda x: 0.0 if x < 0.5 else 100.0)
    result = validation.function_validate(_Worker(_FunctionModel()), "m", {
        "path": _function_path(),
        "checks": {"continuity_probes": [{"at": 0.5, "delta": 0.1, "max_jump": 1.0, "unit": "Pa"}]},
    })
    probe = result["checks"][-1]
    assert probe["check"] == "caller_selected_local_continuity_probes"
    assert probe["status"] == "FAIL"
    assert probe["probes"][0]["observed_jump"] == 100.0
    assert result["status"] == "VALIDATION_FAILED"
    assert result["continuity"]["global_data_continuity"] == "UNVERIFIED"


def test_function_boundary_probe_separates_none_extrapolation_from_in_domain_samples(monkeypatch):
    _patch_function_reader(monkeypatch, interp="linear", extrap="none")
    original = validation._w13.function_evaluate

    def evaluate(worker, model_tag, arguments):
        response = original(worker, model_tag, arguments)
        for row in response["results"]:
            if row["coordinates"][0] < 0 or row["coordinates"][0] > 1:
                row.update({"value": None, "value_status": "EVALUATION_ERROR",
                            "errors": [{"stage": "evaluateComplex", "code": "OUTSIDE_DEFINED_RANGE"}]})
        return response

    monkeypatch.setattr(validation._w13, "function_evaluate", evaluate)
    result = validation.function_validate(_Worker(_FunctionModel()), "m", {"path": _function_path()})
    primary = next(row for row in result["checks"] if row["check"] == "configured_interval_sample_availability")
    boundary = next(row for row in result["checks"] if row["check"] == "actual_boundary_neighbor_behavior")
    assert primary["status"] == "PASS"
    assert boundary["status"] == "OBSERVED"
    assert [row["value_status"] for row in boundary["samples"]] == ["OK", "EVALUATION_ERROR", "OK", "EVALUATION_ERROR"]
    assert result["ok"] is False
    assert result["status"] == "PARTIAL_UNVERIFIED"


@pytest.mark.parametrize("checks", [
    {"continuity": []},
    {"continuity_probes": [{"at": 0.5, "delta": -1, "max_jump": 0, "unit": "Pa"}]},
    {"continuity_probes": [{"at": 0.0, "delta": 0.1, "max_jump": 0, "unit": "Pa"}]},
])
def test_function_invalid_continuity_probe_inputs_are_rejected(monkeypatch, checks):
    _patch_function_reader(monkeypatch)
    with pytest.raises(ExecutionContractError) as exc:
        validation.function_validate(_Worker(_FunctionModel()), "m", {"path": _function_path(), "checks": checks})
    assert exc.value.code == "INVALID_REQUEST"


def _selection_args(*, preview=True):
    return {
        "selection": {"kind": "named", "component": "comp1", "tag": "box1", "geometry": "geom1"},
        "rule": {"kind": "box", "entity_dimension": 1, "geometry": "geom1", "unit": "m",
                 "bounds": {"xmin": 0, "xmax": 1, "ymin": 0, "ymax": 1, "zmin": 0, "zmax": 1}},
        "preview": preview,
    }


def test_selection_rebind_preview_preserves_rule_identity_and_performs_no_write():
    selection = _SpatialSelectionNode()
    worker = _Worker(_Model(_Component(selection_node=selection)))
    result = validation.selection_rebind(worker, "m", _selection_args())
    assert result["preview"] is True
    assert result["write_performed"] is False
    assert result["requested_semantics"]["properties"] == {
        "xmin": 0.0, "xmax": 1.0, "ymin": 0.0, "ymax": 1.0, "zmin": 0.0, "zmax": 1.0,
        "condition": "intersects",
    }
    assert "entitydim" not in result["requested_semantics"]["properties"]
    assert selection.set_calls == []


def test_selection_rebind_write_requires_final_semantic_readback(monkeypatch):
    selection = _SpatialSelectionNode()
    worker = _Worker(_Model(_Component(selection_node=selection)))
    calls = []

    def apply(_path, node, _worker, _model_tag, _type_id, definition):
        calls.append(deepcopy(definition))
        node._dimension = definition["entity_dimension"]
        node._entities = [8, 9]
        return {"applied": [{"property": "entitydim"}], "properties": {"applied": [{"property": "xmin"}]},
                "failed": [], "execution_state_unknown": False}

    monkeypatch.setattr(validation._w13, "_apply_selection_definition", apply)
    args = _selection_args(preview=False)
    result = validation.selection_rebind(worker, "m", args)
    assert calls[0]["entity_dimension"] == 1
    assert "entitydim" not in calls[0]["properties"]
    assert result["entities"] == [8, 9]
    assert result["current_entity_dimension"] == 1
    assert result["numeric_id_stability"] == "NOT_CLAIMED_ACROSS_GEOMETRY_REVISIONS"
    assert result["status"] == "REBOUND"


def test_selection_rebind_failure_receipt_does_not_infer_change_from_empty_write_report(monkeypatch):
    worker = _Worker(_Model())
    monkeypatch.setattr(validation._w13, "_apply_selection_definition", lambda *_args: {
        "applied": [], "properties": {"applied": [], "failed": [{"property": "xmin"}]},
        "failed": [{"property": "xmin"}], "execution_state_unknown": False,
    })
    result = validation.selection_rebind(worker, "m", _selection_args(preview=False))
    assert result["write_performed"] is False
    assert result["partial_change"] is False
    assert result["status"] == "FAILED"


def test_selection_rebind_refuses_stale_geometry_or_wrong_type_without_write(monkeypatch):
    selection = _SpatialSelectionNode(geometry="geom2")
    worker = _Worker(_Model(_Component(selection_node=selection)))
    with pytest.raises(ExecutionContractError) as exc:
        validation.selection_rebind(worker, "m", _selection_args(preview=False))
    assert exc.value.code == "API_UNSUPPORTED"
    assert selection.set_calls == []

    selection = _SpatialSelectionNode()
    selection.getType = lambda: "Ball"
    worker = _Worker(_Model(_Component(selection_node=selection)))
    with pytest.raises(ExecutionContractError) as exc:
        validation.selection_rebind(worker, "m", _selection_args(preview=False))
    assert exc.value.code == "API_UNSUPPORTED"
    assert selection.set_calls == []


def test_selection_rebind_rejects_malformed_rule_types_before_api_write():
    worker = _Worker(_Model())
    args = _selection_args()
    args["rule"]["kind"] = []
    with pytest.raises(ExecutionContractError) as exc:
        validation.selection_rebind(worker, "m", args)
    assert exc.value.code == "API_UNSUPPORTED"
