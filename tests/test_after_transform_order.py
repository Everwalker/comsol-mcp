"""Main-confirmed existing after contract, normal Eval with inert native leaves."""
from __future__ import annotations

import cmath
import json
import math
from types import SimpleNamespace

import pytest

from comsol_mcp._g3_ops import dispatch
from test_g3_w17 import FWiredTree


EXPRESSION = "unfamiliar_complex_field"


def _numbers(value):
    if isinstance(value, list):
        for child in value:
            yield from _numbers(child)
    else:
        yield value


def _tree(tmp_path, *, point_values=None):
    tree = FWiredTree(numerical_real=3.0, numerical_imag=4.0, is_complex=True)
    worker = tree.worker
    worker.paths = SimpleNamespace(project_root=tmp_path)
    original = tree.numerical_list.factory
    created = []

    def factory(tag, *args):
        node = original(tag, *args)
        if tag.endswith("_meas"):
            node.real_data, node.imag_data, node.is_complex_flag = 2.0, None, False
        if point_values is not None:
            # Existing native EvalPoint leaf layout: [expression*point][inner].
            real = [[z.real] * 3 for z in point_values]
            imag = [[z.imag] * 3 for z in point_values]

            def read(method, args, values):
                node._guard(method, args)
                return values

            node.getData = lambda: read("getData", (), real)
            node.getReal = lambda *a: read("getReal", a, real)
            node.getImagData = lambda: read("getImagData", (), imag)
            node.getImag = lambda *a: read("getImag", a, imag)
        created.append(node)
        return node

    tree.numerical_list.factory = factory
    return tree, worker, created


def _evaluate(worker, **options):
    return dispatch("result.evaluate", worker, "Model", {"spec": {
        "expressions": [EXPRESSION], "solution": {"dataset": "dset1"},
        "complex_transform_order": "after", "storage": "inline", **options,
    }})


def _observe(case, result, tree, created, expected_current, reference, inputs):
    actual = list(_numbers(result["values"]))
    assert result["status"]["ok"] is True
    assert not tree.numerical_list.items
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    print("CONTRACT_COUNTER " + json.dumps({
        "case": case, "actual_values": result["values"],
        "reference": reference, "historical_formula": expected_current,
        "classification": "MAIN_CONFIRMED_EXISTING_AFTER_CONTRACT",
        "field_array": result["field_array"]["shape"],
        "output_units": result["expression_units"],
    }, sort_keys=True))
    assert actual == pytest.approx([reference] * 3)
    assert result["field_array"]["values"] == result["values"]
    assert result["field_array"]["shape"] == [1, 1, 3, 1]


def test_weighted_average_after_phase(tmp_path):
    tree, worker, created = _tree(tmp_path)
    result = _evaluate(worker, aggregate="average", complex_mode="phase",
                       weight_expression="unfamiliar_positive_weight")
    numerator, measure = 3.0 + 4.0j, 2.0
    _observe("weighted_average_explicit_after_phase_M2", result, tree, created,
             math.atan2(4.0, 3.0) / measure, cmath.phase(numerator / measure),
             {"native_numerator": {"real": 3.0, "imag": 4.0}, "native_measure": measure})


def test_point_integral_after_phase(tmp_path):
    points = (1.0 + 1.0j, -1.0 + 1.0j)
    tree, worker, created = _tree(tmp_path, point_values=points)
    result = _evaluate(worker, aggregate="integral", complex_mode="phase", entity_dim=0)
    _observe("point_integral_explicit_after_phase", result, tree, created,
             math.pi, cmath.phase(sum(points)),
             {"points": [{"real": z.real, "imag": z.imag} for z in points],
              "measure": "existing discrete point sum, no continuum physical claim"})


def test_point_integral_after_abs(tmp_path):
    points = (1.0 + 1.0j, -1.0 + 1.0j)
    tree, worker, created = _tree(tmp_path, point_values=points)
    result = _evaluate(worker, aggregate="integral", complex_mode="abs", entity_dim=0)
    _observe("point_integral_explicit_after_abs", result, tree, created,
             2.0 * math.sqrt(2.0), abs(sum(points)),
             {"points": [{"real": z.real, "imag": z.imag} for z in points],
              "measure": "existing discrete point sum, no continuum physical claim"})


# The following fixtures emulate only native getter leaves. All request
# validation, feature choice/binding, reductions, division, FieldArray wrapping,
# selectors, units, exports and cleanup remain production code.
MODES = ("preserve", "real", "imag", "abs", "phase")
BRANCHES = (
    ("weighted_average", "average", False, True),
    ("point_average", "average", True, False),
    ("weighted_point_average", "average", True, True),
    ("point_integral", "integral", True, False),
    ("weighted_point_integral", "integral", True, True),
    ("weighted_point_none", "none", True, True),
    ("weighted_point_global", "global", True, True),
    ("native_average", "average", False, False),
    ("native_integral", "integral", False, False),
    ("native_weighted_integral", "integral", False, True),
)


def _reference_transform(z, mode):
    if mode == "preserve":
        return {"real": z.real, "imag": z.imag}
    if mode == "real":
        return z.real
    if mode == "imag":
        return z.imag
    if mode == "abs":
        return abs(z)
    return cmath.phase(z) if z else 0.0


def _cube(expressions=1, outers=1, inners=3):
    return [[[
        [complex(1 + e + 2 * o + i, 1 + i), complex(-2 * (e + 1) + o, -1 - i / 2)]
        for i in range(inners)] for o in range(outers)] for e in range(expressions)]


def _dynamic_fixture(tmp_path, branch, mode, *, order="after", cube=None,
                     binding=True, weights=None):
    _name, aggregate, point, weighted = branch
    cube = cube if cube is not None else _cube()
    ne, no, ni, np = len(cube), len(cube[0]), len(cube[0][0]), len(cube[0][0][0])
    weights = weights if weights is not None else [
        [[1.0 + o + i, 2.0] if weighted else [1.0] * np for i in range(ni)]
        for o in range(no)
    ]
    tree = FWiredTree(numerical_real=0.0, is_complex=True)
    tree.solution_info.props.update(getOuterSolnum=list(range(1, no + 1)), getMaxInner=ni)
    tree.solver.props["getPVals"] = [i * 0.1 for i in range(ni)]
    if not binding:
        assert no == 1
        tree.solution_info.getSolnum = None
    worker = tree.worker
    worker.paths = SimpleNamespace(project_root=tmp_path)
    original = tree.numerical_list.factory
    created = []

    def projected(z):
        if order == "before" and mode != "preserve":
            return complex(_reference_transform(z, mode), 0.0)
        return z

    def factory(tag, *args):
        node = original(tag, *args)
        measure = tag.endswith("_meas")
        numerator = tag.endswith("_num")
        node.is_complex_flag = not measure and not (order == "before" and mode != "preserve")

        def native_values(outer):
            o = int(outer) - 1
            expression_count = 1 if measure else ne
            rows = []
            for e in range(expression_count):
                by_inner = []
                for i in range(ni):
                    w = weights[o][i]
                    values = ([complex(x) for x in w] if measure else [
                        projected(z) * (w[k] if numerator else 1)
                        for k, z in enumerate(cube[e][o][i])
                    ])
                    if not point:
                        value = sum(values)
                        if not measure and not numerator and node.type_id.startswith("Av"):
                            value /= np
                        values = [value]
                    by_inner.append(values)
                if point:
                    rows.extend([[by_inner[i][k] for i in range(ni)] for k in range(np)])
                else:
                    rows.append([by_inner[i][0] for i in range(ni)])
            return rows

        def read(method, args, component):
            node._guard(method, args)
            outer = args[-1] if args else node.props.get("outersolnum", 1)
            rows = native_values(outer)
            return [[(z.real if component == "real" else z.imag) for z in row] for row in rows]

        node.getReal = lambda *a: read("getReal", a, "real")
        node.getData = lambda: read("getData", (), "real")
        node.getImag = lambda *a: read("getImag", a, "imag")
        node.getImagData = lambda: read("getImagData", (), "imag")
        created.append(node)
        return node

    tree.numerical_list.factory = factory
    names = [f"arbitrary_expression_{e}" for e in range(ne)]
    spec = {"expressions": names, "solution": {"dataset": "dset1"},
            "aggregate": aggregate, "complex_mode": mode,
            "complex_transform_order": order, "storage": "inline"}
    if point:
        spec["entity_dim"] = 0
    if weighted:
        spec["weight_expression"] = "unfamiliar_spatial_weight"

    expected = []
    for e in range(ne):
        outer_rows = []
        for o in range(no):
            inner_rows = []
            for i in range(ni):
                w = weights[o][i]
                terms = [projected(z) * (w[k] if weighted else 1)
                         for k, z in enumerate(cube[e][o][i])]
                z = sum(terms)
                if aggregate == "average":
                    z /= sum(w) if weighted else np
                value = (z.real if order == "before" and mode != "preserve"
                         else _reference_transform(z, mode))
                inner_rows.append([value])
            outer_rows.append(inner_rows)
        expected.append(outer_rows)
    return tree, worker, created, spec, expected


def _assert_nested(actual, expected):
    if isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected)
        for value, reference in zip(actual, expected):
            _assert_nested(value, reference)
    elif isinstance(expected, dict):
        assert set(actual) == set(expected)
        for key, reference in expected.items():
            assert actual[key] == pytest.approx(reference)
    else:
        assert actual == pytest.approx(expected)


@pytest.mark.parametrize("branch", BRANCHES, ids=[b[0] for b in BRANCHES])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("order", ["after", "before"])
def test_normal_aggregate_all_modes_and_orders(tmp_path, branch, mode, order):
    tree, worker, created, spec, expected = _dynamic_fixture(tmp_path, branch, mode, order=order)
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    _assert_nested(result["values"], expected)
    assert result["status"]["ok"] and not tree.numerical_list.items
    assert result["field_array"]["values"] == result["values"]
    assert result["field_array"]["is_complex"] is (mode == "preserve")
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    for node in created:
        assert node.calls.count(("run", ())) == 1
        if node.type_id == "EvalGlobal":
            # Original primary EvalGlobal uses getData; only its auxiliary
            # integral features use outer-aware getReal/getImag.
            assert node.calls.count(("getData", ())) == 1
            assert node.calls.count(("getImagData", ())) == int(node.is_complex_flag)
            assert not any(call[0] in {"getReal", "getImag"} for call in node.calls)
        else:
            assert node.calls.count(("getReal", (False, 1))) == 1
            assert node.calls.count(("getImag", (1,))) == int(node.is_complex_flag)
            assert not any(call[0] in {"getData", "getImagData"} for call in node.calls)
    if order == "after" and mode == "phase":
        derivation = result["value_unit_derivation"]
        assert result["expression_units"] == dict.fromkeys(spec["expressions"], "rad")
        assert result["native_expression_units"] == dict.fromkeys(spec["expressions"], "K")
        assert derivation["output_unit"] == "rad"
        assert derivation["final_range_guarantee"] == "[-pi, pi]"
        assert derivation["preprocessing"]["point_sum_applied"] is branch[2]
        assert derivation["preprocessing"]["measure_division_applied"] is (
            branch[1] == "average" and (branch[2] or branch[3]))
        assert derivation["postprocessing"] == {
            "point_sum_applied": False, "measure_division_applied": False, "denominator_source": None}
    else:
        assert "value_unit_derivation" not in result
    if order == "before" and mode != "preserve":
        function = {"real": "real", "imag": "imag", "abs": "abs", "phase": "arg"}[mode]
        assert result["evaluated_expressions"] == [f"{function}(({e}))" for e in spec["expressions"]]


@pytest.mark.parametrize("branch", BRANCHES[:7], ids=[b[0] for b in BRANCHES[:7]])
@pytest.mark.parametrize("mode", ["phase", "abs", "preserve"])
def test_multi_expression_outer_inner_then_selector(tmp_path, branch, mode):
    tree, worker, created, spec, expected = _dynamic_fixture(tmp_path, branch, mode, cube=_cube(2, 2, 3))
    spec["solution"].update(outer=[2], inner="last")
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    selected = [[[expected[e][1][-1]] for _ in [0]] for e in range(2)]
    _assert_nested(result["values"], selected)
    field = result["field_array"]
    assert field["shape"] == [2, 1, 1, 1]
    assert field["coords"]["outer"] == [2] and field["coords"]["inner"] == [3]
    assert field["coords"]["expression"] == spec["expressions"]
    assert not tree.numerical_list.items
    for node in created:
        assert node.calls.count(("run", ())) == 2
        if node.type_id == "EvalGlobal":
            assert node.calls.count(("getData", ())) == 2
        else:
            assert node.calls.count(("getReal", (False, 1))) == 1
            assert node.calls.count(("getReal", (False, 2))) == 1


@pytest.mark.parametrize("mode", ["phase", "abs"])
def test_final_scalar_axes_zero_and_cancellation(tmp_path, mode):
    pairs = [(0j, 0j), (1+0j, -2+0j), (-1+0j, -2+0j), (1j, 1j),
             (-1j, -1j), (1+1j, -1-1j), (1+0j, -1+0j)]
    tree, worker, _created, spec, expected = _dynamic_fixture(
        tmp_path, BRANCHES[3], mode, cube=[[[list(pair) for pair in pairs]]])
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    _assert_nested(result["values"], expected)
    if mode == "phase":
        assert list(_numbers(result["values"])) == pytest.approx(
            [0.0, math.pi, math.pi, math.pi / 2, -math.pi / 2, 0.0, 0.0])
    assert not tree.numerical_list.items


@pytest.mark.parametrize("binding", [True, False])
@pytest.mark.parametrize("surface", ["inline", "artifact", "json", "csv"])
@pytest.mark.parametrize("branch", [BRANCHES[0], BRANCHES[2]], ids=[BRANCHES[0][0], BRANCHES[2][0]])
def test_after_final_values_units_and_metadata_all_exports(tmp_path, binding, surface, branch):
    import csv
    from pathlib import Path
    tree, worker, created, spec, expected = _dynamic_fixture(tmp_path, branch, "phase", binding=binding)
    if surface in {"json", "csv"}:
        target = tmp_path / f"after.{surface}"
        dispatch("result.field_export", worker, "Model", {"spec": spec, "format": surface, "destination": str(target)})
        if surface == "csv":
            with target.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            assert [float(row["real"]) for row in rows] == pytest.approx(list(_numbers(expected)))
            assert {row["unit"] for row in rows} == {"rad"}
            assert not tree.numerical_list.items
            return
        saved = json.loads(target.read_text())
        values, result = saved["values"], saved["metadata"]
    else:
        spec["storage"] = surface
        result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
        if surface == "artifact":
            path = Path(result["artifact"]["artifact_ref"])
            saved = json.loads((path if path.is_absolute() else tmp_path / path).read_text())
            values, result = saved["values"], saved["metadata"]
        else:
            values = result["values"]
    assert list(_numbers(values)) == pytest.approx(list(_numbers(expected)))
    assert result["expression_units"] == dict.fromkeys(spec["expressions"], "rad")
    assert result["native_expression_units"] == dict.fromkeys(spec["expressions"], "K")
    assert result["value_unit_derivation"]["preprocessing"]["measure_division_applied"] is True
    assert result["value_unit_derivation"]["native_unit_conversion"] == "UNVERIFIED"
    assert (result.get("field_array") is not None) is binding
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    assert not tree.numerical_list.items


def test_requested_scalar_transform_called_once_per_final_tuple(tmp_path, monkeypatch):
    from comsol_mcp import _complex_transform as service
    original = service.transform_complex_value
    calls = []

    def recorded(real, imag, mode):
        calls.append((real, imag, mode))
        return original(real, imag, mode)

    monkeypatch.setattr(service, "transform_complex_value", recorded)
    tree, worker, _created, spec, expected = _dynamic_fixture(tmp_path, BRANCHES[2], "phase", cube=_cube(2, 2, 3))
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    _assert_nested(result["values"], expected)
    assert len([c for c in calls if c[2] == "phase"]) == 2 * 2 * 3
    assert {c[2] for c in calls} == {"preserve", "phase"}


@pytest.mark.parametrize("branch", [BRANCHES[0], BRANCHES[3]], ids=[BRANCHES[0][0], BRANCHES[3][0]])
def test_missing_imaginary_still_refused_before_reduction(tmp_path, branch):
    from comsol_mcp._execution_contract import ExecutionContractError
    tree, worker, _created, spec, _expected = _dynamic_fixture(tmp_path, branch, "phase")
    original = tree.numerical_list.factory

    def factory(tag, *args):
        node = original(tag, *args)
        if not tag.endswith("_meas"):
            node.getImag = lambda *a: node._guard("getImag", a)
        return node

    tree.numerical_list.factory = factory
    with pytest.raises(ExecutionContractError) as error:
        dispatch("result.evaluate", worker, "Model", {"spec": spec})
    assert error.value.code == "COMPLEX_DATA_ERROR"
    assert not tree.numerical_list.items


@pytest.mark.parametrize("bad", [None, 1.0, {}, {"real": 1.0}, {"real": True, "imag": 0.0},
    {"real": "1", "imag": 0}, {"real": float("inf"), "imag": 0},
    {"real": 1, "imag": float("nan")}])
def test_pure_post_aggregate_transform_requires_validated_components(bad):
    from comsol_mcp._complex_transform import transform_preserved_data
    from comsol_mcp._execution_contract import ExecutionContractError
    with pytest.raises(ExecutionContractError) as error:
        transform_preserved_data([bad], "phase")
    assert error.value.code == "COMPLEX_DATA_ERROR"


@pytest.mark.parametrize("mode", ["real", "imag", "abs", "phase"])
def test_confirmed_real_native_status_is_distinct_from_internal_complex_maps(tmp_path, monkeypatch, mode):
    from comsol_mcp._solution_binding import FieldArray
    original_init = FieldArray.__init__
    internal_flags = []

    def recorded_init(self, *args, **kwargs):
        internal_flags.append(kwargs.get("is_complex"))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(FieldArray, "__init__", recorded_init)
    cube = [[[[complex(-1.0), complex(2.0)], [complex(-3.0), complex(-1.0)]]]]
    tree, worker, created, spec, expected = _dynamic_fixture(tmp_path, BRANCHES[2], mode, cube=cube)
    original = tree.numerical_list.factory

    def factory(tag, *args):
        node = original(tag, *args)
        node.is_complex_flag = False
        return node

    tree.numerical_list.factory = factory
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    _assert_nested(result["values"], expected)
    assert result["is_complex"] is False and result["field_array"]["is_complex"] is False
    assert True in internal_flags and internal_flags[-1] is False
    assert not any(call[0] in {"getImag", "getImagData"} for node in created for call in node.calls)
    assert not tree.numerical_list.items


def test_missing_weighted_numerator_imaginary_keeps_original_error_and_cleanup(tmp_path):
    from comsol_mcp._execution_contract import ExecutionContractError
    tree, worker, _created, spec, _expected = _dynamic_fixture(tmp_path, BRANCHES[0], "phase")
    original = tree.numerical_list.factory

    def factory(tag, *args):
        node = original(tag, *args)
        if tag.endswith("_num"):
            node.getImag = lambda *a: node._guard("getImag", a)
        return node

    tree.numerical_list.factory = factory
    with pytest.raises(ExecutionContractError) as error:
        dispatch("result.evaluate", worker, "Model", {"spec": spec})
    assert error.value.code == "ENGINE_CALL_FAILED"
    assert not tree.numerical_list.items


def test_strict_weighted_after_phase_preserves_native_evidence_and_uncertainty(tmp_path):
    from comsol_mcp import _g3_results as results
    tree = FWiredTree(numerical_real=3.0, numerical_imag=4.0, is_complex=True,
                     native_expression_units={"w": "1"}, weight_minimum=0.5)
    worker = tree.worker
    worker.paths = SimpleNamespace(project_root=tmp_path)
    original = tree.numerical_list.factory

    def factory(tag, *args):
        node = original(tag, *args)
        if node.type_id.startswith("Min"):
            node.is_complex_flag, node.imag_data = False, None
        elif tag.endswith("_meas"):
            node.real_data, node.imag_data, node.is_complex_flag = 2.0, None, False
        return node

    tree.numerical_list.factory = factory
    spec = {"expressions": [EXPRESSION], "solution": {"dataset": "dset1"},
            "aggregate": "average", "complex_mode": "phase", "complex_transform_order": "after",
            "weight_expression": "w", "selection": {"kind": "explicit", "component": "comp1",
            "geometry": "geom1", "entity_dimension": 3, "entities": [1, 2]}, "storage": "inline"}
    result = results.result_evaluate(worker, "Model", {"spec": spec}, strict_metric_evidence=True)
    assert list(_numbers(result["values"])) == pytest.approx([cmath.phase((3+4j)/2)] * 3)
    assert result["expression_units"] == {EXPRESSION: "rad"}
    assert result["native_expression_units"] == {EXPRESSION: "K"}
    evidence = result["strict_metric_evidence"]
    assert evidence["expression_unit_readback"] == {EXPRESSION: "K"}
    assert evidence["weight_validation"]["status"] == "UNVERIFIED"
    assert evidence["weight_validation"]["unit_evidence"]["roi_context_dimensionality"]["status"] == "UNVERIFIED"
    assert not tree.numerical_list.items
