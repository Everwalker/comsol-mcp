"""Dedicated native two-mode-subspace overlap adapter (W23 v2).

The versioned v2 request is intentionally separate from the scalar
``result.mode_overlap`` contract.  It binds two independently solved Numeric
Port modes, a signal, and an incident reference, then asks COMSOL IntSurface
features for all four Gram entries, both coupling entries, and both reference
powers.  It never accepts caller-supplied fields or quadrature arrays.

This module is not registered in the public operation table yet.  Its focused
tests use synthetic Worker/readback substitutions and therefore establish
software behavior only; native status remains NOT_RUN until the handler is
integrated and invoked through an approved managed route.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ._g2_contract import ExecutionContractError
from ._w23_basis_v2_contract import NATIVE_DEFINITION_SCHEMA, validate_basis_request


OPERATION_ID = "result.mode_overlap_basis_v2"
DEFINITION_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap_basis_v2:definition:1.0.0"
RESULT_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap_basis_v2:native-result:1.0.0"
NORMAL_READBACK_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap_basis_v2:surface-normal-readback:1.0.0"
DEFINITION_SCHEMA_VERSION = "1.0.0"
_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_UNIT = re.compile(r"^[A-Za-z0-9_*/^.-]+$")
_AXES = ("x", "y", "z")
_FIELD_VARIABLES = {
    "signal": {
        "E": {axis: f"ewfd.E{axis}" for axis in _AXES},
        "H": {axis: f"ewfd.H{axis}" for axis in _AXES},
    },
    "mode": {
        "E": {axis: f"ewfd.Emode{axis}_2" for axis in _AXES},
        "H": {axis: f"ewfd.Hmode{axis}_2" for axis in _AXES},
    },
    "incident": {
        "E": {axis: f"ewfd.Emode{axis}_1" for axis in _AXES},
        "H": {axis: f"ewfd.Hmode{axis}_1" for axis in _AXES},
    },
}
_FREQUENCY_SCALE = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}
_INTEGRAL_TOLERANCES = {
    "policy_id": "w23.two_mode_native_surface_integrals.v1",
    "frequency_relative_tolerance": 1e-12,
    "surface_area_relative_tolerance": 1e-10,
    "surface_area_absolute_tolerance_m2": 1e-30,
    "entity_dimension_3d_boundary": 2,
    "power_unit": "W",
    "feature_type": "IntSurface",
}
_NORMAL_READBACK_POLICY = {
    "schema_id": NORMAL_READBACK_SCHEMA_ID,
    "schema_version": "1.0.0",
    "component_frame": "COMSOL_GLOBAL_XYZ_OUTWARD_RELATIVE_TO_MESHED_DOMAINS",
    "area_unit": "m^2",
    "second_moment_relative_tolerance": 1e-8,
    "normal_variance_absolute_tolerance": 1e-10,
    "mean_normal_unit_tolerance": 1e-10,
}
_OPERATION_ENVELOPE_FIELDS = frozenset({
    "project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id",
})

_COMPLEX_INTEGRAL_SCHEMA = {
    "type": "object",
    "required": ["real", "imag", "unit", "expression", "dataset_id", "solution_id",
                 "feature_type", "cleanup", "selection_measure_m2", "selection_measure_source", "is_complex"],
    "properties": {
        "real": {"type": "number"}, "imag": {"type": "number"}, "unit": {"const": "W"},
        "expression": {"type": "string"}, "dataset_id": {"type": "string", "minLength": 1},
        "solution_id": {"type": "string", "minLength": 1}, "feature_type": {"const": "IntSurface"},
        "cleanup": {"type": "object", "required": ["created", "removed", "cleanup_failed", "type_id", "tag"]},
        "selection_measure_m2": {"type": "number", "exclusiveMinimum": 0},
        "selection_measure_source": {"type": "string", "minLength": 1}, "is_complex": {"type": "boolean"},
    },
    "additionalProperties": False,
}
_NORMAL_INTEGRAL_SCHEMA = {
    "type": "object",
    "required": ["real", "imag", "unit", "expression", "dataset_id", "solution_id",
                 "feature_type", "cleanup", "selection_measure_m2", "selection_measure_source", "is_complex"],
    "properties": {
        "real": {"type": "number"}, "imag": {"const": 0.0},
        "unit": {"const": "m^2"}, "expression": {"type": "string"},
        "dataset_id": {"type": "string", "minLength": 1},
        "solution_id": {"type": "string", "minLength": 1},
        "feature_type": {"const": "IntSurface"},
        "cleanup": {"type": "object", "required": ["created", "removed", "cleanup_failed", "type_id", "tag"]},
        "selection_measure_m2": {"type": "number", "exclusiveMinimum": 0},
        "selection_measure_source": {"type": "string", "minLength": 1},
        "is_complex": {"const": False},
    },
    "additionalProperties": False,
}
_NORMAL_READBACK_SCHEMA = {
    "$id": NORMAL_READBACK_SCHEMA_ID,
    "type": "object",
    "required": ["schema_id", "schema_version", "status", "source_binding", "selection",
                 "native_integrals", "observed_outward_normal_xyz_area_mean", "observed_outward_normal_norm",
                 "observed_normal_variance", "native_component_frame", "request_frame_id",
                 "area_unit", "second_moment_relative_tolerance", "normal_variance_absolute_tolerance",
                 "mean_normal_unit_tolerance",
                 "request_frame_mapping_status", "requested_forward_axis_xyz", "requested_integration_sign",
                 "signed_observed_outward_normal_xyz", "outward_component_dot_requested_axis_if_frames_coincide",
                 "signed_outward_component_dot_requested_axis_if_frames_coincide",
                 "port_orientation_relation_status"],
    "properties": {
        "schema_id": {"const": NORMAL_READBACK_SCHEMA_ID}, "schema_version": {"const": "1.0.0"},
        "status": {"const": "NATIVE_OUTWARD_NORMAL_READBACK_PASSED_FRAME_LINK_UNVERIFIED"},
        "source_binding": {"type": "object", "required": ["dataset_id", "solution_id", "outer_index", "inner_index", "solnum"]},
        "selection": {"type": "object", "required": ["component", "geometry", "tag", "entity_dimension", "entity_ids", "area_m2"]},
        "native_integrals": {
            "type": "object", "required": ["x", "y", "z", "squared_norm"],
            "properties": {key: _NORMAL_INTEGRAL_SCHEMA for key in ("x", "y", "z", "squared_norm")},
            "additionalProperties": False,
        },
        "observed_outward_normal_xyz_area_mean": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
        "observed_outward_normal_norm": {"type": "number", "minimum": 0},
        "observed_normal_variance": {"type": "number"},
        "native_component_frame": {"const": _NORMAL_READBACK_POLICY["component_frame"]},
        "area_unit": {"const": _NORMAL_READBACK_POLICY["area_unit"]},
        "second_moment_relative_tolerance": {"const": _NORMAL_READBACK_POLICY["second_moment_relative_tolerance"]},
        "normal_variance_absolute_tolerance": {"const": _NORMAL_READBACK_POLICY["normal_variance_absolute_tolerance"]},
        "mean_normal_unit_tolerance": {"const": _NORMAL_READBACK_POLICY["mean_normal_unit_tolerance"]},
        "request_frame_id": {"type": "string", "minLength": 1},
        "request_frame_mapping_status": {"const": "UNVERIFIED"},
        "requested_forward_axis_xyz": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
        "requested_integration_sign": {"enum": [-1, 1]},
        "signed_observed_outward_normal_xyz": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
        "outward_component_dot_requested_axis_if_frames_coincide": {"type": "number"},
        "signed_outward_component_dot_requested_axis_if_frames_coincide": {"type": "number"},
        "port_orientation_relation_status": {"const": "UNVERIFIED"},
    },
    "additionalProperties": False,
}

NATIVE_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": RESULT_SCHEMA_ID,
    "title": "result.mode_overlap_basis_v2 native integral result v1.0.0",
    "type": "object",
    "required": ["schema_id", "schema_version", "operation_id", "status", "result_status",
                 "algorithm_id", "basis_request_id", "definition_sha256", "identity",
                 "managed_execution_binding", "source_readbacks", "surface_readbacks",
                 "native_integrals", "term_bindings", "policy", "origin",
                 "study_or_solver_invoked", "caller_field_arrays_accepted", "native_result",
                 "quadrature_comparison", "projection_acceptance", "production_route_status"],
    "properties": {
        "schema_id": {"const": RESULT_SCHEMA_ID}, "schema_version": {"const": "1.0.0"},
        "operation_id": {"const": OPERATION_ID}, "status": {"const": "SUCCEEDED"},
        "result_status": {"const": "COMPUTED_NATIVE_TWO_MODE_INTEGRALS"},
        "algorithm_id": {"const": "reciprocal_lossless_forward_two_mode_basis_projection_v2"},
        "basis_request_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "definition_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "identity": {"type": "object", "required": ["basis_id", "case", "project_id", "model_ref", "model_tag",
            "model_revision", "geometry_revision", "frequency_hz", "coordinate_frame", "mode_ids", "mode_indices"]},
        "managed_execution_binding": {"type": "object", "required": ["project_id", "model_ref", "model_revision", "source"]},
        "source_readbacks": {"type": "object", "required": ["signal", "mode_0", "mode_1", "incident"]},
        "surface_readbacks": {
            "type": "object", "required": ["output", "input"],
            "properties": {
                key: {
                    "type": "object",
                    "required": ["selection", "entity_dimension", "entity_ids", "aperture_id",
                                 "expected_area_m2", "measured_area_m2"],
                    "properties": {"normal_provenance": _NORMAL_READBACK_SCHEMA},
                }
                for key in ("output", "input")
            },
        },
        "native_integrals": {
            "type": "object", "required": ["gram_matrix", "coupling_vector", "signal_power",
                "incident_reference_power", "terms"],
            "properties": {
                "gram_matrix": {"type": "array", "minItems": 2, "maxItems": 2,
                    "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": _COMPLEX_INTEGRAL_SCHEMA}},
                "coupling_vector": {"type": "array", "minItems": 2, "maxItems": 2, "items": _COMPLEX_INTEGRAL_SCHEMA},
                "signal_power": _COMPLEX_INTEGRAL_SCHEMA,
                "incident_reference_power": _COMPLEX_INTEGRAL_SCHEMA,
                "terms": {"type": "object", "required": ["G00", "G01", "G10", "G11", "b0", "b1", "P_signal", "P_incident"],
                    "additionalProperties": _COMPLEX_INTEGRAL_SCHEMA},
            },
            "additionalProperties": False,
        },
        "term_bindings": {"type": "object", "required": ["G00", "G01", "G10", "G11", "b0", "b1", "P_signal", "P_incident"],
            "additionalProperties": {"type": "object"}},
        "policy": {"const": _INTEGRAL_TOLERANCES},
        "origin": {"const": "COMSOL_NATIVE_INT_SURFACE"},
        "study_or_solver_invoked": {"const": False},
        "caller_field_arrays_accepted": {"const": False},
        "native_result": {"const": "COMSOL_NATIVE_RAW"},
        "quadrature_comparison": {"const": "NOT_RUN"},
        "projection_acceptance": {"const": "NOT_RUN"},
        "production_route_status": {"const": "ROUTE_REGISTERED_NATIVE_INTEGRATION_ONLY"},
        "isolation_proof": {"type": "object"},
        "domain_state": {"enum": ["succeeded", "partial", "failed", "unknown"]},
        "verification_status": {"enum": ["PASSED", "FAILED", "NOT_RUN", "NOT_APPLICABLE", "UNKNOWN"]},
        "execution_state_unknown": {"type": "boolean"}, "partial_change": {"type": "boolean"},
        "cleanup_failed": {"type": "boolean"},
        "dispatch_stage": {"enum": ["validation", "post_dispatch"]},
        "domain_outcome": {"type": "object"}, "effect": {"const": "evaluate"},
        "operation": {"const": OPERATION_ID},
    },
    "additionalProperties": False,
    "$comment": "Raw native integrals only; no independent quadrature, basis projection acceptance, or optical-science conclusion is implied.",
}


def _fail(code: str, message: str, *, details: Mapping[str, Any] | None = None,
          stage: str = "validation") -> None:
    raise ExecutionContractError(code, message, details=details, stage=stage)


def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail("INVALID_REQUEST", f"{label} must be an object with string keys")
    return value


def _tag(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _TAG.fullmatch(value):
        _fail("INVALID_REQUEST", f"{label} must be a valid COMSOL tag")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail("INVALID_REQUEST", f"{label} must be finite numeric data")
    return float(value)


def build_definition(
    basis_request: Mapping[str, Any], mode_axis_parameters: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    """Create a versioned, digest-bound definition for the registered handler."""
    definition = {
        "schema_id": DEFINITION_SCHEMA_ID,
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "basis_request": dict(basis_request),
        "mode_axis_parameters": [dict(row) for row in mode_axis_parameters],
    }
    checked = _validate_definition({**definition, "definition_sha256": _sha256(definition)})
    return checked


def _validate_definition(value: Any) -> dict[str, Any]:
    definition = _mapping(value, "definition")
    required = {"schema_id", "schema_version", "basis_request", "mode_axis_parameters", "definition_sha256"}
    if set(definition) != required:
        _fail("INVALID_REQUEST", "v2 definition must contain exactly its schema, canonical basis request, mode-axis map, and digest")
    if (definition.get("schema_id") != DEFINITION_SCHEMA_ID
            or definition.get("schema_version") != DEFINITION_SCHEMA_VERSION):
        _fail("SCHEMA_VERSION_UNSUPPORTED", "unsupported native two-mode definition schema")
    basis_request = _mapping(definition.get("basis_request"), "definition.basis_request")
    try:
        validate_basis_request(basis_request)
    except ExecutionContractError:
        raise
    except Exception as exc:
        _fail("INVALID_REQUEST", f"canonical W23 v2 basis request is invalid: {exc}")
    modes = basis_request.get("basis_modes")
    rows = definition.get("mode_axis_parameters")
    if not isinstance(modes, list) or len(modes) != 2 or not isinstance(rows, list) or len(rows) != 2:
        _fail("INVALID_REQUEST", "v2 definition requires exactly two mode-axis bindings")
    normalized_modes = []
    for expected, row in zip(modes, rows):
        if not isinstance(row, Mapping) or set(row) != {"mode_id", "parameter"}:
            _fail("INVALID_REQUEST", "each mode-axis binding must contain exactly mode_id and parameter")
        if row.get("mode_id") != expected.get("mode_id"):
            _fail("INVALID_REQUEST", "mode-axis binding order/identity differs from the canonical basis modes")
        normalized_modes.append({"mode_id": str(row["mode_id"]),
                                 "parameter": _tag(row["parameter"], "mode-axis parameter")})
    identity = {
        "schema_id": DEFINITION_SCHEMA_ID,
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "basis_request": dict(basis_request),
        "mode_axis_parameters": normalized_modes,
    }
    expected_digest = _sha256(identity)
    if definition.get("definition_sha256") != expected_digest:
        _fail("IDENTITY_DIGEST_MISMATCH", "native v2 definition digest does not match its exact basis and mode-axis bindings")
    return {**identity, "definition_sha256": expected_digest}


def validate_request_shape(arguments: Mapping[str, Any]) -> None:
    args = _mapping(arguments, "arguments")
    _validate_operation_arguments(args)
    _validate_definition(args["definition"])


def _validate_operation_arguments(args: Mapping[str, Any]) -> None:
    if "definition" not in args or set(args) - ({"definition"} | _OPERATION_ENVELOPE_FIELDS):
        _fail("INVALID_REQUEST", "result.mode_overlap_basis_v2 accepts definition and managed execution identity only")
    if "project_id" in args and (not isinstance(args["project_id"], str) or not args["project_id"]):
        _fail("INVALID_REQUEST", "operation project_id must be a nonempty managed identity")
    if "session_id" in args and (not isinstance(args["session_id"], str) or not args["session_id"]):
        _fail("INVALID_REQUEST", "operation session_id must be a nonempty managed identity")
    if "model_ref" in args:
        ref = args["model_ref"]
        required_ref = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
        if (not isinstance(ref, Mapping) or set(ref) != required_ref
                or type(ref.get("schema_version")) is not int or ref.get("schema_version") != 1
                or any(not isinstance(ref.get(key), str) or not ref[key]
                       for key in ("session_id", "server_instance_id", "model_tag"))
                or type(ref.get("generation")) is not int or ref["generation"] < 1):
            _fail("MODEL_IDENTITY_MISMATCH", "operation model_ref must be a complete managed ModelRef")
    if "expected_revision" in args and (type(args["expected_revision"]) is not int or args["expected_revision"] < 0):
        _fail("INVALID_REQUEST", "operation expected_revision must be a nonnegative integer")
    for key in ("idempotency_key", "request_id"):
        if key in args and (not isinstance(args[key], str) or not args[key]):
            _fail("INVALID_REQUEST", f"operation {key} must be a nonempty string")


def _source_roles(request: Mapping[str, Any]) -> dict[str, tuple[str, Mapping[str, Any]]]:
    modes = request["basis_modes"]
    return {
        "signal": ("signal", request["signal_source"]),
        "mode_0": ("mode", modes[0]["source"]),
        "mode_1": ("mode", modes[1]["source"]),
        "incident": ("incident", request["incident_source"]),
    }


def _solution_selector(role: str, source_kind: str, source: Mapping[str, Any],
                       native_axes: Mapping[str, Any], mode_axis: str | None) -> list[str]:
    names = native_axes.get("parameter_names")
    by_name = native_axes.get("parameters_by_pair")
    if not isinstance(names, list) or not names or not isinstance(by_name, Mapping) or set(names) != set(by_name):
        _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{role} has incomplete native selected-pair parameter metadata")
    lowered = [str(name).lower() for name in names]
    if len(set(lowered)) != len(lowered):
        _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{role} parameter axes contain duplicate names")
    if source_kind == "mode":
        if mode_axis is None or mode_axis.lower() not in lowered:
            _fail("MODE_IDENTITY_MISMATCH", f"{role} exact mode-axis parameter is absent from native SolutionInfo")
    selectors: list[str] = []
    mode_axis_seen = False
    for name in names:
        name = _tag(name, f"{role} native parameter axis")
        pair = by_name[name]
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{role} parameter {name!r} lacks its native value/unit pair")
        value = _finite(pair[0], f"{role}.{name} native parameter")
        unit = pair[1]
        if unit is None:
            unit = ""
        if not isinstance(unit, str) or (unit and not _UNIT.fullmatch(unit)):
            _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{role} parameter {name!r} has an unsupported unit")
        if source_kind == "mode" and name.lower() == mode_axis.lower():
            # Numeric Port mode_index is provenance; setind consumes the exact
            # native inner solution index from this readback.
            selectors.append(f"setind({mode_axis},{source['inner_index']})")
            mode_axis_seen = True
        else:
            literal = format(value, ".17g") + (f"[{unit}]" if unit else "")
            selectors.append(f"setval({name},{literal})")
    if source_kind == "mode" and not mode_axis_seen:
        _fail("MODE_IDENTITY_MISMATCH", f"{role} exact mode-axis selector could not be constructed")
    if not selectors:
        _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{role} has no exact selector arguments for withsol")
    return selectors


def _withsol(role: Mapping[str, Any], variable: str) -> str:
    source = role["source"]
    solution = _tag(source.get("solution_id"), f"{role['role']} solution tag")
    return f"withsol('{solution}',{variable},{','.join(role['withsol_selectors'])})"


def _field_map(role: Mapping[str, Any], *, withsol: bool) -> dict[str, dict[str, str]]:
    raw = _FIELD_VARIABLES[role["source_kind"]]
    if not withsol:
        return {kind: dict(values) for kind, values in raw.items()}
    return {kind: {axis: _withsol(role, expression) for axis, expression in values.items()}
            for kind, values in raw.items()}


def _bilinear_expression(a: Mapping[str, Any], b: Mapping[str, Any], *, normal_sign: int,
                         withsol_b: bool) -> str:
    from ._w23_results import _expression_cross_dot

    fields_a = _field_map(a, withsol=False)
    fields_b = _field_map(b, withsol=withsol_b)
    first = _expression_cross_dot(fields_b["E"], fields_a["H"], dimension=3,
                                  conjugate_magnetic=True)
    second = _expression_cross_dot(fields_a["E"], fields_b["H"], dimension=3,
                                   conjugate_magnetic=False, conjugate_electric=True)
    # This is B(a,b), not the v1 route's reciprocal_overlap_numerator=4*B.
    return f"0.25*({normal_sign})*(({first})+({second}))"


def _power_expression(role: Mapping[str, Any], *, normal_sign: int) -> str:
    from ._w23_results import _expression_cross_dot

    fields = _field_map(role, withsol=False)
    flux = _expression_cross_dot(fields["E"], fields["H"], dimension=3,
                                 conjugate_magnetic=True)
    return f"0.5*real(({normal_sign})*({flux}))"


def _one_real(value: Any, label: str) -> float:
    if isinstance(value, Mapping):
        if not {"real", "imag"}.issubset(value):
            _fail("NATIVE_READBACK_UNVERIFIED", f"{label} complex scalar is malformed", stage="post_dispatch")
        real = _finite(value["real"], f"{label}.real")
        imag = _finite(value["imag"], f"{label}.imag")
        if imag != 0.0:
            _fail("NATIVE_READBACK_UNVERIFIED", f"{label} must be a real geometric measure", stage="post_dispatch")
        return real
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) != 1:
            _fail("NATIVE_READBACK_UNVERIFIED", f"{label} must resolve to one exact selected scalar", stage="post_dispatch")
        return _one_real(value[0], label)
    return _finite(value, label)


def _complex_record(record: Mapping[str, Any], *, label: str, expected_unit: str = "W",
                    require_complex: bool = False) -> dict[str, Any]:
    if record.get("unit") != expected_unit:
        _fail("UNIT_MISMATCH", f"{label} native integral unit {record.get('unit')!r} differs from {expected_unit!r}",
              details={"native_record": dict(record)}, stage="post_dispatch")
    value = record.get("value")
    if not isinstance(value, complex) or not math.isfinite(value.real) or not math.isfinite(value.imag):
        _fail("NATIVE_RESULT_INVALID", f"{label} native integral is not a finite complex scalar",
              details={"native_record": dict(record)}, stage="post_dispatch")
    if type(record.get("is_complex")) is not bool:
        _fail("COMPLEX_DATA_ERROR", f"{label} omitted the native complex-status readback",
              details={"native_record": dict(record)}, stage="post_dispatch")
    if require_complex and record["is_complex"] is not True:
        _fail("COMPLEX_DATA_ERROR", f"{label} reciprocal integral requires a native complex readback",
              details={"native_record": dict(record)}, stage="post_dispatch")
    if record["is_complex"] is False and value.imag != 0.0:
        _fail("COMPLEX_DATA_ERROR", f"{label} carried an imaginary value without a native complex flag",
              details={"native_record": dict(record)}, stage="post_dispatch")
    cleanup = record.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("type_id") != "IntSurface" or cleanup.get("removed") is not True or cleanup.get("cleanup_failed") is True:
        _fail("EXECUTION_STATE_UNKNOWN", f"{label} temporary IntSurface cleanup was not verified",
              details={"native_record": dict(record)}, stage="post_dispatch")
    return {
        "real": value.real, "imag": value.imag, "unit": expected_unit,
        "expression": record.get("expression"), "dataset_id": record.get("dataset"),
        "solution_id": record.get("solution"), "feature_type": cleanup.get("type_id"),
        "cleanup": dict(cleanup), "selection_measure_m2": record.get("selection_measure"),
        "selection_measure_source": record.get("selection_measure_source"),
        "is_complex": record.get("is_complex"),
    }


def _check_selection_measure(record: Mapping[str, Any], surface: Mapping[str, Any], label: str) -> float:
    source = record.get("selection_measure_source")
    if not isinstance(source, str) or "engine integral of 1" not in source:
        _fail("NATIVE_READBACK_UNVERIFIED", f"{label} area lacks a native integral-of-one readback",
              details={"record": dict(record)}, stage="post_dispatch")
    measured = _one_real(record.get("selection_measure"), f"{label} native selected area")
    expected = _finite(surface.get("surface_area_m2"), f"{label} expected area")
    if measured <= 0.0 or expected <= 0.0 or not math.isclose(
        measured, expected,
        rel_tol=_INTEGRAL_TOLERANCES["surface_area_relative_tolerance"],
        abs_tol=_INTEGRAL_TOLERANCES["surface_area_absolute_tolerance_m2"],
    ):
        _fail("SELECTION_MEASURE_MISMATCH", f"{label} native selection area differs from the frozen port aperture",
              details={"measured_area_m2": measured, "expected_area_m2": expected,
                       "selection_measure_source": source}, stage="post_dispatch")
    return measured


def _real_surface_integral(record: Mapping[str, Any], *, expression: str, source: Mapping[str, Any],
                           surface: Mapping[str, Any], label: str) -> tuple[float, dict[str, Any]]:
    """Validate one native area integral without mixing it into the W-valued term schema."""
    if record.get("unit") != _NORMAL_READBACK_POLICY["area_unit"]:
        _fail("UNIT_MISMATCH", f"{label} native normal integral unit {record.get('unit')!r} is not m^2",
              details={"record": dict(record)}, stage="post_dispatch")
    if record.get("is_complex") is not False:
        _fail("COMPLEX_DATA_ERROR", f"{label} geometric normal integral must be natively real",
              details={"record": dict(record)}, stage="post_dispatch")
    value = record.get("value")
    if (not isinstance(value, complex) or not math.isfinite(value.real)
            or not math.isfinite(value.imag) or value.imag != 0.0):
        _fail("NATIVE_RESULT_INVALID", f"{label} native normal integral is not a finite real scalar",
              details={"record": dict(record)}, stage="post_dispatch")
    cleanup = record.get("cleanup")
    if (not isinstance(cleanup, Mapping) or cleanup.get("type_id") != "IntSurface"
            or cleanup.get("removed") is not True or cleanup.get("cleanup_failed") is True):
        _fail("EXECUTION_STATE_UNKNOWN", f"{label} normal IntSurface cleanup was not verified",
              details={"record": dict(record)}, stage="post_dispatch")
    if (record.get("dataset") != source.get("dataset_id")
            or record.get("solution") != source.get("solution_id")
            or record.get("expression") != expression):
        _fail("SOLUTION_BINDING_MISMATCH", f"{label} normal integral is not bound to the exact native source/expression",
              details={"record": dict(record)}, stage="post_dispatch")
    area = _check_selection_measure(record, surface, label)
    return value.real, {
        "real": value.real, "imag": 0.0, "unit": _NORMAL_READBACK_POLICY["area_unit"],
        "expression": expression, "dataset_id": record.get("dataset"),
        "solution_id": record.get("solution"), "feature_type": cleanup.get("type_id"),
        "cleanup": dict(cleanup), "selection_measure_m2": area,
        "selection_measure_source": record.get("selection_measure_source"),
        "is_complex": False,
    }


def _native_surface_normal_readback(worker: Any, model_tag: str, source: Mapping[str, Any],
                                    selection: Mapping[str, Any], readback: Mapping[str, Any],
                                    surface: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    """Read and validate the actual outward normal distribution on one named surface.

    COMSOL's nx/ny/nz are global normal components pointing outward relative to
    meshed domains. Their area integrals and the integrated squared norm expose
    the measured direction and reject a materially varying normal field. This
    deliberately does not authenticate the request's coordinate-frame mapping
    or the physical Port propagation convention.
    """
    from ._w23_results import _native_integral

    expressions = {"x": "nx", "y": "ny", "z": "nz",
                   "squared_norm": "nx^2+ny^2+nz^2"}
    records: dict[str, dict[str, Any]] = {}
    for component, expression in expressions.items():
        raw = _native_integral(worker, model_tag, source, selection, readback,
                               expression, label=f"W23 v2 normal {label} {component}")
        if not isinstance(raw, Mapping):
            _fail("NATIVE_READBACK_UNVERIFIED", f"{label} outward-normal integral {component} is not structured",
                  stage="post_dispatch")
        _, records[component] = _real_surface_integral(
            raw, expression=expression, source=source, surface=surface,
            label=f"{label} outward-normal integral {component}")

    area = records["x"]["selection_measure_m2"]
    for component in ("y", "z", "squared_norm"):
        if not math.isclose(records[component]["selection_measure_m2"], area,
                            rel_tol=_INTEGRAL_TOLERANCES["surface_area_relative_tolerance"],
                            abs_tol=_INTEGRAL_TOLERANCES["surface_area_absolute_tolerance_m2"]):
            _fail("SELECTION_MEASURE_MISMATCH", f"{label} normal component integrals do not share one measured area",
                  stage="post_dispatch")

    outward = [records[axis]["real"] / area for axis in "xyz"]
    mean_norm = math.sqrt(sum(value * value for value in outward))
    second_moment = records["squared_norm"]["real"] / area
    variance = second_moment - mean_norm * mean_norm
    if (not all(math.isfinite(value) for value in (*outward, mean_norm, second_moment, variance))
            or not math.isclose(second_moment, 1.0,
                                rel_tol=_NORMAL_READBACK_POLICY["second_moment_relative_tolerance"],
                                abs_tol=_NORMAL_READBACK_POLICY["second_moment_relative_tolerance"])):
        _fail("NATIVE_NORMAL_INVALID", f"{label} native outward-normal field is not finite/unit length",
              details={"mean_normal_xyz": outward, "mean_normal_norm": mean_norm,
                       "mean_squared_norm": second_moment}, stage="post_dispatch")
    if (variance < -_NORMAL_READBACK_POLICY["normal_variance_absolute_tolerance"]
            or variance > _NORMAL_READBACK_POLICY["normal_variance_absolute_tolerance"]
            or abs(mean_norm - 1.0) > _NORMAL_READBACK_POLICY["mean_normal_unit_tolerance"]):
        _fail("NON_PLANAR_SURFACE_NORMAL", f"{label} named surface does not have one uniform outward normal",
              details={"mean_normal_xyz": outward, "mean_normal_norm": mean_norm,
                       "normal_variance": variance,
                       "normal_variance_tolerance": _NORMAL_READBACK_POLICY["normal_variance_absolute_tolerance"]},
              stage="post_dispatch")

    forward_axis = [_finite(value, f"{label} requested forward axis") for value in surface["axis_xyz"]]
    requested_sign = surface["native_normal_sign"]
    component_dot = sum(outward[index] * forward_axis[index] for index in range(3))
    signed_outward = [requested_sign * value for value in outward]
    signed_component_dot = requested_sign * component_dot
    return {
        "schema_id": NORMAL_READBACK_SCHEMA_ID,
        "schema_version": _NORMAL_READBACK_POLICY["schema_version"],
        "area_unit": _NORMAL_READBACK_POLICY["area_unit"],
        "second_moment_relative_tolerance": _NORMAL_READBACK_POLICY["second_moment_relative_tolerance"],
        "normal_variance_absolute_tolerance": _NORMAL_READBACK_POLICY["normal_variance_absolute_tolerance"],
        "mean_normal_unit_tolerance": _NORMAL_READBACK_POLICY["mean_normal_unit_tolerance"],
        "status": "NATIVE_OUTWARD_NORMAL_READBACK_PASSED_FRAME_LINK_UNVERIFIED",
        "source_binding": {
            "dataset_id": source["dataset_id"], "solution_id": source["solution_id"],
            "outer_index": source["outer_index"], "inner_index": source["inner_index"],
            "solnum": source["solnum"],
        },
        "selection": {"component": selection["component"], "geometry": selection["geometry"],
                      "tag": selection["tag"], "entity_dimension": readback["entity_dimension"],
                      "entity_ids": list(readback["entity_ids"]), "area_m2": area},
        "native_integrals": {key: records[key] for key in expressions},
        "observed_outward_normal_xyz_area_mean": outward,
        "observed_outward_normal_norm": mean_norm,
        "observed_normal_variance": variance,
        "native_component_frame": _NORMAL_READBACK_POLICY["component_frame"],
        "request_frame_id": surface["frame_id"],
        "request_frame_mapping_status": "UNVERIFIED",
        "requested_forward_axis_xyz": forward_axis,
        "requested_integration_sign": requested_sign,
        "signed_observed_outward_normal_xyz": signed_outward,
        "outward_component_dot_requested_axis_if_frames_coincide": component_dot,
        "signed_outward_component_dot_requested_axis_if_frames_coincide": signed_component_dot,
        "port_orientation_relation_status": "UNVERIFIED",
    }


def _validate_native_source(request: Mapping[str, Any], role: str, source_kind: str,
                            source: Mapping[str, Any], native_binding: Mapping[str, Any],
                            axes: Mapping[str, Any], model_tag: str) -> None:
    if (native_binding.get("binding_complete") is not True
            or native_binding.get("dataset") != source.get("dataset_id")
            or native_binding.get("solution") != source.get("solution_id")):
        _fail("DATASET_BINDING_INCOMPLETE", f"{role} native dataset/solution binding differs from the exact requested source")
    if (axes.get("outer_index") != source.get("outer_index")
            or axes.get("inner_index") != source.get("inner_index")
            or axes.get("solnum") != source.get("solnum")):
        _fail("SOLUTION_INDEX_MISMATCH", f"{role} native SolutionInfo pair/solnum differs from the requested source",
              details={"requested": {key: source.get(key) for key in ("outer_index", "inner_index", "solnum")},
                       "native": dict(axes)})
    if not isinstance(model_tag, str) or source.get("model_ref", {}).get("model_tag") != model_tag:
        _fail("MODEL_IDENTITY_MISMATCH", f"{role} source ModelRef tag differs from the managed model tag")
    frequency = axes.get("frequency")
    expected_frequency = _finite(request.get("frequency_hz"), "requested optical frequency")
    if (not isinstance(frequency, Mapping) or frequency.get("unit") not in _FREQUENCY_SCALE
            or not math.isclose(_finite(frequency.get("value"), f"{role} native frequency" )
                                * _FREQUENCY_SCALE[frequency["unit"]], expected_frequency,
                                rel_tol=_INTEGRAL_TOLERANCES["frequency_relative_tolerance"], abs_tol=0.0)):
        _fail("FREQUENCY_MISMATCH", f"{role} actual SolutionInfo frequency differs from the v2 basis request")
    if (type(source.get("outer_index")) is not int or source["outer_index"] < 1
            or type(source.get("inner_index")) is not int or source["inner_index"] < 1):
        _fail("SOLUTION_INDEX_MISMATCH", f"{role} exact native source indices are invalid")
    if source_kind == "mode" and (type(source.get("mode_index")) is not int or source["mode_index"] < 1):
        _fail("MODE_IDENTITY_MISMATCH", f"{role} Numeric Port mode index provenance is invalid")


def _validate_managed_execution_identity(request: Mapping[str, Any], model_tag: str,
                                         arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Bind request identity to daemon-installed context before any Worker read."""
    from ._observation_store import current_context

    context = current_context()
    if not isinstance(context, Mapping):
        _fail("MANAGED_EXECUTION_CONTEXT_REQUIRED", "W23 v2 requires the active managed execution context")
    active_ref = context.get("model_ref")
    active_project = context.get("project_id")
    active_revision = context.get("revision")
    if active_project != request.get("project_id"):
        _fail("PROJECT_IDENTITY_MISMATCH", "W23 v2 project ID differs from the authoritative managed execution context")
    if not isinstance(active_ref, Mapping) or dict(active_ref) != dict(request.get("model_ref", {})):
        _fail("MODEL_IDENTITY_MISMATCH", "W23 v2 full ModelRef differs from the authoritative managed execution context")
    if not isinstance(model_tag, str) or active_ref.get("model_tag") != model_tag:
        _fail("MODEL_IDENTITY_MISMATCH", "W23 v2 model tag differs from the active managed ModelRef")
    if type(active_revision) is not int or active_revision < 0:
        _fail("REVISION_CONFLICT", "active managed model revision is unavailable")
    if request.get("model_revision") != active_revision:
        _fail("REVISION_CONFLICT", "W23 v2 request model revision is stale relative to managed execution")
    envelope_expectations = {
        "project_id": active_project,
        "session_id": active_ref.get("session_id"),
        "model_ref": dict(active_ref),
        "expected_revision": active_revision,
    }
    for key, expected in envelope_expectations.items():
        if key in arguments and arguments[key] != expected:
            code = "REVISION_CONFLICT" if key == "expected_revision" else (
                "PROJECT_IDENTITY_MISMATCH" if key == "project_id" else "MODEL_IDENTITY_MISMATCH")
            _fail(code, f"W23 v2 operation envelope {key} differs from the active managed execution context")
    return {"project_id": active_project, "model_ref": dict(active_ref),
            "model_revision": active_revision, "source": "managed_observation_context"}


def result_mode_overlap_basis_v2(worker: Any, model_tag: str,
                                 arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Read exact native source/selection metadata and integrate G, b, and powers.

    The result is native integration plumbing only. It does not compute eta or
    claim the independent raw-field comparison, subspace conditioning, optical
    acceptance, or any Study.run/solver activity.
    """
    args = _mapping(arguments, "arguments")
    _validate_operation_arguments(args)
    definition = _validate_definition(args["definition"])
    request = definition["basis_request"]
    managed_binding = _validate_managed_execution_identity(request, model_tag, args)
    from ._g3_common import bound_model
    from ._w23_results import _native_integral, _solution_info, _source_selection, _space_dimension

    model = bound_model(worker, model_tag)
    source_roles = _source_roles(request)
    axis_by_mode = {row["mode_id"]: row["parameter"] for row in definition["mode_axis_parameters"]}
    roles: dict[str, dict[str, Any]] = {}
    selection_readbacks: dict[str, dict[str, Any]] = {}
    expected_outer = None
    expected_frequency = request["frequency_hz"]

    for role, (source_kind, source) in source_roles.items():
        surface_key = "input" if role == "incident" else "output"
        surface = request["input_surface" if surface_key == "input" else "output_surface"]
        native_binding, native_axes = _solution_info(model, source, label=role)
        _validate_native_source(request, role, source_kind, source, native_binding, native_axes, model_tag)
        if expected_outer is None:
            expected_outer = source["outer_index"]
        elif source["outer_index"] != expected_outer:
            _fail("SOLUTION_INDEX_MISMATCH", "all W23 v2 basis sources must share one exact native outer index")
        if not math.isclose(native_axes["frequency"]["frequency_hz"], expected_frequency,
                            rel_tol=_INTEGRAL_TOLERANCES["frequency_relative_tolerance"], abs_tol=0.0):
            _fail("FREQUENCY_MISMATCH", f"{role} frequency differs from the registered basis frequency")
        if _space_dimension(model, source["dataset_id"], native_binding, label=role) != 3:
            _fail("GEOMETRY_DIMENSION_MISMATCH", f"{role} source is not a native 3-D solution")

        mode_axis = None
        if source_kind == "mode":
            mode_row = next(row for row in request["basis_modes"] if row["source"] is source)
            mode_axis = axis_by_mode[mode_row["mode_id"]]
        selectors = _solution_selector(role, source_kind, source, native_axes, mode_axis)
        roles[role] = {
            "role": role, "source_kind": source_kind, "source": dict(source),
            "native_binding": dict(native_binding), "solution_axes": dict(native_axes),
            "mode_axis_parameter": mode_axis, "withsol_selectors": selectors,
        }
        selection = {"component": surface["component"], "geometry": surface["geometry"],
                     "tag": surface["selection_tag"]}
        native_selection = _source_selection(worker, model_tag, native_binding, selection, label=role)
        if native_selection.get("entity_dimension") != 2:
            _fail("SELECTION_DIMENSION_MISMATCH", f"{role} named selection is not a 3-D boundary")
        if (native_selection.get("component") != selection["component"]
                or native_selection.get("geometry") != selection["geometry"]
                or native_selection.get("selection_tag") != selection["tag"]):
            _fail("SELECTION_READBACK_MISMATCH", f"{role} native named selection identity differs from the frozen port")
        selection_readbacks[role] = native_selection

    for role in source_roles:
        entities = selection_readbacks[role].get("entity_ids")
        if (not isinstance(entities, list) or not entities
                or any(type(entity) is not int or entity < 1 for entity in entities)
                or len(set(entities)) != len(entities)):
            _fail("SELECTION_READBACK_MISMATCH", f"{role} named selection has invalid or duplicate boundary IDs")
    output_ids = [tuple(sorted(selection_readbacks[role]["entity_ids"]))
                  for role in ("signal", "mode_0", "mode_1")]
    if any(entities != output_ids[0] for entities in output_ids[1:]):
        _fail("SELECTION_READBACK_MISMATCH", "signal and both basis modes do not resolve to the same output boundary IDs")

    normal_readbacks = {}
    for key, role in (("output", "signal"), ("input", "incident")):
        surface = request[f"{key}_surface"]
        source = roles[role]["source"]
        selection = {"component": surface["component"], "geometry": surface["geometry"],
                     "tag": surface["selection_tag"]}
        normal_readbacks[key] = _native_surface_normal_readback(
            worker, model_tag, source, selection, selection_readbacks[role], surface,
            label=key)

    # Mapping from the immutable canonical request's eight terms to one
    # current dataset plus, when needed, an exact withsol expression. The
    # source on the left side is the current result dataset; the right-side
    # source is selected only through SolutionInfo-derived withsol selectors.
    terms: list[dict[str, Any]] = []
    surface_by_term: dict[str, str] = {}
    for i in range(2):
        for j in range(2):
            terms.append({"term_id": f"G{i}{j}", "role_a": f"mode_{i}", "role_b": f"mode_{j}",
                          "functional": "B(a,b)", "surface": "output"})
    terms.extend([
        {"term_id": "b0", "role_a": "mode_0", "role_b": "signal", "functional": "B(a,b)", "surface": "output"},
        {"term_id": "b1", "role_a": "mode_1", "role_b": "signal", "functional": "B(a,b)", "surface": "output"},
        {"term_id": "P_signal", "role_a": "signal", "role_b": "signal", "functional": "P(a)", "surface": "output"},
        {"term_id": "P_incident", "role_a": "incident", "role_b": "incident", "functional": "P(a)", "surface": "input"},
    ])
    integrals: dict[str, dict[str, Any]] = {}
    term_bindings: dict[str, dict[str, Any]] = {}
    for term in terms:
        term_id = term["term_id"]
        role_a, role_b = roles[term["role_a"]], roles[term["role_b"]]
        surface_key = term["surface"]
        surface = request["output_surface" if surface_key == "output" else "input_surface"]
        selection = {"component": surface["component"], "geometry": surface["geometry"],
                     "tag": surface["selection_tag"]}
        readback = selection_readbacks[term["role_a"]]
        normal_sign = surface["native_normal_sign"]
        if term["functional"] == "B(a,b)":
            expression = _bilinear_expression(role_a, role_b, normal_sign=normal_sign,
                                              withsol_b=term["role_a"] != term["role_b"])
        else:
            expression = _power_expression(role_a, normal_sign=normal_sign)
        record = _native_integral(worker, model_tag, role_a["source"], selection, readback,
                                  expression, label=f"W23 v2 {term_id}")
        if not isinstance(record, Mapping):
            _fail("NATIVE_READBACK_UNVERIFIED", f"{term_id} native integral did not return a structured record",
                  stage="post_dispatch")
        parsed = _complex_record(record, label=term_id,
                                 require_complex=term["functional"] == "B(a,b)")
        measured_area = _check_selection_measure(record, surface, f"{surface_key} port")
        integrals[term_id] = parsed
        term_bindings[term_id] = {
            "role_a": term["role_a"], "role_b": term["role_b"],
            "dataset_id": role_a["source"]["dataset_id"],
            "solution_id": role_a["source"]["solution_id"],
            "outer_index": role_a["source"]["outer_index"],
            "inner_index": role_a["source"]["inner_index"],
            "solnum": role_a["source"]["solnum"],
            "selection_tag": surface["selection_tag"],
            "entity_dimension": 2,
            "entity_ids": list(readback["entity_ids"]),
            "aperture_id": surface["aperture_id"],
            "area_m2": measured_area,
            "coordinate_frame": request["coordinate_frame"],
            "unit": "W", "expression": expression,
        }

    gram = [[integrals["G00"], integrals["G01"]],
            [integrals["G10"], integrals["G11"]]]
    couplings = [integrals["b0"], integrals["b1"]]
    powers = {"signal_power": integrals["P_signal"],
              "incident_reference_power": integrals["P_incident"]}
    native_source_bindings = {
        role: {"dataset_binding": row["native_binding"],
               "solution_axes": row["solution_axes"],
               "mode_axis_parameter": row["mode_axis_parameter"],
            "numeric_port_mode_index": {
                "value": row["source"].get("mode_index"),
                "origin": "canonical_request_provenance_only",
                "native_port_mode_index_readback": "NOT_PROVIDED",
            }}
        for role, row in roles.items()
    }
    surface_readbacks = {}
    for key in ("output", "input"):
        integral_key = "P_signal" if key == "output" else "P_incident"
        source_role = "signal" if key == "output" else "incident"
        measure = _one_real(integrals[integral_key]["selection_measure_m2"],
                            f"{key} native measured area")
        surface_readbacks[key] = {
            "selection": {"component": request[f"{key}_surface"]["component"],
                          "geometry": request[f"{key}_surface"]["geometry"],
                          "tag": request[f"{key}_surface"]["selection_tag"]},
            "entity_dimension": selection_readbacks[source_role]["entity_dimension"],
            "entity_ids": list(selection_readbacks[source_role]["entity_ids"]),
            "aperture_id": request[f"{key}_surface"]["aperture_id"],
            "expected_area_m2": request[f"{key}_surface"]["surface_area_m2"],
            "measured_area_m2": measure,
            "normal_provenance": normal_readbacks[key],
        }
    return {
        "schema_id": RESULT_SCHEMA_ID, "schema_version": "1.0.0",
        "operation_id": OPERATION_ID, "status": "SUCCEEDED",
        "result_status": "COMPUTED_NATIVE_TWO_MODE_INTEGRALS",
        "algorithm_id": "reciprocal_lossless_forward_two_mode_basis_projection_v2",
        "basis_request_id": request["request_id"],
        "definition_sha256": definition["definition_sha256"],
        "identity": {
            "basis_id": request["basis_id"], "case": dict(request["case"]),
            "project_id": request["project_id"], "model_ref": dict(request["model_ref"]),
            "model_tag": model_tag, "model_revision": request["model_revision"],
            "geometry_revision": request["geometry_revision"],
            "frequency_hz": request["frequency_hz"],
            "coordinate_frame": request["coordinate_frame"],
            "mode_ids": [row["mode_id"] for row in request["basis_modes"]],
            "mode_indices": [row["mode_index"] for row in request["basis_modes"]],
        },
        "managed_execution_binding": managed_binding,
        "source_readbacks": native_source_bindings,
        "surface_readbacks": surface_readbacks,
        "native_integrals": {"gram_matrix": gram, "coupling_vector": couplings,
                             **powers, "terms": integrals},
        "term_bindings": term_bindings,
        "policy": dict(_INTEGRAL_TOLERANCES),
        "origin": "COMSOL_NATIVE_INT_SURFACE",
        "study_or_solver_invoked": False,
        "caller_field_arrays_accepted": False,
        "native_result": "COMSOL_NATIVE_RAW",
        "quadrature_comparison": "NOT_RUN",
        "projection_acceptance": "NOT_RUN",
        "production_route_status": "ROUTE_REGISTERED_NATIVE_INTEGRATION_ONLY",
    }


OPERATIONS = {OPERATION_ID: result_mode_overlap_basis_v2}
OPERATION_ARGUMENTS = {OPERATION_ID: ("definition",)}
OPERATION_REQUIRED = {OPERATION_ID: ("definition",)}

__all__ = [
    "DEFINITION_SCHEMA_ID", "DEFINITION_SCHEMA_VERSION", "OPERATION_ARGUMENTS",
    "NATIVE_DEFINITION_SCHEMA", "NATIVE_RESULT_SCHEMA", "OPERATION_ID", "OPERATION_REQUIRED", "OPERATIONS", "RESULT_SCHEMA_ID",
    "build_definition", "result_mode_overlap_basis_v2", "validate_request_shape",
]
