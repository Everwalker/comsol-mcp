"""Packaged identity and wire validation for the W23 v2 basis request.

The offline numerical/reference implementation lives in ``tools``. Production
result adapters must validate the same request identity without importing that
unpackaged development tree or treating its digest as managed provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from ._execution_contract import ExecutionContractError


BASIS_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap:two-mode-basis:2.0.0"
BASIS_SCHEMA_VERSION = "2.0.0"
BASIS_PROFILE_ID = "full3d.reciprocal_lossless_forward_two_mode_subspace_v2"
BASIS_ALGORITHM_ID = "reciprocal_lossless_forward_two_mode_basis_projection_v2"
PHASOR_CONVENTION = "full_physical_complex_phasor_including_reconstructed_envelope_phase"
REQUEST_BINDING_KEYS = (
    "schema_id", "schema_version", "profile", "algorithm_id", "basis_id", "case",
    "project_id", "model_ref", "model_revision", "geometry_revision", "frequency_hz",
    "coordinate_frame", "power_unit", "field_units", "phasor_convention",
    "output_surface", "input_surface", "signal_source", "basis_modes",
    "incident_source", "applicability", "policy", "quadratures",
    "native_verification", "native_result", "dispatchable", "production_route_status",
    "native_integral_plan",
)
REQUEST_KEYS = frozenset((*REQUEST_BINDING_KEYS, "request_id", "study_or_solver_invoked"))
_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_REQUEST_POLICY = {
    "policy_id": "w23.full3d.two_mode_basis.numerical_gates.v1",
    "power_unit": "W", "field_electric_unit": "V/m", "field_magnetic_unit": "A/m",
    "coordinate_unit": "m", "area_unit": "m^2", "power_floor_w": 1e-12,
    "hermitian_relative_tolerance": 1e-8,
    "max_normalized_gram_condition": 1e6,
    "native_vs_quadrature_relative_tolerance": 1e-3,
    "native_integral_absolute_normalized_tolerance": 1e-5,
    "eta_absolute_tolerance": 1e-10,
}

# Registry-facing shape for the versioned adapter. The adapter's strict
# semantic validator below remains authoritative for nested source, surface,
# provenance, and applicability fields; this schema publishes the closed wire
# envelope without duplicating every cross-field invariant in JSON Schema.
BASIS_REQUEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "W23 full-3D two-mode basis request v2",
    "type": "object",
    "required": sorted(REQUEST_KEYS),
    "properties": {
        "schema_id": {"const": BASIS_SCHEMA_ID},
        "schema_version": {"const": BASIS_SCHEMA_VERSION},
        "profile": {"const": BASIS_PROFILE_ID},
        "algorithm_id": {"const": BASIS_ALGORITHM_ID},
        "basis_id": {"type": "string", "minLength": 1},
        "case": {"type": "object", "required": ["case_id", "case_identity_sha256"]},
        "project_id": {"type": "string", "minLength": 1},
        "model_ref": {
            "type": "object", "required": ["schema_version", "session_id", "server_instance_id", "model_tag", "generation"],
            "properties": {
                "schema_version": {"const": 1},
                "session_id": {"type": "string", "minLength": 1},
                "server_instance_id": {"type": "string", "minLength": 1},
                "model_tag": {"type": "string", "minLength": 1},
                "generation": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        "model_revision": {"type": "integer", "minimum": 0},
        "geometry_revision": {"type": "integer", "minimum": 0},
        "frequency_hz": {"type": "number", "exclusiveMinimum": 0},
        "coordinate_frame": {"type": "string", "minLength": 1},
        "power_unit": {"const": "W"},
        "field_units": {"const": {"electric": "V/m", "magnetic": "A/m"}},
        "phasor_convention": {"const": PHASOR_CONVENTION},
        "output_surface": {"type": "object"},
        "input_surface": {"type": "object"},
        "signal_source": {"type": "object"},
        "basis_modes": {"type": "array", "minItems": 2, "maxItems": 2},
        "incident_source": {"type": "object"},
        "applicability": {"type": "object"},
        "policy": {"const": _REQUEST_POLICY},
        "quadratures": {"type": "object"},
        "native_verification": {"const": "NOT_RUN"},
        "native_result": {"const": "NOT_RUN"},
        "dispatchable": {"const": False},
        "production_route_status": {"const": "NOT_REGISTERED"},
        "native_integral_plan": {"type": "object"},
        "request_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "study_or_solver_invoked": {"const": False},
    },
    "additionalProperties": False,
    "$comment": "Nested cross-field identities are checked by validate_basis_request before scheduling.",
}

NATIVE_DEFINITION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:comsol-mcp:result.mode_overlap_basis_v2:definition:1.0.0",
    "title": "result.mode_overlap_basis_v2 definition v1.0.0",
    "type": "object",
    "required": ["schema_id", "schema_version", "basis_request", "mode_axis_parameters", "definition_sha256"],
    "properties": {
        "schema_id": {"const": "urn:comsol-mcp:result.mode_overlap_basis_v2:definition:1.0.0"},
        "schema_version": {"const": "1.0.0"},
        "basis_request": BASIS_REQUEST_SCHEMA,
        "mode_axis_parameters": {
            "type": "array", "minItems": 2, "maxItems": 2,
            "items": {
                "type": "object", "required": ["mode_id", "parameter"],
                "properties": {"mode_id": {"type": "string", "minLength": 1},
                               "parameter": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        },
        "definition_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
    "additionalProperties": False,
}


def _fail(code: str, message: str) -> None:
    raise ExecutionContractError(code, message, stage="validation")


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail("INVALID_REQUEST", f"{label} must be finite numeric data")
    return float(value)


def basis_request_binding(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one canonical digest body shared by builder and adapter."""
    if not isinstance(request, Mapping) or any(key not in request for key in REQUEST_BINDING_KEYS):
        _fail("INVALID_REQUEST", "W23 v2 basis request is missing canonical identity fields")
    return {key: request[key] for key in REQUEST_BINDING_KEYS}


def _validate_model_ref(value: Any) -> dict[str, Any]:
    required = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("MODEL_IDENTITY_MISMATCH", "W23 v2 request requires the complete managed ModelRef")
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or any(not isinstance(value.get(key), str) or not value[key]
                   for key in ("session_id", "server_instance_id", "model_tag"))
            or type(value.get("generation")) is not int or value["generation"] < 1):
        _fail("MODEL_IDENTITY_MISMATCH", "W23 v2 request ModelRef identity is incomplete")
    return dict(value)


def _validate_surface(value: Any, *, expected_plane: str, expected_port: str,
                      frame: str) -> dict[str, Any]:
    required = {
        "plane_id", "port_id", "component", "geometry", "selection_tag", "entity_dimension",
        "frame_id", "coordinate_unit", "measure_unit", "center_xyz_um", "axis_xyz",
        "native_normal_sign", "aperture_id", "aperture_shape", "surface_area_m2",
    }
    allowed = required | {"sample_radius_um", "half_widths_uv_um"}
    if not isinstance(value, Mapping) or not required.issubset(value) or set(value) - allowed:
        _fail("INVALID_REQUEST", f"{expected_plane} must be an exact named W23 v2 surface definition")
    if (value.get("plane_id") != expected_plane or value.get("port_id") != expected_port
            or value.get("entity_dimension") != 2 or value.get("frame_id") != frame
            or value.get("coordinate_unit") != "um" or value.get("measure_unit") != "m^2"):
        _fail("INVALID_REQUEST", f"{expected_plane} identity, dimension, frame, or units are invalid")
    for key in ("component", "geometry", "selection_tag"):
        if not isinstance(value.get(key), str) or not _TAG.fullmatch(value[key]):
            _fail("INVALID_REQUEST", f"{expected_plane}.{key} must be a safe nonempty COMSOL tag")
    if any(not isinstance(value.get(key), str) or not value[key]
           for key in ("plane_id", "aperture_id")):
        _fail("INVALID_REQUEST", f"{expected_plane} plane and aperture IDs must be nonempty")
    if (type(value.get("native_normal_sign")) is not int
            or value["native_normal_sign"] not in {-1, 1}):
        _fail("INVALID_REQUEST", f"{expected_plane} normal orientation must be exactly +1 or -1")
    center, axis = value.get("center_xyz_um"), value.get("axis_xyz")
    if not isinstance(center, list) or len(center) != 3 or not isinstance(axis, list) or len(axis) != 3:
        _fail("INVALID_REQUEST", f"{expected_plane} center and axis must be xyz vectors")
    for item in center:
        _finite(item, f"{expected_plane}.center_xyz_um")
    components = [_finite(item, f"{expected_plane}.axis_xyz") for item in axis]
    norm = math.sqrt(math.fsum(item * item for item in components))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1e-10:
        _fail("INVALID_REQUEST", f"{expected_plane} axis must be a unit xyz vector")
    area = _finite(value.get("surface_area_m2"), f"{expected_plane}.surface_area_m2")
    if area <= 0.0:
        _fail("INVALID_REQUEST", f"{expected_plane} area must be positive")
    shape = value.get("aperture_shape")
    if shape == "circular":
        if "half_widths_uv_um" in value or _finite(value.get("sample_radius_um"), f"{expected_plane}.sample_radius_um") <= 0:
            _fail("INVALID_REQUEST", f"{expected_plane} circular aperture radius is invalid")
    elif shape == "rectangle":
        widths = value.get("half_widths_uv_um")
        if "sample_radius_um" in value or not isinstance(widths, list) or len(widths) != 2:
            _fail("INVALID_REQUEST", f"{expected_plane} rectangular aperture widths are invalid")
        if min(_finite(item, f"{expected_plane}.half_widths_uv_um") for item in widths) <= 0:
            _fail("INVALID_REQUEST", f"{expected_plane} rectangular aperture widths must be positive")
    else:
        _fail("INVALID_REQUEST", f"{expected_plane} aperture shape is unsupported")
    return dict(value)


def _validate_source(value: Any, *, label: str, request: Mapping[str, Any],
                     expected_port: str, mode: bool) -> dict[str, Any]:
    keys = {
        "dataset_id", "solution_id", "outer_index", "inner_index", "solnum", "port_id",
        "frequency_hz", "project_id", "model_ref", "model_revision", "geometry_revision",
        "coordinate_frame",
    }
    if mode:
        keys.add("mode_index")
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail("INVALID_REQUEST", f"{label} source has an incomplete or extra identity field")
    if any(not isinstance(value.get(key), str) or not value[key]
           for key in ("dataset_id", "solution_id", "port_id", "project_id", "coordinate_frame")):
        _fail("INVALID_REQUEST", f"{label} source has an empty native identity")
    if (value["port_id"] != expected_port or value["project_id"] != request["project_id"]
            or value["model_ref"] != request["model_ref"]
            or value["model_revision"] != request["model_revision"]
            or value["geometry_revision"] != request["geometry_revision"]
            or value["coordinate_frame"] != request["coordinate_frame"]
            or not math.isclose(_finite(value["frequency_hz"], f"{label}.frequency_hz"),
                                request["frequency_hz"], rel_tol=1e-12, abs_tol=0.0)):
        _fail("MODEL_IDENTITY_MISMATCH", f"{label} source differs from the W23 v2 project/model/revision/frame/frequency identity")
    for key in ("outer_index", "inner_index", "solnum"):
        if type(value.get(key)) is not int or value[key] < 1:
            _fail("SOLUTION_INDEX_MISMATCH", f"{label}.{key} must be a positive exact native index")
    if mode and (type(value.get("mode_index")) is not int or value["mode_index"] < 1):
        _fail("MODE_IDENTITY_MISMATCH", f"{label}.mode_index must be positive Numeric Port provenance")
    return dict(value)


def validate_basis_request(request: Any) -> dict[str, Any]:
    """Validate the canonical, self-digested request without any ``tools`` import.

    This checks payload integrity and local schema consistency. Managed
    provenance is intentionally established separately by comparing its exact
    project, ModelRef, and revision to the active execution context.
    """
    if not isinstance(request, Mapping) or set(request) != REQUEST_KEYS:
        _fail("INVALID_REQUEST", "W23 v2 basis request has missing or unsupported top-level fields")
    if (request.get("schema_id") != BASIS_SCHEMA_ID
            or request.get("schema_version") != BASIS_SCHEMA_VERSION
            or request.get("profile") != BASIS_PROFILE_ID
            or request.get("algorithm_id") != BASIS_ALGORITHM_ID
            or request.get("dispatchable") is not False
            or request.get("native_result") != "NOT_RUN"
            or request.get("native_verification") != "NOT_RUN"
            or request.get("production_route_status") != "NOT_REGISTERED"
            or request.get("study_or_solver_invoked") is not False
            or request.get("phasor_convention") != PHASOR_CONVENTION
            or request.get("power_unit") != "W"
            or request.get("field_units") != {"electric": "V/m", "magnetic": "A/m"}
            or request.get("policy") != _REQUEST_POLICY):
        _fail("INVALID_REQUEST", "W23 v2 schema, scope, field units, phasor convention, or frozen policy is invalid")
    if not isinstance(request.get("basis_id"), str) or not request["basis_id"].strip():
        _fail("INVALID_REQUEST", "basis_id must be nonempty")
    if (not isinstance(request.get("case"), Mapping)
            or any(not isinstance(request["case"].get(key), str) or not request["case"][key]
                   for key in ("case_id", "case_identity_sha256"))):
        _fail("INVALID_REQUEST", "W23 v2 case identity must be registered and immutable")
    if not isinstance(request.get("project_id"), str) or not request["project_id"]:
        _fail("PROJECT_IDENTITY_REQUIRED", "W23 v2 basis request requires a registered project ID")
    ref = _validate_model_ref(request.get("model_ref"))
    if type(request.get("model_revision")) is not int or request["model_revision"] < 0:
        _fail("REVISION_CONFLICT", "W23 v2 model_revision must be a nonnegative integer")
    if type(request.get("geometry_revision")) is not int or request["geometry_revision"] < 0:
        _fail("REVISION_CONFLICT", "W23 v2 geometry_revision must be a nonnegative integer")
    frequency = _finite(request.get("frequency_hz"), "W23 v2 frequency_hz")
    if frequency <= 0.0:
        _fail("INVALID_REQUEST", "W23 v2 frequency_hz must be positive")
    frame = request.get("coordinate_frame")
    if not isinstance(frame, str) or not frame:
        _fail("INVALID_REQUEST", "W23 v2 coordinate_frame must be nonempty")
    if ref.get("model_tag") == "" or _validate_model_ref(ref) != dict(request["model_ref"]):
        _fail("MODEL_IDENTITY_MISMATCH", "W23 v2 ModelRef is invalid")
    if ref.get("model_tag") != request["model_ref"].get("model_tag"):
        _fail("MODEL_IDENTITY_MISMATCH", "W23 v2 ModelRef model tag is invalid")
    _validate_surface(request.get("output_surface"), expected_plane="receiver_port",
                      expected_port="2", frame=frame)
    _validate_surface(request.get("input_surface"), expected_plane="input_port",
                      expected_port="1", frame=frame)
    signal = _validate_source(request.get("signal_source"), label="signal", request=request,
                              expected_port="2", mode=False)
    incident = _validate_source(request.get("incident_source"), label="incident", request=request,
                                expected_port="1", mode=False)
    modes = request.get("basis_modes")
    if not isinstance(modes, list) or len(modes) != 2:
        _fail("INVALID_REQUEST", "W23 v2 requires exactly two basis mode sources")
    normalized_modes = []
    for index, row in enumerate(modes):
        if not isinstance(row, Mapping) or set(row) != {"mode_id", "mode_index", "source"}:
            _fail("INVALID_REQUEST", "each W23 v2 basis mode must bind an ID, Numeric Port index, and source")
        if not isinstance(row["mode_id"], str) or not row["mode_id"]:
            _fail("INVALID_REQUEST", "basis mode IDs must be nonempty")
        source = _validate_source(row["source"], label=f"mode_{index}", request=request,
                                  expected_port="2", mode=True)
        if type(row["mode_index"]) is not int or row["mode_index"] < 1 or row["mode_index"] != source["mode_index"]:
            _fail("MODE_IDENTITY_MISMATCH", "Numeric Port mode index provenance is malformed")
        normalized_modes.append(dict(row))
    if (len({row["mode_id"] for row in normalized_modes}) != 2
            or len({row["mode_index"] for row in normalized_modes}) != 2):
        _fail("MODE_IDENTITY_MISMATCH", "W23 v2 basis mode identities must be distinct")
    applicability = request.get("applicability")
    required_conditions = {"reciprocal", "lossless", "forward_propagating", "non_evanescent", "non_leaky"}
    if not isinstance(applicability, Mapping) or any(applicability.get(key) is not True for key in required_conditions):
        _fail("INVALID_REQUEST", "W23 v2 applicability conditions are incomplete")
    quadratures = request.get("quadratures")
    if not isinstance(quadratures, Mapping) or set(quadratures) != {"output", "input"}:
        _fail("INVALID_REQUEST", "W23 v2 request must retain both frozen reference quadratures")
    integral_plan = request.get("native_integral_plan")
    if (not isinstance(integral_plan, Mapping)
            or integral_plan.get("origin_required") != "COMSOL_NATIVE_INTEGRATION_FEATURES"
            or integral_plan.get("source_arrays_accepted") is not False
            or integral_plan.get("status") != "PLAN_ONLY_NOT_DISPATCHED"):
        _fail("INVALID_REQUEST", "W23 v2 native integral recipe must remain source-array-free and undispatched")
    terms = integral_plan.get("terms")
    term_ids = {f"G{i}{j}" for i in range(2) for j in range(2)} | {"b0", "b1", "P_signal", "P_incident"}
    if not isinstance(terms, list) or len(terms) != 8 or {row.get("integral_id") for row in terms if isinstance(row, Mapping)} != term_ids:
        _fail("INVALID_REQUEST", "W23 v2 plan must contain exactly four Gram, two coupling, and two power integrals")
    if _digest(basis_request_binding(request)) != request.get("request_id"):
        _fail("IDENTITY_DIGEST_MISMATCH", "W23 v2 request identity digest does not match its canonical source/surface contract")
    return dict(request)


__all__ = [
    "BASIS_ALGORITHM_ID", "BASIS_PROFILE_ID", "BASIS_SCHEMA_ID", "BASIS_SCHEMA_VERSION",
    "BASIS_REQUEST_SCHEMA", "NATIVE_DEFINITION_SCHEMA", "PHASOR_CONVENTION",
    "REQUEST_BINDING_KEYS", "REQUEST_KEYS", "basis_request_binding", "validate_basis_request",
]
