"""Native, selector-bound implementation of W23 mode overlap.

The array kernel in :mod:`_mode_overlap` is deliberately software-only.  This
adapter accepts native COMSOL dataset/solution identities, model-named
integration surfaces, and bare field-variable names.  It asks COMSOL's own
``IntLine``/``IntSurface`` numerical features to integrate the complex
Poynting and reciprocity expressions; it never accepts caller field arrays,
coordinates, or quadrature weights.

The adapter returns raw engine integrals and their bindings.  It does not
claim the optical model, reciprocal-mode assumptions, mesh convergence, or
physical acceptance have been independently validated.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ._g2_contract import ExecutionContractError


DEFINITION_SCHEMA_VERSION = "1.0.0"
NATIVE_RESULT_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap:native-result:1.0.0"
PHASOR_CONVENTION_ID = "full_physical_complex_phasor_including_reconstructed_envelope_phase"
_PHASOR_SIGNS = ("exp(-i omega t)", "exp(+i omega t)")
_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_FREQUENCY_NAMES = frozenset({"f", "freq", "frequency"})


_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["electric", "magnetic"],
    "properties": {
        "electric": {
            "type": "object", "additionalProperties": False,
            "required": ["x", "y", "z"],
            "properties": {axis: {"$ref": "#/$defs/variable"} for axis in "xyz"},
        },
        "magnetic": {
            "type": "object", "additionalProperties": False,
            "required": ["x", "y", "z"],
            "properties": {axis: {"$ref": "#/$defs/variable"} for axis in "xyz"},
        },
    },
}
_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["dataset_id", "solution_id", "outer_index", "inner_index"],
    "properties": {
        "dataset_id": {"$ref": "#/$defs/tag"},
        "solution_id": {"$ref": "#/$defs/tag"},
        "outer_index": {"type": "integer", "minimum": 1},
        "inner_index": {"type": "integer", "minimum": 1},
    },
}
_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["component", "geometry", "tag"],
    "properties": {
        "component": {"$ref": "#/$defs/tag"},
        "geometry": {"$ref": "#/$defs/tag"},
        "tag": {"$ref": "#/$defs/tag"},
    },
    "description": "A named native COMSOL geometry selection; its entity ids and dimension are read back before integration.",
}

NATIVE_DEFINITION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:comsol-mcp:result.mode_overlap:native-definition:1.0.0",
    "title": "result.mode_overlap native COMSOL definition v1.0.0",
    "type": "object", "additionalProperties": False,
    "required": [
        "schema_version", "phasor_convention", "output_surface", "signal",
        "reference_mode", "incident_reference", "power_floor",
    ],
    "properties": {
        "schema_version": {"const": DEFINITION_SCHEMA_VERSION},
        "phasor_convention": {"$ref": "#/$defs/phasorConvention"},
        "output_surface": {"$ref": "#/$defs/surface"},
        "signal": {"$ref": "#/$defs/signal"},
        "reference_mode": {"$ref": "#/$defs/referenceMode"},
        "incident_reference": {"$ref": "#/$defs/incidentReference"},
        "power_floor": {"$ref": "#/$defs/powerFloor"},
        "eta_mode_upper_tolerance": {"type": "number", "minimum": 0},
        "capture": {
            "type": "object", "additionalProperties": False,
            "required": ["aperture_id", "plane_id", "selection", "normal_sign", "incident_reference_id"],
            "properties": {
                "aperture_id": {"$ref": "#/$defs/nonEmpty"},
                "plane_id": {"$ref": "#/$defs/nonEmpty"},
                "selection": {"$ref": "#/$defs/selection"},
                "normal_sign": {"enum": [-1, 1]},
                "incident_reference_id": {"$ref": "#/$defs/nonEmpty"},
            },
            "description": "Optional separately named receiving aperture on the output plane; its signed native signal flux is divided by the already computed, explicitly identified incident-reference power.",
        },
    },
    "$defs": {
        "tag": {"type": "string", "minLength": 1, "pattern": _TAG.pattern},
        "variable": {
            "type": "string", "minLength": 1, "pattern": _VARIABLE.pattern,
            "description": "One COMSOL variable identifier, with optional dotted namespace; expressions and caller arrays are forbidden.",
        },
        "phasorConvention": {
            "type": "object", "additionalProperties": False,
            "required": ["time_dependence", "complex_field_representation"],
            "properties": {
                "time_dependence": {"enum": list(_PHASOR_SIGNS)},
                "complex_field_representation": {"const": PHASOR_CONVENTION_ID},
            },
        },
        "source": _SOURCE_SCHEMA,
        "fields": _FIELD_SCHEMA,
        "selection": _SELECTION_SCHEMA,
        "surface": {
            "type": "object", "additionalProperties": False,
            "required": ["plane_id", "selection", "normal_sign"],
            "properties": {
                "plane_id": {"$ref": "#/$defs/nonEmpty"},
                "selection": {"$ref": "#/$defs/selection"},
                "normal_sign": {"enum": [-1, 1], "description": "Sign applied to COMSOL's native outward boundary normal."},
            },
        },
        "signal": {
            "type": "object", "additionalProperties": False,
            "required": ["field_id", "source", "fields"],
            "properties": {
                "field_id": {"$ref": "#/$defs/nonEmpty"},
                "source": {"$ref": "#/$defs/source"},
                "fields": {"$ref": "#/$defs/fields"},
            },
        },
        "referenceMode": {
            "type": "object", "additionalProperties": False,
            "required": ["mode_id", "mode_axis_parameter", "source", "fields"],
            "properties": {
                "mode_id": {"$ref": "#/$defs/nonEmpty"},
                "mode_axis_parameter": {"$ref": "#/$defs/tag"},
                "source": {"$ref": "#/$defs/source"},
                "fields": {"$ref": "#/$defs/fields"},
            },
        },
        "incidentReference": {
            "type": "object", "additionalProperties": False,
            "required": ["reference_id", "input_plane_id", "selection", "normal_sign", "source", "fields"],
            "properties": {
                "reference_id": {"$ref": "#/$defs/nonEmpty"},
                "input_plane_id": {"$ref": "#/$defs/nonEmpty"},
                "selection": {"$ref": "#/$defs/selection"},
                "normal_sign": {"enum": [-1, 1]},
                "source": {"$ref": "#/$defs/source"},
                "fields": {"$ref": "#/$defs/fields"},
            },
        },
        "powerFloor": {
            "type": "object", "additionalProperties": False,
            "required": ["value", "unit"],
            "properties": {
                "value": {"type": "number", "exclusiveMinimum": 0},
                "unit": {"enum": ["W", "W/m"]},
            },
        },
        "nonEmpty": {"type": "string", "minLength": 1},
    },
}

NATIVE_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": NATIVE_RESULT_SCHEMA_ID,
    "title": "result.mode_overlap native result v1.0.0",
    "type": "object", "additionalProperties": False,
    "required": [
        "schema_version", "operation_id", "status", "result_status", "algorithm_id", "phasor_convention",
        "evidence", "identity", "surface", "incident_surface", "integrals", "normalization_power_floor",
        "overlap_amplitude", "normalized_overlap", "projected_mode_power", "eta_mode",
        "eta_mode_range_diagnostic", "eta_capture",
    ],
    "properties": {
        "schema_version": {"const": "1.0.0"},
        "operation_id": {"const": "result.mode_overlap"},
        "status": {"const": "SUCCEEDED"},
        "result_status": {"const": "COMPUTED_NATIVE_INTEGRALS"},
        "algorithm_id": {"const": "reciprocal_lossless_forward_planar_v1"},
        "phasor_convention": {"type": "object"},
        "evidence": {"type": "object"},
        "identity": {"type": "object"},
        "surface": {"type": "object"},
        "incident_surface": {"type": "object"},
        "integrals": {"type": "object"},
        "normalization_power_floor": {"type": "object"},
        "overlap_amplitude": {"type": "object"},
        "normalized_overlap": {"type": "number", "minimum": 0},
        "projected_mode_power": {"type": "object"},
        "eta_mode": {"type": "number", "minimum": 0},
        "eta_mode_range_diagnostic": {"type": "object"},
        "eta_capture": {"type": "object"},
    },
}


def validate_definition_shape(value: Any) -> dict[str, Any]:
    """Strictly validate the public native definition without touching COMSOL."""
    obj = _mapping(value, "definition")
    _keys(obj, (
        "schema_version", "phasor_convention", "output_surface", "signal",
        "reference_mode", "incident_reference", "power_floor",
    ), (
        "schema_version", "phasor_convention", "output_surface", "signal",
        "reference_mode", "incident_reference", "power_floor", "eta_mode_upper_tolerance", "capture",
    ), "definition")
    if obj["schema_version"] != DEFINITION_SCHEMA_VERSION:
        _fail("SCHEMA_VERSION_UNSUPPORTED", f"unsupported native definition schema {obj['schema_version']!r}")
    convention = _mapping(obj["phasor_convention"], "definition.phasor_convention")
    _keys(convention, ("time_dependence", "complex_field_representation"),
          ("time_dependence", "complex_field_representation"), "definition.phasor_convention")
    if convention["time_dependence"] not in _PHASOR_SIGNS:
        _fail("PHASOR_CONVENTION_MISMATCH", "unsupported phasor time dependence")
    if convention["complex_field_representation"] != PHASOR_CONVENTION_ID:
        _fail("PHASOR_CONVENTION_MISMATCH", "full reconstructed complex phasor E/H values are required")

    _validate_surface(obj["output_surface"], "definition.output_surface")
    _validate_signal(obj["signal"], "definition.signal")
    _validate_reference_mode(obj["reference_mode"], "definition.reference_mode")
    _validate_incident(obj["incident_reference"], "definition.incident_reference")
    floor = _mapping(obj["power_floor"], "definition.power_floor")
    _keys(floor, ("value", "unit"), ("value", "unit"), "definition.power_floor")
    _positive_number(floor["value"], "definition.power_floor.value")
    if floor["unit"] not in {"W", "W/m"}:
        _fail("UNIT_MISMATCH", "definition.power_floor.unit must be W or W/m")
    if "eta_mode_upper_tolerance" in obj:
        _nonnegative_number(obj["eta_mode_upper_tolerance"], "definition.eta_mode_upper_tolerance")
    if "capture" in obj:
        capture = _mapping(obj["capture"], "definition.capture")
        capture_keys = ("aperture_id", "plane_id", "selection", "normal_sign", "incident_reference_id")
        _keys(capture, capture_keys, capture_keys, "definition.capture")
        _text(capture["aperture_id"], "definition.capture.aperture_id")
        _text(capture["plane_id"], "definition.capture.plane_id")
        _validate_selection(capture["selection"], "definition.capture.selection")
        if isinstance(capture["normal_sign"], bool) or capture["normal_sign"] not in (-1, 1):
            _fail("INVALID_REQUEST", "definition.capture.normal_sign must be -1 or 1")
        _text(capture["incident_reference_id"], "definition.capture.incident_reference_id")
        if capture["plane_id"] != obj["output_surface"]["plane_id"]:
            _fail("PLANE_MISMATCH", "definition.capture.plane_id must equal output_surface.plane_id")
        if capture["normal_sign"] != obj["output_surface"]["normal_sign"]:
            _fail("INVALID_NORMAL", "definition.capture.normal_sign must use the same oriented +x plane convention as output_surface")
        if capture["incident_reference_id"] != obj["incident_reference"]["reference_id"]:
            _fail("SOURCE_MISMATCH", "definition.capture.incident_reference_id must name the explicit incident_reference")
    return dict(obj)


def _fail(code: str, message: str, *, details: Mapping[str, Any] | None = None,
          stage: str = "validation") -> None:
    raise ExecutionContractError(code, message, details=details, stage=stage)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail("INVALID_REQUEST", f"{label} must be an object with string keys")
    return value


def _keys(value: Mapping[str, Any], required: Sequence[str], allowed: Sequence[str], label: str) -> None:
    missing = sorted(set(required) - set(value))
    unknown = sorted(set(value) - set(allowed))
    if missing:
        _fail("INVALID_REQUEST", f"{label} is missing required fields: {missing}")
    if unknown:
        _fail("INVALID_REQUEST", f"{label} has unsupported fields: {unknown}")


def _text(value: Any, label: str, *, tag: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("INVALID_REQUEST", f"{label} must be a non-empty string")
    result = value.strip()
    if tag and not _TAG.fullmatch(result):
        _fail("INVALID_REQUEST", f"{label} is not a supported COMSOL tag")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("INVALID_REQUEST", f"{label} must be a positive integer")
    return value


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        _fail("INVALID_REQUEST", f"{label} must be a positive finite number")
    return float(value)


def _nonnegative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        _fail("INVALID_REQUEST", f"{label} must be a non-negative finite number")
    return float(value)


def _validate_source(value: Any, label: str) -> dict[str, Any]:
    source = _mapping(value, label)
    _keys(source, ("dataset_id", "solution_id", "outer_index", "inner_index"),
          ("dataset_id", "solution_id", "outer_index", "inner_index"), label)
    return {
        "dataset_id": _text(source["dataset_id"], f"{label}.dataset_id", tag=True),
        "solution_id": _text(source["solution_id"], f"{label}.solution_id", tag=True),
        "outer_index": _positive_int(source["outer_index"], f"{label}.outer_index"),
        "inner_index": _positive_int(source["inner_index"], f"{label}.inner_index"),
    }


def _validate_fields(value: Any, label: str) -> dict[str, dict[str, str]]:
    fields = _mapping(value, label)
    _keys(fields, ("electric", "magnetic"), ("electric", "magnetic"), label)
    result: dict[str, dict[str, str]] = {}
    for field_kind in ("electric", "magnetic"):
        components = _mapping(fields[field_kind], f"{label}.{field_kind}")
        _keys(components, ("x", "y", "z"), ("x", "y", "z"), f"{label}.{field_kind}")
        result[field_kind] = {}
        for axis in "xyz":
            variable = _text(components[axis], f"{label}.{field_kind}.{axis}")
            if not _VARIABLE.fullmatch(variable):
                _fail("INVALID_REQUEST", f"{label}.{field_kind}.{axis} must be a bare COMSOL variable identifier")
            result[field_kind][axis] = variable
    return result


def _validate_selection(value: Any, label: str) -> dict[str, str]:
    selection = _mapping(value, label)
    _keys(selection, ("component", "geometry", "tag"), ("component", "geometry", "tag"), label)
    return {name: _text(selection[name], f"{label}.{name}", tag=True)
            for name in ("component", "geometry", "tag")}


def _validate_surface(value: Any, label: str) -> dict[str, Any]:
    surface = _mapping(value, label)
    _keys(surface, ("plane_id", "selection", "normal_sign"),
          ("plane_id", "selection", "normal_sign"), label)
    sign = surface["normal_sign"]
    if isinstance(sign, bool) or not isinstance(sign, int) or sign not in (-1, 1):
        _fail("INVALID_REQUEST", f"{label}.normal_sign must be -1 or 1")
    return {"plane_id": _text(surface["plane_id"], f"{label}.plane_id"),
            "selection": _validate_selection(surface["selection"], f"{label}.selection"),
            "normal_sign": int(sign)}


def _validate_signal(value: Any, label: str) -> dict[str, Any]:
    signal = _mapping(value, label)
    _keys(signal, ("field_id", "source", "fields"), ("field_id", "source", "fields"), label)
    return {"field_id": _text(signal["field_id"], f"{label}.field_id"),
            "source": _validate_source(signal["source"], f"{label}.source"),
            "fields": _validate_fields(signal["fields"], f"{label}.fields")}


def _validate_reference_mode(value: Any, label: str) -> dict[str, Any]:
    mode = _mapping(value, label)
    _keys(mode, ("mode_id", "mode_axis_parameter", "source", "fields"),
          ("mode_id", "mode_axis_parameter", "source", "fields"), label)
    return {"mode_id": _text(mode["mode_id"], f"{label}.mode_id"),
            "mode_axis_parameter": _text(mode["mode_axis_parameter"], f"{label}.mode_axis_parameter", tag=True),
            "source": _validate_source(mode["source"], f"{label}.source"),
            "fields": _validate_fields(mode["fields"], f"{label}.fields")}


def _validate_incident(value: Any, label: str) -> dict[str, Any]:
    incident = _mapping(value, label)
    required = ("reference_id", "input_plane_id", "selection", "normal_sign", "source", "fields")
    _keys(incident, required, required, label)
    sign = incident["normal_sign"]
    if isinstance(sign, bool) or not isinstance(sign, int) or sign not in (-1, 1):
        _fail("INVALID_REQUEST", f"{label}.normal_sign must be -1 or 1")
    return {
        "reference_id": _text(incident["reference_id"], f"{label}.reference_id"),
        "input_plane_id": _text(incident["input_plane_id"], f"{label}.input_plane_id"),
        "selection": _validate_selection(incident["selection"], f"{label}.selection"),
        "normal_sign": int(sign),
        "source": _validate_source(incident["source"], f"{label}.source"),
        "fields": _validate_fields(incident["fields"], f"{label}.fields"),
    }


def validate_request_shape(arguments: Mapping[str, Any]) -> None:
    """Schema-level request hook used by the common registry before dispatch."""
    _mapping(arguments, "arguments")
    if "definition" not in arguments:
        _fail("INVALID_REQUEST", "definition is required")
    validate_definition_shape(arguments["definition"])


def _solution_info(model: Any, source: Mapping[str, Any], *, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from ._g3_results import _result_solution_binding, _resolve_dataset_binding

    binding = _resolve_dataset_binding(model, str(source["dataset_id"]), requested_solution=str(source["solution_id"]))
    if binding.get("binding_complete") is not True or binding.get("solution") != source["solution_id"]:
        _fail("DATASET_BINDING_INCOMPLETE", f"{label} dataset does not resolve to its requested native solution")
    solution = _result_solution_binding(model, str(source["solution_id"]))
    if not isinstance(solution, Mapping) or solution.get("pair_mapping_complete") is not True:
        _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{label} requires complete native SolutionInfo pair metadata")
    pair = next((row for row in solution.get("solnum_pairs", [])
                 if isinstance(row, Mapping)
                 and row.get("outer") == source["outer_index"]
                 and row.get("inner") == source["inner_index"]), None)
    if pair is None:
        _fail("SOLUTION_INDEX_NOT_FOUND", f"{label} requested outer/inner pair is absent from native SolutionInfo")
    key = (int(source["outer_index"]), int(source["inner_index"]))
    names = list((solution.get("parameter_names_by_pair") or {}).get(key) or [])
    values = list((solution.get("parameter_values_by_pair") or {}).get(key) or [])
    units = list((solution.get("parameter_units_by_pair") or {}).get(key) or [])
    if len(names) != len(values) or len(names) != len(units):
        _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{label} selected parameter metadata is incomplete")
    if isinstance(pair.get("solnum"), bool) or not isinstance(pair.get("solnum"), int):
        _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{label} native solnum mapping is unavailable")
    frequency = _frequency_signature(names, values, units, label=label)
    return dict(binding), {
        "outer_index": int(source["outer_index"]),
        "inner_index": int(source["inner_index"]),
        "solnum": int(pair["solnum"]),
        "frequency": frequency,
        "parameter_names": [str(name) for name in names],
        "parameter_values": list(values),
        "parameter_units": list(units),
        "parameters_by_pair": dict(zip((str(name) for name in names), zip(values, units))),
    }


def _frequency_signature(names: Sequence[Any], values: Sequence[Any], units: Sequence[Any], *, label: str) -> dict[str, Any]:
    matches = [i for i, name in enumerate(names) if str(name).strip().lower() in _FREQUENCY_NAMES]
    if len(matches) != 1:
        _fail("FREQUENCY_METADATA_UNAVAILABLE", f"{label} must expose one native frequency parameter in SolutionInfo")
    index = matches[0]
    raw_value, raw_unit = values[index], units[index]
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)) or not math.isfinite(float(raw_value)):
        _fail("FREQUENCY_METADATA_UNAVAILABLE", f"{label} native frequency value is not finite numeric data")
    unit = str(raw_unit or "").strip()
    scale = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}.get(unit)
    if scale is None:
        _fail("FREQUENCY_METADATA_UNAVAILABLE", f"{label} frequency unit {unit!r} is not supported for exact comparison")
    hz = float(raw_value) * scale
    if not math.isfinite(hz) or hz <= 0:
        _fail("FREQUENCY_METADATA_UNAVAILABLE", f"{label} native frequency must be positive")
    return {"parameter": str(names[index]), "value": float(raw_value), "unit": unit, "frequency_hz": hz}


def _same_frequency(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return math.isclose(float(left["frequency_hz"]), float(right["frequency_hz"]), rel_tol=1e-12, abs_tol=0.0)


def _source_selection(worker: Any, model_tag: str, source_binding: Mapping[str, Any], selection: Mapping[str, str], *, label: str) -> dict[str, Any]:
    from ._g3_common import resolve_selection_entities

    if source_binding.get("component") != selection["component"] or source_binding.get("geometry") != selection["geometry"]:
        _fail("GEOMETRY_FRAME_MISMATCH", f"{label} selection and solution dataset do not share the same native component/geometry")
    resolved = resolve_selection_entities(worker, model_tag, {
        "kind": "named", "component": selection["component"],
        "geometry": selection["geometry"], "tag": selection["tag"],
    })
    entities = resolved.get("entities")
    dimension = resolved.get("entity_dimension")
    if not isinstance(entities, (list, tuple)) or not entities:
        _fail("EMPTY_INTEGRATION_SURFACE", f"{label} native named selection has no resolved entities")
    if isinstance(dimension, bool) or not isinstance(dimension, int):
        _fail("SELECTION_DIMENSION_UNAVAILABLE", f"{label} named selection dimension was not read back from COMSOL")
    if resolved.get("component") != selection["component"] or resolved.get("geometry") != selection["geometry"] or resolved.get("selection_tag") != selection["tag"]:
        _fail("SELECTION_READBACK_MISMATCH", f"{label} named selection identity did not match the request")
    return {
        "component": selection["component"], "geometry": selection["geometry"],
        "selection_tag": selection["tag"], "entity_dimension": dimension,
        "entity_ids": [int(entity) for entity in entities],
        "entity_count": len(entities), "readback_source": resolved.get("source"),
    }


def _space_dimension(model: Any, dataset_tag: str, dataset_binding: Mapping[str, Any], *, label: str) -> int:
    from ._g3_results import _call, _coordinate_context

    result_list = _call(model, "result")
    datasets = _call(result_list, "dataset")
    dataset = _call(datasets, "get", dataset_tag)
    context = _coordinate_context(model, dataset, [], dataset_binding=dataset_binding)
    dimension = context.get("space_dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension not in (2, 3):
        _fail("GEOMETRY_DIMENSION_UNAVAILABLE", f"{label} source geometry space dimension is not verified as 2D or 3D")
    return dimension


def _expression_cross_dot(electric: Mapping[str, str], magnetic: Mapping[str, str], *, dimension: int,
                          conjugate_magnetic: bool, conjugate_electric: bool = False) -> str:
    def e(axis: str) -> str:
        value = electric[axis]
        return f"conj({value})" if conjugate_electric else value

    def h(axis: str) -> str:
        value = magnetic[axis]
        return f"conj({value})" if conjugate_magnetic else value

    terms = [f"({e('y')}*{h('z')}-{e('z')}*{h('y')})*nx",
             f"({e('z')}*{h('x')}-{e('x')}*{h('z')})*ny"]
    if dimension == 3:
        terms.append(f"({e('x')}*{h('y')}-{e('y')}*{h('x')})*nz")
    return "(" + "+".join(terms) + ")"


def _withsol(expression: str, source: Mapping[str, Any], mode_axis: str, mode_index: int,
             mode_native: Mapping[str, Any]) -> str:
    # COMSOL's documented withsol/setind form selects an exact eigenmode from
    # the referenced solver sequence.  Other selected parameter values are
    # replayed from native SolutionInfo as setval arguments so cross-solutions
    # cannot silently use a different frequency or outer sweep value.
    args = [f"setind({mode_axis},{mode_index})"]
    params = mode_native.get("parameters_by_pair", {})
    for name, row in params.items():
        if not _TAG.fullmatch(str(name)):
            _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"native parameter name {name!r} cannot be bound safely in withsol")
        if str(name).strip().lower() == mode_axis.lower():
            continue
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"mode parameter {name!r} has no native value/unit pair")
        value, unit = row
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"mode parameter {name!r} is not replayable as a numeric value")
        unit_text = str(unit or "").strip()
        if unit_text and not re.fullmatch(r"[A-Za-z0-9_*/^.-]+", unit_text):
            _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"mode parameter {name!r} has an unsupported unit spelling")
        literal = format(float(value), ".17g") + (f"[{unit_text}]" if unit_text else "")
        args.append(f"setval({name},{literal})")
    return f"withsol('{source['solution_id']}',{expression},{','.join(args)})"


def _native_integral(worker: Any, model_tag: str, source: Mapping[str, Any], selection: Mapping[str, Any],
                     selection_readback: Mapping[str, Any], expression: str, *, label: str) -> dict[str, Any]:
    from ._g3_results import result_evaluate

    if (selection_readback.get("component") != selection.get("component")
            or selection_readback.get("geometry") != selection.get("geometry")
            or selection_readback.get("selection_tag") != selection.get("tag")):
        _fail("SELECTION_READBACK_MISMATCH", f"{label} native selection identity is not bound to the verified readback")
    entity_dimension = selection_readback.get("entity_dimension")
    if isinstance(entity_dimension, bool) or not isinstance(entity_dimension, int) or entity_dimension not in (1, 2):
        _fail("SELECTION_DIMENSION_UNAVAILABLE", f"{label} native selection dimension is not verified")

    result = result_evaluate(worker, model_tag, {
        "spec": {
            "expressions": [expression],
            # MeasureSpec reads this top-level selector to choose IntLine or
            # IntSurface; it must come from COMSOL selection readback, never
            # from an extra caller-controlled definition field.
            "entity_dim": entity_dimension,
            "solution": {
                "dataset": source["dataset_id"], "solution": source["solution_id"],
                "outer": source["outer_index"], "inner": source["inner_index"],
            },
            "aggregate": "integral", "complex_mode": "preserve", "storage": "inline",
            "selection": {
                "kind": "named", "component": selection["component"],
                "geometry": selection["geometry"], "tag": selection["tag"],
                "entity_dimension": entity_dimension,
            },
        },
    })
    if not isinstance(result, Mapping):
        _fail("NATIVE_INTEGRAL_FAILED", f"{label} COMSOL numerical integration returned no structured result",
              stage="post_dispatch")
    cleanup = result.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("cleanup_failed") is True or cleanup.get("removed") is not True:
        _fail("EXECUTION_STATE_UNKNOWN", f"{label} temporary numerical feature removal was not verified",
              details={"cleanup": dict(cleanup) if isinstance(cleanup, Mapping) else cleanup,
                       "native_status": result.get("status")}, stage="post_dispatch")
    status = result.get("status")
    if not isinstance(status, Mapping):
        _fail("NATIVE_INTEGRAL_FAILED", f"{label} COMSOL numerical integration omitted status readback",
              details={"cleanup": dict(cleanup)}, stage="post_dispatch")
    if status.get("execution_state_unknown") is True or status.get("cleanup_failed") is True:
        _fail("EXECUTION_STATE_UNKNOWN", f"{label} native integration reported an unresolved engine state",
              details={"cleanup": dict(cleanup), "native_status": dict(status)}, stage="post_dispatch")
    if status.get("ok") is not True:
        engine_error = status.get("engine_error")
        if isinstance(engine_error, Mapping):
            cause_code = engine_error.get("code")
            cause_message = engine_error.get("message")
            _fail(str(cause_code) if isinstance(cause_code, str) and cause_code else "NATIVE_INTEGRAL_FAILED",
                  str(cause_message) if isinstance(cause_message, str) and cause_message else
                  f"{label} COMSOL numerical integration failed",
                  details={"cleanup": dict(cleanup), "native_status": dict(status),
                           "native_engine_error": dict(engine_error)}, stage="post_dispatch")
        _fail("NATIVE_INTEGRAL_FAILED", f"{label} COMSOL numerical integration did not report a clean result",
              details={"cleanup": dict(cleanup), "native_status": dict(status)}, stage="post_dispatch")
    expected_type = "IntLine" if entity_dimension == 1 else "IntSurface"
    if cleanup.get("type_id") != expected_type:
        _fail("NATIVE_READBACK_UNVERIFIED", f"{label} numerical feature type does not match the verified integration dimension",
              details={"expected_type": expected_type, "cleanup": dict(cleanup)}, stage="post_dispatch")
    if result.get("dataset") != source.get("dataset_id") or result.get("solution") != source.get("solution_id"):
        _fail("SOLUTION_BINDING_MISMATCH", f"{label} numerical result did not read back the requested dataset and solution",
              details={"requested_dataset": source.get("dataset_id"), "requested_solution": source.get("solution_id"),
                       "actual_dataset": result.get("dataset"), "actual_solution": result.get("solution")},
              stage="post_dispatch")
    axes = result.get("solution_axes")
    if (not isinstance(axes, Mapping) or axes.get("pair_mapping_complete") is not True
            or source.get("outer_index") not in axes.get("outer", [])
            or source.get("inner_index") not in axes.get("inner", [])):
        _fail("SOLUTION_AXIS_METADATA_UNAVAILABLE", f"{label} numerical result did not verify the requested outer/inner solution axes",
              details={"requested_outer": source.get("outer_index"), "requested_inner": source.get("inner_index"),
                       "solution_axes": dict(axes) if isinstance(axes, Mapping) else axes},
              stage="post_dispatch")
    field_array = result.get("field_array")
    if not isinstance(field_array, Mapping):
        _fail("NATIVE_READBACK_UNVERIFIED", f"{label} native result did not carry a typed FieldArray binding",
              stage="post_dispatch")
    units = field_array.get("units")
    expression_units = units.get("expression") if isinstance(units, Mapping) else None
    actual_unit = expression_units.get(expression) if isinstance(expression_units, Mapping) else None
    if not isinstance(actual_unit, str) or not actual_unit.strip():
        _fail("UNIT_READBACK_UNAVAILABLE", f"{label} result expression unit was not read back",
              stage="post_dispatch")
    raw = _single_native_value(result.get("values"), is_complex=result.get("is_complex"), label=label)
    return {
        "value": raw, "is_complex": bool(result.get("is_complex")),
        "unit": actual_unit.strip(), "expression": expression,
        "dataset": result.get("dataset"), "solution": result.get("solution"),
        "selection_measure": result.get("selection_measure"),
        "selection_measure_source": result.get("selection_measure_source"),
        "feature_type": cleanup.get("type_id"), "cleanup": dict(cleanup),
        "dataset_binding": {"dataset": result.get("dataset"), "solution": result.get("solution")},
        "solution_axes": dict(axes),
        "coordinate_frame": "native model geometry",
    }


def _single_native_value(value: Any, *, is_complex: Any, label: str) -> complex:
    leaves: list[Any] = []

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            if set(item) == {"real", "imag"}:
                leaves.append(item)
            else:
                for child in item.values():
                    walk(child)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                walk(child)
        else:
            leaves.append(item)

    walk(value)
    if len(leaves) != 1 or type(is_complex) is not bool:
        _fail("FIELD_ARRAY_SHAPE_MISMATCH", f"{label} must resolve to exactly one native scalar and a complex-status readback")
    raw = leaves[0]
    if isinstance(raw, Mapping):
        if is_complex is not True:
            _fail("COMPLEX_DATA_ERROR", f"{label} returned complex-shaped data without a native complex flag")
        real, imag = raw.get("real"), raw.get("imag")
    else:
        if is_complex is True:
            _fail("COMPLEX_DATA_ERROR", f"{label} native complex result omitted its imaginary part")
        real, imag = raw, 0.0
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number))
           for number in (real, imag)):
        _fail("NATIVE_RESULT_INVALID", f"{label} contains non-finite or non-numeric native data")
    return complex(float(real), float(imag))


def _power_value(record: Mapping[str, Any], *, expected_unit: str, floor: float, label: str) -> tuple[float, dict[str, Any]]:
    if record.get("unit") != expected_unit:
        _fail("UNIT_MISMATCH", f"{label} native integrated unit {record.get('unit')!r} does not equal {expected_unit!r}")
    raw = record["value"]
    tolerance = 64.0 * math.ulp(max(abs(raw.real), floor))
    if abs(raw.imag) > tolerance:
        _fail("COMPLEX_POWER_INVALID", f"{label} signed time-average power has nonzero imaginary residual {raw.imag!r}")
    if raw.real <= floor:
        _fail("NON_POSITIVE_FORWARD_POWER", f"{label} forward power {raw.real!r} does not exceed power floor {floor!r}")
    return raw.real, {"real": raw.real, "imag": raw.imag, "unit": expected_unit,
                      "native_complex_flag": record["is_complex"], "imaginary_tolerance": tolerance}


def _signed_flux_value(record: Mapping[str, Any], *, expected_unit: str, floor: float,
                       label: str) -> tuple[float, dict[str, Any]]:
    """Read signed aperture flux without imposing positivity or clipping it."""
    if record.get("unit") != expected_unit:
        _fail("UNIT_MISMATCH", f"{label} native integrated unit {record.get('unit')!r} does not equal {expected_unit!r}")
    raw = record["value"]
    tolerance = 64.0 * math.ulp(max(abs(raw.real), floor))
    if abs(raw.imag) > tolerance:
        _fail("COMPLEX_POWER_INVALID", f"{label} signed time-average flux has nonzero imaginary residual {raw.imag!r}")
    return raw.real, {"real": raw.real, "imag": raw.imag, "unit": expected_unit,
                      "native_complex_flag": record["is_complex"], "imaginary_tolerance": tolerance}


def _integral_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    """Convert the internal complex scalar into a JSON-safe evidence pair."""
    evidence = dict(record)
    value = evidence.get("value")
    if isinstance(value, complex):
        evidence["value"] = {"real": value.real, "imag": value.imag}
    return evidence


def result_mode_overlap(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Compute native signed power and reciprocal mode overlap on named surfaces."""
    args = _mapping(arguments, "arguments")
    _keys(args, ("definition",), ("definition",), "arguments")
    definition = validate_definition_shape(args.get("definition"))
    output_surface = definition["output_surface"]
    signal = definition["signal"]
    mode = definition["reference_mode"]
    incident = definition["incident_reference"]
    capture = definition.get("capture")

    from ._g3_common import bound_model
    model = bound_model(worker, model_tag)
    sources = {
        "signal": signal["source"],
        "reference_mode": mode["source"],
        "incident_reference": incident["source"],
    }
    bindings: dict[str, dict[str, Any]] = {}
    axes: dict[str, dict[str, Any]] = {}
    for label, source in sources.items():
        bindings[label], axes[label] = _solution_info(model, source, label=label)
    frequency = axes["signal"]["frequency"]
    for label in ("reference_mode", "incident_reference"):
        if not _same_frequency(frequency, axes[label]["frequency"]):
            _fail("FREQUENCY_MISMATCH", f"{label} native solution frequency differs from signal frequency")
    if axes["signal"]["outer_index"] != axes["reference_mode"]["outer_index"]:
        _fail("SOLUTION_INDEX_MISMATCH", "signal and reference mode must use the same native outer index")
    if axes["signal"]["outer_index"] != axes["incident_reference"]["outer_index"]:
        _fail("SOLUTION_INDEX_MISMATCH", "signal and incident reference must use the same native outer index")
    if axes["signal"]["inner_index"] != axes["incident_reference"]["inner_index"]:
        _fail("SOLUTION_INDEX_MISMATCH", "signal and incident reference must use the same native inner solution index")
    mode_axis = mode["mode_axis_parameter"]
    if mode["source"]["inner_index"] != axes["reference_mode"]["inner_index"]:
        _fail("MODE_IDENTITY_MISMATCH", "reference mode index does not match the bound native inner solution")
    if mode_axis.lower() not in {str(name).lower() for name in axes["reference_mode"]["parameter_names"]}:
        _fail("MODE_IDENTITY_MISMATCH", f"reference mode axis {mode_axis!r} is absent from native SolutionInfo")

    surface_info: dict[str, dict[str, Any]] = {}
    for label, selection, binding in (
        ("output_surface", output_surface["selection"], bindings["signal"]),
        ("reference_mode_surface", output_surface["selection"], bindings["reference_mode"]),
        ("incident_surface", incident["selection"], bindings["incident_reference"]),
    ):
        surface_info[label] = _source_selection(worker, model_tag, binding, selection, label=label)
    if capture is not None:
        surface_info["capture_surface"] = _source_selection(
            worker, model_tag, bindings["signal"], capture["selection"], label="capture_surface")
    dimensions: dict[str, int] = {}
    for label, source in sources.items():
        dimensions[label] = _space_dimension(model, source["dataset_id"], bindings[label], label=label)
    geom_dimensions = set(dimensions.values())
    if len(geom_dimensions) != 1:
        _fail("GEOMETRY_FRAME_MISMATCH", "all native sources must bind to one shared 2D/3D geometry frame")
    geometry_dimension = next(iter(geom_dimensions))
    expected_entity_dim = geometry_dimension - 1
    for label, selection_readback in surface_info.items():
        if selection_readback["entity_dimension"] != expected_entity_dim:
            _fail("SELECTION_DIMENSION_MISMATCH", f"{label} entity dimension must be {expected_entity_dim} for the native {geometry_dimension}D transverse plane")
    power_unit = "W" if geometry_dimension == 3 else "W/m"
    expected_measure = "area" if geometry_dimension == 3 else "line_per_unit_depth"
    floor_def = definition["power_floor"]
    if floor_def["unit"] != power_unit:
        _fail("UNIT_MISMATCH", f"power_floor.unit must be {power_unit!r} for the native {geometry_dimension}D geometry")
    floor = float(floor_def["value"])

    # The reciprocal vector formula is evaluated by COMSOL's own numerical
    # feature.  COMSOL supplies nx/ny/nz from the selected native boundary;
    # normal_sign only records which side of that oriented boundary is inward.
    signal_flux = _expression_cross_dot(signal["fields"]["electric"], signal["fields"]["magnetic"],
                                        dimension=geometry_dimension, conjugate_magnetic=True)
    mode_flux = _expression_cross_dot(mode["fields"]["electric"], mode["fields"]["magnetic"],
                                      dimension=geometry_dimension, conjugate_magnetic=True)
    input_flux = _expression_cross_dot(incident["fields"]["electric"], incident["fields"]["magnetic"],
                                       dimension=geometry_dimension, conjugate_magnetic=True)
    signal_expr = f"0.5*real(({output_surface['normal_sign']})*({signal_flux}))"
    mode_expr = f"0.5*real(({output_surface['normal_sign']})*({mode_flux}))"
    incident_expr = f"0.5*real(({incident['normal_sign']})*({input_flux}))"

    mode_solution = mode["source"]
    mode_axis_index = int(mode_solution["inner_index"])
    mode_e = {axis: _withsol(expr, mode_solution, mode_axis, mode_axis_index, axes["reference_mode"])
              for axis, expr in mode["fields"]["electric"].items()}
    mode_h = {axis: _withsol(expr, mode_solution, mode_axis, mode_axis_index, axes["reference_mode"])
              for axis, expr in mode["fields"]["magnetic"].items()}
    cross_first = _expression_cross_dot(signal["fields"]["electric"], mode_h,
                                        dimension=geometry_dimension, conjugate_magnetic=True)
    cross_second = _expression_cross_dot(mode_e, signal["fields"]["magnetic"],
                                         dimension=geometry_dimension, conjugate_magnetic=False,
                                         conjugate_electric=True)
    overlap_expr = f"({output_surface['normal_sign']})*(({cross_first})+({cross_second}))"

    signal_integral = _native_integral(worker, model_tag, signal["source"], output_surface["selection"],
                                       surface_info["output_surface"], signal_expr, label="signal power")
    mode_integral = _native_integral(worker, model_tag, mode["source"], output_surface["selection"],
                                     surface_info["reference_mode_surface"], mode_expr, label="reference mode power")
    cross_integral = _native_integral(worker, model_tag, signal["source"], output_surface["selection"],
                                      surface_info["output_surface"], overlap_expr, label="reciprocal overlap numerator")
    incident_integral = _native_integral(worker, model_tag, incident["source"], incident["selection"],
                                         surface_info["incident_surface"], incident_expr, label="incident reference power")
    capture_integral = None
    capture_power = None
    capture_power_raw = None
    if capture is not None:
        capture_flux = _expression_cross_dot(signal["fields"]["electric"], signal["fields"]["magnetic"],
                                             dimension=geometry_dimension, conjugate_magnetic=True)
        capture_expr = f"0.5*real(({capture['normal_sign']})*({capture_flux}))"
        capture_integral = _native_integral(worker, model_tag, signal["source"], capture["selection"],
                                            surface_info["capture_surface"], capture_expr,
                                            label="capture aperture signed signal flux")
    signal_power, signal_power_raw = _power_value(signal_integral, expected_unit=power_unit, floor=floor, label="signal")
    mode_power, mode_power_raw = _power_value(mode_integral, expected_unit=power_unit, floor=floor, label="reference mode")
    incident_power, incident_power_raw = _power_value(incident_integral, expected_unit=power_unit, floor=floor, label="incident reference")
    if capture_integral is not None:
        capture_power, capture_power_raw = _signed_flux_value(
            capture_integral, expected_unit=power_unit, floor=floor,
            label="capture aperture signed signal flux")
    if cross_integral.get("unit") != power_unit:
        _fail("UNIT_MISMATCH", f"reciprocal overlap numerator unit {cross_integral.get('unit')!r} does not equal {power_unit!r}")
    if cross_integral.get("is_complex") is not True:
        _fail("COMPLEX_DATA_ERROR", "reciprocal overlap numerator requires native complex readback, including its imaginary component")
    numerator: complex = cross_integral["value"]
    amplitude_denominator = 4.0 * math.sqrt(signal_power) * math.sqrt(mode_power)
    if not math.isfinite(amplitude_denominator) or amplitude_denominator <= 0:
        _fail("NATIVE_RESULT_INVALID", "reciprocal overlap normalization denominator is not positive and finite")
    normalized_overlap = (abs(numerator) / amplitude_denominator) ** 2
    projected_power = normalized_overlap * signal_power
    eta_mode = projected_power / incident_power
    if any(not math.isfinite(number) for number in (numerator.real, numerator.imag, normalized_overlap, projected_power, eta_mode)):
        _fail("NATIVE_RESULT_INVALID", "native mode-overlap outputs contain non-finite values")
    eta_capture = None if capture_power is None else capture_power / incident_power
    if eta_capture is not None and not math.isfinite(eta_capture):
        _fail("NATIVE_RESULT_INVALID", "native capture-aperture ratio is not finite")

    tolerance = definition.get("eta_mode_upper_tolerance")
    range_status = "NOT_ASSESSED" if tolerance is None else (
        "WITHIN_DECLARED_UPPER_BOUND" if eta_mode <= 1.0 + float(tolerance) else "EXCEEDS_DECLARED_UPPER_BOUND"
    )
    result = {
        "schema_version": "1.0.0",
        "operation_id": "result.mode_overlap",
        # Keep the generic status inside the shared outcome vocabulary while
        # retaining a precise domain-specific result state alongside it.
        "status": "SUCCEEDED",
        "result_status": "COMPUTED_NATIVE_INTEGRALS",
        "algorithm_id": "reciprocal_lossless_forward_planar_v1",
        "phasor_convention": dict(definition["phasor_convention"]),
        "evidence": {
            "scope": "NATIVE_COMSOL_NUMERICAL_INTEGRALS",
            "native_status": "ENGINE_READS_COMPLETED",
            "independent_quadrature_benchmark": "NOT_RUN",
            "physical_model_acceptance": "NOT_RUN",
            "caller_field_arrays_accepted": False,
            "field_values_source": "COMSOL IntLine/IntSurface feature expressions",
            "normal_source": "COMSOL native boundary nx/ny/nz multiplied by declared normal_sign",
            "cleanup_verified": all(row.get("cleanup", {}).get("removed") is True for row in
                                     (signal_integral, mode_integral, cross_integral, incident_integral)
                                     + ((capture_integral,) if capture_integral is not None else ())),
        },
        "identity": {
            "signal": {"field_id": signal["field_id"], "source": {**signal["source"], **axes["signal"]}},
            "reference_mode": {"mode_id": mode["mode_id"], "mode_axis_parameter": mode_axis,
                               "source": {**mode["source"], **axes["reference_mode"]}},
            "incident_reference": {"reference_id": incident["reference_id"],
                                   "input_plane_id": incident["input_plane_id"],
                                   "source": {**incident["source"], **axes["incident_reference"]}},
            "shared_geometry": {"component": output_surface["selection"]["component"],
                                "geometry": output_surface["selection"]["geometry"],
                                "space_dimension": geometry_dimension,
                                "frequency_hz": frequency["frequency_hz"]},
        },
        "surface": {
            "plane_id": output_surface["plane_id"], "selection": surface_info["output_surface"],
            "geometry_dimension": geometry_dimension, "integration_measure": expected_measure,
            "power_unit": power_unit, "normal_sign": output_surface["normal_sign"],
            "normal_source": "native geometry outward normal",
            "quadrature_source": "COMSOL IntLine/IntSurface numerical integration",
        },
        "incident_surface": {
            "plane_id": incident["input_plane_id"], "selection": surface_info["incident_surface"],
            "geometry_dimension": geometry_dimension, "integration_measure": expected_measure,
            "normal_sign": incident["normal_sign"],
        },
        "integrals": {
            "signal_power": {**signal_power_raw, "engine": _integral_evidence(signal_integral)},
            "reference_mode_power": {**mode_power_raw, "engine": _integral_evidence(mode_integral)},
            "reciprocal_overlap_numerator": {
                "real": numerator.real, "imag": numerator.imag, "unit": power_unit,
                "native_complex_flag": cross_integral["is_complex"], "engine": _integral_evidence(cross_integral),
            },
            "incident_reference_power": {**incident_power_raw, "reference_id": incident["reference_id"],
                                          "engine": _integral_evidence(incident_integral)},
        },
        "normalization_power_floor": {"value": floor, "unit": power_unit},
        "overlap_amplitude": {"real": numerator.real / amplitude_denominator,
                              "imag": numerator.imag / amplitude_denominator, "unit": "1"},
        "normalized_overlap": normalized_overlap,
        "projected_mode_power": {"value": projected_power, "unit": power_unit,
                                 "region_id": output_surface["plane_id"]},
        "eta_mode": eta_mode,
        "eta_mode_range_diagnostic": {
            "status": range_status, "reference_upper_bound": 1.0,
            "declared_tolerance": tolerance,
            "effective_upper_bound": None if tolerance is None else 1.0 + float(tolerance),
            "raw_eta_mode": eta_mode, "clamped": False,
        },
        "eta_capture": ({
            "status": "COMPUTED_NATIVE_APERTURE_FLUX",
            "value": eta_capture,
            "unit": "1",
            "region": {"aperture_id": capture["aperture_id"], "plane_id": capture["plane_id"],
                       "selection": surface_info["capture_surface"], "normal_sign": capture["normal_sign"]},
            "numerator": {**capture_power_raw, "name": "signed_signal_poynting_flux",
                          "region_id": capture["aperture_id"],
                          "engine": _integral_evidence(capture_integral)},
            "denominator": {"reference_id": incident["reference_id"], "value": incident_power,
                            "unit": power_unit, "input_plane_id": incident["input_plane_id"],
                            "source": {**incident["source"], **axes["incident_reference"]},
                            "phasor_convention": dict(definition["phasor_convention"])},
            "signed_flux_preserved": True,
        } if capture_integral is not None else {
            "status": "NOT_COMPUTED",
            "reason": "this definition binds no separate native capture aperture",
            "incident_reference_id": incident["reference_id"],
        }),
    }
    if capture_integral is not None:
        result["surface"]["capture_aperture"] = {
            "aperture_id": capture["aperture_id"], "plane_id": capture["plane_id"],
            "selection": surface_info["capture_surface"], "normal_sign": capture["normal_sign"],
        }
        result["integrals"]["capture_aperture_signal_flux"] = {
            **capture_power_raw, "engine": _integral_evidence(capture_integral),
        }
    return result


OPERATIONS = {"result.mode_overlap": result_mode_overlap}

__all__ = [
    "DEFINITION_SCHEMA_VERSION", "NATIVE_DEFINITION_SCHEMA", "NATIVE_RESULT_SCHEMA",
    "OPERATIONS", "result_mode_overlap", "validate_definition_shape", "validate_request_shape",
]
