"""Read the native Numeric Port -> BMA -> solution lineage for W23 v2.

This module only reads COMSOL model metadata.  It does not run a Study or
solver.  A basis ordinal is deliberately kept separate from the Numeric Port
``PortModeNumber`` property: the latter is configuration readback, while the
former identifies one member of the requested two-eigensolution basis.

COMSOL API use is pinned to the local 6.4 documentation corpus:

* ``PhysicsFeature`` / ``PropFeature``: feature tags, type, selection, property
  presence and typed property getters (PhysicsFeature chunk 23153,
  sha256 9eeb763b12eb4d5f6eaff7561381bb200aab2150165063410a4e9fa3592b5e7c;
  PropFeature chunk 23013,
  sha256 27bc4be83990dd4be32ba2b2318b9b575084961339a2cd6feed1ceda96490d4b).
* ``SolutionInfo``: getSolnum and getSolverSequence (chunk 23067,
  sha256 94d28f308be1be1b57d81c40932be6aebfd91e134c221b8e2f7e2235d51b9abb).
* ``Selection.entities(int)`` (Selection chunk 23052,
  sha256 451cce8061404cde87fb8fe72d85ae1a1a4c3a7887f4e779a73916f8a24184d6).
* ``[StudyStep]`` solver-tree ``study`` / ``studystep`` properties
  (Programming Reference page 214, chunk 10969, sha256
  5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105).
* ``ModelParam.evaluate(expression, unit)`` (ParamBase chunk 23001,
  sha256 d7b6be3acb71d7ba99292c7740cad05a413a5503ba6c913aa976b4d0c788c9c9).

The local KB does not document the specific ``PortModeNumber`` property's
semantics.  The W23 fixture source sets and reads that property as a Numeric
Port configuration value, so this adapter reports its actual integer without
interpreting it as an eigensolution ordinal.  Field-variable-to-eigensolution
association still requires later native field-sampling evidence.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ._g2_contract import ExecutionContractError


def _call(node: Any, method: str, *args: Any) -> Any:
    # Keep result-contract imports lightweight: native dependencies are loaded
    # only when a managed COMSOL read is actually attempted.
    from ._g2_engine import _call as native_call

    return native_call(node, method, *args)


def _tag_list(node: Any) -> list[str]:
    from ._g3_common import tag_list

    return tag_list(node)


PROVENANCE_SCHEMA_ID = "urn:comsol-mcp:result.mode_overlap_basis_v2:native-mode-provenance:1.0.0"
PROVENANCE_SCHEMA_VERSION = "1.0.0"
NATIVE_MODE_PROVENANCE_SCHEMA = {
    "$id": PROVENANCE_SCHEMA_ID,
    "type": "object",
    "required": ["schema_id", "schema_version", "status",
                 "configuration_and_solution_lineage_status", "basis_ordinal_semantics",
                 "field_variable_to_eigensolution_mapping_status", "numeric_port",
                 "mode_readbacks", "api_document_provenance", "unverified_reasons"],
    "properties": {
        "schema_id": {"const": PROVENANCE_SCHEMA_ID},
        "schema_version": {"const": PROVENANCE_SCHEMA_VERSION},
        "status": {"const": "UNVERIFIED"},
        "configuration_and_solution_lineage_status": {"const": "UNVERIFIED"},
        "configuration_containment_status": {"enum": ["UNVERIFIED",
            "SOLUTIONINFO_SEQUENCE_CONTAINS_OUTPUT_PORT_BMA_CONFIGURATION"]},
        "basis_ordinal_semantics": {"const": "BASIS_EIGENSOLUTION_ORDINAL_NOT_NUMERIC_PORT_MODE_NUMBER"},
        "field_variable_to_eigensolution_mapping_status": {"const": "UNVERIFIED_NATIVE_FIELD_SAMPLE_REQUIRED"},
        "numeric_port": {"type": "object"},
        "mode_readbacks": {
            "type": "object", "required": ["mode_0", "mode_1"],
            "properties": {"mode_0": {"type": "object"}, "mode_1": {"type": "object"}},
            "additionalProperties": False,
        },
        "api_document_provenance": {"type": "array", "minItems": 1,
                                     "items": {"type": "object"}},
        "unverified_reasons": {"type": "array", "items": {"type": "object"}},
    },
    "additionalProperties": False,
}
_FREQUENCY_RELATIVE_TOLERANCE = 1e-12
_MAX_SOLVER_TREE_DEPTH = 32
_MAX_SOLVER_TREE_NODES = 4096
_API_SOURCES = [
    {"document": "PhysicsFeature.html", "comsol_version": "6.4", "chunk_id": 23153,
     "sha256": "9eeb763b12eb4d5f6eaff7561381bb200aab2150165063410a4e9fa3592b5e7c"},
    {"document": "ProblemFeature.html (PropFeature methods)", "comsol_version": "6.4", "chunk_id": 23013,
     "sha256": "27bc4be83990dd4be32ba2b2318b9b575084961339a2cd6feed1ceda96490d4b"},
    {"document": "Selection.html", "comsol_version": "6.4", "chunk_id": 23052,
     "sha256": "451cce8061404cde87fb8fe72d85ae1a1a4c3a7887f4e779a73916f8a24184d6"},
    {"document": "SolutionInfo.html", "comsol_version": "6.4", "chunk_id": 23067,
     "sha256": "94d28f308be1be1b57d81c40932be6aebfd91e134c221b8e2f7e2235d51b9abb"},
    {"document": "COMSOL_ProgrammingReferenceManual.pdf page 214", "comsol_version": "6.4",
     "chunk_id": 10969, "sha256": "5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105"},
    {"document": "ParamBase.html", "comsol_version": "6.4", "chunk_id": 23001,
     "sha256": "d7b6be3acb71d7ba99292c7740cad05a413a5503ba6c913aa976b4d0c788c9c9"},
]


def _mismatch(message: str, *, details: Mapping[str, Any] | None = None) -> None:
    raise ExecutionContractError("NATIVE_MODE_PROVENANCE_MISMATCH", message, details=details)


def _cause_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_unknown_native_transport(exc: BaseException) -> bool:
    """Find worker timeout/transport uncertainty through ``_call`` wrappers.

    These failures cannot be downgraded to an ordinary unavailable API: the
    caller must stop issuing native reads and must not proceed to IntSurface
    work. A structured method-refusal response remains an API gap.
    """
    for cause in _cause_chain(exc):
        class_names = {cls.__name__ for cls in type(cause).__mro__}
        if "JavaWorkerTimeout" in class_names:
            return True
        if (getattr(cause, "code", None) == "EXECUTION_STATE_UNKNOWN"
                or getattr(cause, "execution_state_unknown", None) is True):
            return True
        if "JavaWorkerError" in class_names:
            reply = getattr(cause, "reply", {})
            failure = getattr(cause, "failure", {})
            if (isinstance(reply, Mapping)
                    and (reply.get("status") in {"UNKNOWN", "RUNNING", "QUEUED"}
                         or reply.get("execution_state_unknown") is True
                         or reply.get("engine_state_unknown") is True
                         or reply.get("code") == "EXECUTION_STATE_UNKNOWN")):
                return True
            if (isinstance(failure, Mapping)
                    and (failure.get("execution_state_unknown") is True
                         or failure.get("engine_state_unknown") is True
                         or failure.get("code") == "EXECUTION_STATE_UNKNOWN")):
                return True
            if not reply and isinstance(cause.__cause__, (OSError, TimeoutError, EOFError)):
                return True
    return False


def _reraise_unknown_native_transport(exc: BaseException) -> None:
    if _is_unknown_native_transport(exc):
        raise exc


def _attempt(errors: list[dict[str, Any]], label: str, fn: Any) -> tuple[bool, Any]:
    try:
        value = fn()
        if isinstance(value, Mapping) and value.get("status") in {"UNKNOWN", "RUNNING", "QUEUED"}:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN",
                f"native read {label} returned worker state {value.get('status')}")
        return True, value
    except Exception as exc:  # Native metadata unavailability is recorded, never fabricated.
        _reraise_unknown_native_transport(exc)
        errors.append({"path": label,
                       "code": getattr(exc, "code", type(exc).__name__),
                       "message": str(exc)[:300]})
        return False, None


def _native_string(node: Any, method: str, *args: Any) -> str | None:
    value = _call(node, method, *args)
    return value if isinstance(value, str) and value.strip() else None


def _property(feature: Any, name: str, getter: str, errors: list[dict[str, Any]],
              path: str) -> tuple[bool, Any]:
    ok, present = _attempt(errors, f"{path}.hasProperty({name})",
                            lambda: _call(feature, "hasProperty", name))
    if not ok or present is not True:
        if ok:
            errors.append({"path": f"{path}.{name}", "code": "PROPERTY_ABSENT",
                           "message": "native feature does not expose this property"})
        return False, None
    return _attempt(errors, f"{path}.{getter}({name})",
                    lambda: _call(feature, getter, name))


def _positive_int_array(value: Any, *, label: str) -> list[int] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    result = list(value)
    if (not result or any(type(item) is not int or item < 1 for item in result)
            or len(set(result)) != len(result)):
        return None
    return result


def _mode_axis_is_complete(row: Mapping[str, Any], role_data: Mapping[str, Any]) -> bool:
    info = row.get("solution_info_readback")
    axis = info.get("mode_axis_parameter") if isinstance(info, Mapping) else None
    if not isinstance(axis, Mapping):
        return False
    value = axis.get("value")
    return (
        axis.get("parameter") == role_data.get("mode_axis_parameter")
        and isinstance(axis.get("parameter"), str) and bool(axis["parameter"])
        and type(value) in (int, float) and math.isfinite(float(value))
        and isinstance(axis.get("unit"), str)
        and axis.get("semantics") == "SOLUTIONINFO_PARAMETER_VALUE_NOT_BASIS_ORDINAL"
    )


def _base_payload(request: Mapping[str, Any], reasons: list[dict[str, Any]]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for index, mode in enumerate(request.get("basis_modes", [])):
        source = mode.get("source", {}) if isinstance(mode, Mapping) else {}
        rows[f"mode_{index}"] = {
            "mode_id": mode.get("mode_id") if isinstance(mode, Mapping) else None,
            "basis_mode_ordinal": mode.get("mode_index") if isinstance(mode, Mapping) else None,
            "basis_ordinal_origin": "canonical_request_provenance_only",
            "numeric_port_mode_number": "NOT_PROVIDED",
            "native_source_binding": {
                key: source.get(key) for key in
                ("dataset_id", "solution_id", "outer_index", "inner_index", "solnum")
            } if isinstance(source, Mapping) else None,
            "solution_info_readback": None,
            "solution_to_solver_sequence": {"status": "UNVERIFIED", "solver_sequence_tag": None},
            "solver_to_bma_step": {"status": "UNVERIFIED", "study_tag": None,
                                   "step_tag": None, "feature_type": None},
            "basis_ordinal_to_field_mapping_status": "UNVERIFIED_NATIVE_FIELD_SAMPLE_REQUIRED",
        }
    return {
        "schema_id": PROVENANCE_SCHEMA_ID,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "status": "UNVERIFIED",
        "configuration_and_solution_lineage_status": "UNVERIFIED",
        "configuration_containment_status": "UNVERIFIED",
        "basis_ordinal_semantics": "BASIS_EIGENSOLUTION_ORDINAL_NOT_NUMERIC_PORT_MODE_NUMBER",
        "field_variable_to_eigensolution_mapping_status": "UNVERIFIED_NATIVE_FIELD_SAMPLE_REQUIRED",
        "numeric_port": {"status": "UNVERIFIED", "component": None,
                          "physics_interface_tag": "ewfd", "physics_interface_type": None,
                          "feature_tag": None, "feature_name": None, "feature_type": None,
                          "port_type": None, "port_name": None,
                          "port_mode_number": "NOT_PROVIDED",
                          "port_mode_number_semantics": "CONFIGURATION_VALUE_ONLY_NOT_BASIS_ORDINAL",
                          "selection": {"entity_dimension": 2, "entity_ids": None,
                                        "requested_surface_relation": "UNVERIFIED"}},
        "mode_readbacks": rows,
        "api_document_provenance": list(_API_SOURCES),
        "unverified_reasons": reasons,
    }


def _read_port(model: Any, request: Mapping[str, Any], output_selection: Mapping[str, Any],
               payload: dict[str, Any], errors: list[dict[str, Any]]) -> dict[str, Any] | None:
    surface = request["output_surface"]
    port_id = str(surface["port_id"])
    row = payload["numeric_port"]
    row["component"] = surface["component"]
    ok, component = _attempt(errors, f"component({surface['component']})",
                             lambda: _call(model, "component", surface["component"]))
    if not ok:
        return None
    ok, physics = _attempt(errors, "component.physics(ewfd)",
                           lambda: _call(component, "physics", "ewfd"))
    if not ok:
        return None
    ok, physics_type = _attempt(errors, "physics.getType()", lambda: _call(physics, "getType"))
    row["physics_interface_type"] = physics_type if isinstance(physics_type, str) else None
    if ok and row["physics_interface_type"] != "ElectromagneticWaves":
        _mismatch("the requested ewfd field variables do not resolve to an ElectromagneticWaves interface",
                  details={"observed_type": row["physics_interface_type"]})

    ok, feature_tags = _attempt(errors, "physics.feature().tags()",
                                lambda: _tag_list(_call(physics, "feature")))
    if not ok:
        return None
    candidates: list[tuple[str, Any]] = []
    for tag in feature_tags:
        ok, feature = _attempt(errors, f"physics.feature({tag})",
                               lambda tag=tag: _call(physics, "feature", tag))
        if not ok:
            continue
        try:
            feature_type = _native_string(feature, "getType")
            has_port_name = _call(feature, "hasProperty", "PortName") is True
            port_name = _native_string(feature, "getString", "PortName") if has_port_name else None
            if feature_type == "Port" and port_name == port_id:
                candidates.append((tag, feature))
        except Exception as exc:
            _reraise_unknown_native_transport(exc)
            errors.append({"path": f"physics.feature({tag}).PortName", "code": getattr(exc, "code", type(exc).__name__),
                           "message": str(exc)[:300]})
    if len(candidates) > 1:
        _mismatch("multiple EWFD Port features claim the requested Numeric PortName",
                  details={"port_name": port_id, "feature_tags": [row[0] for row in candidates]})
    if not candidates:
        # An unreadable feature property is an API/readback gap; a complete
        # feature scan with no matching Port is a model/request contradiction.
        if not any(item.get("path", "").startswith("physics.feature(") for item in errors):
            _mismatch("no actual EWFD Port feature has the requested PortName",
                      details={"port_name": port_id, "feature_tags": feature_tags})
        return None

    feature_tag, feature = candidates[0]
    row["feature_tag"] = feature_tag
    ok, name = _attempt(errors, f"physics.feature({feature_tag}).name()",
                        lambda: _native_string(feature, "name"))
    row["feature_name"] = name if ok else None
    ok, feature_type = _attempt(errors, f"physics.feature({feature_tag}).getType()",
                                lambda: _native_string(feature, "getType"))
    row["feature_type"] = feature_type if ok else None
    if ok and feature_type != "Port":
        _mismatch("selected Numeric Port feature changed type during readback")

    ok_type, port_type = _property(feature, "PortType", "getString", errors,
                                   f"physics.feature({feature_tag})")
    ok_name, port_name = _property(feature, "PortName", "getString", errors,
                                   f"physics.feature({feature_tag})")
    ok_mode, mode_number = _property(feature, "PortModeNumber", "getInt", errors,
                                     f"physics.feature({feature_tag})")
    row["port_type"] = port_type if ok_type and isinstance(port_type, str) else None
    row["port_name"] = port_name if ok_name and isinstance(port_name, str) else None
    if ok_type and row["port_type"] != "Numeric":
        _mismatch("actual output Port is not configured as Numeric",
                  details={"feature_tag": feature_tag, "port_type": row["port_type"]})
    if ok_name and row["port_name"] != port_id:
        _mismatch("actual Numeric PortName differs from the requested output port",
                  details={"feature_tag": feature_tag, "expected": port_id,
                           "observed": row["port_name"]})
    if ok_mode and (type(mode_number) is not int or mode_number < 1):
        _mismatch("actual Numeric Port PortModeNumber is not a positive integer",
                  details={"feature_tag": feature_tag, "port_mode_number": mode_number})
    if ok_mode and type(mode_number) is int and mode_number > 0:
        # This is intentionally not compared with basis_mode_ordinal.
        row["port_mode_number"] = mode_number

    ok, selection = _attempt(errors, f"physics.feature({feature_tag}).selection()",
                             lambda: _call(feature, "selection"))
    entity_ids = None
    if ok:
        ok_entities, entities = _attempt(errors, f"physics.feature({feature_tag}).selection().entities(2)",
                                         lambda: _call(selection, "entities", 2))
        entity_ids = _positive_int_array(entities, label="Numeric Port selection") if ok_entities else None
        if ok_entities and entity_ids is None:
            errors.append({"path": f"physics.feature({feature_tag}).selection().entities(2)",
                           "code": "SELECTION_READBACK_INVALID",
                           "message": "COMSOL returned an empty, duplicate, or non-positive boundary ID list"})
    row["selection"]["entity_ids"] = entity_ids
    requested_ids = output_selection.get("entity_ids") if isinstance(output_selection, Mapping) else None
    if entity_ids is not None and isinstance(requested_ids, list):
        if sorted(entity_ids) != sorted(requested_ids):
            _mismatch("actual Numeric Port boundary selection differs from the requested output boundary",
                      details={"port_feature_tag": feature_tag,
                               "port_entity_ids": entity_ids,
                               "requested_entity_ids": requested_ids})
        row["selection"]["requested_surface_relation"] = "VERIFIED_SAME_BOUNDARY_ENTITY_IDS"

    if (ok and ok_type and ok_name and ok_mode and type(mode_number) is int and mode_number > 0
            and isinstance(row["feature_name"], str) and bool(row["feature_name"])
            and row["port_type"] == "Numeric" and row["port_name"] == port_id
            and entity_ids is not None and row["selection"]["requested_surface_relation"] == "VERIFIED_SAME_BOUNDARY_ENTITY_IDS"):
        row["status"] = "VERIFIED_NATIVE_NUMERIC_PORT_CONFIGURATION"
    return {"tag": feature_tag, "feature": feature, "port_name": port_id,
            "port_mode_number": row["port_mode_number"], "entity_ids": entity_ids}


def _solver_step_bindings(sequence: Any, *, label: str,
                          errors: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    error_start = len(errors)
    ok, roots = _attempt(errors, f"{label}.feature()", lambda: _call(sequence, "feature"))
    if not ok:
        return None
    ok, root_tags = _attempt(errors, f"{label}.feature().tags()", lambda: _tag_list(roots))
    if not ok:
        return None
    found: list[dict[str, Any]] = []
    visited = 0

    def walk(parent: Any, tag: str, path: str, depth: int) -> None:
        nonlocal visited
        visited += 1
        if visited > _MAX_SOLVER_TREE_NODES or depth > _MAX_SOLVER_TREE_DEPTH:
            raise ExecutionContractError("SOLVER_TREE_LIMIT", "solver feature tree exceeded the bounded readback limit")
        node = _call(parent, "feature", tag)
        kind = _native_string(node, "getType")
        if kind == "StudyStep":
            fields: dict[str, Any] = {"path": path, "feature_type": kind,
                                      "study": None, "studystep": None}
            for prop in ("study", "studystep"):
                ok_prop, value = _property(node, prop, "getString", errors, path)
                if ok_prop and isinstance(value, str) and value.strip():
                    fields[prop] = value.strip()
                else:
                    errors.append({"path": f"{path}.{prop}", "code": "STUDY_STEP_BINDING_UNAVAILABLE",
                                   "message": "solver StudyStep reference was not read back"})
            found.append(fields)
        child_ok, children = _attempt(errors, f"{path}.feature()", lambda: _call(node, "feature"))
        if not child_ok:
            return
        tags_ok, child_tags = _attempt(errors, f"{path}.feature().tags()", lambda: _tag_list(children))
        if not tags_ok:
            return
        for child in child_tags:
            walk(node, child, f"{path}/{child}", depth + 1)

    try:
        for tag in root_tags:
            walk(sequence, tag, tag, 0)
    except Exception as exc:
        _reraise_unknown_native_transport(exc)
        errors.append({"path": f"{label}.solver_tree", "code": getattr(exc, "code", type(exc).__name__),
                       "message": str(exc)[:300]})
        return None
    if len(errors) != error_start:
        return None
    return found


def _read_solution_lineage(model: Any, request: Mapping[str, Any], role: str,
                           mode_row: Mapping[str, Any], role_data: Mapping[str, Any],
                           payload_row: dict[str, Any], errors: list[dict[str, Any]]) -> dict[str, Any] | None:
    source = role_data.get("source")
    axes = role_data.get("solution_axes")
    binding = role_data.get("native_binding")
    if not isinstance(source, Mapping) or not isinstance(axes, Mapping) or not isinstance(binding, Mapping):
        errors.append({"path": role, "code": "SOURCE_READBACK_UNAVAILABLE",
                       "message": "dataset/solution/SolutionInfo native readback is incomplete"})
        return None
    payload_row["native_source_binding"] = {
        "dataset_id": binding.get("dataset"), "solution_id": binding.get("solution"),
        "outer_index": axes.get("outer_index"), "inner_index": axes.get("inner_index"),
        "solnum": axes.get("solnum"),
    }
    parameter = role_data.get("mode_axis_parameter")
    parameters = axes.get("parameters_by_pair")
    parameter_pair = parameters.get(parameter) if isinstance(parameters, Mapping) and isinstance(parameter, str) else None
    mode_axis = None
    if isinstance(parameter_pair, (list, tuple)) and len(parameter_pair) == 2:
        mode_axis = {"parameter": parameter, "value": parameter_pair[0], "unit": parameter_pair[1],
                     "semantics": "SOLUTIONINFO_PARAMETER_VALUE_NOT_BASIS_ORDINAL"}
    payload_row["solution_info_readback"] = {
        "dataset_binding": dict(binding),
        "outer_index": axes.get("outer_index"), "inner_index": axes.get("inner_index"),
        "solnum": axes.get("solnum"), "mode_axis_parameter": mode_axis,
    }
    if not _mode_axis_is_complete(payload_row, role_data):
        errors.append({"path": f"{role}.solution_info_mode_axis", "code": "MODE_AXIS_READBACK_UNVERIFIED",
                       "message": "the exact selected SolutionInfo parameter/value/unit is missing or malformed; it is not the basis ordinal"})
    solution_tag = source.get("solution_id")
    if not isinstance(solution_tag, str) or not solution_tag:
        errors.append({"path": f"{role}.solution_id", "code": "SOLUTION_BINDING_UNAVAILABLE",
                       "message": "source has no exact selected solution tag"})
        return None

    ok, solution = _attempt(errors, f"model.sol({solution_tag})",
                            lambda: _call(model, "sol", solution_tag))
    if not ok:
        return None
    ok, info = _attempt(errors, f"model.sol({solution_tag}).getSolutioninfo()",
                        lambda: _call(solution, "getSolutioninfo"))
    if not ok:
        return None
    outer = axes.get("outer_index")
    inner = axes.get("inner_index")
    solnum = axes.get("solnum")
    solver_tag = None
    get_solver_sequence_ok, solver_tag = _attempt(
        errors, f"solutionInfo.getSolverSequence({outer})",
        lambda: _call(info, "getSolverSequence", outer))
    inner_ok, inner_values_raw = _attempt(errors, f"solutionInfo.getSolnum({outer}, true)",
                                          lambda: _call(info, "getSolnum", outer, True))
    inner_values = _positive_int_array(inner_values_raw, label=f"{role} SolutionInfo inner indices") if inner_ok else None
    if inner_values is not None and type(inner) is int and inner not in inner_values:
        _mismatch(f"{role} selected SolutionInfo inner index is absent from getSolnum(outer,true)",
                  details={"outer_index": outer, "inner_index": inner, "native_inner_indices": inner_values})
    if inner_values is None:
        errors.append({"path": f"{role}.solution_info_inner_axis", "code": "SOLUTIONINFO_INNER_AXIS_UNVERIFIED",
                       "message": "getSolnum(outer,true) did not return a validated inner-index list"})
    effective_solver_tag = (solver_tag if get_solver_sequence_ok
                            and isinstance(solver_tag, str) and solver_tag else None)
    if effective_solver_tag:
        payload_row["solution_to_solver_sequence"] = {
            "status": "VERIFIED_SOLUTIONINFO_OUTER_TO_SOLVER_SEQUENCE",
            "method": "SolutionInfo.getSolverSequence(outer)",
            "solver_sequence_tag": effective_solver_tag,
            "getSol_tag_readback": "NOT_CALLED_WORKER_ALLOWLIST",
            "inner_index_membership": ("VERIFIED" if inner_values is not None and inner in inner_values
                                       else "UNVERIFIED"),
            "inner_indices_for_outer": inner_values,
            "selected_inner_index": inner,
            "selected_solnum": solnum,
        }
    else:
        errors.append({"path": f"{role}.solution_to_solver_sequence", "code": "SOLVER_SEQUENCE_UNAVAILABLE",
                       "message": "SolutionInfo did not return a solver sequence for the selected outer index"})
        return None

    ok_seq, sequence = _attempt(errors, f"model.sol({effective_solver_tag})",
                                lambda: _call(model, "sol", effective_solver_tag))
    if not ok_seq:
        return None
    study_ok, study_tag = _attempt(errors, f"solverSequence({effective_solver_tag}).study()",
                                  lambda: _call(sequence, "study"))
    if not study_ok or not isinstance(study_tag, str) or not study_tag:
        errors.append({"path": f"{role}.solver_sequence_study", "code": "STUDY_ASSOCIATION_UNAVAILABLE",
                       "message": "solver sequence did not expose an associated study tag"})
        return None
    ok_study, study = _attempt(errors, f"model.study({study_tag})",
                              lambda: _call(model, "study", study_tag))
    if not ok_study:
        return None
    bindings = _solver_step_bindings(sequence, label=f"solverSequence({effective_solver_tag})", errors=errors)
    if bindings is None:
        return None
    payload_row["solver_to_bma_step"] = {"status": "UNVERIFIED", "study_tag": study_tag,
                                         "step_tag": None, "feature_type": None,
                                         "configuration_containment_status": "UNVERIFIED",
                                         "producer_step_binding_status": "UNVERIFIED_SELECTED_SOLUTION_PRODUCER_STEP_API_UNAVAILABLE",
                                         "study_step_bindings": bindings}
    candidates: list[dict[str, Any]] = []
    binding_rows_complete = True
    for step in bindings:
        if step.get("study") != study_tag:
            continue
        step_tag = step.get("studystep")
        if not isinstance(step_tag, str) or not step_tag:
            binding_rows_complete = False
            continue
        ok_feature, study_feature = _attempt(errors, f"study({study_tag}).feature({step_tag})",
                                            lambda step_tag=step_tag: _call(study, "feature", step_tag))
        if not ok_feature:
            binding_rows_complete = False
            continue
        ok_type, step_type = _attempt(errors, f"study({study_tag}).feature({step_tag}).getType()",
                                     lambda study_feature=study_feature: _native_string(study_feature, "getType"))
        if not ok_type:
            binding_rows_complete = False
            continue
        if step_type != "BoundaryModeAnalysis":
            continue
        ok_port, bma_port = _property(study_feature, "PortName", "getString", errors,
                                      f"study({study_tag}).feature({step_tag})")
        if not ok_port or not isinstance(bma_port, str):
            binding_rows_complete = False
            continue
        if bma_port == request["output_surface"]["port_id"]:
            candidates.append({"study_tag": study_tag, "step_tag": step_tag,
                               "feature_type": step_type, "feature": study_feature,
                               "port_name": bma_port})
    if not binding_rows_complete:
        errors.append({"path": f"{role}.solver_to_bma_step", "code": "STUDY_STEP_BINDING_INCOMPLETE",
                       "message": "one or more solver StudyStep references could not be fully read"})
        return None
    if not candidates:
        _mismatch(f"{role} solver tree contains no BoundaryModeAnalysis step for output Port {request['output_surface']['port_id']}",
                  details={"study_tag": study_tag, "study_step_bindings": bindings})
    if len(candidates) > 1:
        _mismatch(f"{role} solver tree ambiguously binds multiple output-port BMA steps",
                  details={"study_tag": study_tag, "step_tags": [row["step_tag"] for row in candidates]})
    bma = candidates[0]
    bma_feature = bma["feature"]
    ok_mode_freq, mode_freq = _property(bma_feature, "modeFreq", "getString", errors,
                                        f"study({study_tag}).feature({bma['step_tag']})")
    ok_neigs, neigs = _property(bma_feature, "neigs", "getInt", errors,
                                f"study({study_tag}).feature({bma['step_tag']})")
    evaluated_hz = None
    if ok_mode_freq and isinstance(mode_freq, str) and mode_freq.strip():
        ok_eval, evaluated = _attempt(errors, f"model.param().evaluate({mode_freq}, Hz)",
                                     lambda: _call(_call(model, "param"), "evaluate", mode_freq, "Hz"))
        if ok_eval and type(evaluated) in (int, float) and math.isfinite(float(evaluated)):
            evaluated_hz = float(evaluated)
            if not math.isclose(evaluated_hz, float(request["frequency_hz"]),
                                rel_tol=_FREQUENCY_RELATIVE_TOLERANCE, abs_tol=0.0):
                _mismatch(f"{role} BMA modeFreq evaluates to a different frequency",
                          details={"modeFreq": mode_freq, "evaluated_hz": evaluated_hz,
                                   "requested_hz": request["frequency_hz"]})
    if ok_neigs and type(neigs) is int and neigs != 2:
        _mismatch(f"{role} output BMA does not request the frozen two-eigensolution basis",
                  details={"bma_tag": bma["step_tag"], "neigs": neigs})
    payload_row["solver_to_bma_step"] = {
        "status": "UNVERIFIED_SELECTED_SOLUTION_PRODUCER_STEP",
        "configuration_containment_status": "SOLUTIONINFO_SEQUENCE_CONTAINS_OUTPUT_PORT_BMA_CONFIGURATION",
        "producer_step_binding_status": "UNVERIFIED_SELECTED_SOLUTION_PRODUCER_STEP_API_UNAVAILABLE",
        "study_tag": study_tag, "step_tag": bma["step_tag"],
        "feature_type": bma["feature_type"], "port_name": bma["port_name"],
        "mode_frequency_expression": mode_freq if ok_mode_freq else "NOT_PROVIDED",
        "mode_frequency_evaluated_hz": evaluated_hz if evaluated_hz is not None else "UNVERIFIED",
        "neigs": neigs if ok_neigs and type(neigs) is int else "NOT_PROVIDED",
        "study_step_bindings": bindings,
    }
    errors.append({"path": f"{role}.selected_solution_producer_step", "code": "PRODUCER_STEP_READBACK_UNAVAILABLE",
                   "message": "SolutionInfo maps the outer solution to a solver sequence, but the available readbacks do not identify which contained StudyStep produced the selected stored solution"})
    return bma


def read_native_mode_provenance(model: Any, request: Mapping[str, Any],
                                roles: Mapping[str, Mapping[str, Any]],
                                output_selection: Mapping[str, Any]) -> dict[str, Any]:
    """Read Port/BMA/solver/SolutionInfo facts; keep missing APIs unverified.

    Definite contradictions raise a typed error before any W-valued integral is
    dispatched. Missing readback methods are carried as explicit UNVERIFIED
    reasons, allowing a raw result only when later aggregation keeps the gate
    pending.
    """
    errors: list[dict[str, Any]] = []
    payload = _base_payload(request, errors)
    port = _read_port(model, request, output_selection, payload, errors)
    bma_by_role: dict[str, dict[str, Any]] = {}
    mode_rows = request.get("basis_modes")
    if not isinstance(mode_rows, list) or len(mode_rows) != 2:
        raise ExecutionContractError("INVALID_REQUEST", "W23 native provenance requires exactly two basis modes")
    for index, mode in enumerate(mode_rows):
        role = f"mode_{index}"
        row = payload["mode_readbacks"][role]
        row_data = roles.get(role)
        if not isinstance(mode, Mapping) or not isinstance(row_data, Mapping):
            errors.append({"path": role, "code": "SOURCE_READBACK_UNAVAILABLE",
                           "message": "native mode row or source readback is absent"})
            continue
        mode_number = mode.get("mode_index")
        row["basis_mode_ordinal"] = mode_number
        row["basis_ordinal_origin"] = "canonical_request_provenance_only"
        if port is not None and type(port.get("port_mode_number")) is int:
            row["numeric_port_mode_number"] = port["port_mode_number"]
        bma = _read_solution_lineage(model, request, role, mode, row_data, row, errors)
        if bma is not None:
            bma_by_role[role] = bma

    mode_rows_by_role = [payload["mode_readbacks"].get("mode_0"),
                         payload["mode_readbacks"].get("mode_1")]
    actual_inner = [row.get("solution_info_readback", {}).get("inner_index")
                    if isinstance(row.get("solution_info_readback"), Mapping) else None
                    for row in mode_rows_by_role]
    actual_solnum = [row.get("solution_info_readback", {}).get("solnum")
                     if isinstance(row.get("solution_info_readback"), Mapping) else None
                     for row in mode_rows_by_role]
    if (len(bma_by_role) == 2 and all(type(value) is int for value in actual_inner)
            and actual_inner[0] == actual_inner[1]) or (
            len(bma_by_role) == 2 and all(type(value) is int for value in actual_solnum)
            and actual_solnum[0] == actual_solnum[1]):
        _mismatch("the two requested BMA basis members do not resolve to distinct SolutionInfo inner/solnum indices",
                  details={"inner_indices": actual_inner, "solnums": actual_solnum})

    if len(bma_by_role) == 2:
        left, right = bma_by_role["mode_0"], bma_by_role["mode_1"]
        if (left["study_tag"], left["step_tag"]) != (right["study_tag"], right["step_tag"]):
            _mismatch("the two basis eigensolutions do not resolve to the same output-Port BMA feature",
                      details={"mode_0": {"study": left["study_tag"], "step": left["step_tag"]},
                               "mode_1": {"study": right["study_tag"], "step": right["step_tag"]}})

    containment_complete = (
        port is not None and payload["numeric_port"].get("status") == "VERIFIED_NATIVE_NUMERIC_PORT_CONFIGURATION"
        and len(bma_by_role) == 2
        and all(payload["mode_readbacks"][f"mode_{index}"].get("solution_to_solver_sequence", {}).get("status")
                == "VERIFIED_SOLUTIONINFO_OUTER_TO_SOLVER_SEQUENCE" for index in range(2))
        and all(payload["mode_readbacks"][f"mode_{index}"].get("solution_to_solver_sequence", {}).get("inner_index_membership")
                == "VERIFIED" for index in range(2))
        and all(_mode_axis_is_complete(payload["mode_readbacks"][f"mode_{index}"], roles[f"mode_{index}"])
                for index in range(2))
        and all(payload["mode_readbacks"][f"mode_{index}"].get("solver_to_bma_step", {}).get("status")
                == "UNVERIFIED_SELECTED_SOLUTION_PRODUCER_STEP" for index in range(2))
        and all(payload["mode_readbacks"][f"mode_{index}"].get("solver_to_bma_step", {}).get("configuration_containment_status")
                == "SOLUTIONINFO_SEQUENCE_CONTAINS_OUTPUT_PORT_BMA_CONFIGURATION" for index in range(2))
        and all(payload["mode_readbacks"][f"mode_{index}"].get("solver_to_bma_step", {}).get("mode_frequency_evaluated_hz")
                != "UNVERIFIED" for index in range(2))
        and all(payload["mode_readbacks"][f"mode_{index}"].get("solver_to_bma_step", {}).get("neigs") == 2
                for index in range(2))
    )
    payload["configuration_containment_status"] = (
        "SOLUTIONINFO_SEQUENCE_CONTAINS_OUTPUT_PORT_BMA_CONFIGURATION"
        if containment_complete else "UNVERIFIED")
    # SolutionInfo maps outer solution numbers to solver sequences, but the
    # selected sequence can contain multiple StudySteps. No current readback
    # identifies which StudyStep produced the selected stored field solution.
    payload["configuration_and_solution_lineage_status"] = "UNVERIFIED"
    payload["status"] = "UNVERIFIED"
    payload["unverified_reasons"] = errors
    # Even a complete metadata chain does not prove which actual field values
    # COMSOL returns for Emode*_2 at each selected inner eigen-solution.
    payload["field_variable_to_eigensolution_mapping_status"] = "UNVERIFIED_NATIVE_FIELD_SAMPLE_REQUIRED"
    return payload


__all__ = ["NATIVE_MODE_PROVENANCE_SCHEMA", "PROVENANCE_SCHEMA_ID",
           "PROVENANCE_SCHEMA_VERSION", "read_native_mode_provenance"]
