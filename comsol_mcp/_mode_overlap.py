"""Finite software-only core for the W23 vector mode-overlap contract.

This module consumes already extracted arrays. It does not call COMSOL, prove
that caller-supplied identities are truthful, or provide native field
extraction. A future managed adapter must bind every array to backend-observed
model, revision, dataset, solution, plane, mode, and incident-reference data.

Supported profile assumption: reciprocal, lossless, forward-propagating,
nondegenerate modes sampled on one registered planar transverse surface. This
kernel does not prove those physical assumptions; a managed native adapter must
bind its own observations and evidence before using the result for acceptance.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import ExecutionContractError


DEFINITION_SCHEMA_VERSION = "1.1.0"
RESULT_SCHEMA_VERSION = "1.2.0"
DEFINITION_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap:kernel-definition:1.1.0"
RESULT_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap:result:1.2.0"
ALGORITHM_ID = "reciprocal_lossless_forward_planar_v1"
APPLICABILITY_PROFILE = "reciprocal_lossless_forward_nondegenerate_planar_v1"
NORMAL_UNIT_TOLERANCE = 1e-9
PLANARITY_ULP_SAFETY_FACTOR = 64
PLANARITY_BOUND_POLICY = "coordinate_local_difference_dot_ulp_64_v1"
_SUPPORTED_TIME_DEPENDENCE = ("exp(-i omega t)", "exp(+i omega t)")
_SUPPORTED_COMPLEX_FIELD_REPRESENTATION = "full_physical_complex_phasor_including_reconstructed_envelope_phase"
_COMPONENT_ORDER = ("x", "y", "z")
_GEOMETRY_UNITS = {
    3: ("area", "m^2", "W"),
    2: ("line_per_unit_depth", "m", "W/m"),
}


DEFINITION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": DEFINITION_SCHEMA_ID,
    "title": "result.mode_overlap hydrated kernel definition v1.1.0",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "profile", "phasor_convention", "plane", "signal", "reference_mode",
        "power_floor", "incident_reference_power",
    ],
    "properties": {
        "schema_version": {"const": DEFINITION_SCHEMA_VERSION},
        "profile": {"const": APPLICABILITY_PROFILE},
        "phasor_convention": {"$ref": "#/$defs/phasorConvention"},
        "plane": {"$ref": "#/$defs/plane"},
        "signal": {"$ref": "#/$defs/field"},
        "reference_mode": {"$ref": "#/$defs/referenceMode"},
        "power_floor": {"$ref": "#/$defs/powerFloor"},
        "incident_reference_power": {"$ref": "#/$defs/incidentPower"},
        "eta_mode_upper_tolerance": {
            "type": "number", "minimum": 0,
            "description": "Optional declared diagnostic tolerance; never changes or clamps the computed eta_mode.",
        },
        "capture": {"$ref": "#/$defs/capture"},
    },
    "$defs": {
        "finiteNumber": {"type": "number"},
        "nonEmpty": {"type": "string", "minLength": 1},
        "phasorConvention": {
            "type": "object", "additionalProperties": False,
            "required": ["time_dependence", "complex_field_representation"],
            "properties": {
                "time_dependence": {"enum": list(_SUPPORTED_TIME_DEPENDENCE)},
                "complex_field_representation": {"const": _SUPPORTED_COMPLEX_FIELD_REPRESENTATION},
            },
            "description": "Explicit complex-phasor time sign and declaration that E/H include reconstructed envelope phase; declarations do not establish source authenticity.",
        },
        "complexScalar": {
            "type": "object", "additionalProperties": False,
            "required": ["real", "imag"],
            "properties": {"real": {"type": "number"}, "imag": {"type": "number"}},
        },
        "source": {
            "type": "object", "additionalProperties": False,
            "required": ["model_ref", "model_revision", "dataset_id", "solution_id", "frequency_hz", "plane_id"],
            "properties": {
                "model_ref": {"$ref": "#/$defs/nonEmpty"},
                "model_revision": {"$ref": "#/$defs/nonEmpty"},
                "dataset_id": {"$ref": "#/$defs/nonEmpty"},
                "solution_id": {"$ref": "#/$defs/nonEmpty"},
                "frequency_hz": {"type": "number", "exclusiveMinimum": 0},
                "plane_id": {"$ref": "#/$defs/nonEmpty"},
            },
        },
        "vector3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
        "coordinates": {
            "type": "array", "minItems": 1,
            "items": {"$ref": "#/$defs/vector3"},
            "description": "Every sample must satisfy the fixed coordinate/local-difference 64-ULP plane-distance bound; no physical geometry tolerance, plane fitting, or projection is applied.",
        },
        "complexVectorSamples": {
            "type": "array", "minItems": 1,
            "items": {"$ref": "#/$defs/vector3"},
            "description": "N by 3 real values; complex data uses required sibling real/imag arrays.",
        },
        "complexArray": {
            "type": "object", "additionalProperties": False,
            "required": ["unit", "real", "imag"],
            "properties": {
                "unit": {"enum": ["V/m", "A/m"]},
                "real": {"$ref": "#/$defs/complexVectorSamples"},
                "imag": {"$ref": "#/$defs/complexVectorSamples"},
            },
        },
        "plane": {
            "type": "object", "additionalProperties": False,
            "required": ["plane_id", "geometry_dimension", "integration_measure", "coordinate_unit", "coordinates", "quadrature_weight_unit", "quadrature_weights", "normal"],
            "properties": {
                "plane_id": {"$ref": "#/$defs/nonEmpty"},
                "geometry_dimension": {"enum": [2, 3]},
                "integration_measure": {"enum": ["line_per_unit_depth", "area"]},
                "coordinate_unit": {"const": "m"},
                "coordinates": {"$ref": "#/$defs/coordinates"},
                "quadrature_weight_unit": {"enum": ["m", "m^2"]},
                "quadrature_weights": {"type": "array", "minItems": 1, "items": {"type": "number", "exclusiveMinimum": 0}},
                "normal": {"$ref": "#/$defs/vector3"},
            },
        },
        "field": {
            "type": "object", "additionalProperties": False,
            "required": ["field_id", "source", "phasor_convention", "coordinate_unit", "sample_coordinates", "component_order", "electric_field", "magnetic_field"],
            "properties": {
                "field_id": {"$ref": "#/$defs/nonEmpty"},
                "source": {"$ref": "#/$defs/source"},
                "phasor_convention": {"$ref": "#/$defs/phasorConvention"},
                "coordinate_unit": {"const": "m"},
                "sample_coordinates": {"$ref": "#/$defs/coordinates"},
                "component_order": {"const": ["x", "y", "z"]},
                "electric_field": {
                    "allOf": [{"$ref": "#/$defs/complexArray"}],
                    "properties": {"unit": {"const": "V/m"}},
                },
                "magnetic_field": {
                    "allOf": [{"$ref": "#/$defs/complexArray"}],
                    "properties": {"unit": {"const": "A/m"}},
                },
                "mode": {"$ref": "#/$defs/mode"},
            },
        },
        "mode": {
            "type": "object", "additionalProperties": False,
            "required": ["mode_id", "eigenvalue", "eigenvalue_unit"],
            "properties": {
                "mode_id": {"$ref": "#/$defs/nonEmpty"},
                "eigenvalue": {"$ref": "#/$defs/complexScalar"},
                "eigenvalue_unit": {"$ref": "#/$defs/nonEmpty"},
            },
        },
        "referenceMode": {
            "allOf": [
                {"$ref": "#/$defs/field"},
                {"type": "object", "required": ["mode"]},
            ],
        },
        "powerFloor": {
            "type": "object", "additionalProperties": False,
            "required": ["value", "unit"],
            "properties": {
                "value": {"type": "number", "exclusiveMinimum": 0},
                "unit": {"enum": ["W", "W/m"]},
            },
        },
        "incidentPower": {
            "type": "object", "additionalProperties": False,
            "required": ["reference_id", "power", "unit", "input_plane_id", "source", "phasor_convention"],
            "properties": {
                "reference_id": {"$ref": "#/$defs/nonEmpty"},
                "power": {"type": "number", "exclusiveMinimum": 0},
                "unit": {"enum": ["W", "W/m"]},
                "input_plane_id": {"$ref": "#/$defs/nonEmpty"},
                "source": {"$ref": "#/$defs/source"},
                "phasor_convention": {"$ref": "#/$defs/phasorConvention"},
            },
        },
        "capture": {
            "type": "object", "additionalProperties": False,
            "required": ["aperture_id", "plane_id", "sample_indices", "incident_reference_power"],
            "properties": {
                "aperture_id": {"$ref": "#/$defs/nonEmpty"},
                "plane_id": {"$ref": "#/$defs/nonEmpty"},
                "sample_indices": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 0}},
                "incident_reference_power": {"$ref": "#/$defs/incidentPower"},
            },
        },
    },
}


RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": RESULT_SCHEMA_ID,
    "title": "result.mode_overlap result v1.2.0",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "operation_id", "algorithm_id", "applicability_profile", "phasor_convention",
        "evidence_status", "identities", "surface", "signal_power", "reference_mode_power",
        "normalization_power_floor", "unnormalized_overlap_numerator", "overlap_amplitude",
        "normalized_overlap", "projected_mode_power", "incident_reference_power",
        "eta_mode", "eta_mode_range_diagnostic",
    ],
    "properties": {
        "schema_version": {"const": RESULT_SCHEMA_VERSION},
        "operation_id": {"const": "result.mode_overlap"},
        "algorithm_id": {"const": ALGORITHM_ID},
        "applicability_profile": {"const": APPLICABILITY_PROFILE},
        "phasor_convention": {"$ref": "#/$defs/phasorConvention"},
        "evidence_status": {
            "type": "object", "additionalProperties": False,
            "required": ["scope", "native_status", "input_boundary"],
            "properties": {
                "scope": {"const": "SOFTWARE_ONLY"},
                "native_status": {"const": "NOT_RUN"},
                "input_boundary": {"const": "CALLER_SUPPLIED_ARRAYS_NOT_NATIVE_EVIDENCE"},
            },
        },
        "identities": {
            "type": "object", "additionalProperties": False,
            "required": ["signal", "reference_mode"],
            "properties": {
                "signal": {"$ref": "#/$defs/fieldIdentity"},
                "reference_mode": {
                    "allOf": [
                        {"$ref": "#/$defs/fieldIdentity"},
                        {"type": "object", "required": ["mode"]},
                    ],
                },
            },
        },
        "surface": {
            "type": "object", "additionalProperties": False,
            "required": ["plane_id", "geometry_dimension", "integration_measure", "coordinate_unit", "quadrature_weight_unit", "normal", "sample_count", "planarity_roundoff"],
            "properties": {
                "plane_id": {"type": "string"}, "geometry_dimension": {"enum": [2, 3]},
                "integration_measure": {"enum": ["area", "line_per_unit_depth"]},
                "coordinate_unit": {"const": "m"}, "quadrature_weight_unit": {"enum": ["m^2", "m"]},
                "normal": {"$ref": "#/$defs/vector3"}, "sample_count": {"type": "integer", "minimum": 1},
                "planarity_roundoff": {"$ref": "#/$defs/planarityRoundoff"},
            },
        },
        "normalization_power_floor": {
            "type": "object", "additionalProperties": False,
            "required": ["value", "unit"],
            "properties": {"value": {"type": "number", "exclusiveMinimum": 0}, "unit": {"enum": ["W", "W/m"]}},
        },
        "signal_power": {"$ref": "#/$defs/power"},
        "reference_mode_power": {"$ref": "#/$defs/power"},
        "unnormalized_overlap_numerator": {"$ref": "#/$defs/complexValue"},
        "overlap_amplitude": {"$ref": "#/$defs/complexValue"},
        "normalized_overlap": {"type": "number"},
        "projected_mode_power": {"$ref": "#/$defs/power"},
        "incident_reference_power": {"$ref": "#/$defs/incidentPowerResult"},
        "eta_mode": {"$ref": "#/$defs/ratio"},
        "eta_mode_range_diagnostic": {"$ref": "#/$defs/rangeDiagnostic"},
        "eta_capture": {"$ref": "#/$defs/captureRatio"},
    },
    "$defs": {
        "phasorConvention": {
            "type": "object", "additionalProperties": False,
            "required": ["time_dependence", "complex_field_representation"],
            "properties": {
                "time_dependence": {"enum": list(_SUPPORTED_TIME_DEPENDENCE)},
                "complex_field_representation": {"const": _SUPPORTED_COMPLEX_FIELD_REPRESENTATION},
            },
        },
        "planarityRoundoff": {
            "type": "object", "additionalProperties": False,
            "required": ["policy_id", "safety_factor", "maximum_sample_bound_m"],
            "properties": {
                "policy_id": {"const": PLANARITY_BOUND_POLICY},
                "safety_factor": {"const": PLANARITY_ULP_SAFETY_FACTOR},
                "maximum_sample_bound_m": {"type": "number", "minimum": 0},
            },
        },
        "complexValue": {
            "type": "object", "additionalProperties": False,
            "required": ["real", "imag", "unit"],
            "properties": {"real": {"type": "number"}, "imag": {"type": "number"}, "unit": {"type": "string"}},
        },
        "power": {
            "type": "object", "additionalProperties": False,
            "required": ["value", "unit", "region_id", "normal"],
            "properties": {
                "value": {"type": "number"}, "unit": {"enum": ["W", "W/m"]},
                "region_id": {"type": "string"}, "normal": {"$ref": "#/$defs/vector3"},
            },
        },
        "vector3": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
        "source": {
            "type": "object", "additionalProperties": False,
            "required": ["model_ref", "model_revision", "dataset_id", "solution_id", "frequency_hz", "plane_id"],
            "properties": {
                "model_ref": {"type": "string"}, "model_revision": {"type": "string"},
                "dataset_id": {"type": "string"}, "solution_id": {"type": "string"},
                "frequency_hz": {"type": "number", "exclusiveMinimum": 0}, "plane_id": {"type": "string"},
            },
        },
        "mode": {
            "type": "object", "additionalProperties": False,
            "required": ["mode_id", "eigenvalue", "eigenvalue_unit"],
            "properties": {
                "mode_id": {"type": "string"},
                "eigenvalue": {"$ref": "#/$defs/complexEigenvalue"},
                "eigenvalue_unit": {"type": "string"},
            },
        },
        "complexEigenvalue": {
            "type": "object", "additionalProperties": False,
            "required": ["real", "imag"],
            "properties": {"real": {"type": "number"}, "imag": {"type": "number"}},
        },
        "fieldIdentity": {
            "type": "object", "additionalProperties": False,
            "required": ["field_id", "source", "phasor_convention"],
            "properties": {
                "field_id": {"type": "string"}, "source": {"$ref": "#/$defs/source"},
                "phasor_convention": {"$ref": "#/$defs/phasorConvention"}, "mode": {"$ref": "#/$defs/mode"},
            },
        },
        "incidentPowerResult": {
            "type": "object", "additionalProperties": False,
            "required": ["reference_id", "value", "unit", "input_plane_id", "source", "phasor_convention"],
            "properties": {
                "reference_id": {"type": "string"}, "value": {"type": "number"},
                "unit": {"enum": ["W", "W/m"]}, "input_plane_id": {"type": "string"},
                "source": {"$ref": "#/$defs/source"},
                "phasor_convention": {"$ref": "#/$defs/phasorConvention"},
            },
        },
        "ratio": {
            "type": "object", "additionalProperties": False,
            "required": ["value", "unit", "region", "numerator", "denominator"],
            "properties": {
                "value": {"type": "number"}, "unit": {"const": "1"},
                "region": {
                    "type": "object", "additionalProperties": False,
                    "required": ["plane_id", "measure"],
                    "properties": {"plane_id": {"type": "string"}, "measure": {"enum": ["area", "line_per_unit_depth"]}},
                },
                "numerator": {"$ref": "#/$defs/numerator"},
                "denominator": {"$ref": "#/$defs/incidentPowerResult"},
            },
        },
        "captureRatio": {
            "type": "object", "additionalProperties": False,
            "required": ["value", "unit", "region", "numerator", "denominator"],
            "properties": {
                "value": {"type": "number"}, "unit": {"const": "1"},
                "region": {
                    "type": "object", "additionalProperties": False,
                    "required": ["aperture_id", "plane_id", "sample_indices"],
                    "properties": {
                        "aperture_id": {"type": "string"}, "plane_id": {"type": "string"},
                        "sample_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                    },
                },
                "numerator": {"$ref": "#/$defs/numerator"},
                "denominator": {"$ref": "#/$defs/incidentPowerResult"},
            },
        },
        "numerator": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "value", "unit", "region_id", "normal"],
            "properties": {
                "name": {"type": "string"}, "value": {"type": "number"},
                "unit": {"enum": ["W", "W/m"]}, "region_id": {"type": "string"},
                "normal": {"$ref": "#/$defs/vector3"},
            },
        },
        "rangeDiagnostic": {
            "type": "object", "additionalProperties": False,
            "required": ["status", "reference_upper_bound", "declared_tolerance", "raw_eta_mode", "clamped"],
            "properties": {
                "status": {"enum": ["NOT_ASSESSED", "WITHIN_DECLARED_UPPER_BOUND", "EXCEEDS_DECLARED_UPPER_BOUND"]},
                "reference_upper_bound": {"const": 1.0},
                "declared_tolerance": {"type": ["number", "null"], "minimum": 0},
                "effective_upper_bound": {"type": "number", "minimum": 1},
                "raw_eta_mode": {"type": "number"}, "clamped": {"const": False},
            },
        },
    },
}


def _error(code: str, message: str) -> None:
    raise ExecutionContractError(code, message, stage="validation")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _error("INVALID_REQUEST", f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        _error("INVALID_REQUEST", f"{label} keys must be strings")
    return value


def _keys(value: Mapping[str, Any], *, required: Sequence[str], allowed: Sequence[str], label: str) -> None:
    missing = sorted(set(required) - set(value))
    unknown = sorted(set(value) - set(allowed))
    if missing:
        _error("INVALID_REQUEST", f"{label} is missing required fields: {missing}")
    if unknown:
        _error("INVALID_REQUEST", f"{label} has unsupported fields: {unknown}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _error("INVALID_REQUEST", f"{label} must be a non-empty string")
    return value


def _number(value: Any, label: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _error("MODE_OVERLAP_DATA_ERROR", f"{label} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        _error("MODE_OVERLAP_DATA_ERROR", f"{label} must be finite")
    if positive and result <= 0.0:
        _error("MODE_OVERLAP_DATA_ERROR", f"{label} must be positive")
    if nonnegative and result < 0.0:
        _error("MODE_OVERLAP_DATA_ERROR", f"{label} must be non-negative")
    return result


def _array(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _error("MODE_OVERLAP_DATA_ERROR", f"{label} must be an array")
    return value


def _vector3(value: Any, label: str) -> tuple[float, float, float]:
    row = _array(value, label)
    if len(row) != 3:
        _error("MODE_OVERLAP_DATA_ERROR", f"{label} must have exactly three components ordered x,y,z")
    return tuple(_number(item, f"{label}[{index}]") for index, item in enumerate(row))  # type: ignore[return-value]


def _coordinates(value: Any, label: str) -> tuple[tuple[float, float, float], ...]:
    rows = _array(value, label)
    if not rows:
        _error("MODE_OVERLAP_DATA_ERROR", f"{label} must not be empty")
    return tuple(_vector3(row, f"{label}[{index}]") for index, row in enumerate(rows))


def _complex_vectors(value: Any, *, label: str, expected_unit: str, count: int) -> tuple[tuple[complex, complex, complex], ...]:
    obj = _mapping(value, label)
    if "imag" not in obj:
        _error("COMPLEX_DATA_ERROR", f"{label}.imag is required; an absent imaginary array is not a real-field declaration")
    _keys(obj, required=("unit", "real", "imag"), allowed=("unit", "real", "imag"), label=label)
    if obj["unit"] != expected_unit:
        _error("UNIT_MISMATCH", f"{label}.unit must be {expected_unit!r}")
    real_rows = _array(obj["real"], f"{label}.real")
    imag_rows = _array(obj["imag"], f"{label}.imag")
    if len(real_rows) != count or len(imag_rows) != count:
        _error("FIELD_ARRAY_SHAPE_MISMATCH", f"{label} sample count must be {count}")
    result: list[tuple[complex, complex, complex]] = []
    for point in range(count):
        real = _vector3(real_rows[point], f"{label}.real[{point}]")
        imag = _vector3(imag_rows[point], f"{label}.imag[{point}]")
        result.append(tuple(complex(real[k], imag[k]) for k in range(3)))
    return tuple(result)


def _complex_scalar(value: Any, label: str) -> dict[str, float]:
    obj = _mapping(value, label)
    if "imag" not in obj:
        _error("COMPLEX_DATA_ERROR", f"{label}.imag is required for a complex-valued mode eigenvalue")
    _keys(obj, required=("real", "imag"), allowed=("real", "imag"), label=label)
    return {"real": _number(obj["real"], f"{label}.real"), "imag": _number(obj["imag"], f"{label}.imag")}


def _source(value: Any, *, label: str) -> dict[str, Any]:
    obj = _mapping(value, label)
    required = ("model_ref", "model_revision", "dataset_id", "solution_id", "frequency_hz", "plane_id")
    _keys(obj, required=required, allowed=required, label=label)
    out = {key: _text(obj[key], f"{label}.{key}") for key in required if key != "frequency_hz"}
    out["frequency_hz"] = _number(obj["frequency_hz"], f"{label}.frequency_hz", positive=True)
    # Keep a stable order in the public JSON-shaped result.
    return {key: out[key] for key in required}


def _phasor_convention(value: Any, *, label: str) -> dict[str, str]:
    obj = _mapping(value, label)
    keys = ("time_dependence", "complex_field_representation")
    _keys(obj, required=keys, allowed=keys, label=label)
    time_dependence = _text(obj["time_dependence"], f"{label}.time_dependence")
    if time_dependence not in _SUPPORTED_TIME_DEPENDENCE:
        _error("UNSUPPORTED_PHASOR_CONVENTION", f"{label}.time_dependence is not a supported explicit convention")
    representation = _text(obj["complex_field_representation"], f"{label}.complex_field_representation")
    if representation != _SUPPORTED_COMPLEX_FIELD_REPRESENTATION:
        _error("UNSUPPORTED_FIELD_REPRESENTATION", f"{label} must declare reconstructed full physical complex phasors, not an un-reconstructed envelope")
    return {"time_dependence": time_dependence, "complex_field_representation": representation}


def _validate_planarity(coordinates: Sequence[Sequence[float]], normal: Sequence[float]) -> float:
    """Require every node to lie in the plane perpendicular to normal.

    The signed distance uses local coordinate differences. Its acceptance
    bound is a fixed ULP policy over the input coordinates and dot-product
    arithmetic; it is not a physical geometry tolerance or surface fit.
    """
    origin = coordinates[0]
    maximum_bound = 0.0
    for index, coordinate in enumerate(coordinates[1:], start=1):
        displacement = tuple(coordinate[axis] - origin[axis] for axis in range(3))
        if any(not math.isfinite(value) for value in displacement):
            _error("NON_FINITE_GEOMETRY", f"plane.coordinates[{index}] - plane.coordinates[0] is non-finite")
        products = tuple(displacement[axis] * normal[axis] for axis in range(3))
        if any(not math.isfinite(value) for value in products):
            _error("NON_FINITE_GEOMETRY", f"plane.coordinates[{index}] projection products are non-finite")
        try:
            signed_distance = math.fsum(products)
            dot_magnitude = math.fsum(abs(value) for value in products)
            input_and_difference_ulps = math.fsum(
                abs(normal[axis]) * (
                    math.ulp(origin[axis]) + math.ulp(coordinate[axis]) + math.ulp(displacement[axis])
                )
                + math.ulp(products[axis])
                for axis in range(3)
            )
            roundoff_basis = math.fsum((input_and_difference_ulps, math.ulp(dot_magnitude)))
            bound = PLANARITY_ULP_SAFETY_FACTOR * roundoff_basis
        except (OverflowError, ValueError):
            _error("NON_FINITE_GEOMETRY", f"plane.coordinates[{index}] planarity bound is non-finite")
        if not all(math.isfinite(value) for value in (signed_distance, dot_magnitude, bound)):
            _error("NON_FINITE_GEOMETRY", f"plane.coordinates[{index}] distance or planarity bound is non-finite")
        maximum_bound = max(maximum_bound, bound)
        if abs(signed_distance) > bound:
            _error(
                "NON_PLANAR_SAMPLING_GEOMETRY",
                f"plane.coordinates[{index}] is {abs(signed_distance):.9g} m off the plane perpendicular to plane.normal; "
                f"coordinate-ULP bound is {bound:.9g} m (policy={PLANARITY_BOUND_POLICY}, "
                f"safety_factor={PLANARITY_ULP_SAFETY_FACTOR})",
            )
    return maximum_bound


def _field(value: Any, *, label: str, coordinates: tuple[tuple[float, float, float], ...], plane_id: str,
           require_mode: bool = False) -> tuple[dict[str, Any], tuple[tuple[complex, complex, complex], ...], tuple[tuple[complex, complex, complex], ...]]:
    obj = _mapping(value, label)
    required = ("field_id", "source", "phasor_convention", "coordinate_unit", "sample_coordinates", "component_order", "electric_field", "magnetic_field")
    allowed = (*required, "mode")
    _keys(obj, required=required, allowed=allowed, label=label)
    if require_mode and "mode" not in obj:
        _error("INVALID_REQUEST", f"{label}.mode is required for the reference mode")
    field_id = _text(obj["field_id"], f"{label}.field_id")
    source = _source(obj["source"], label=f"{label}.source")
    phasor_convention = _phasor_convention(obj["phasor_convention"], label=f"{label}.phasor_convention")
    if obj["coordinate_unit"] != "m":
        _error("UNIT_MISMATCH", f"{label}.coordinate_unit must be 'm'")
    sample_coordinates = _coordinates(obj["sample_coordinates"], f"{label}.sample_coordinates")
    if source["plane_id"] != plane_id:
        _error("PLANE_MISMATCH", f"{label} source plane_id does not match registered plane")
    if sample_coordinates != coordinates:
        _error("COORDINATE_MISMATCH", f"{label} sample coordinates differ from the registered quadrature coordinates")
    order = _array(obj["component_order"], f"{label}.component_order")
    if tuple(order) != _COMPONENT_ORDER:
        _error("FIELD_COMPONENT_ORDER_MISMATCH", f"{label}.component_order must be ['x','y','z']")
    electric = _complex_vectors(obj["electric_field"], label=f"{label}.electric_field", expected_unit="V/m", count=len(coordinates))
    magnetic = _complex_vectors(obj["magnetic_field"], label=f"{label}.magnetic_field", expected_unit="A/m", count=len(coordinates))
    mode_record: dict[str, Any] | None = None
    if "mode" in obj:
        mode = _mapping(obj["mode"], f"{label}.mode")
        _keys(mode, required=("mode_id", "eigenvalue", "eigenvalue_unit"), allowed=("mode_id", "eigenvalue", "eigenvalue_unit"), label=f"{label}.mode")
        mode_record = {
            "mode_id": _text(mode["mode_id"], f"{label}.mode.mode_id"),
            "eigenvalue": _complex_scalar(mode["eigenvalue"], f"{label}.mode.eigenvalue"),
            "eigenvalue_unit": _text(mode["eigenvalue_unit"], f"{label}.mode.eigenvalue_unit"),
        }
    identity: dict[str, Any] = {"field_id": field_id, "source": source, "phasor_convention": phasor_convention}
    if mode_record is not None:
        identity["mode"] = mode_record
    return identity, electric, magnetic


def _dot(a: Sequence[complex], b: Sequence[float]) -> complex:
    return sum((a[index] * b[index] for index in range(3)), 0j)


def _cross(a: Sequence[complex], b: Sequence[complex]) -> tuple[complex, complex, complex]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _conjugate(vector: Sequence[complex]) -> tuple[complex, complex, complex]:
    return tuple(value.conjugate() for value in vector)  # type: ignore[return-value]


def _integrated_power(electric: Sequence[Sequence[complex]], magnetic: Sequence[Sequence[complex]],
                      normal: tuple[float, float, float], weights: Sequence[float],
                      sample_indices: Sequence[int] | None = None) -> float:
    indices = range(len(weights)) if sample_indices is None else sample_indices
    total = 0.0
    for index in indices:
        flux = _dot(_cross(electric[index], _conjugate(magnetic[index])), normal)
        total += 0.5 * flux.real * weights[index]
    if not math.isfinite(total):
        _error("MODE_OVERLAP_DATA_ERROR", "integrated Poynting flux is not finite")
    return total


def _validate_incident_power(value: Any, *, label: str, expected_unit: str,
                             signal_source: Mapping[str, Any], phasor_convention: Mapping[str, Any],
                             power_floor: float) -> dict[str, Any]:
    obj = _mapping(value, label)
    keys = ("reference_id", "power", "unit", "input_plane_id", "source", "phasor_convention")
    _keys(obj, required=keys, allowed=keys, label=label)
    if obj["unit"] != expected_unit:
        _error("UNIT_MISMATCH", f"{label}.unit must be {expected_unit!r} for this integration measure")
    power = _number(obj["power"], f"{label}.power")
    if power <= 0.0:
        _error("NON_POSITIVE_INCIDENT_POWER", f"{label}.power must be positive")
    if power <= power_floor:
        _error("NON_POSITIVE_INCIDENT_POWER", f"{label}.power must exceed declared power_floor")
    input_plane_id = _text(obj["input_plane_id"], f"{label}.input_plane_id")
    source = _source(obj["source"], label=f"{label}.source")
    reference_convention = _phasor_convention(obj["phasor_convention"], label=f"{label}.phasor_convention")
    if reference_convention != phasor_convention:
        _error("PHASOR_CONVENTION_MISMATCH", f"{label}.phasor_convention must exactly match the signal/reference field convention")
    if source["plane_id"] != input_plane_id:
        _error("PLANE_MISMATCH", f"{label}.source.plane_id must equal input_plane_id")
    for key in ("model_ref", "model_revision", "frequency_hz"):
        if source[key] != signal_source[key]:
            _error("SOURCE_MISMATCH", f"{label}.source.{key} must match the signal source")
    return {
        "reference_id": _text(obj["reference_id"], f"{label}.reference_id"),
        "value": power,
        "unit": expected_unit,
        "input_plane_id": input_plane_id,
        "source": source,
        "phasor_convention": reference_convention,
    }


def _public_complex(value: complex, unit: str) -> dict[str, Any]:
    return {"real": float(value.real), "imag": float(value.imag), "unit": unit}


def _public_power(value: float, unit: str, region_id: str, normal: Sequence[float]) -> dict[str, Any]:
    return {"value": float(value), "unit": unit, "region_id": region_id, "normal": [float(item) for item in normal]}


def _ratio(value: float, unit: str, region: Mapping[str, Any], numerator: Mapping[str, Any],
           denominator: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "value": float(value), "unit": "1", "region": dict(region),
        "numerator": dict(numerator), "denominator": dict(denominator),
    }


def compute_mode_overlap(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Compute vector overlap and independently normalized modal/capture powers.

    The input is an internal array contract. Its identities and provenance are
    structurally checked and echoed, not authenticated as native observations.
    Missing imaginary arrays fail closed, including for numerically real data;
    callers represent a confirmed zero imaginary part with explicit zero arrays.
    """
    obj = _mapping(definition, "definition")
    required = ("schema_version", "profile", "phasor_convention", "plane", "signal", "reference_mode", "power_floor", "incident_reference_power")
    allowed = (*required, "eta_mode_upper_tolerance", "capture")
    _keys(obj, required=required, allowed=allowed, label="definition")
    if obj["schema_version"] != DEFINITION_SCHEMA_VERSION:
        _error("SCHEMA_VERSION_UNSUPPORTED", f"unsupported mode-overlap definition schema {obj['schema_version']!r}")
    if obj["profile"] != APPLICABILITY_PROFILE:
        _error("APPLICABILITY_PROFILE_UNSUPPORTED", f"unsupported mode-overlap profile {obj['profile']!r}")
    declared_convention = _phasor_convention(obj["phasor_convention"], label="definition.phasor_convention")

    plane = _mapping(obj["plane"], "plane")
    plane_keys = ("plane_id", "geometry_dimension", "integration_measure", "coordinate_unit", "coordinates", "quadrature_weight_unit", "quadrature_weights", "normal")
    _keys(plane, required=plane_keys, allowed=plane_keys, label="plane")
    plane_id = _text(plane["plane_id"], "plane.plane_id")
    dimension = plane["geometry_dimension"]
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension not in (2, 3):
        _error("INVALID_REQUEST", "plane.geometry_dimension must be 2 or 3")
    measure, weight_unit, power_unit = _GEOMETRY_UNITS[dimension]
    if plane["integration_measure"] != measure or plane["quadrature_weight_unit"] != weight_unit:
        _error("UNIT_MISMATCH", f"geometry_dimension={dimension} requires integration_measure={measure!r} and quadrature_weight_unit={weight_unit!r}")
    if plane["coordinate_unit"] != "m":
        _error("UNIT_MISMATCH", "plane.coordinate_unit must be 'm'; convert coordinates before calling this core")
    coordinates = _coordinates(plane["coordinates"], "plane.coordinates")
    weights_raw = _array(plane["quadrature_weights"], "plane.quadrature_weights")
    if len(weights_raw) != len(coordinates):
        _error("FIELD_ARRAY_SHAPE_MISMATCH", "quadrature_weights count must match coordinates")
    weights = tuple(_number(item, f"plane.quadrature_weights[{index}]", positive=True) for index, item in enumerate(weights_raw))
    normal = _vector3(plane["normal"], "plane.normal")
    normal_norm = math.sqrt(sum(item * item for item in normal))
    if not math.isclose(normal_norm, 1.0, rel_tol=0.0, abs_tol=NORMAL_UNIT_TOLERANCE):
        _error("INVALID_NORMAL", f"plane.normal must be unit length within {NORMAL_UNIT_TOLERANCE:g}; it is never renormalized")
    maximum_planarity_bound = _validate_planarity(coordinates, normal)

    floor = _mapping(obj["power_floor"], "power_floor")
    _keys(floor, required=("value", "unit"), allowed=("value", "unit"), label="power_floor")
    if floor["unit"] != power_unit:
        _error("UNIT_MISMATCH", f"power_floor.unit must be {power_unit!r}")
    power_floor = _number(floor["value"], "power_floor.value", positive=True)

    signal_identity, e_signal, h_signal = _field(obj["signal"], label="signal", coordinates=coordinates, plane_id=plane_id)
    mode_identity, e_mode, h_mode = _field(obj["reference_mode"], label="reference_mode", coordinates=coordinates, plane_id=plane_id, require_mode=True)
    if signal_identity["phasor_convention"] != declared_convention or mode_identity["phasor_convention"] != declared_convention:
        _error("PHASOR_CONVENTION_MISMATCH", "definition, signal, and reference_mode phasor conventions must match exactly")
    signal_source = signal_identity["source"]
    mode_source = mode_identity["source"]
    for key in ("model_ref", "model_revision", "frequency_hz"):
        if signal_source[key] != mode_source[key]:
            _error("SOURCE_MISMATCH", f"signal and reference_mode source.{key} must match")

    signal_power = _integrated_power(e_signal, h_signal, normal, weights)
    mode_power = _integrated_power(e_mode, h_mode, normal, weights)
    if signal_power <= power_floor:
        _error("NON_POSITIVE_FORWARD_POWER", "signal forward power must exceed declared power_floor")
    if mode_power <= power_floor:
        _error("NON_POSITIVE_FORWARD_POWER", "reference-mode forward power must exceed declared power_floor")

    numerator = 0j
    for point in range(len(coordinates)):
        first = _cross(e_signal[point], _conjugate(h_mode[point]))
        second = _cross(_conjugate(e_mode[point]), h_signal[point])
        combined = tuple(first[index] + second[index] for index in range(3))
        numerator += _dot(combined, normal) * weights[point]
    if not math.isfinite(numerator.real) or not math.isfinite(numerator.imag):
        _error("MODE_OVERLAP_DATA_ERROR", "unnormalized overlap numerator is not finite")
    amplitude_denominator = 4.0 * math.sqrt(signal_power) * math.sqrt(mode_power)
    if not math.isfinite(amplitude_denominator) or amplitude_denominator <= 0.0:
        _error("MODE_OVERLAP_DATA_ERROR", "overlap normalization denominator is not finite and positive")
    amplitude = numerator / amplitude_denominator
    normalized_overlap = abs(amplitude) ** 2
    projected_power = normalized_overlap * signal_power
    if not all(math.isfinite(value) for value in (amplitude.real, amplitude.imag, normalized_overlap, projected_power)):
        _error("MODE_OVERLAP_DATA_ERROR", "normalized overlap result is not finite")

    incident = _validate_incident_power(obj["incident_reference_power"], label="incident_reference_power", expected_unit=power_unit,
                                        signal_source=signal_source, phasor_convention=declared_convention,
                                        power_floor=power_floor)
    eta_mode = projected_power / incident["value"]
    if not math.isfinite(eta_mode):
        _error("MODE_OVERLAP_DATA_ERROR", "eta_mode is not finite")
    tol: float | None = None
    if "eta_mode_upper_tolerance" in obj:
        tol = _number(obj["eta_mode_upper_tolerance"], "eta_mode_upper_tolerance", nonnegative=True)
        range_status = "WITHIN_DECLARED_UPPER_BOUND" if eta_mode <= 1.0 + tol else "EXCEEDS_DECLARED_UPPER_BOUND"
        range_diagnostic: dict[str, Any] = {
            "status": range_status, "reference_upper_bound": 1.0,
            "declared_tolerance": tol, "effective_upper_bound": 1.0 + tol,
            "raw_eta_mode": eta_mode, "clamped": False,
        }
    else:
        range_diagnostic = {"status": "NOT_ASSESSED", "reference_upper_bound": 1.0,
                            "declared_tolerance": None, "raw_eta_mode": eta_mode, "clamped": False}

    identities: dict[str, Any] = {"signal": signal_identity, "reference_mode": mode_identity}
    surface = {
        "plane_id": plane_id, "geometry_dimension": dimension, "integration_measure": measure,
        "coordinate_unit": "m", "quadrature_weight_unit": weight_unit,
        "normal": [float(item) for item in normal], "sample_count": len(coordinates),
        "planarity_roundoff": {
            "policy_id": PLANARITY_BOUND_POLICY,
            "safety_factor": PLANARITY_ULP_SAFETY_FACTOR,
            "maximum_sample_bound_m": maximum_planarity_bound,
        },
    }
    signal_power_record = _public_power(signal_power, power_unit, plane_id, normal)
    mode_power_record = _public_power(mode_power, power_unit, plane_id, normal)
    projected_record = _public_power(projected_power, power_unit, plane_id, normal)
    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "operation_id": "result.mode_overlap",
        "algorithm_id": ALGORITHM_ID,
        "applicability_profile": APPLICABILITY_PROFILE,
        "phasor_convention": declared_convention,
        "evidence_status": {
            "scope": "SOFTWARE_ONLY", "native_status": "NOT_RUN",
            "input_boundary": "CALLER_SUPPLIED_ARRAYS_NOT_NATIVE_EVIDENCE",
        },
        "identities": identities,
        "surface": surface,
        "normalization_power_floor": {"value": power_floor, "unit": power_unit},
        "signal_power": signal_power_record,
        "reference_mode_power": mode_power_record,
        "unnormalized_overlap_numerator": _public_complex(numerator, power_unit),
        "overlap_amplitude": _public_complex(amplitude, "1"),
        "normalized_overlap": float(normalized_overlap),
        "projected_mode_power": projected_record,
        "incident_reference_power": incident,
        "eta_mode": _ratio(
            eta_mode, "1", {"plane_id": plane_id, "measure": measure},
            {"name": "projected_mode_power", **projected_record}, incident,
        ),
        "eta_mode_range_diagnostic": range_diagnostic,
    }
    if "capture" in obj:
        capture = _mapping(obj["capture"], "capture")
        _keys(capture, required=("aperture_id", "plane_id", "sample_indices", "incident_reference_power"),
              allowed=("aperture_id", "plane_id", "sample_indices", "incident_reference_power"), label="capture")
        aperture_id = _text(capture["aperture_id"], "capture.aperture_id")
        if capture["plane_id"] != plane_id:
            _error("PLANE_MISMATCH", "capture.plane_id must match the sampled plane")
        indices_raw = _array(capture["sample_indices"], "capture.sample_indices")
        if not indices_raw:
            _error("INVALID_REQUEST", "capture.sample_indices must not be empty")
        indices: list[int] = []
        for pos, value in enumerate(indices_raw):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value >= len(weights):
                _error("INVALID_REQUEST", f"capture.sample_indices[{pos}] must be an in-range zero-based integer")
            indices.append(value)
        if len(set(indices)) != len(indices):
            _error("INVALID_REQUEST", "capture.sample_indices must be unique")
        capture_power = _integrated_power(e_signal, h_signal, normal, weights, indices)
        capture_incident = _validate_incident_power(capture["incident_reference_power"], label="capture.incident_reference_power",
                                                    expected_unit=power_unit, signal_source=signal_source,
                                                    phasor_convention=declared_convention, power_floor=power_floor)
        eta_capture = capture_power / capture_incident["value"]
        if not math.isfinite(eta_capture):
            _error("MODE_OVERLAP_DATA_ERROR", "eta_capture is not finite")
        result["eta_capture"] = _ratio(
            eta_capture, "1", {"aperture_id": aperture_id, "plane_id": plane_id, "sample_indices": indices},
            {"name": "signed_signal_poynting_flux", **_public_power(capture_power, power_unit, aperture_id, normal)},
            capture_incident,
        )
    return result


__all__ = [
    "ALGORITHM_ID", "APPLICABILITY_PROFILE", "DEFINITION_SCHEMA", "DEFINITION_SCHEMA_ID",
    "DEFINITION_SCHEMA_VERSION", "NORMAL_UNIT_TOLERANCE", "PLANARITY_BOUND_POLICY", "PLANARITY_ULP_SAFETY_FACTOR",
    "RESULT_SCHEMA", "RESULT_SCHEMA_ID",
    "RESULT_SCHEMA_VERSION", "compute_mode_overlap",
]
