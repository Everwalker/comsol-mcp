from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp import _g3_results as results
from test_g3_results import FakeEngineError, _raw_eval_stationary_fixture


class UnitFeature:
    def __init__(self, raw: Any = None, *, array_error: BaseException | None = None,
                 string_error: BaseException | None = None) -> None:
        self.raw = raw
        self.array_error = array_error
        self.string_error = string_error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def getStringArray(self, name: str) -> Any:
        self.calls.append(("getStringArray", (name,)))
        if self.array_error is not None:
            raise self.array_error
        return self.raw

    def getString(self, name: str) -> Any:
        self.calls.append(("getString", (name,)))
        if self.string_error is not None:
            raise self.string_error
        return self.raw


def _observe(feature: UnitFeature, expressions: list[str], *, requested: Any = None,
             feature_expressions: list[str] | None = None, setter_called: bool = False,
             request_present: bool | None = None):
    if request_present is None:
        request_present = requested is not None
    return results._feature_units_and_observation(
        feature,
        expressions,
        feature_expressions=feature_expressions,
        requested_units=requested,
        requested_units_present=request_present,
        setter_called=setter_called,
    )


@pytest.mark.parametrize(
    ("raw", "expected", "raw_shape", "mapping_status"),
    [
        (None, {}, "null", "UNVERIFIED_RAW_NULL"),
        ("", {"E": None}, "string", "UNVERIFIED_POSITIONAL_SHAPE_HAS_NULL_EMPTY_OR_UNEXPECTED_ITEMS"),
        ([], {"E": None}, "sequence", "UNVERIFIED_LENGTH_MISMATCH"),
    ],
)
def test_none_empty_string_and_empty_array_remain_distinct_and_legacy_map_is_unchanged(
    raw: Any, expected: dict[str, Any], raw_shape: str, mapping_status: str
) -> None:
    feature = UnitFeature(raw)
    legacy, observation = _observe(feature, ["E"])

    assert legacy == expected
    assert feature.calls == [("getStringArray", ("unit",))]
    assert observation["raw_property"]["shape"] == raw_shape
    assert observation["mapping_status"] == mapping_status
    assert observation["native_unit_conversion"] == "UNVERIFIED"
    assert observation["physical_dimensionality"] == "UNVERIFIED"


def test_array_values_are_indexed_and_request_is_separate_from_observed_property() -> None:
    feature = UnitFeature(["K", ""])
    legacy, observation = _observe(
        feature, ["T", "U"], requested=["degC", "m"], setter_called=True
    )

    assert legacy == {"T": "K", "U": None}
    assert feature.calls == [("getStringArray", ("unit",))]
    assert observation["requested_spec_units"]["items"] == [
        {"kind": "string", "value": "degC"}, {"kind": "string", "value": "m"}
    ]
    assert observation["python_setter_call_shape"] == (
        "_call(feature, 'set', 'unit', spec['units']) with the original supplied Python value; "
        "result_evaluate does not validate or normalize spec.units"
    )
    assert observation["mapping_status"] == (
        "UNVERIFIED_POSITIONAL_SHAPE_HAS_NULL_EMPTY_OR_UNEXPECTED_ITEMS"
    )
    assert [row["native_property_value_index"] for row in observation["expression_rows"]] == [0, 1]
    assert [row["native_property_value"] for row in observation["expression_rows"]] == [
        {"kind": "string", "value": "K"}, {"kind": "string", "value": ""}
    ]
    assert observation["multi_expression_eval_setter_semantics"] == "UNVERIFIED"


def test_absent_and_explicit_null_requested_units_are_distinct() -> None:
    _legacy, absent = _observe(UnitFeature(["K"]), ["T"])
    _legacy, explicit_null = _observe(
        UnitFeature(["K"]), ["T"], requested=None, request_present=True
    )

    assert absent["requested_spec_units"] == {"kind": "absent"}
    assert explicit_null["requested_spec_units"] == {"kind": "null"}


def test_string_fallback_records_first_safe_failure_and_legacy_broadcast() -> None:
    feature = UnitFeature(
        "V/m", array_error=FakeEngineError("private message omitted", code="API_UNSUPPORTED")
    )
    legacy, observation = _observe(feature, ["Ex", "Ey"])

    assert legacy == {"Ex": "V/m", "Ey": "V/m"}
    assert feature.calls == [
        ("getStringArray", ("unit",)), ("getString", ("unit",))
    ]
    assert observation["chosen_getter"] == "getString"
    assert observation["getter_attempts"] == [
        {"method": "getStringArray", "outcome": "failed",
         "failure": {"type": "comsol_mcp._execution_contract.ExecutionContractError", "code": "ENGINE_CALL_FAILED"}},
        {"method": "getString", "outcome": "returned"},
    ]
    assert observation["mapping_status"] == "OBSERVED_LEGACY_SINGLETON_BROADCAST"
    assert [row["native_property_value_index"] for row in observation["expression_rows"]] == [0, 0]
    assert "private message omitted" not in json.dumps(observation)


def test_missing_array_getter_records_allowlisted_api_unsupported_code() -> None:
    class StringOnlyFeature:
        def getString(self, name: str) -> str:
            assert name == "unit"
            return "V/m"

    legacy, observation = _observe(StringOnlyFeature(), ["Ex", "Ey"])  # type: ignore[arg-type]

    assert legacy == {"Ex": "V/m", "Ey": "V/m"}
    assert observation["getter_attempts"][0]["failure"] == {
        "type": "comsol_mcp._execution_contract.ExecutionContractError",
        "code": "API_UNSUPPORTED",
    }


def test_both_getters_failing_is_explicit_and_does_not_leak_exception_text() -> None:
    feature = UnitFeature(
        array_error=FakeEngineError("credential-like payload", code="ARRAY_DENIED"),
        string_error=ValueError("private exception details"),
    )
    legacy, observation = _observe(feature, ["E"])

    assert legacy == {}
    assert feature.calls == [
        ("getStringArray", ("unit",)), ("getString", ("unit",))
    ]
    assert observation["property_observation_status"] == "UNVERIFIED_GETTER_FAILED"
    assert observation["mapping_status"] == "UNVERIFIED_GETTER_FAILED"
    assert [entry["failure"]["type"] for entry in observation["getter_attempts"]] == [
        "comsol_mcp._execution_contract.ExecutionContractError",
        "comsol_mcp._execution_contract.ExecutionContractError",
    ]
    serialized = json.dumps(observation)
    assert "credential-like payload" not in serialized
    assert "private exception details" not in serialized


def test_unrecognized_execution_error_code_is_omitted() -> None:
    secret_code = "credential-token-should-not-appear"
    feature = UnitFeature(
        "K", array_error=ExecutionContractError(secret_code, "private details")
    )
    legacy, observation = _observe(feature, ["T"])

    assert legacy == {"T": "K"}
    assert observation["getter_attempts"][0]["failure"]["code"] is None
    assert secret_code not in json.dumps(observation)


def test_unexpected_scalar_and_length_mismatch_keep_legacy_output() -> None:
    scalar_feature = UnitFeature(17)
    scalar_legacy, scalar_observation = _observe(scalar_feature, ["T"])
    mismatch_feature = UnitFeature(["K", "m", "s"])
    mismatch_legacy, mismatch_observation = _observe(mismatch_feature, ["T", "U"])

    assert scalar_legacy == {}
    assert scalar_observation["raw_property"] == {
        "shape": "unexpected_type", "count": None, "items": [], "type": "builtins.int"
    }
    assert scalar_observation["mapping_status"] == "UNVERIFIED_RAW_TYPE"
    assert mismatch_legacy == {"T": None, "U": None}
    assert mismatch_observation["raw_property"]["count"] == 3
    assert mismatch_observation["mapping_status"] == "UNVERIFIED_LENGTH_MISMATCH"
    assert [row["native_property_value_index"] for row in mismatch_observation["expression_rows"]] == [None, None]


def test_duplicate_expression_names_keep_rows_even_when_legacy_dict_collapses() -> None:
    feature = UnitFeature(["K", "m"])
    legacy, observation = _observe(feature, ["T", "T"], requested=["K", "m"], setter_called=True)

    assert legacy == {"T": "m"}
    assert [row["index"] for row in observation["expression_rows"]] == [0, 1]
    assert [row["requested_expression_occurrence"] for row in observation["expression_rows"]] == [1, 2]
    assert [row["duplicate_requested_expression_name"] for row in observation["expression_rows"]] == [False, True]
    assert [row["native_property_value"]["value"] for row in observation["expression_rows"]] == ["K", "m"]


def test_observation_payload_is_bounded_and_shape_mismatch_stays_unverified() -> None:
    long_unit = "u" * 250
    feature = UnitFeature([long_unit] * 70)
    legacy, observation = _observe(feature, [f"e{i}" for i in range(70)], requested=["K"] * 70)

    assert len(legacy) == 70
    assert len(observation["expression_rows"]) == 64
    assert observation["rows_truncated"] is True
    assert observation["raw_property"]["count"] == 70
    assert len(observation["raw_property"]["items"]) == 64
    assert observation["requested_spec_units"]["count"] == 70
    assert len(observation["requested_spec_units"]["items"]) == 64
    assert observation["requested_spec_units"]["items_truncated"] is True
    assert observation["mapping_status"] == "UNVERIFIED_OBSERVATION_TRUNCATED"
    assert observation["raw_property"]["items"][0] == {
        "kind": "string", "value_status": "omitted_too_long", "length": 250
    }


def _configure_eval_unit_getter(monkeypatch: pytest.MonkeyPatch, raw: Any) -> tuple[Any, list[Any]]:
    tree, nodes = _raw_eval_stationary_fixture(monkeypatch, count=1, point_count=2)
    original_factory = tree.numerical.factory

    def factory(tag: str, feature_type: str) -> Any:
        node = original_factory(tag, feature_type)
        node.props["unit"] = raw
        original_set = node.set

        def set_with_fixture_readback(name: str, value: Any) -> None:
            original_set(name, value)
            if name == "unit":
                node.props["unit"] = raw

        node.set = set_with_fixture_readback
        return node

    tree.numerical.factory = factory
    return tree, nodes


def _eval_arguments(*, units: Any = None, storage: str = "inline") -> dict[str, Any]:
    spec: dict[str, Any] = {
        "expressions": ["T"],
        "solution": {"dataset": "dset1", "solution": "sol1"},
        "aggregate": "none",
        "complex_mode": "real",
        "storage": storage,
    }
    if units is not None:
        spec["units"] = units
    return {"spec": spec}


def test_normal_eval_keeps_legacy_map_budget_cleanup_and_single_getter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree, nodes = _configure_eval_unit_getter(monkeypatch, ["K"])
    result = results.result_evaluate(tree.worker, "Model", _eval_arguments(units=["degC"]))

    assert result["expression_units"] == {"T": "K"}
    assert result["expression_unit_observation"]["requested_spec_units"]["items"] == [
        {"kind": "string", "value": "degC"}
    ]
    assert result["expression_unit_observation"]["raw_property"]["items"] == [
        {"kind": "string", "value": "K"}
    ]
    assert [call for call in nodes[0].calls if call == ("getStringArray", ("unit",))] == [
        ("getStringArray", ("unit",))
    ]
    assert ("set", ("unit", ["degC"])) in nodes[0].calls
    assert result["result_budget"]["status"] == "PASS"
    assert result["result_budget"]["publish_allowed"] is True
    assert result["cleanup"]["removed"] is True
    assert result["cleanup"]["cleanup_failed"] is False
    assert result["status"]["execution_state_unknown"] is False


def test_scalar_units_observation_does_not_normalize_the_existing_setter_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree, nodes = _configure_eval_unit_getter(monkeypatch, ["K"])
    result = results.result_evaluate(tree.worker, "Model", _eval_arguments(units="degC"))

    observation = result["expression_unit_observation"]
    assert observation["requested_spec_units"] == {"kind": "scalar_string", "value": "degC"}
    assert observation["python_setter_call_shape"] == (
        "_call(feature, 'set', 'unit', spec['units']) with the original supplied Python value; "
        "result_evaluate does not validate or normalize spec.units"
    )
    assert ("set", ("unit", "degC")) in nodes[0].calls
    assert not any(call == ("set", ("unit", ["degC"])) for call in nodes[0].calls)
    assert result["expression_units"] == {"T": "K"}
    assert result["cleanup"]["removed"] is True


def test_artifact_json_retains_generated_observation_without_changing_legacy_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree, _nodes = _configure_eval_unit_getter(monkeypatch, [""])
    monkeypatch.setattr(results, "trusted_project_root", lambda _worker: tmp_path)
    result = results.result_evaluate(tree.worker, "Model", _eval_arguments(units=["1"], storage="artifact"))

    assert result["expression_units"] == {"T": None}
    observation = result["expression_unit_observation"]
    artifact_ref = Path(result["artifact"]["artifact_ref"])
    artifact_path = artifact_ref if artifact_ref.is_absolute() else tmp_path / artifact_ref
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved["metadata"]["expression_unit_observation"] == observation
    assert observation["mapping_status"] == (
        "UNVERIFIED_POSITIONAL_SHAPE_HAS_NULL_EMPTY_OR_UNEXPECTED_ITEMS"
    )
