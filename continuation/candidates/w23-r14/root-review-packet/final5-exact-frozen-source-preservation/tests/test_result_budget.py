"""Control tests for the pre-getData C13 budget contract."""

from __future__ import annotations

import pytest

from comsol_mcp._result_budget import (
    ResultBudgetRefused,
    guarded_getdata,
    new_memory_ledger,
    plan_interp_budget,
    plan_result_budget,
    record_client_peak,
)
from comsol_mcp._g3_results import (
    _make_result_budget_guard,
    _run_bound_feature,
)


class _Getter:
    def __init__(self) -> None:
        self.calls = 0

    def getData(self) -> list[list[list[float]]]:
        self.calls += 1
        return [[[1.0]]]


def test_interp_budget_uses_expression_solution_axes_and_points_before_getdata() -> None:
    decision = plan_interp_budget(
        expression_count=2,
        point_count=4,
        outer_indices=(1, 2),
        inner_indices_by_outer=((1, 2), (1, 2, 3)),
        max_elements=50,
        max_bytes=400,
        complex_components=1,
    )

    assert decision["status"] == "PASS"
    assert decision["allowed"] is True
    # 2 expressions * (2 + 3) real solution entries * 4 points.
    assert decision["computed"]["elements"] == 40
    assert decision["computed"]["bytes"] == 320
    assert decision["computed"]["numeric_payload_bytes"] == 320
    assert decision["limits"]["max_numeric_payload_bytes"] == 400
    assert decision["budget_scope"] == "numeric_payload_only"
    assert decision["computed"]["raw_point_upper_bound"] == 4
    assert decision["getdata_called"] is False

    feature = _Getter()
    assert guarded_getdata(feature, decision) == [[[1.0]]]
    assert feature.calls == 1


def test_complex_interp_budget_accounts_for_both_transport_arrays() -> None:
    decision = plan_interp_budget(
        expression_count=1,
        point_count=3,
        outer_indices=(1,),
        inner_indices_by_outer=((1, 2),),
        max_elements=20,
        max_bytes=200,
        complex_components=2,
    )

    assert decision["status"] == "PASS"
    assert decision["computed"]["elements"] == 12
    assert decision["computed"]["bytes"] == 96


def test_getndata_observation_is_not_promoted_to_eval_point_count() -> None:
    decision = plan_result_budget(
        expression_count=1,
        point_count=8,
        outer_count=1,
        inner_count=2,
        max_elements=100,
        max_bytes=10_000,
        feature_kind="Eval",
        expression_count_verified=True,
        point_count_verified=True,
        solution_axes_verified=True,
        expression_source="validated-expressions",
        point_count_source="untrusted-getNData",
        solution_axes_source="validated-SolutionBinding",
        reported_data_vector_count=8,
    )

    assert decision["status"] == "BLOCKED"
    assert decision["reason_code"] == "RAW_POINT_UPPER_BOUND_UNVERIFIED"
    assert decision["data_vector_count"]["used_for_point_bound"] is False


def test_eval_accepts_only_an_explicit_verified_raw_point_upper_bound() -> None:
    decision = plan_result_budget(
        expression_count=2,
        point_count=4,
        outer_count=2,
        inner_count=3,
        max_elements=300,
        max_bytes=20_000,
        feature_kind="Eval",
        expression_count_verified=True,
        point_count_verified=True,
        solution_axes_verified=True,
        expression_source="validated-expressions",
        point_count_source="selection-coordinate-contract",
        solution_axes_source="validated-SolutionBinding",
        raw_point_upper_bounds_by_outer=(8, 10),
        raw_upper_bound_verified=True,
        raw_upper_bound_source="adapter-proof:mesh+eval-refine-pattern",
        reported_data_vector_count=2,
    )

    assert decision["status"] == "PASS"
    # Per-outer native shape bounds (8, 10), not getNData() (2), drive the budget.
    assert decision["computed"]["elements"] == 108
    assert decision["computed"]["raw_point_upper_bound"] == 10
    assert decision["computed"]["raw_point_upper_bounds_by_outer"] == [8, 10]


def test_budget_refusal_happens_before_getdata_and_blocks_publish() -> None:
    decision = plan_interp_budget(
        expression_count=3,
        point_count=10,
        outer_indices=(1, 2),
        inner_indices_by_outer=((1,), (1,)),
        max_elements=20,
        max_bytes=200,
    )
    assert decision["status"] == "BLOCKED"
    assert decision["reason_code"] == "ELEMENT_BUDGET_EXCEEDED"
    assert decision["publish_allowed"] is False

    feature = _Getter()
    with pytest.raises(ResultBudgetRefused) as exc_info:
        guarded_getdata(feature, decision)
    assert exc_info.value.decision["getdata_called"] is False
    assert feature.calls == 0


def test_missing_verification_is_fail_closed_even_with_positive_dimensions() -> None:
    decision = plan_result_budget(
        expression_count=1,
        point_count=2,
        outer_count=1,
        inner_count=1,
        max_elements=10,
        max_bytes=100,
        # All three verification flags intentionally remain false.
    )
    assert decision["status"] == "BLOCKED"
    assert decision["reason_code"] == "EXPRESSION_METADATA_UNVERIFIED"
    assert decision["allowed"] is False


def test_memory_ledger_keeps_engine_cache_unmeasured_and_client_scopes_separate() -> None:
    ledger = new_memory_ledger(operation="result.evaluate", run_id="run-c13")
    ledger = record_client_peak(
        ledger,
        "client_transport",
        1234,
        source="client-tracemalloc",
        sample_count=3,
    )
    ledger = record_client_peak(ledger, "client_array", 2048, source="array-nbytes")

    assert ledger["engine_internal_cache"]["status"] == "UNMEASURED"
    assert ledger["engine_internal_cache"]["peak_bytes"] is None
    assert ledger["client_transport"]["peak_bytes"] == 1234
    assert ledger["client_array"]["peak_bytes"] == 2048
    assert ledger["claims"]["engine_peak_inferred_from_client"] is False

    with pytest.raises(ValueError, match="only client"):
        record_client_peak(ledger, "engine_internal_cache", 10, source="client-tracemalloc")


class _ShapeFeature:
    def __init__(self, *, point_count: int = 2, data: object = None) -> None:
        self.point_count = point_count
        self.data = data if data is not None else [[[1.0, 2.0], [3.0, 4.0]]]
        self.shape_calls = 0
        self.data_calls = 0
        self.run_calls = 0
        self.outer = 1

    def set(self, name: str, value: object) -> None:
        if name == "outersolnum":
            self.outer = int(value)

    def run(self) -> None:
        self.run_calls += 1

    def isComplex(self) -> bool:
        return False

    def getCoordinatesShape(self) -> dict[str, object]:
        self.shape_calls += 1
        return {
            "kind": "coordinates_shape",
            "shape": [3, self.point_count],
            "dimension": 3,
            "point_count": self.point_count,
            "values_transmitted": False,
        }

    def getData(self) -> object:
        self.data_calls += 1
        return self.data


def _binding() -> dict[str, object]:
    return {
        "pair_mapping_complete": True,
        "outer_indices": [1],
        "inner_indices_by_outer": {1: [1, 2]},
    }


def test_eval_budget_reads_shape_then_refuses_before_getdata() -> None:
    feature = _ShapeFeature(point_count=2)
    records: list[dict[str, object]] = []
    guard = _make_result_budget_guard(
        operation="result.evaluate",
        feature_kind="Eval",
        expressions=("T", "q"),
        binding=_binding(),
        max_elements=3,
        max_bytes=1024,
        records=records,
    )

    with pytest.raises(ResultBudgetRefused) as exc_info:
        _run_bound_feature(feature, _binding(), num_expressions=2, budget_guard=guard)

    assert feature.shape_calls == 1
    assert feature.data_calls == 0
    assert exc_info.value.decision["reason_code"] == "ELEMENT_BUDGET_EXCEEDED"
    assert exc_info.value.decision["publish_allowed"] is False
    assert records[0]["requested"]["raw_point_upper_bound"] == 2


def test_eval_budget_success_records_native_shape_before_getdata() -> None:
    feature = _ShapeFeature(point_count=2)
    records: list[dict[str, object]] = []
    guard = _make_result_budget_guard(
        operation="result.evaluate",
        feature_kind="Eval",
        expressions=("T",),
        binding=_binding(),
        max_elements=10,
        max_bytes=1000,
        records=records,
    )

    real, imag, status, layout = _run_bound_feature(
        feature,
        _binding(),
        num_expressions=1,
        budget_guard=guard,
    )

    assert real == feature.data
    assert imag is None
    assert status is False
    assert layout == "expression,solnum,point"
    assert feature.shape_calls == 1
    assert feature.data_calls == 1
    assert records[0]["computed"]["raw_point_upper_bound"] == 2
    assert records[0]["getdata_called"] is True


def test_interp_budget_uses_request_point_count_without_shape_alias() -> None:
    class InterpFeature(_ShapeFeature):
        def getCoordinatesShape(self) -> dict[str, object]:
            raise AssertionError("Interp must use the validated request point count")

    feature = InterpFeature(point_count=3, data=[[[1.0, 2.0, 3.0]]])
    records: list[dict[str, object]] = []
    guard = _make_result_budget_guard(
        operation="result.at_points",
        feature_kind="Interp",
        expressions=("T",),
        binding=_binding(),
        point_count=3,
        max_elements=10,
        max_bytes=1000,
        records=records,
    )

    _run_bound_feature(feature, _binding(), num_expressions=1, budget_guard=guard)

    assert feature.data_calls == 1
    assert records[0]["computed"]["raw_point_upper_bound"] == 3
    assert records[0]["computed"]["raw_upper_bound_source"] == "validated-Interp-request-coordinates;interp-coordinate-columns"
