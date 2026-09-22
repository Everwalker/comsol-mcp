"""Counterexamples for the G3.3 typed solution and numeric contracts.

These tests deliberately use a small engine-shaped fixture.  They exercise the
parts of the public API that can be checked without starting COMSOL: the four
axis field layout, the SolutionInfo pair mapping, strict selections, and
fail-closed complex/statistical math.
"""
from __future__ import annotations

import pytest

from comsol_mcp._complex_transform import effective_engine_expression, transform_complex_data
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._measure_spec import MeasureSpec
from comsol_mcp._solution_binding import FieldArray, SolutionBinding
from comsol_mcp._g3_results import (
    _field_array_context,
    _field_array_is_complex,
    _field_array_with_values,
    _axisymmetric_measure_readback,
    _nested_divide,
    _normalise_evalpoint_components,
    _point_reduce,
    _run_bound_feature,
    _selected_inner_expression_cells,
)


def _four_axis_values() -> list[list[list[list[int]]]]:
    # Every value carries all four coordinates: expression, outer, inner,
    # point.  A squeezed or transposed implementation cannot pass this check.
    return [
        [
            [[1000 + 100 * o + 10 * i + p for p in range(3)] for i in range(3)]
            for o in range(2)
        ],
        [
            [[2000 + 100 * o + 10 * i + p for p in range(3)] for i in range(3)]
            for o in range(2)
        ],
    ]


def test_field_array_keeps_expression_outer_inner_point_axes() -> None:
    field = FieldArray(
        _four_axis_values(),
        axes=("expression", "outer", "inner", "point"),
        coords={"outer": [11, 22], "inner": [3, 7, 9]},
        units={"point": "m"},
        is_complex=False,
    )

    assert field.shape == (2, 2, 3, 3)
    assert field.axes == ["expression", "outer", "inner", "point"]
    assert field.coords["outer"] == [11, 22]
    assert field.coords["inner"] == [3, 7, 9]

    selected = field.select(outer=[22], inner="last")
    assert selected.axes == field.axes
    assert selected.shape == (2, 1, 1, 3)
    assert selected.values[0][0][0] == [1120, 1121, 1122]
    assert selected.values[1][0][0] == [2120, 2121, 2122]

    with pytest.raises(ExecutionContractError) as exc:
        field.select(outer=0)
    assert exc.value.code == "INVALID_REQUEST"


def test_final_statistics_are_selected_from_rebuilt_field_array() -> None:
    """Selecting ``outer=all, inner=all`` must retain std/rms values.

    The raw field is kept as the axis/metadata template, but aggregate
    branches replace its values.  Selecting the old template after statistics
    would publish the raw mean (130.025 here) in place of the computed std.
    """
    template = FieldArray(
        [[[[130.025], [130.025]]]],
        axes=("expression", "outer", "inner", "point"),
        coords={"expression": ["T"], "outer": [1], "inner": [1, 2], "point": [1]},
        units={"expression": {"T": "K"}, "point": "index"},
        metadata={"aggregate": "std"},
        is_complex=False,
    )
    std_values = [[[[0.0144337567], [0.0144337567]]]]

    selected = _field_array_with_values(
        template,
        std_values,
        is_complex=False,
        selectors={"outer": "all", "inner": "all"},
    )

    assert selected.values == std_values
    assert selected.values != template.values
    assert selected.axes == template.axes
    assert selected.coords["inner"] == [1, 2]
    assert selected.metadata["aggregate"] == "std"


def test_field_array_complex_flag_describes_published_values() -> None:
    assert _field_array_is_complex(True, "preserve", "none") is True
    assert _field_array_is_complex(True, "real", "none") is False
    assert _field_array_is_complex(True, "abs", "none") is False
    assert _field_array_is_complex(True, "phase", "none") is False
    assert _field_array_is_complex(True, "preserve", "std") is False
    assert _field_array_is_complex(False, "preserve", "none") is False


def test_axisymmetric_measure_requires_native_readback() -> None:
    class Feature:
        def __init__(self) -> None:
            self.props = {"intvolume": "off"}
            self.calls: list[tuple[str, object]] = []

        def properties(self) -> list[str]:
            return list(self.props)

        def set(self, name: str, value: object) -> None:
            self.calls.append((name, value))
            self.props[name] = value

        def getString(self, name: str) -> object:
            return self.props.get(name)

    feature = Feature()
    evidence = _axisymmetric_measure_readback(feature, role="aggregate")
    assert evidence["native_property"] == "intvolume"
    assert evidence["readback_verified"] is True
    assert evidence["manual_radial_weighting"] is False
    assert ("intvolume", "on") in feature.calls

    class Missing:
        def properties(self) -> list[str]:
            return []

    with pytest.raises(ExecutionContractError) as exc:
        _axisymmetric_measure_readback(Missing(), role="aggregate")
    assert exc.value.code == "AXISYMMETRY_READBACK_UNAVAILABLE"


class _SolutionInfo:
    """SolutionInfo fixture whose solnums are intentionally non-contiguous."""

    def __init__(self) -> None:
        self.solnum_calls: list[tuple[int, bool]] = []

    def getOuterSolnum(self) -> list[int]:
        # The implementation must also support an empty result on COMSOL; this
        # fixture exposes labels explicitly when asked by the caller.
        return [1, 2]

    def getSolnum(self, outer: int, strict: bool) -> list[int]:
        self.solnum_calls.append((outer, strict))
        # COMSOL returns this outer level's actual inner numbers.  They may
        # repeat across outer levels and need not be a global solnum axis.
        return {1: [3, 7, 9], 2: [3, 7, 9]}[outer]

    def getPNames(self, pairs: list[list[int]]) -> list[list[str]]:
        return [["temperature", "frequency"] for _ in pairs]

    def getPvals(self, pairs: list[list[int]]) -> list[list[float]]:
        return [[float(o), float(inner)] for o, inner in pairs]

    def getUnits(self, pairs: list[list[int]]) -> list[list[str]]:
        return [["K", "Hz"] for _ in pairs]


def test_solution_info_mapping_preserves_real_outer_inner_solnums() -> None:
    info = _SolutionInfo()
    binding = SolutionBinding.resolve_solution_info(info, outer_indices=[1, 2])

    assert info.solnum_calls == [(1, True), (2, True)]
    assert binding["outer_indices"] == [1, 2]
    assert binding["inner_indices_by_outer"] == {1: [3, 7, 9], 2: [3, 7, 9]}
    assert binding["solnum_pairs"] == [
        {"outer": 1, "inner": 3, "solnum": 3},
        {"outer": 1, "inner": 7, "solnum": 7},
        {"outer": 1, "inner": 9, "solnum": 9},
        {"outer": 2, "inner": 3, "solnum": 3},
        {"outer": 2, "inner": 7, "solnum": 7},
        {"outer": 2, "inner": 9, "solnum": 9},
    ]
    assert binding["parameter_values_by_pair"][(2, 9)] == [2.0, 9.0]
    assert binding["parameter_units_by_pair"][(1, 3)] == ["K", "Hz"]


def test_solution_info_empty_outer_metadata_does_not_invent_two_axes() -> None:
    class EmptyOuter(_SolutionInfo):
        def getOuterSolnum(self) -> list[int]:
            return []

        def getSolnum(self, outer: int, strict: bool) -> list[int]:
            raise RuntimeError("no outer mapping")

    binding = SolutionBinding.resolve_solution_info(EmptyOuter())
    assert binding["outer_indices"] == []
    assert binding["pair_mapping_complete"] is False
    assert binding["read_errors"]


def test_repeated_inner_numbers_are_scoped_by_outer() -> None:
    info = _SolutionInfo()
    binding = SolutionBinding.resolve_solution_info(info, outer_indices=[1, 2])
    raw = [[
        [[103, 107], [107, 111], [109, 113]],
        [[203, 207], [207, 211], [209, 213]],
    ]]
    field = SolutionBinding.field_array_from_engine(
        raw,
        binding,
        num_expressions=1,
        layout="expression,outer,inner,point",
    )
    assert field.shape == (1, 2, 3, 2)
    assert field.values[0][0][0] == [103, 107]
    assert field.values[0][1][0] == [203, 207]


def test_bound_feature_reads_each_outer_without_global_solnum_overwrite() -> None:
    """A repeated inner solnum is scoped to its selected outer solution."""
    class _OuterFeature:
        def __init__(self) -> None:
            self.outer = 1
            self.selected: list[tuple[str, object]] = []

        def set(self, name: str, value: object) -> None:
            self.selected.append((name, value))
            if name == "outersolnum":
                self.outer = int(value)

        def run(self) -> None:
            return None

        def isComplex(self) -> bool:
            return False

        def getData(self) -> list[list[list[float]]]:
            offset = 100.0 * self.outer
            return [
                [[offset + 1.0, offset + 2.0], [offset + 3.0, offset + 4.0]],
                [[offset + 11.0, offset + 12.0], [offset + 13.0, offset + 14.0]],
            ]

    binding = {
        "outer_indices": [1, 2],
        "inner_indices": [1, 2],
        "inner_indices_by_outer": {1: [1, 2], 2: [1, 2]},
        "solnum_pairs": [
            {"outer": 1, "inner": 1, "solnum": 1},
            {"outer": 1, "inner": 2, "solnum": 2},
            {"outer": 2, "inner": 1, "solnum": 1},
            {"outer": 2, "inner": 2, "solnum": 2},
        ],
    }
    feature = _OuterFeature()
    real, imag, is_complex, layout = _run_bound_feature(
        feature,
        binding,
        num_expressions=2,
    )

    assert imag is None
    assert is_complex is False
    assert layout == "expression,outer,inner,point"
    field = SolutionBinding.field_array_from_engine(
        real,
        binding,
        num_expressions=2,
        layout=layout,
    )
    assert field.shape == (2, 2, 2, 2)
    assert field.values[0][0][0] == [101.0, 102.0]
    assert field.values[0][1][0] == [201.0, 202.0]
    assert field.values[1][1][1] == [213.0, 214.0]
    assert [value for name, value in feature.selected if name == "outersolnum"] == [1, 2]


def test_bound_aggregate_feature_uses_outer_aware_native_getters() -> None:
    """COMSOL aggregate getReal() otherwise silently returns outer level 1."""
    class _OuterAggregateFeature:
        def __init__(self) -> None:
            self.selected: list[tuple[str, object]] = []
            self.default_getter_calls = 0

        def set(self, name: str, value: object) -> None:
            self.selected.append((name, value))

        def run(self) -> None:
            return None

        def isComplex(self, outer: int) -> bool:
            assert outer in (1, 2)
            return False

        def getReal(self, real_part: bool, outer: int) -> list[list[float]]:
            assert real_part is False
            return [[100.0 * outer + 1.0, 100.0 * outer + 2.0]]

        def getImag(self, *args: object) -> object:
            raise AssertionError("real aggregate fixture must not read imaginary data")

        def getData(self) -> object:
            self.default_getter_calls += 1
            raise AssertionError("aggregate path must use getReal(false, outer)")

    binding = {
        "outer_indices": [1, 2],
        "inner_indices": [1, 2],
        "inner_indices_by_outer": {1: [1, 2], 2: [1, 2]},
        "solnum_pairs": [],
    }
    feature = _OuterAggregateFeature()
    real, imag, is_complex, layout = _run_bound_feature(
        feature,
        binding,
        num_expressions=1,
        outer_getters=True,
    )

    assert real == [[[[101.0], [102.0]], [[201.0], [202.0]]]]
    assert imag is None
    assert is_complex is False
    assert layout == "expression,outer,inner,point"
    assert feature.default_getter_calls == 0


def test_centered_aggregate_selects_actual_inner_label_from_full_getter_axis() -> None:
    # Native getReal(false, outer) may return every inner value even after a
    # solnum setter.  Selecting by the real SolutionInfo label avoids taking
    # the first value or treating the local axis as a global enumeration.
    values = [[30.0, 70.0, 90.0]]
    assert _selected_inner_expression_cells(
        values,
        inner_labels=[3, 7, 9],
        inner=7,
        num_expressions=1,
        label="centered variance",
    ) == [70.0]

    with pytest.raises(ExecutionContractError) as exc_info:
        _selected_inner_expression_cells(
            values,
            inner_labels=[3, 7, 9],
            inner=11,
            num_expressions=1,
            label="centered variance",
        )
    assert exc_info.value.code == "SOLUTION_AXIS_METADATA_UNAVAILABLE"


class _Selection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, object]] = []

    def set(self, value: object) -> None:
        self.calls.append(("set", value))
        if self.fail:
            raise RuntimeError("selection rejected")

    def all(self) -> None:
        self.calls.append(("all", None))


class _Feature:
    def __init__(self, selection: _Selection) -> None:
        self._selection = selection

    def selection(self) -> _Selection:
        return self._selection


def test_measure_selection_failure_is_not_swallowed() -> None:
    spec = MeasureSpec(entity_dim=2, selection={"entities": [1, 2]})
    with pytest.raises(ExecutionContractError) as exc:
        spec.apply_selection(_Feature(_Selection(fail=True)))
    assert exc.value.code == "SELECTION_APPLY_FAILED"


@pytest.mark.parametrize("bad_dimension", [True, 1.5, "1"])
def test_measure_entity_dimension_rejects_non_integer_values(bad_dimension: object) -> None:
    with pytest.raises(ExecutionContractError) as exc:
        MeasureSpec(entity_dim=bad_dimension)  # type: ignore[arg-type]
    assert exc.value.code == "INVALID_SELECTION"


def test_public_selection_kind_explicit_binds_without_dimension_alias_loss() -> None:
    selection = _Selection()
    spec = MeasureSpec(
        entity_dim=None,
        space_dim=3,
        selection={"kind": "explicit", "entity_dimension": 1, "entities": [2, 4]},
    )
    spec.apply_selection(_Feature(selection))
    assert selection.calls == [("set", [2, 4])]
    assert spec.entity_dim == 1
    assert spec.integral_feature_type == "IntLine"


@pytest.mark.parametrize("aggregate", ["integral", "average", "minimum", "maximum", "std", "rms"])
def test_point_measure_uses_native_evalpoint_and_counting_measure(aggregate: str) -> None:
    spec = MeasureSpec(aggregate=aggregate, entity_dim=0, space_dim=3)
    assert spec.feature_type == "EvalPoint"
    assert spec.integral_feature_type == "EvalPoint"


def test_statistics_refuse_missing_or_negative_second_moments() -> None:
    with pytest.raises(ExecutionContractError) as exc:
        MeasureSpec.compute_statistics(2.0, 1.0, "std")
    assert exc.value.code == "MISSING_STATISTIC_INTEGRAL"

    with pytest.raises(ExecutionContractError) as exc:
        MeasureSpec.compute_statistics(2.0, 1.0, "rms")
    assert exc.value.code == "MISSING_STATISTIC_INTEGRAL"

    with pytest.raises(ExecutionContractError) as exc:
        MeasureSpec.compute_statistics(2.0, 1.0, "std", variance_integral=-1.0)
    assert exc.value.code == "INVALID_STATISTIC_INTEGRAL"


def test_unknown_complex_status_cannot_zero_pad_missing_imaginary_data() -> None:
    with pytest.raises(ExecutionContractError) as exc:
        transform_complex_data([1.0, 2.0], None, "real", is_complex=None)
    assert exc.value.code == "COMPLEX_DATA_ERROR"

    with pytest.raises(ExecutionContractError) as exc:
        transform_complex_data([[1.0, 2.0]], [[3.0], [4.0]], "abs", is_complex=True)
    assert exc.value.code == "COMPLEX_DATA_ERROR"


def test_measure_denominator_broadcasts_one_expression_over_multiple_fields() -> None:
    # The measure feature is intentionally evaluated with one expression (w or
    # 1), while the requested field may carry two expressions.  A shape check
    # that compares only the leading expression axis must broadcast that
    # explicit singleton; it must not copy the first numerator expression.
    assert _nested_divide([[6.0], [9.0]], [[2.0]]) == [[3.0], [4.5]]


def test_complex_aggregate_uses_one_native_effective_expression() -> None:
    assert effective_engine_expression("x*(3+4*i)", "real") == "real((x*(3+4*i)))"
    assert effective_engine_expression("x*(3+4*i)", "imag") == "imag((x*(3+4*i)))"
    assert effective_engine_expression("x*(3+4*i)", "abs") == "abs((x*(3+4*i)))"
    assert effective_engine_expression("x*(3+4*i)", "phase") == "arg((x*(3+4*i)))"
    assert effective_engine_expression("x*(3+4*i)", "preserve") == "x*(3+4*i)"


def test_field_array_context_publishes_units_and_pair_coordinates() -> None:
    binding = {
        "binding_source": "SolutionInfo.getSolnum(outer, strict)",
        "pair_mapping_complete": True,
        "outer_indices": [1],
        "inner_indices": [1],
        "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1}],
        "parameter_names": ["t"],
        "parameter_names_by_pair": {(1, 1): ["t"]},
        "parameter_values_by_pair": {(1, 1): [0.5]},
        "parameter_units_by_pair": {(1, 1): ["s"]},
    }
    coords, units, metadata = _field_array_context(
        binding,
        ["T", "q"],
        expression_units={"T": "K", "q": None},
        length_unit="m",
        point_count=1,
    )
    assert coords["expression"] == ["T", "q"]
    assert coords["time"] == [0.5]
    assert coords["parameters"] == [{"t": 0.5}]
    assert units["expression"] == {"T": "K", "q": None}
    assert units["time"] == "s"
    assert metadata["unit_readback_status"] == "UNVERIFIED"
    assert metadata["solution_pairs"][0]["parameter_units"] == {"t": "s"}


def test_aggregate_field_array_uses_index_point_axis_and_no_spatial_source() -> None:
    binding = {
        "binding_source": "SolutionInfo.getSolnum(outer, strict)",
        "pair_mapping_complete": True,
        "outer_indices": [1],
        "inner_indices": [1],
        "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1}],
    }
    _coords, units, metadata = _field_array_context(
        binding,
        ["T"],
        expression_units={"T": "K"},
        length_unit="index",
        point_count=1,
        point_coordinate_source="aggregate",
    )
    assert units["point"] == "index"
    assert metadata["point_coordinate_source"] == "aggregate"


def test_field_array_spatial_coordinates_are_point_major() -> None:
    binding = {
        "binding_source": "SolutionInfo.getSolnum(outer, strict)",
        "pair_mapping_complete": True,
        "outer_indices": [1],
        "inner_indices": [1],
        "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1}],
    }
    coords, _units, metadata = _field_array_context(
        binding,
        ["T"],
        expression_units={"T": "K"},
        length_unit="mm",
        point_count=2,
        point_coordinates=[[1.0, 2.0], [3.0, 4.0]],
        point_coordinate_source="request",
    )
    assert coords["spatial"] == [[1.0, 2.0], [3.0, 4.0]]
    assert metadata["point_coordinate_source"] == "request"


def test_evalpoint_rows_are_grouped_by_expression_before_point_reduction() -> None:
    # Native EvalPoint returns [expr*point][inner], not [expr][inner][point].
    real, imag, point_count = _normalise_evalpoint_components(
        [[2.0], [4.0], [1.0], [3.0]],
        None,
        num_expressions=2,
    )
    assert point_count == 2
    assert real == [[[2.0, 4.0]], [[1.0, 3.0]]]
    assert _point_reduce(real, "sum") == [[[6.0]], [[4.0]]]
