"""Software-only aggregation tests; fake Worker outputs never establish native acceptance."""
from __future__ import annotations

import pytest

from tests import test_w23_mode_basis_v2 as math_fixtures
from tests import test_w23_mode_basis_v2_results as native_fixtures
from tools.w23_mode_basis_v2 import (
    build_two_mode_basis_field_contracts,
    independent_two_mode_basis_from_native_field_readbacks,
)
from tools.w23_mode_basis_v2_aggregate import (
    AggregationError,
    aggregate_two_mode_native_result,
)


def _independent_reference(request):
    contracts = build_two_mode_basis_field_contracts(request)
    signal, modes, incident, _, _ = math_fixtures._analytic_fields()
    output_q = contracts["quadratures"]["output"]
    input_q = contracts["quadratures"]["input"]
    raw = {
        "signal": math_fixtures._raw_readback(contracts["output"]["signal"], output_q, signal),
        "modes": [math_fixtures._raw_readback(row, output_q, field)
                  for row, field in zip(contracts["output"]["modes"], modes)],
        "incident": math_fixtures._raw_readback(contracts["incident"], input_q, incident),
    }
    return independent_two_mode_basis_from_native_field_readbacks(
        request, contracts, signal_raw=raw["signal"], mode_raws=raw["modes"],
        incident_raw=raw["incident"])


def _set_term(result, term_id, value, *, power=False):
    record = result["native_integrals"]["terms"][term_id]
    record["real"] = float(value.real if isinstance(value, complex) else value)
    record["imag"] = 0.0 if power else float(value.imag)


def _aligned_response(monkeypatch):
    request, definition = native_fixtures._definition()
    native_fixtures._install_native_test_doubles(monkeypatch, request)
    response = native_fixtures._invoke(request, definition)
    independent = _independent_reference(request)
    for i in range(2):
        for j in range(2):
            _set_term(response, f"G{i}{j}", complex(
                independent["gram_raw"][i][j]["real"], independent["gram_raw"][i][j]["imag"]))
    for index in range(2):
        _set_term(response, f"b{index}", complex(
            independent["coupling"][index]["real"], independent["coupling"][index]["imag"]))
    _set_term(response, "P_signal", independent["signal_power_w"], power=True)
    _set_term(response, "P_incident", independent["incident_power_w"], power=True)
    return request, definition, response, independent


def test_aggregator_compares_raw_registered_terms_to_independent_fields_but_keeps_acceptance_pending(monkeypatch):
    request, definition, response, independent = _aligned_response(monkeypatch)

    result = aggregate_two_mode_native_result(definition, response, independent)

    assert result["status"] == "SOFTWARE_COMPARISON_PASS_PROVENANCE_PENDING"
    assert result["native_input_status"] == "COMSOL_NATIVE_RAW"
    assert result["numerical_comparison"]["status"] == "SOFTWARE_NATIVE_INTEGRAL_COMPARISON_VALIDATION_ONLY"
    assert result["native_acceptance"] == "NOT_RUN"
    assert result["provenance_gates"]["native_dispatch_authentication"] == "NOT_PROVIDED_TO_OFFLINE_AGGREGATOR"
    assert result["provenance_gates"]["numeric_port_mode_index_readbacks"] == {
        "mode_0": "UNVERIFIED", "mode_1": "UNVERIFIED"}
    assert result["provenance_gates"]["native_surface_frame_normal_readback"] == (
        "PRESENT_IN_ENVELOPE_UNAUTHENTICATED_FRAME_AND_PORT_RELATION_UNVERIFIED")
    assert result["basis_request_id"] == request["request_id"]
    assert result["study_or_solver_invoked_by_aggregator"] is False


def test_aggregator_preserves_legacy_raw_integral_results_without_normal_provenance(monkeypatch):
    request, definition, response, independent = _aligned_response(monkeypatch)
    for key in ("output", "input"):
        response["surface_readbacks"][key].pop("normal_provenance")

    result = aggregate_two_mode_native_result(definition, response, independent)

    assert result["status"] == "SOFTWARE_COMPARISON_PASS_PROVENANCE_PENDING"
    assert result["provenance_gates"]["native_surface_frame_normal_readback"] == (
        "NOT_PRESENT_IN_LEGACY_RAW_RESULT")
    assert result["native_acceptance"] == "NOT_RUN"
    assert result["basis_request_id"] == request["request_id"]


def test_aggregator_rejects_partial_normal_provenance(monkeypatch):
    _request, definition, response, independent = _aligned_response(monkeypatch)
    response["surface_readbacks"]["input"].pop("normal_provenance")

    with pytest.raises(AggregationError, match="only partially present"):
        aggregate_two_mode_native_result(definition, response, independent)


def test_aggregator_rejects_foreign_managed_identity_before_comparison(monkeypatch):
    _request, definition, response, independent = _aligned_response(monkeypatch)
    response["managed_execution_binding"]["model_revision"] += 1

    with pytest.raises(AggregationError, match="managed project/ModelRef/revision"):
        aggregate_two_mode_native_result(definition, response, independent)


def test_aggregator_rejects_matrix_slot_that_disagrees_with_term_table(monkeypatch):
    _request, definition, response, independent = _aligned_response(monkeypatch)
    slot = response["native_integrals"]["gram_matrix"][0][0]
    response["native_integrals"]["gram_matrix"][0][0] = {**slot, "real": slot["real"] + 0.1}

    with pytest.raises(AggregationError, match=r"Gram matrix slot \[0,0\] disagrees"):
        aggregate_two_mode_native_result(definition, response, independent)


def test_aggregator_rejects_wrong_native_mode_index_readback(monkeypatch):
    _request, definition, response, independent = _aligned_response(monkeypatch)
    response["source_readbacks"]["mode_0"]["numeric_port_mode_index"][
        "native_port_mode_index_readback"] = 2

    with pytest.raises(AggregationError, match="mode-index readback differs"):
        aggregate_two_mode_native_result(definition, response, independent)


def test_aggregator_rejects_unverified_temporary_integral_cleanup(monkeypatch):
    _request, definition, response, independent = _aligned_response(monkeypatch)
    response["native_integrals"]["terms"]["G00"]["cleanup"]["removed"] = False

    with pytest.raises(AggregationError, match="cleanup evidence"):
        aggregate_two_mode_native_result(definition, response, independent)


def test_aggregator_applies_frozen_complex_integral_comparison_threshold(monkeypatch):
    _request, definition, response, independent = _aligned_response(monkeypatch)
    coupling = response["native_integrals"]["terms"]["b0"]
    coupling["real"] *= 1.01

    with pytest.raises(AggregationError, match=r"b\[0\] relative error"):
        aggregate_two_mode_native_result(definition, response, independent)


def test_aggregator_rejects_field_reference_without_all_four_cleanup_validations(monkeypatch):
    _request, definition, response, independent = _aligned_response(monkeypatch)
    independent["field_readback_validations"].pop("mode_1")

    with pytest.raises(AggregationError, match="all four field-readback validations"):
        aggregate_two_mode_native_result(definition, response, independent)
