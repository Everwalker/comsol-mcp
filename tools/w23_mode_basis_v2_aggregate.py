"""Software-only aggregation of W23 v2 managed integrals and field quadrature.

This adapter joins the registered route's raw IntSurface result to the
independent complex-field quadrature/projection reference. Its input envelope
is not authenticated here. A numerical match therefore remains a software
comparison result: it cannot establish native provenance, Numeric Port mode
identity, or scientific acceptance.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from jsonschema import ValidationError, validate

from comsol_mcp._w23_basis_v2_contract import basis_request_binding, validate_basis_request
from comsol_mcp._w23_basis_v2_results import (
    NATIVE_RESULT_SCHEMA,
    OPERATION_ID,
    RESULT_SCHEMA_ID,
    validate_request_shape,
)
from tools.w23_mode_basis_v2 import (
    BASIS_SCHEMA_ID,
    TwoModeBasisError,
    compare_two_mode_native_integrals_to_field_reference,
)


AGGREGATION_SCHEMA_ID = "urn:comsol-mcp:w23:two-mode-result-aggregation:1.0.0"
_FREQUENCY_SCALE = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}
_TERM_ROLES = {
    "G00": ("mode_0", "mode_0", "output"),
    "G01": ("mode_0", "mode_1", "output"),
    "G10": ("mode_1", "mode_0", "output"),
    "G11": ("mode_1", "mode_1", "output"),
    "b0": ("mode_0", "signal", "output"),
    "b1": ("mode_1", "signal", "output"),
    "P_signal": ("signal", "signal", "output"),
    "P_incident": ("incident", "incident", "input"),
}


class AggregationError(ValueError):
    """The native-result and independent-reference envelopes do not agree."""


def _fail(message: str) -> None:
    raise AggregationError(message)


def _json_sha256(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(f"aggregation input is not finite JSON data: {exc}")
    return hashlib.sha256(payload).hexdigest()


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be finite numeric data")
    result = float(value)
    if not math.isfinite(result):
        _fail(f"{label} must be finite numeric data")
    return result


def _close(actual: Any, expected: Any, *, label: str, rel_tol: float = 1e-10,
           abs_tol: float = 1e-30) -> float:
    observed = _finite(actual, f"{label} observed")
    target = _finite(expected, f"{label} expected")
    if not math.isclose(observed, target, rel_tol=rel_tol, abs_tol=abs_tol):
        _fail(f"{label} differs from the frozen request/readback")
    return observed


def _source_roles(request: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        "signal": request["signal_source"],
        "mode_0": request["basis_modes"][0]["source"],
        "mode_1": request["basis_modes"][1]["source"],
        "incident": request["incident_source"],
    }


def _verify_result_identity(request: Mapping[str, Any], definition: Mapping[str, Any],
                            result: Mapping[str, Any]) -> None:
    expected = {
        "basis_id": request["basis_id"], "case": dict(request["case"]),
        "project_id": request["project_id"], "model_ref": dict(request["model_ref"]),
        "model_tag": request["model_ref"]["model_tag"],
        "model_revision": request["model_revision"],
        "geometry_revision": request["geometry_revision"],
        "frequency_hz": request["frequency_hz"],
        "coordinate_frame": request["coordinate_frame"],
        "mode_ids": [row["mode_id"] for row in request["basis_modes"]],
        "mode_indices": [row["mode_index"] for row in request["basis_modes"]],
    }
    if result.get("schema_id") != RESULT_SCHEMA_ID or result.get("operation_id") != OPERATION_ID:
        _fail("input is not the registered W23 v2 native-integral result schema")
    if (result.get("schema_version") != "1.0.0"
            or result.get("status") != "SUCCEEDED"
            or result.get("result_status") != "COMPUTED_NATIVE_TWO_MODE_INTEGRALS"
            or result.get("origin") != "COMSOL_NATIVE_INT_SURFACE"
            or result.get("native_result") != "COMSOL_NATIVE_RAW"
            or result.get("quadrature_comparison") != "NOT_RUN"
            or result.get("projection_acceptance") != "NOT_RUN"
            or result.get("production_route_status") != "ROUTE_REGISTERED_NATIVE_INTEGRATION_ONLY"
            or result.get("study_or_solver_invoked") is not False
            or result.get("caller_field_arrays_accepted") is not False):
        _fail("native result status/origin is not the expected raw-integral-only envelope")
    if (result.get("basis_request_id") != request.get("request_id")
            or result.get("definition_sha256") != definition.get("definition_sha256")
            or result.get("identity") != expected):
        _fail("native result identity differs from the exact digest-bound v2 request")
    managed = result.get("managed_execution_binding")
    expected_managed = {
        "project_id": request["project_id"], "model_ref": dict(request["model_ref"]),
        "model_revision": request["model_revision"], "source": "managed_observation_context",
    }
    if managed != expected_managed:
        _fail("native result managed project/ModelRef/revision binding differs from the request")


def _verify_source_readbacks(request: Mapping[str, Any], definition: Mapping[str, Any],
                             result: Mapping[str, Any]) -> dict[str, str]:
    roles = _source_roles(request)
    raw = result.get("source_readbacks")
    if not isinstance(raw, Mapping) or set(raw) != set(roles):
        _fail("native source readbacks do not cover signal, both basis members, and incident reference")
    mode_parameters = {row["mode_id"]: row["parameter"]
                       for row in definition["mode_axis_parameters"]}
    outer_indices = {source["outer_index"] for source in roles.values()}
    if len(outer_indices) != 1:
        _fail("all four v2 source identities must share the exact native outer index")
    mode_readback_status: dict[str, str] = {}
    for role, source in roles.items():
        item = raw[role]
        if not isinstance(item, Mapping):
            _fail(f"{role} native source readback is malformed")
        binding, axes = item.get("dataset_binding"), item.get("solution_axes")
        surface = request["input_surface"] if role == "incident" else request["output_surface"]
        if (not isinstance(binding, Mapping) or binding.get("binding_complete") is not True
                or binding.get("dataset") != source["dataset_id"]
                or binding.get("solution") != source["solution_id"]
                or binding.get("component") != surface["component"]
                or binding.get("geometry") != surface["geometry"]):
            _fail(f"{role} dataset/solution/component readback differs from the requested source")
        if not isinstance(axes, Mapping):
            _fail(f"{role} native SolutionInfo readback is missing")
        for key in ("outer_index", "inner_index", "solnum"):
            if type(axes.get(key)) is not int or axes[key] != source[key]:
                _fail(f"{role} native SolutionInfo {key} differs from the exact requested source")
        names = axes.get("parameter_names")
        pairs = axes.get("parameters_by_pair")
        if (not isinstance(names, list) or not names
                or not all(isinstance(name, str) and name for name in names)
                or len({name.lower() for name in names}) != len(names)
                or not isinstance(pairs, Mapping) or set(pairs) != set(names)):
            _fail(f"{role} native SolutionInfo parameter/value-unit table is incomplete")
        frequency = axes.get("frequency")
        frequency_names = [name for name in names if name.strip().lower() in {
            "f", "freq", "frequency"}]
        if (not isinstance(frequency, Mapping) or len(frequency_names) != 1
                or frequency.get("parameter") != frequency_names[0]):
            _fail(f"{role} native SolutionInfo must identify one exact frequency parameter")
        native_frequency_pair = pairs[frequency_names[0]]
        if (not isinstance(native_frequency_pair, (list, tuple)) or len(native_frequency_pair) != 2
                or native_frequency_pair[0] != frequency.get("value")
                or native_frequency_pair[1] != frequency.get("unit")):
            _fail(f"{role} native frequency summary differs from its SolutionInfo pair")
        if (not isinstance(frequency, Mapping) or frequency.get("unit") not in _FREQUENCY_SCALE
                or not math.isclose(
                    _finite(frequency.get("value"), f"{role} native frequency")
                    * _FREQUENCY_SCALE[frequency["unit"]],
                    _finite(request["frequency_hz"], "requested frequency"),
                    rel_tol=1e-12, abs_tol=0.0)):
            _fail(f"{role} native SolutionInfo frequency differs from the requested frequency")
        summary_hz = _finite(frequency.get("frequency_hz"), f"{role} frequency_hz")
        if (summary_hz <= 0.0 or not math.isclose(
                summary_hz, _finite(request["frequency_hz"], "requested frequency"),
                rel_tol=1e-12, abs_tol=0.0)):
            _fail(f"{role} native frequency summary differs from the requested frequency")
        expected_mode_axis = None
        if role.startswith("mode_"):
            index = int(role[-1])
            mode_row = request["basis_modes"][index]
            expected_mode_axis = mode_parameters[mode_row["mode_id"]]
            mode_pair = pairs.get(expected_mode_axis)
            if (item.get("mode_axis_parameter") != expected_mode_axis
                    or expected_mode_axis not in names
                    or not isinstance(mode_pair, (list, tuple)) or len(mode_pair) != 2
                    or not isinstance(mode_pair[1], str)):
                _fail(f"{role} mode-axis selector is not bound to its exact SolutionInfo parameter")
            _finite(mode_pair[0], f"{role} mode-axis value")
            selector = item.get("numeric_port_mode_index")
            if (not isinstance(selector, Mapping)
                    or selector.get("value") != mode_row["mode_index"]
                    or selector.get("origin") != "canonical_request_provenance_only"):
                _fail(f"{role} Numeric Port mode index provenance is malformed")
            mode_readback = selector.get("native_port_mode_index_readback")
            if mode_readback == "NOT_PROVIDED":
                mode_readback_status[role] = "UNVERIFIED"
            elif type(mode_readback) is int and mode_readback == mode_row["mode_index"]:
                mode_readback_status[role] = "READBACK_PRESENT_PENDING_MANAGED_ATTESTATION"
            else:
                _fail(f"{role} native Numeric Port mode-index readback differs from the requested basis identity")
        elif item.get("mode_axis_parameter") is not None:
            _fail(f"{role} non-mode source must not carry a mode-axis selector")
    return mode_readback_status


def _verify_surfaces_and_terms(request: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    surfaces = result.get("surface_readbacks")
    terms = result.get("term_bindings")
    native = result.get("native_integrals")
    if not isinstance(surfaces, Mapping) or set(surfaces) != {"output", "input"}:
        _fail("native output/input surface readbacks are incomplete")
    if not isinstance(terms, Mapping) or set(terms) != set(_TERM_ROLES):
        _fail("native result does not bind all four Gram, two coupling, and two power terms")
    if not isinstance(native, Mapping) or not isinstance(native.get("terms"), Mapping):
        _fail("native integral matrix/vector/power terms are unavailable")
    if set(native["terms"]) != set(_TERM_ROLES):
        _fail("native integral term table is incomplete")

    measured_surfaces: dict[str, dict[str, Any]] = {}
    for key in ("output", "input"):
        expected = request["output_surface" if key == "output" else "input_surface"]
        row = surfaces[key]
        selection = row.get("selection") if isinstance(row, Mapping) else None
        expected_selection = {"component": expected["component"], "geometry": expected["geometry"],
                              "tag": expected["selection_tag"]}
        if (not isinstance(selection, Mapping) or dict(selection) != expected_selection
                or row.get("aperture_id") != expected["aperture_id"]
                or row.get("entity_dimension") != 2):
            _fail(f"{key} native named boundary selection differs from the exact request")
        ids = row.get("entity_ids")
        if (not isinstance(ids, list) or not ids
                or any(type(item) is not int or item < 1 for item in ids)
                or len(set(ids)) != len(ids)):
            _fail(f"{key} native boundary entity IDs are invalid")
        area = _close(row.get("measured_area_m2"), expected["surface_area_m2"],
                      label=f"{key} measured area")
        _close(row.get("expected_area_m2"), expected["surface_area_m2"], label=f"{key} expected area")
        measured_surfaces[key] = {"entity_ids": ids, "area_m2": area,
                                  "tag": expected["selection_tag"],
                                  "aperture_id": expected["aperture_id"]}

    source_roles = _source_roles(request)
    for term_id, (role_a, role_b, surface_key) in _TERM_ROLES.items():
        binding = terms[term_id]
        source = source_roles[role_a]
        surface = measured_surfaces[surface_key]
        if (binding.get("role_a") != role_a or binding.get("role_b") != role_b
                or binding.get("dataset_id") != source["dataset_id"]
                or binding.get("solution_id") != source["solution_id"]
                or binding.get("outer_index") != source["outer_index"]
                or binding.get("inner_index") != source["inner_index"]
                or binding.get("solnum") != source["solnum"]
                or binding.get("selection_tag") != surface["tag"]
                or binding.get("entity_dimension") != 2
                or binding.get("entity_ids") != surface["entity_ids"]
                or binding.get("aperture_id") != surface["aperture_id"]
                or binding.get("coordinate_frame") != request["coordinate_frame"]
                or binding.get("unit") != "W"
                or not isinstance(binding.get("expression"), str)
                or not binding["expression"]):
            _fail(f"native term binding {term_id} does not match its source, aperture, and unit")
        term = native["terms"][term_id]
        if (not isinstance(term, Mapping) or term.get("unit") != "W"
                or term.get("feature_type") != "IntSurface"
                or term.get("dataset_id") != source["dataset_id"]
                or term.get("solution_id") != source["solution_id"]
                or term.get("expression") != binding["expression"]):
            _fail(f"native integral term {term_id} differs from its exact term binding")
        cleanup = term.get("cleanup")
        if (not isinstance(cleanup, Mapping) or cleanup.get("created") is not True
                or cleanup.get("removed") is not True or cleanup.get("cleanup_failed") is not False
                or cleanup.get("type_id") != "IntSurface"):
            _fail(f"native integral term {term_id} lacks exact temporary-feature cleanup evidence")
        measure = _close(term.get("selection_measure_m2"), surface["area_m2"],
                         label=f"{term_id} integral measure")
        _close(binding.get("area_m2"), measure, label=f"{term_id} bound area")
        measure_source = term.get("selection_measure_source")
        if not isinstance(measure_source, str) or "engine integral of 1" not in measure_source:
            _fail(f"{term_id} native selection measure lacks integral-of-one provenance")

    output_entities = [terms[name]["entity_ids"] for name in ("G00", "G01", "G10", "G11", "b0", "b1", "P_signal")]
    if any(ids != output_entities[0] for ids in output_entities[1:]):
        _fail("all output-plane native integrals must resolve to the same exact boundary entity IDs")


def _term_complex(native_result: Mapping[str, Any], term_id: str, *, require_complex: bool) -> dict[str, Any]:
    record = native_result["native_integrals"]["terms"][term_id]
    real = _finite(record.get("real"), f"{term_id}.real")
    imag = _finite(record.get("imag"), f"{term_id}.imag")
    if record.get("unit") != "W" or type(record.get("is_complex")) is not bool:
        _fail(f"{term_id} is missing complex W-unit readback")
    if require_complex and record["is_complex"] is not True:
        _fail(f"{term_id} reciprocal bilinear term lacks complex readback")
    if not record["is_complex"] and imag != 0.0:
        _fail(f"{term_id} has an imaginary value without complex readback")
    return {"real": real, "imag": imag, "unit": "W"}


def aggregate_two_mode_native_result(
    definition: Mapping[str, Any], native_result: Mapping[str, Any],
    independent_reference: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare a raw managed v2 result to exact independent vector quadrature.

    The function returns a numerical comparison even if native mode-index
    readback is unavailable, but it leaves overall scientific acceptance
    NOT_RUN and reports the missing/unauthenticated provenance gates.
    """
    try:
        validate_request_shape({"definition": dict(definition)})
        validate_basis_request(definition["basis_request"])
        validate(instance=dict(native_result), schema=NATIVE_RESULT_SCHEMA)
    except Exception as exc:
        if isinstance(exc, AggregationError):
            raise
        _fail(f"definition or native result failed its registered schema gate: {exc}")
    request = definition["basis_request"]
    if not isinstance(native_result, Mapping):
        _fail("native result must be a mapping")
    _verify_result_identity(request, definition, native_result)
    mode_readback = _verify_source_readbacks(request, definition, native_result)
    _verify_surfaces_and_terms(request, native_result)

    if (not isinstance(independent_reference, Mapping)
            or independent_reference.get("native_result") != "NOT_RUN"
            or independent_reference.get("status") != "SOFTWARE_RECOMPUTED_TWO_MODE_BASIS_FROM_FIELD_SAMPLES"):
        _fail("independent source/grid-validated field quadrature reference is required")
    validations = independent_reference.get("field_readback_validations")
    expected_roles = {"signal", "mode_0", "mode_1", "incident"}
    if not isinstance(validations, Mapping) or set(validations) != expected_roles:
        _fail("independent field reference must retain all four field-readback validations")
    for role, evidence in validations.items():
        cleanup = evidence.get("cleanup") if isinstance(evidence, Mapping) else None
        if (not isinstance(evidence, Mapping)
                or evidence.get("status") != "NATIVE_RAW_VECTOR_READBACK_VALIDATED"
                or evidence.get("native_result") != "COMSOL_NATIVE_RAW"
                or not isinstance(cleanup, Mapping) or cleanup.get("removed") is not True
                or cleanup.get("cleanup_failed") is not False):
            _fail(f"independent {role} field readback/temporary interpolation cleanup is incomplete")

    terms = native_result["native_integrals"]["terms"]
    gram = {"00": _term_complex(native_result, "G00", require_complex=True),
            "01": _term_complex(native_result, "G01", require_complex=True),
            "10": _term_complex(native_result, "G10", require_complex=True),
            "11": _term_complex(native_result, "G11", require_complex=True)}
    coupling = {"0": _term_complex(native_result, "b0", require_complex=True),
                "1": _term_complex(native_result, "b1", require_complex=True)}
    signal = _term_complex(native_result, "P_signal", require_complex=False)
    incident = _term_complex(native_result, "P_incident", require_complex=False)
    for key, record in (("G00", gram["00"]), ("G01", gram["01"]),
                        ("G10", gram["10"]), ("G11", gram["11"])):
        if record != {"real": terms[key]["real"], "imag": terms[key]["imag"], "unit": "W"}:
            _fail(f"native Gram matrix entry {key} differs from its term record")
    native_integrals = native_result["native_integrals"]
    for i in range(2):
        row = native_integrals["gram_matrix"][i]
        for j in range(2):
            expected = terms[f"G{i}{j}"]
            if dict(row[j]) != dict(expected):
                _fail(f"native Gram matrix slot [{i},{j}] disagrees with its term table")
    for i in range(2):
        row = native_integrals["coupling_vector"][i]
        expected = terms[f"b{i}"]
        if dict(row) != dict(expected):
            _fail(f"native coupling-vector slot {i} disagrees with its term table")
    for key, expected in (("signal_power", signal), ("incident_reference_power", incident)):
        observed = native_integrals[key]
        term_id = "P_signal" if key == "signal_power" else "P_incident"
        if dict(observed) != dict(terms[term_id]):
            _fail(f"native {key} summary differs from its term record")

    # This legacy numerical comparer consumes a normalized envelope. Its
    # binding is reconstructed from the canonical request; it is not evidence
    # that the caller's native-result envelope is authenticated.
    comparison_envelope = {
        "schema_id": BASIS_SCHEMA_ID, "status": "SUCCEEDED",
        "result_status": "COMPUTED_NATIVE_BASIS_INTEGRALS",
        "origin": "COMSOL_NATIVE_INTEGRATION_FEATURES",
        "request_id": request["request_id"],
        "binding": basis_request_binding(request),
        "integrals": {"gram": gram, "coupling": coupling,
                      "signal_power": signal, "incident_power": incident},
    }
    try:
        comparison = compare_two_mode_native_integrals_to_field_reference(
            request, comparison_envelope, independent_reference)
    except TwoModeBasisError as exc:
        _fail(f"native integral and independent quadrature comparison failed: {exc}")
    return {
        "schema_id": AGGREGATION_SCHEMA_ID,
        "status": "SOFTWARE_COMPARISON_PASS_PROVENANCE_PENDING",
        "operation_id": OPERATION_ID,
        "basis_request_id": request["request_id"],
        "native_result_sha256": _json_sha256(native_result),
        "independent_reference_source_identity": dict(independent_reference["source_identity"]),
        "native_input_status": "COMSOL_NATIVE_RAW",
        "numerical_comparison": comparison,
        "provenance_gates": {
            "native_dispatch_authentication": "NOT_PROVIDED_TO_OFFLINE_AGGREGATOR",
            "numeric_port_mode_index_readbacks": mode_readback,
            "native_surface_frame_normal_readback": "NOT_PRESENT_IN_RAW_INTEGRAL_RESULT",
            "overall_scientific_acceptance": "NOT_RUN",
        },
        "native_acceptance": "NOT_RUN",
        "study_or_solver_invoked_by_aggregator": False,
        "evidence_scope": (
            "software comparison of a schema-valid raw-result envelope with an independent "
            "field-quadrature envelope; not authenticated native or scientific acceptance"),
    }


__all__ = ["AGGREGATION_SCHEMA_ID", "AggregationError", "aggregate_two_mode_native_result"]
