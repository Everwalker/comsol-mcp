from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from comsol_mcp._g3_ops import dispatch
from comsol_mcp import _g3_results as results
from comsol_mcp._execution_contract import ExecutionContractError
from test_g3_results import _dispatch_public_result_evaluate, _raw_eval_stationary_fixture
from test_g3_w17 import FWiredTree


EXPRESSION = "unfamiliar_output_field"


def _tree(tmp_path: Path, *, real=None, imag=None):
    tree = FWiredTree(numerical_real=real if real is not None else [[3.0, 6.0]],
                     numerical_imag=imag if imag is not None else [[4.0, 8.0]], is_complex=True)
    worker = tree.worker
    worker.paths = SimpleNamespace(project_root=tmp_path)
    created = []
    original = tree.numerical_list.factory

    def factory(tag: str, *args: Any):
        node = original(tag, *args)
        if tag.endswith(("_meas", "_selmeasure")):
            # Inert native integral-of-1/weight leaf, distinct from the field.
            node.real_data, node.imag_data, node.is_complex_flag = 2.0, None, False
        created.append(node)
        return node

    tree.numerical_list.factory = factory
    return tree, worker, created


def _spec(**extra: Any):
    return {"expressions": [EXPRESSION], "solution": {"dataset": "dset1"},
            "aggregate": "none", "complex_mode": "phase", **extra}


def _numbers(value: Any):
    if isinstance(value, list):
        for child in value:
            yield from _numbers(child)
    else:
        yield value


def _artifact_json(result: dict[str, Any], root: Path):
    path = Path(result["artifact"]["artifact_ref"])
    return json.loads((path if path.is_absolute() else root / path).read_text())


@pytest.mark.parametrize("surface", ["response", "field_array", "auto_json", "field_export_json"])
def test_normal_after_phase_value_units_all_surfaces(tmp_path: Path, surface: str):
    tree, worker, created = _tree(tmp_path)
    if surface == "field_export_json":
        target = tmp_path / "phase.json"
        dispatch("result.field_export", worker, "Model", {
            "spec": _spec(), "format": "json", "destination": str(target),
        })
        saved = json.loads(target.read_text())
        values, metadata = saved["values"], saved["metadata"]
        unit = metadata["expression_units"][EXPRESSION]
    else:
        result = dispatch("result.evaluate", worker, "Model", {
            "spec": _spec(storage="artifact" if surface == "auto_json" else "inline"),
        })
        if surface == "auto_json":
            saved = _artifact_json(result, tmp_path)
            values, metadata = saved["values"], saved["metadata"]
            unit = metadata["field_array"]["units"]["expression"][EXPRESSION]
        else:
            values, metadata = result["values"], result
            unit = (result["expression_units"][EXPRESSION] if surface == "response"
                    else result["field_array"]["units"]["expression"][EXPRESSION])
    assert list(_numbers(values))
    assert all(math.isclose(v, math.atan2(4.0, 3.0)) for v in _numbers(values))
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    assert not tree.numerical_list.items
    print(json.dumps({"surface": surface, "values": values, "unit": unit,
                      "native_property": metadata["expression_unit_observation"]["raw_property"]}))
    assert unit == "rad"
    assert metadata["native_expression_units"] == {EXPRESSION: "K"}
    _assert_derivation(metadata)


def _assert_derivation(result, *, predivide=False, pre_point_sum=False):
    evidence = result["value_unit_derivation"]
    assert evidence["source"] == "PYTHON_MATH_ATAN2"
    assert evidence["output_unit"] == "rad"
    assert evidence["status"] == "DERIVED_PYTHON_PHASE_RADIANS"
    assert evidence["complex_transform_order"] == "after"
    assert "phase_convention" not in evidence
    scalar = evidence["scalar_intermediate"]
    assert scalar["source"] == "PYTHON_MATH_ATAN2" and scalar["output_unit"] == "rad"
    assert scalar["phase_convention"] == {"interval": "[-pi, pi]", "zero_magnitude_value": 0.0}
    assert evidence["final_range_guarantee"] == "[-pi, pi]"
    post = evidence["postprocessing"]
    assert post["measure_division_applied"] is False
    assert post["point_sum_applied"] is False
    assert post["denominator_source"] is None
    pre = evidence["preprocessing"]
    assert pre["measure_division_applied"] is predivide
    assert pre["point_sum_applied"] is pre_point_sum
    if predivide:
        assert pre["denominator_source"]
        if "denominator_source" in result:
            assert pre["denominator_source"] == result["denominator_source"]
    else:
        assert pre["denominator_source"] is None
    assert evidence["native_unit_conversion"] == "UNVERIFIED"
    assert evidence["physical_dimensionality"] == "UNVERIFIED"
    observation = result["expression_unit_observation"]
    assert observation["native_unit_conversion"] == "UNVERIFIED"
    assert observation["phase_output_unit_semantics"].startswith("UNVERIFIED")


def _unit_leaf(tree, raw):
    original = tree.numerical_list.factory

    def factory(tag, *args):
        node = original(tag, *args)
        getter = node.getStringArray

        def read(name):
            value = getter(name)
            return raw if name == "unit" else value

        node.getStringArray = read
        return node

    tree.numerical_list.factory = factory


@pytest.mark.parametrize("raw,native", [(["K"], {EXPRESSION: "K"}),
    ([None], {EXPRESSION: None}), ([""], {EXPRESSION: None}),
    (None, {}), ([], {EXPRESSION: None})])
def test_after_phase_known_radians_do_not_certify_unknown_native_property(tmp_path, raw, native):
    tree, worker, created = _tree(tmp_path)
    _unit_leaf(tree, raw)
    result = dispatch("result.evaluate", worker, "Model", {"spec": _spec(units=["degC"])})
    assert result["expression_units"] == {EXPRESSION: "rad"}
    assert result["native_expression_units"] == native
    field = result["field_array"]
    assert field["units"]["expression"] == {EXPRESSION: "rad"}
    assert field["metadata"]["expression_units"] == {EXPRESSION: "rad"}
    assert field["metadata"]["native_expression_units"] == native
    assert field["metadata"]["unit_readback_status"] == ("VERIFIED" if raw == ["K"] else "UNVERIFIED")
    assert field["metadata"]["unit_readback_scope"] == "native_numerical_feature_property"
    assert field["metadata"]["expression_unit_observation"] == result["expression_unit_observation"]
    assert field["metadata"]["value_unit_derivation"] == result["value_unit_derivation"]
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    assert ("set", ("unit", ["degC"])) in created[0].calls
    assert result["cleanup"]["removed"] and result["status"]["ok"]
    _assert_derivation(result)


def test_multiple_expression_positions_keep_raw_observation(tmp_path):
    expressions = ["alien_A", "alien_B"]
    tree, worker, created = _tree(tmp_path, real=[[3.0, 3.0], [0.0, 0.0]],
                                  imag=[[4.0, 4.0], [2.0, 2.0]])
    _unit_leaf(tree, ["K", None])
    result = dispatch("result.evaluate", worker, "Model", {"spec": _spec(expressions=expressions)})
    assert result["expression_units"] == dict.fromkeys(expressions, "rad")
    assert result["native_expression_units"] == dict(zip(expressions, ["K", None]))
    assert all(math.isclose(v, math.atan2(4, 3)) for v in _numbers(result["values"][0]))
    assert all(math.isclose(v, math.pi / 2) for v in _numbers(result["values"][1]))
    rows = result["expression_unit_observation"]["expression_rows"]
    assert [row["requested_expression"]["value"] for row in rows] == expressions
    assert [row["native_property_value"]["kind"] for row in rows] == ["string", "null"]
    assert rows[1]["duplicate_requested_expression_name"] is False
    assert result["field_array"]["shape"][0] == 2
    assert result["field_array"]["coords"]["expression"] == expressions
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1


def test_duplicate_expression_request_remains_refused_before_native(tmp_path):
    # The normal validator already rejects duplicate names. The unchanged
    # observation-helper duplicate-row control runs in the existing unit suite.
    tree, worker, created = _tree(tmp_path)
    with pytest.raises(ExecutionContractError) as exc:
        dispatch("result.evaluate", worker, "Model", {
            "spec": _spec(expressions=["repeated", "repeated"]),
        })
    assert exc.value.code == "INVALID_REQUEST"
    assert not created


def test_python_phase_convention_zero_and_negative_axis_are_not_native_claims(tmp_path):
    tree, worker, _ = _tree(tmp_path, real=[[0.0, -3.0, -3.0, 0.0]],
                           imag=[[0.0, 0.0, -0.0, 4.0]])
    result = dispatch("result.evaluate", worker, "Model", {"spec": _spec()})
    assert list(_numbers(result["values"])) == pytest.approx([0.0, math.pi, -math.pi, math.pi / 2])
    _assert_derivation(result)


@pytest.mark.parametrize("raw,native", [(["1"], {EXPRESSION: "1"}), (["deg"], {EXPRESSION: "deg"}),
    (["K"], {EXPRESSION: "K"}), ([None], {EXPRESSION: None}), ([""], {EXPRESSION: None}), (None, {})])
def test_before_native_phase_preserves_actual_property_and_numeric_scale(tmp_path, raw, native):
    tree = FWiredTree(numerical_real=123.5, is_complex=False)
    _unit_leaf(tree, raw)
    result = dispatch("result.evaluate", tree.worker, "Model", {
        "spec": _spec(complex_transform_order="before"),
    })
    assert set(_numbers(result["values"])) == {123.5}
    assert result["evaluated_expressions"] == [f"arg(({EXPRESSION}))"]
    assert result["expression_units"] == native == result["native_expression_units"]
    assert "value_unit_derivation" not in result
    assert "value_unit_derivation" not in result["field_array"]["metadata"]
    assert result["expression_unit_observation"]["phase_output_unit_semantics"].startswith("UNVERIFIED")


@pytest.mark.parametrize("mode,expected", [("preserve", {"real": 3.0, "imag": 4.0}),
    ("real", 3.0), ("imag", 4.0), ("abs", 5.0)])
def test_other_after_modes_keep_values_and_native_units(tmp_path, mode, expected):
    tree, worker, _ = _tree(tmp_path)
    result = dispatch("result.evaluate", worker, "Model", {"spec": _spec(complex_mode=mode)})
    assert result["values"][0][0][0][0] == expected
    assert result["expression_units"] == result["native_expression_units"] == {EXPRESSION: "K"}
    assert result["field_array"]["units"]["expression"] == {EXPRESSION: "K"}
    assert "value_unit_derivation" not in result


@pytest.mark.parametrize("aggregate,weight", [("global", None), ("integral", None),
    ("average", None), ("integral", "2"), ("average", "2")])
def test_allowed_after_aggregates_rebuild_final_values_with_separate_unit_maps(tmp_path, aggregate, weight):
    tree, worker, _ = _tree(tmp_path, real=3.0, imag=4.0)
    spec = _spec(aggregate=aggregate, complex_transform_order="after",
                 solution={"dataset": "dset1", "inner": "last"})
    if weight is not None:
        spec["weight_expression"] = weight
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    # Main confirmed after means scalar transform after the complete aggregate.
    expected = math.atan2(4, 3)
    assert all(math.isclose(v, expected) for v in _numbers(result["values"]))
    assert result["field_array"]["values"] == result["values"]
    assert result["field_array"]["shape"][2] == 1
    final_unit = "rad"
    assert result["expression_units"] == {EXPRESSION: final_unit}
    assert result["native_expression_units"] == {EXPRESSION: "K"}
    assert result["field_array"]["units"]["expression"] == {EXPRESSION: final_unit}
    _assert_derivation(result, predivide=bool(weight and aggregate == "average"))


@pytest.mark.parametrize("aggregate", ["std", "rms", "minimum", "maximum"])
def test_undefined_after_phase_aggregates_remain_refused(tmp_path, aggregate):
    tree, worker, created = _tree(tmp_path)
    with pytest.raises(ExecutionContractError) as exc:
        dispatch("result.evaluate", worker, "Model", {
            "spec": _spec(aggregate=aggregate, complex_transform_order="after"),
        })
    assert exc.value.code == "API_UNSUPPORTED"
    assert not created


@pytest.mark.parametrize("storage", ["inline", "artifact", "csv"])
def test_count_only_single_value_without_fieldarray_keeps_budget_and_output_units(tmp_path, monkeypatch, storage):
    tree, nodes = _raw_eval_stationary_fixture(monkeypatch, count=1, point_count=1, values=[[[3.0]]])
    original = tree.numerical.factory

    def factory(tag, kind):
        node = original(tag, kind)
        node.complex_ = True
        node.getImagData = lambda: node._guard("getImagData", ()) or [[[4.0]]]
        node.props["unit"] = [None]
        return node

    tree.numerical.factory = factory
    worker = tree.worker
    worker.paths = SimpleNamespace(project_root=tmp_path)
    if storage == "csv":
        target = tmp_path / "single.csv"
        dispatch("result.field_export", worker, "Model", {
            "spec": _spec(), "format": "csv", "destination": str(target),
        })
        with target.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1 and rows[0]["unit"] == "rad"
        assert float(rows[0]["real"]) == pytest.approx(math.atan2(4, 3))
    else:
        result = dispatch("result.evaluate", worker, "Model", {"spec": _spec(storage=storage)})
        assert result["field_array"] is None
        assert result["expression_units"] == {EXPRESSION: "rad"}
        assert result["native_expression_units"] == {EXPRESSION: None}
        assert result["result_budget"]["status"] == "PASS"
        if storage == "artifact":
            result = _artifact_json(result, tmp_path)["metadata"]
            assert result["expression_units"] == {EXPRESSION: "rad"}
            assert result["native_expression_units"] == {EXPRESSION: None}
        _assert_derivation(result)
    assert nodes[0].calls.count(("getStringArray", ("unit",))) == 1
    assert nodes[0].calls.count(("getData", ())) == 1
    assert not tree.numerical.items


def test_fieldarray_csv_leaf_units_use_returned_phase_not_native_property(tmp_path):
    tree, worker, _ = _tree(tmp_path)
    target = tmp_path / "field.csv"
    dispatch("result.field_export", worker, "Model", {
        "spec": _spec(), "format": "csv", "destination": str(target),
    })
    with target.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {row["unit"] for row in rows} == {"rad"}
    assert all(float(row["real"]) == pytest.approx(math.atan2(4, 3)) for row in rows)


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_multiple_expression_exports_keep_indexed_values_and_radian_labels(tmp_path, fmt):
    expressions = ["different_A", "different_B"]
    tree, worker, _ = _tree(tmp_path, real=[[3.0, 3.0], [0.0, 0.0]],
                           imag=[[4.0, 4.0], [2.0, 2.0]])
    target = tmp_path / f"multi.{fmt}"
    dispatch("result.field_export", worker, "Model", {
        "spec": _spec(expressions=expressions), "format": fmt, "destination": str(target),
    })
    if fmt == "json":
        saved = json.loads(target.read_text())
        assert saved["metadata"]["expression_units"] == dict.fromkeys(expressions, "rad")
        assert saved["metadata"]["native_expression_units"] == dict.fromkeys(expressions, "K")
        assert saved["metadata"]["field_array"]["units"]["expression"] == dict.fromkeys(expressions, "rad")
    else:
        with target.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert {row["expr"] for row in rows} == set(expressions)
        assert {row["unit"] for row in rows} == {"rad"}
        for row in rows:
            expected = math.atan2(4, 3) if row["expr"] == expressions[0] else math.pi / 2
            assert float(row["real"]) == pytest.approx(expected)


def test_strict_native_unit_evidence_is_not_overwritten_by_python_phase(tmp_path):
    tree, worker, _ = _tree(tmp_path, real=3.0, imag=4.0)
    result = results.result_evaluate(worker, "Model", {
        "spec": _spec(aggregate="integral", complex_transform_order="after", selection={
            "kind": "explicit", "component": "comp1", "geometry": "geom1",
            "entity_dimension": 3, "entities": [1, 2],
        }),
    }, strict_metric_evidence=True)
    assert result["expression_units"] == {EXPRESSION: "rad"}
    assert result["strict_metric_evidence"]["expression_unit_readback"] == {EXPRESSION: "K"}
    assert result["native_expression_units"] == {EXPRESSION: "K"}


def test_after_phase_cleanup_failure_remains_unknown_and_does_not_export(tmp_path):
    tree = FWiredTree(numerical_real=3.0, numerical_imag=4.0, is_complex=True,
                      fail_numerical_remove=True)
    worker = tree.worker
    worker.paths = SimpleNamespace(project_root=tmp_path)
    result = dispatch("result.evaluate", worker, "Model", {
        "spec": _spec(aggregate="global", complex_transform_order="after", storage="artifact"),
    })
    assert result["status"]["execution_state_unknown"] is True
    assert result["status"]["ok"] is False
    assert "artifact" not in result
    assert not list(tmp_path.rglob("*.json"))


def test_managed_public_normal_eval_returns_phase_units_and_retains_native_evidence(tmp_path, monkeypatch):
    from tools import run_native_w23_saved_mph_diagnostic as reader

    tree, nodes = _raw_eval_stationary_fixture(monkeypatch, count=1, point_count=1, values=[[[3.0]]])
    original = tree.numerical.factory

    def factory(tag, kind):
        node = original(tag, kind)
        node.complex_ = True
        node.getImagData = lambda: node._guard("getImagData", ()) or [[[4.0]]]
        return node

    tree.numerical.factory = factory
    response, daemon, _ref, _project = _dispatch_public_result_evaluate(
        tmp_path, monkeypatch, tree, {"spec": _spec(storage="inline")},
        request_id="phase-unit-public-eval",
    )
    try:
        assert response["success"] is True, response
        data = reader._response_body(response)
        assert list(_numbers(data["values"])) == pytest.approx([math.atan2(4, 3)])
        assert data["expression_units"] == {EXPRESSION: "rad"}
        assert data["native_expression_units"] == {EXPRESSION: "K"}
        assert data["field_array"] is None
        assert data["result_budget"]["status"] == "PASS"
        assert data["cleanup"]["removed"] and data["status"]["ok"]
        assert nodes[0].calls.count(("getStringArray", ("unit",))) == 1
        assert nodes[0].calls.count(("getData", ())) == 1
        _assert_derivation(data)
    finally:
        daemon.close()


def _post_phase_tree(tmp_path, branch, *, measure=2.0, binding=True, points=2):
    """Native leaves only; keep the normal Eval/measure/statistics consumers."""
    tree, worker, created = _tree(tmp_path, real=3.0, imag=4.0)
    original = tree.numerical_list.factory

    def factory(tag, *args):
        node = original(tag, *args)
        is_measure = tag.endswith("_meas")
        if is_measure:
            node.real_data = measure
        if branch == "point":
            real = [[measure / points if is_measure else -1.0] * 3 for _ in range(points)]
            imag = None if is_measure else [[0.0] * 3 for _ in range(points)]

            def real_read(method, args):
                node._guard(method, args)
                return real

            def imag_read(method, args):
                node._guard(method, args)
                return imag

            node.getData = lambda: real_read("getData", ())
            node.getReal = lambda *a: real_read("getReal", a)
            node.getImagData = lambda: imag_read("getImagData", ())
            node.getImag = lambda *a: imag_read("getImag", a)
        return node

    tree.numerical_list.factory = factory
    if not binding:
        # _result_solution_binding explicitly supports absent getSolnum. A
        # callable that fails instead creates incomplete metadata and is refused.
        tree.solution_info.getSolnum = None
    spec = _spec(aggregate="average", complex_transform_order="after")
    if branch == "weighted":
        spec["weight_expression"] = "unfamiliar_positive_weight"
    else:
        spec["entity_dim"] = 0
    expected = math.atan2(4, 3) if branch == "weighted" else math.pi
    return tree, worker, created, spec, expected


@pytest.mark.parametrize("branch", ["weighted", "point"])
def test_post_phase_measure_division_unit_counter(tmp_path, branch):
    tree, worker, created, spec, expected = _post_phase_tree(tmp_path, branch)
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    assert all(math.isclose(v, expected) for v in _numbers(result["values"]))
    assert result["native_expression_units"] == {EXPRESSION: "K"}
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    assert not tree.numerical_list.items
    print(json.dumps({"branch": branch, "values": result["values"],
                      "denominator": result["denominator_measure"],
                      "output_units": result["expression_units"],
                      "native_units": result["native_expression_units"]}))
    assert result["expression_units"] == {EXPRESSION: "rad"}
    _assert_derivation(result, predivide=True, pre_point_sum=branch == "point")


@pytest.mark.parametrize("branch", ["weighted", "point"])
@pytest.mark.parametrize("binding", [True, False])
@pytest.mark.parametrize("measure", [1.0, 2.0])
@pytest.mark.parametrize("surface", ["response", "auto_json", "export_json", "export_csv"])
def test_post_phase_division_unknown_units_propagate_all_surfaces(tmp_path, branch, binding, measure, surface):
    tree, worker, created, spec, expected = _post_phase_tree(
        tmp_path, branch, measure=measure, binding=binding,
    )
    if surface.startswith("export_"):
        fmt = surface.removeprefix("export_")
        path = tmp_path / f"postdivide.{fmt}"
        dispatch("result.field_export", worker, "Model", {
            "spec": spec, "format": fmt, "destination": str(path),
        })
        if fmt == "csv":
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            assert rows and {row["unit"] for row in rows} == {"rad"}
            assert all(float(row["real"]) == pytest.approx(expected) for row in rows)
            # Division precedes Python atan2; final radians are derived from
            # that scalar operation, never from native K or a guessed rad/M.
            assert created[0].calls.count(("getStringArray", ("unit",))) == 1
            assert not tree.numerical_list.items
            return
        saved = json.loads(path.read_text())
        values, result = saved["values"], saved["metadata"]
    else:
        spec["storage"] = "artifact" if surface == "auto_json" else "inline"
        result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
        if surface == "auto_json":
            saved = _artifact_json(result, tmp_path)
            values, result = saved["values"], saved["metadata"]
        else:
            values = result["values"]
    assert all(math.isclose(v, expected) for v in _numbers(values))
    assert result["expression_units"] == {EXPRESSION: "rad"}
    assert result["native_expression_units"] == {EXPRESSION: "K"}
    _assert_derivation(result, predivide=True, pre_point_sum=branch == "point")
    field = result.get("field_array")
    if binding:
        assert field["units"]["expression"] == {EXPRESSION: "rad"}
        assert field["metadata"]["expression_units"] == {EXPRESSION: "rad"}
        assert field["metadata"]["native_expression_units"] == {EXPRESSION: "K"}
        assert field["metadata"]["unit_readback_status"] == "VERIFIED"
        assert field["metadata"]["unit_readback_scope"] == "native_numerical_feature_property"
        assert field["metadata"]["value_unit_derivation"] == result["value_unit_derivation"]
        assert field["metadata"]["expression_unit_observation"] == result["expression_unit_observation"]
    else:
        assert field is None
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    assert not tree.numerical_list.items


@pytest.mark.parametrize("branch", ["weighted", "point"])
@pytest.mark.parametrize("raw,native", [(["K"], "K"), ([None], None), ([""], None), (["1"], "1")])
def test_native_tokens_and_measure_one_do_not_certify_post_division_unit(tmp_path, branch, raw, native):
    tree, worker, _created, spec, expected = _post_phase_tree(tmp_path, branch, measure=1.0)
    _unit_leaf(tree, raw)
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    assert all(math.isclose(v, expected) for v in _numbers(result["values"]))
    assert result["expression_units"] == {EXPRESSION: "rad"}
    assert result["native_expression_units"] == {EXPRESSION: native}
    assert result["expression_unit_observation"]["raw_property"]["items"] == [
        {"kind": "null"} if raw == [None] else {"kind": "string", "value": raw[0]}
    ]
    _assert_derivation(result, predivide=True, pre_point_sum=branch == "point")


@pytest.mark.parametrize("weighted", [False, True])
def test_existing_point_sum_can_exceed_scalar_atan2_range_without_rewrapping(tmp_path, weighted):
    tree, worker, _created, spec, _expected = _post_phase_tree(tmp_path, "point", points=3)
    spec["aggregate"] = "integral"
    if weighted:
        spec["weight_expression"] = "another_weight_spelling"
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    assert set(_numbers(result["values"])) == {math.pi}
    assert result["expression_units"] == {EXPRESSION: "rad"}
    assert result["field_array"]["units"]["expression"] == {EXPRESSION: "rad"}
    _assert_derivation(result, pre_point_sum=True)


def test_post_division_cleanup_failure_stays_unknown_and_no_auto_artifact(tmp_path):
    tree, worker, _created, spec, expected = _post_phase_tree(tmp_path, "weighted")
    tree.fail_numerical_remove = True
    spec["storage"] = "artifact"
    result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
    assert result["status"]["execution_state_unknown"] and not result["status"]["ok"]
    assert "artifact" not in result
    assert all(math.isclose(v, expected) for v in _numbers(result["values"]))
    assert result["expression_units"] == {EXPRESSION: "rad"}
    _assert_derivation(result, predivide=True)


@pytest.mark.parametrize("binding", [True, False])
@pytest.mark.parametrize("surface", ["response", "export_json", "export_csv"])
def test_each_expression_remains_unknown_after_measure_division(tmp_path, binding, surface):
    names = ["another_field_A", "another_field_B"]
    tree, worker, created, spec, _expected = _post_phase_tree(tmp_path, "weighted", binding=binding)
    original = tree.numerical_list.factory

    def factory(tag, *args):
        node = original(tag, *args)
        if not tag.endswith("_meas"):
            def read(method, args, value):
                node._guard(method, args)
                return value
            real, imag = [[3.0] * 3, [0.0] * 3], [[4.0] * 3, [2.0] * 3]
            node.getData = lambda: read("getData", (), real)
            node.getReal = lambda *a: read("getReal", a, real)
            node.getImagData = lambda: read("getImagData", (), imag)
            node.getImag = lambda *a: read("getImag", a, imag)
        return node

    tree.numerical_list.factory = factory
    _unit_leaf(tree, [None, "1"])
    spec["expressions"] = names
    expected = dict(zip(names, [math.atan2(4, 3), math.pi / 2]))
    if surface.startswith("export_"):
        fmt = surface.removeprefix("export_")
        path = tmp_path / f"multi_unknown.{fmt}"
        dispatch("result.field_export", worker, "Model", {
            "spec": spec, "format": fmt, "destination": str(path),
        })
        if fmt == "csv":
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            assert {row["expr"] for row in rows} == set(names)
            assert {row["unit"] for row in rows} == {"rad"}
            for row in rows:
                assert float(row["real"]) == pytest.approx(expected[row["expr"]])
            return
        saved = json.loads(path.read_text())
        values, result = saved["values"], saved["metadata"]
    else:
        result = dispatch("result.evaluate", worker, "Model", {"spec": spec})
        values = result["values"]
    for index, name in enumerate(names):
        assert all(v == pytest.approx(expected[name]) for v in _numbers(values[index]))
    assert result["expression_units"] == dict.fromkeys(names, "rad")
    assert result["native_expression_units"] == dict(zip(names, [None, "1"]))
    if binding:
        assert result["field_array"]["units"]["expression"] == dict.fromkeys(names, "rad")
    else:
        assert result["field_array"] is None
    assert created[0].calls.count(("getStringArray", ("unit",))) == 1
    _assert_derivation(result, predivide=True)
