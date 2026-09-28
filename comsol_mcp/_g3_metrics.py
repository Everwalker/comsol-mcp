"""F16 project-scoped metric definitions, native evaluations, and comparisons."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
import copy
from collections.abc import Mapping
from typing import Any

from ._execution_contract import ExecutionContractError
from ._metric_contract import definition_sha256, normalize_arguments


_WEIGHT_INTEGRATION_ORDER = 4


def _context(operation_id: str) -> dict[str, Any]:
    from ._observation_store import current_context
    context = current_context()
    project_id = context.get("project_id")
    model_ref = context.get("model_ref")
    revision = context.get("revision")
    if (not isinstance(project_id, str) or not project_id or not isinstance(model_ref, Mapping)
            or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise ExecutionContractError("PROJECT_IDENTITY_REQUIRED", f"{operation_id} requires a bound project, ModelRef, and revision")
    return context


def _json_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_json_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(value), sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def metric_define(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = normalize_arguments("metric.define", arguments)
    context = _context("metric.define")
    project_id = context["project_id"]
    definition = args["definition"]
    semantic_hash = definition_sha256(definition)
    record = {
        "kind": "w17_metric_definition_version",
        "schema_version": 1,
        "project_id": project_id,
        "metric_id": args["metric_id"],
        "definition": definition,
        "definition_sha256": semantic_hash,
        "created_model_ref": dict(context["model_ref"]),
        "created_revision": context["revision"],
        "producer": context["producer"],
        "removed": False,
    }
    version = context["store"].append_metric_definition(project_id, args["metric_id"], record)
    return {
        "metric_id": args["metric_id"],
        "version": version["version"],
        "definition_sha256": version["definition_sha256"],
        "active": not version["removed"],
        "immutable": True,
        "idempotent_same_definition": version["definition_sha256"] == semantic_hash,
    }


def metric_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = normalize_arguments("metric.list", arguments)
    context = _context("metric.list")
    filter_value = args.get("filter", {})
    rows = context["store"].read_metric_definitions(
        context["project_id"], metric_id=filter_value.get("metric_id"),
        include_removed=filter_value.get("include_removed", False),
    )
    return {
        "metrics": [{
            "metric_id": row["metric_id"], "version": row["version"],
            "definition": row.get("definition"), "definition_sha256": row["definition_sha256"],
            "active": not row.get("removed", False), "created_revision": row.get("created_revision"),
            "created_model_ref": row.get("created_model_ref"), "record_sha256": row.get("sha256"),
        } for row in rows],
        "snapshot": "single_sqlite_read_transaction",
    }


def metric_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = normalize_arguments("metric.remove", arguments)
    context = _context("metric.remove")
    rows = context["store"].read_metric_definitions(context["project_id"], metric_id=args["metric_id"], include_removed=True)
    if not rows:
        raise ExecutionContractError("METRIC_NOT_FOUND", "metric definition was not found in this project", stage="validation")
    current = rows[0]
    if current.get("removed"):
        return {"metric_id": args["metric_id"], "version": current["version"], "active": False, "idempotent": True}
    tombstone = {
        "kind": "w17_metric_definition_version",
        "schema_version": 1,
        "project_id": context["project_id"],
        "metric_id": args["metric_id"],
        "definition": current["definition"],
        "definition_sha256": current["definition_sha256"],
        "created_model_ref": dict(context["model_ref"]),
        "created_revision": context["revision"],
        "producer": context["producer"],
        "removed": True,
        "tombstones_version": current["version"],
        "_expected_latest_version": current["version"],
    }
    try:
        version = context["store"].append_metric_definition(context["project_id"], args["metric_id"], tombstone)
    except ValueError as exc:
        raise ExecutionContractError("REVISION_CONFLICT", "metric definition changed before its tombstone could be appended", stage="validation") from exc
    return {"metric_id": args["metric_id"], "version": version["version"], "active": False, "immutable": True}


def _selected_pairs(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence = result.get("strict_metric_evidence")
    pairs = evidence.get("selected_solution_pairs") if isinstance(evidence, Mapping) else None
    if not isinstance(pairs, list) or not pairs:
        raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "native metric result did not provide exact selected outer/inner/solnum tuples")
    field_array = result.get("field_array")
    values = field_array.get("values") if isinstance(field_array, Mapping) else None
    coords = field_array.get("coords") if isinstance(field_array, Mapping) else None
    if not isinstance(values, list) or len(values) != 1 or not isinstance(coords, Mapping):
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "metric evaluation requires one expression with explicit field-array axes")
    outers = coords.get("outer")
    inners = coords.get("inner")
    axes = field_array.get("axes")
    shape = field_array.get("shape")
    points = coords.get("point")
    if (not isinstance(outers, list) or not outers or not isinstance(inners, list) or not inners
            or any(isinstance(index, bool) or not isinstance(index, int) or index < 1 for index in outers + inners)
            or len(set(outers)) != len(outers) or len(set(inners)) != len(inners)
            or axes != ["expression", "outer", "inner", "point"]
            or shape != [1, len(outers), len(inners), 1]
            or not isinstance(points, list) or len(points) != 1):
        raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "metric field array must expose one scalar point for each exact outer/inner tuple")
    expr_row = values[0]
    output: list[dict[str, Any]] = []
    seen_pairs = set()
    expected_axes = [(outer, inner) for outer in outers for inner in inners]
    actual_axes = []
    for pair in pairs:
        if not isinstance(pair, Mapping):
            raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "native metric tuple is malformed")
        outer, inner = pair.get("outer"), pair.get("inner")
        solnum = pair.get("solnum")
        if (any(isinstance(index, bool) or not isinstance(index, int) or index < 1 for index in (outer, inner, solnum))
                or outer not in outers or inner not in inners or (outer, inner, solnum) in seen_pairs):
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "native metric tuple is absent from field-array coordinates")
        seen_pairs.add((outer, inner, solnum))
        actual_axes.append((outer, inner))
        outer_row = expr_row[outers.index(outer)]
        inner_row = outer_row[inners.index(inner)]
        if not isinstance(inner_row, list) or len(inner_row) != 1:
            raise ExecutionContractError("FIELD_ARRAY_SHAPE_MISMATCH", "metric aggregate must return exactly one scalar per selected solution tuple")
        value = inner_row[0]
        if isinstance(value, Mapping):
            if set(value) != {"real", "imag"}:
                raise ExecutionContractError("INVALID_RESULT", "complex metric value has an unsupported shape")
            real, imag = value.get("real"), value.get("imag")
            if (isinstance(real, bool) or not isinstance(real, (int, float)) or not math.isfinite(float(real))
                    or isinstance(imag, bool) or not isinstance(imag, (int, float)) or not math.isfinite(float(imag))):
                raise ExecutionContractError("INVALID_RESULT", "metric complex value is non-finite or malformed")
            value = {"real": float(real), "imag": float(imag)}
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ExecutionContractError("INVALID_RESULT", "metric value is not a finite real or complex scalar")
        else:
            value = float(value)
        output.append({"outer": outer, "inner": inner, "solnum": solnum, "value": value})
    if actual_axes != expected_axes:
        raise ExecutionContractError("SOLUTION_AXIS_METADATA_UNAVAILABLE", "native metric readback does not cover every exact outer/inner tuple")
    return output


def _verified_weight_evidence(
    native: Mapping[str, Any],
    definition: Mapping[str, Any],
    selected_pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate the strict adapter's sampled weight, unit, denominator and cleanup proof."""
    evidence = native.get("strict_metric_evidence")
    weight = evidence.get("weight_validation") if isinstance(evidence, Mapping) else None
    if not isinstance(weight, Mapping):
        raise ExecutionContractError("WEIGHT_EVIDENCE_UNAVAILABLE", "weighted metric has no strict native weight-validation evidence", stage="post_dispatch")
    expected_keys = {
        "status", "expression", "unit_evidence", "minimum", "denominator",
        "selection_roles", "same_dataset_solution_tuple_and_selection",
    }
    if set(weight) != expected_keys or weight.get("expression") != definition.get("weight", {}).get("expression"):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "weight evidence is malformed or does not bind to the immutable expression", stage="post_dispatch")
    if weight.get("same_dataset_solution_tuple_and_selection") is not True:
        raise ExecutionContractError("WEIGHT_EVIDENCE_UNAVAILABLE", "weight evidence does not bind the same dataset, tuple, and ROI", stage="post_dispatch")
    if (evidence.get("status") != "VERIFIED"
            or evidence.get("selection_source") != "actual_transient_numerical_feature_readback"
            or evidence.get("selection_membership_identical") is not True):
        raise ExecutionContractError("SELECTION_READBACK_UNAVAILABLE", "weighted metric does not carry verified transient feature selection readbacks", stage="post_dispatch")
    required_roles = {"primary", "weight_validation", "denominator", "numerator"}
    if set(weight.get("selection_roles", [])) != required_roles:
        raise ExecutionContractError("SELECTION_READBACK_UNAVAILABLE", "weighted metric evidence omits a numerical feature selection role", stage="post_dispatch")

    selection_rows = evidence.get("selection_features") if isinstance(evidence, Mapping) else None
    if not isinstance(selection_rows, list):
        raise ExecutionContractError("SELECTION_READBACK_UNAVAILABLE", "weighted metric evidence has no feature-level selection readbacks", stage="post_dispatch")
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    feature_tags: set[str] = set()
    primary_entities = None
    primary_selection_identity = None
    for row in selection_rows:
        if not isinstance(row, Mapping):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "native feature selection evidence is malformed", stage="post_dispatch")
        role = row.get("role")
        tag = row.get("feature_tag")
        entities = row.get("entities")
        if (not isinstance(role, str) or not isinstance(tag, str) or not tag or tag in feature_tags
                or not isinstance(entities, list) or not entities
                or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in entities)):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "feature selection readback lacks a unique tag or exact entity list", stage="post_dispatch")
        native_readback = row.get("native_selection_readback")
        if (not isinstance(native_readback, Mapping)
                or native_readback.get("entities") != entities
                or native_readback.get("geometry") != row.get("geometry")
                or native_readback.get("dimension") != row.get("entity_dimension")):
            raise ExecutionContractError("SELECTION_READBACK_UNAVAILABLE", "feature selection evidence is not bound to its native selection getter", stage="post_dispatch")
        identity = {key: row.get(key) for key in ("component", "geometry", "entity_dimension", "kind", "tag")}
        if (not isinstance(identity["component"], str) or not identity["component"]
                or not isinstance(identity["geometry"], str) or not identity["geometry"]
                or isinstance(identity["entity_dimension"], bool)
                or not isinstance(identity["entity_dimension"], int)
                or identity["kind"] not in {"all", "named", "explicit"}):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "feature selection identity is malformed", stage="post_dispatch")
        feature_tags.add(tag)
        by_role.setdefault(role, []).append(row)
        if role == "primary":
            primary_entities = entities
            primary_selection_identity = identity
    for role in required_roles:
        rows = by_role.get(role, [])
        if (len(rows) != 1 or rows[0].get("entities") != primary_entities
                or {key: rows[0].get(key) for key in ("component", "geometry", "entity_dimension", "kind", "tag")} != primary_selection_identity):
            raise ExecutionContractError("SELECTION_READBACK_MISMATCH", f"weighted {role} feature did not read back the exact primary ROI", stage="post_dispatch")
    selection = definition.get("selection")
    if isinstance(selection, Mapping) and selection.get("kind") == "explicit":
        if primary_entities != selection.get("entities"):
            raise ExecutionContractError("SELECTION_READBACK_MISMATCH", "native weighted primary selection differs from the immutable metric selection", stage="post_dispatch")

    cleanup = native.get("cleanup")
    cleanup_rows = []
    if isinstance(cleanup, Mapping):
        cleanup_rows.append(cleanup)
        children = cleanup.get("children")
        if isinstance(children, list):
            cleanup_rows.extend(row for row in children if isinstance(row, Mapping))
    clean_by_tag = {row.get("tag"): row for row in cleanup_rows if isinstance(row.get("tag"), str)}
    for role in required_roles:
        row = by_role[role][0]
        record = clean_by_tag.get(row.get("feature_tag"))
        if (not isinstance(record, Mapping) or record.get("created") is not True
                or record.get("removed") is not True or record.get("verified_removed") is not True
                or record.get("cleanup_failed") is not False):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"weighted {role} numerical feature cleanup is not verified", stage="post_dispatch")

    unit_evidence = weight.get("unit_evidence")
    required_unit_keys = {
        "status", "roi_context_dimensionality",
        "global_parameter_context_unit_readback", "feature_unit_property_readback",
    }
    if not isinstance(unit_evidence, Mapping) or set(unit_evidence) != required_unit_keys:
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "native weight unit evidence is malformed", stage="post_dispatch")
    global_readback = unit_evidence.get("global_parameter_context_unit_readback")
    if (not isinstance(global_readback, Mapping)
            or global_readback.get("source") != "Model.param().evaluateUnit(expression)"
            or global_readback.get("scope") != "global_parameter_context"
            or global_readback.get("expression") != weight.get("expression")
            or not isinstance(global_readback.get("status"), str)
            or global_readback.get("status") not in {"READBACK_ONLY", "UNAVAILABLE"}):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "ParamBase unit evidence is not labeled as a global-context diagnostic", stage="post_dispatch")
    roi_unit = unit_evidence.get("roi_context_dimensionality")
    if (not isinstance(roi_unit, Mapping)
            or roi_unit.get("expression") != weight.get("expression")
            or roi_unit.get("scope") != "selected_numerical_feature_expression_over_dataset_and_selection"):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "ROI-context unit evidence is malformed or bound to another expression", stage="post_dispatch")
    if roi_unit.get("status") == "UNVERIFIED":
        if (set(roi_unit) != {"status", "expression", "scope", "reason"}
                or unit_evidence.get("status") != "UNVERIFIED"):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "unverified ROI-context unit evidence has an inconsistent status or shape", stage="post_dispatch")
        raise ExecutionContractError("WEIGHT_UNIT_UNVERIFIED", "weighted expression dimensionality is not verified in the selected numerical-feature ROI context", stage="post_dispatch")
    if (roi_unit.get("status") != "VERIFIED"
            or set(roi_unit) != {"status", "expression", "scope", "unit", "source", "binding"}
            or unit_evidence.get("status") != "VERIFIED"
            or roi_unit.get("source") != "verified_same_scope_native_readback"):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "ROI-context unit verification does not match the strict same-scope evidence contract", stage="post_dispatch")
    solution = definition.get("solution")
    expected_binding = {
        "dataset": solution.get("dataset") if isinstance(solution, Mapping) else None,
        "solution": solution.get("solution") if isinstance(solution, Mapping) else None,
        "selection": {**dict(primary_selection_identity or {}), "entities": primary_entities},
        "selected_solution_pairs": selected_pairs,
    }
    if roi_unit.get("binding") != expected_binding:
        raise ExecutionContractError("WEIGHT_UNIT_UNVERIFIED", "ROI-context dimensionality proof is not bound to the exact dataset, ROI readback, and selected solution tuples", stage="post_dispatch")
    property_readback = unit_evidence.get("feature_unit_property_readback")
    if (not isinstance(property_readback, Mapping)
            or property_readback.get("source") != "NumericalFeature.getStringArray('unit')"
            or property_readback.get("interpretation") != "configuration_readback_or_model_dependent_default"
            or property_readback.get("dimensionality_verified") is not False):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "feature unit property is misrepresented as dimensionality proof", stage="post_dispatch")
    if roi_unit.get("unit") != "1":
        raise ExecutionContractError("WEIGHT_UNIT_MISMATCH", "native ROI-context expression unit is not exactly dimensionless '1'", stage="post_dispatch")

    minimum = weight.get("minimum")
    denominator = weight.get("denominator")
    if not isinstance(minimum, Mapping) or set(minimum) != {"status", "source", "by_solution", "sampling"}:
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "native minimum evidence is malformed", stage="post_dispatch")
    if not isinstance(denominator, Mapping) or set(denominator) != {"status", "source", "strictly_positive_finite", "by_solution"}:
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "native denominator evidence is malformed", stage="post_dispatch")
    if minimum.get("status") != "VERIFIED" or denominator.get("status") != "VERIFIED" or denominator.get("strictly_positive_finite") is not True:
        raise ExecutionContractError("WEIGHT_EVIDENCE_UNAVAILABLE", "sampled minimum or denominator proof is incomplete", stage="post_dispatch")
    expected_tuples = [(row["outer"], row["inner"], row["solnum"]) for row in selected_pairs]
    minima = minimum.get("by_solution")
    denominators = denominator.get("by_solution")
    if not isinstance(minima, list) or not isinstance(denominators, list):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "weight samples are malformed", stage="post_dispatch")
    observed_minimum_tuples = []
    for row in minima:
        if not isinstance(row, Mapping) or set(row) != {"outer", "inner", "solnum", "minimum"}:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "weight minimum tuple is malformed", stage="post_dispatch")
        observed_minimum_tuples.append((row.get("outer"), row.get("inner"), row.get("solnum")))
        value = row.get("minimum")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0.0:
            raise ExecutionContractError("NEGATIVE_WEIGHT_READBACK", "native sampled weight minimum is negative or invalid", stage="post_dispatch")
    observed_denominator_tuples = []
    for row in denominators:
        if not isinstance(row, Mapping) or set(row) != {"outer", "inner", "solnum", "value"}:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "weight denominator tuple is malformed", stage="post_dispatch")
        observed_denominator_tuples.append((row.get("outer"), row.get("inner"), row.get("solnum")))
        value = row.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ExecutionContractError("ZERO_OR_INVALID_MEASURE", "native weighted denominator is not strictly positive and finite", stage="post_dispatch")
    if observed_minimum_tuples != expected_tuples or observed_denominator_tuples != expected_tuples:
        raise ExecutionContractError("SOLUTION_AXIS_MISMATCH", "weight minima and denominators do not cover the exact selected solution tuples", stage="post_dispatch")

    sampling = minimum.get("sampling")
    dimension = selection.get("entity_dimension") if isinstance(selection, Mapping) else None
    minimum_sources = {
        0: "EvalPoint.getData_native_samples",
        1: "MinLine.getReal_native_minimum",
        2: "MinSurface.getReal_native_minimum",
        3: "MinVolume.getReal_native_minimum",
    }
    if (dimension not in minimum_sources or minimum.get("source") != minimum_sources[dimension]
            or denominator.get("source") != "native_integral_of_weight_over_same_dataset_and_ROI"):
        raise ExecutionContractError("WEIGHT_EVIDENCE_UNAVAILABLE", "native minimum or denominator source does not match the selected domain dimension", stage="post_dispatch")
    if dimension == 0:
        required_sampling = {
            "method": "native_EvalPoint_values_at_selected_point_entities",
            "points_property_readback": None,
            "minimum_intorder_readback": None,
            "sampling_order": "all_selected_point_entities",
            "scope": "selected_discrete_point_entities",
            "continuous_roi_nonnegativity": "NOT_APPLICABLE_TO_DISCRETE_SELECTION",
            "integral_rule_configuration": {
                "status": "DISCRETE_POINT_SELECTION_READBACK",
                "minimum_points": "all_selected_point_entities",
                "numerator": "EvalPoint_selected_entities",
                "denominator": "EvalPoint_selected_entities",
                "actual_point_row_to_entity_identity": "UNVERIFIED_NOT_EXPOSED",
            },
            "actual_sample_coverage": "UNVERIFIED_POINT_ROW_TO_ENTITY_IDENTITY_NOT_EXPOSED",
        }
    else:
        expected_integral_rule = {
            "source": "native_numerical_feature_property_set_and_readback",
            "method": "integration",
            "intorderactive": "on",
            "intorder": _WEIGHT_INTEGRATION_ORDER,
        }
        required_sampling = {
            "method": "native_minimum_at_integration_points",
            "points_property_readback": "integration",
            "minimum_intorder_readback": _WEIGHT_INTEGRATION_ORDER,
            "sampling_order": f"Gauss_integration_points_intorder_{_WEIGHT_INTEGRATION_ORDER}",
            "scope": "sampled_integration_points_only",
            "continuous_roi_nonnegativity": "NOT_PROVEN",
            "integral_rule_configuration": {
                "status": "VERIFIED_MATCHING_METHOD_AND_ORDER_READBACKS",
                "minimum_points": "integration",
                "minimum_intorder": _WEIGHT_INTEGRATION_ORDER,
                "numerator": expected_integral_rule,
                "denominator": expected_integral_rule,
                "actual_gauss_point_set_identity": "UNVERIFIED_NATIVE_POINT_IDENTITIES_NOT_EXPOSED",
            },
            "actual_sample_coverage": "UNVERIFIED_NATIVE_GAUSS_POINT_IDENTITIES_NOT_EXPOSED",
        }
    if sampling != required_sampling:
        raise ExecutionContractError("WEIGHT_SAMPLING_UNVERIFIED", "native weight minimum does not disclose the exact sampling rule and scope", stage="post_dispatch")
    if sampling.get("actual_sample_coverage") != "VERIFIED_EXACT_NATIVE_POINT_SET_IDENTITY":
        raise ExecutionContractError("WEIGHT_SAMPLING_UNVERIFIED", "native feature readbacks do not expose exact point-set identity across the minimum, numerator, and denominator", stage="post_dispatch")
    if weight.get("status") != "VERIFIED":
        raise ExecutionContractError("WEIGHT_EVIDENCE_UNAVAILABLE", "native weighted metric evidence is not fully verified", stage="post_dispatch")
    return dict(weight)


def metric_evaluate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = normalize_arguments("metric.evaluate", arguments)
    context = _context("metric.evaluate")
    project_id = context["project_id"]
    definitions = context["store"].read_metric_definitions(project_id, include_removed=False)
    by_id = {row["metric_id"]: row for row in definitions}
    missing = [metric_id for metric_id in args["metric_ids"] if metric_id not in by_id]
    if missing:
        raise ExecutionContractError("METRIC_NOT_FOUND", "one or more metric definitions are unavailable in this project")
    selected_definitions = [by_id[metric_id] for metric_id in args["metric_ids"]]
    from ._g3_results import result_evaluate
    from ._observation_store import register_observation
    items: list[dict[str, Any]] = []
    for definition_record in selected_definitions:
        definition = definition_record["definition"]
        solution = args.get("solution", definition["solution"])
        metric_spec = {
            "expressions": [definition["expression"]],
            "solution": solution,
            "selection": definition["selection"],
            "entity_dim": definition["selection"]["entity_dimension"],
            "aggregate": definition["aggregate"],
            "complex_mode": definition["complex_mode"],
            "complex_transform_order": "before",
            "storage": "inline",
        }
        weight = definition.get("weight")
        if isinstance(weight, Mapping):
            metric_spec["weight_expression"] = weight["expression"]
        native = result_evaluate(worker, model_tag, {"spec": metric_spec}, strict_metric_evidence=True)
        status = native.get("status")
        if not isinstance(status, Mapping) or status.get("ok") is not True:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "native metric evaluation did not return a verified successful status", stage="post_dispatch")
        if native.get("cleanup", {}).get("cleanup_failed"):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "native metric evaluation cleanup is uncertain", stage="post_dispatch")
        expression_units = native.get("expression_units")
        actual_unit = expression_units.get(definition["expression"]) if isinstance(expression_units, Mapping) else None
        if not isinstance(actual_unit, str) or actual_unit != definition["expected_unit"]:
            raise ExecutionContractError("UNIT_READBACK_MISMATCH", "native expression unit readback does not match expected_unit", stage="post_dispatch")
        tuples = _selected_pairs(native)
        weight_evidence = _verified_weight_evidence(native, definition, tuples) if weight is not None else None
        threshold = definition.get("threshold")
        outcomes = []
        for item in tuples:
            value = item["value"]
            if threshold is None:
                outcome = "NOT_REQUESTED"
            elif isinstance(value, Mapping):
                raise ExecutionContractError("API_UNSUPPORTED", "threshold evaluation requires a real scalar projection")
            else:
                relation = threshold["relation"]
                allowed = {"gt": value > threshold["value"], "gte": value >= threshold["value"],
                           "lt": value < threshold["value"], "lte": value <= threshold["value"]}[relation]
                outcome = "MET" if allowed else "NOT_MET"
            outcomes.append({"outer": item["outer"], "inner": item["inner"], "solnum": item["solnum"], "status": outcome})
        observation = register_observation(worker, model_tag, native)
        items.append({
            "metric_id": definition_record["metric_id"],
            "definition_version": definition_record["version"],
            "definition": definition,
            "definition_sha256": definition_record["definition_sha256"],
            "unit": actual_unit,
            "values": tuples,
            "threshold_outcomes": outcomes,
            "observation_ref": observation,
            "native_evidence": native["strict_metric_evidence"],
            **({"weight_evidence": weight_evidence} if weight_evidence is not None else {}),
            "dataset": native.get("dataset"), "solution": native.get("solution"),
            "aggregate": native.get("aggregate"), "complex_mode": native.get("complex_mode"),
            "is_complex": bool(native.get("field_array", {}).get("is_complex")) if isinstance(native.get("field_array"), Mapping) else None,
        })
    evaluation_id = "mev_" + uuid.uuid4().hex
    evaluation = {
        "kind": "w17_metric_evaluation", "schema_version": 1,
        "evaluation_id": evaluation_id, "project_id": project_id,
        "created_model_ref": dict(context["model_ref"]), "created_revision": context["revision"],
        "producer": context["producer"], "items": items,
        "solution_override": args.get("solution"),
        "native_status": "VERIFIED_RESULT_READBACK",
    }
    evaluation["sha256"] = _json_sha256(evaluation)
    prior = context["store"].register_artifact_if_absent(evaluation_id, evaluation)
    if prior is not None and prior != evaluation:
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "metric evaluation id is already bound to different immutable evidence", stage="post_dispatch")
    return {"evaluation_id": evaluation_id, "sha256": evaluation["sha256"], "items": items, "immutable": True}


def evaluate_experiment_case_metrics(
    worker: Any,
    model_tag: str,
    metric_records: list[Mapping[str, Any]],
    *,
    case_proof: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate pinned F16 metrics inside the active experiment Worker callback.

    This calls the existing strict native result adapter directly.  It neither
    dispatches another operation nor runs a Study.  The attempt claim and the
    native solution/parameter readbacks must still match before the immutable
    evaluation artifact can be registered.
    """
    context = _context("experiment.run metric evaluation")
    if (not isinstance(metric_records, list) or not metric_records
            or not isinstance(case_proof, Mapping)):
        raise ExecutionContractError("INVALID_METRIC_CASE_BINDING", "an experiment case requires pinned metrics and native case evidence", stage="validation")
    project_id = context["project_id"]
    model_ref = context["model_ref"]
    if (case_proof.get("project_id") != project_id
            or case_proof.get("model_ref") != model_ref
            or case_proof.get("model_revision") != context["revision"]
            or case_proof.get("producer") != context["producer"]
            or case_proof.get("session_id") != model_ref.get("session_id")):
        raise ExecutionContractError("INVALID_METRIC_CASE_BINDING", "experiment case identity differs from its managed Worker context", stage="validation")

    attempt_id = case_proof.get("attempt_id")
    experiment_id = case_proof.get("experiment_id")
    case_id = case_proof.get("case_id")
    run_id = case_proof.get("run_id")
    if (not isinstance(attempt_id, str) or not attempt_id.startswith("att_")
            or not isinstance(experiment_id, str) or not experiment_id.startswith("exp_")
            or not isinstance(case_id, str) or not case_id.startswith("case-")
            or not isinstance(run_id, str) or not run_id.startswith("run_")):
        raise ExecutionContractError("INVALID_METRIC_CASE_BINDING", "experiment attempt identity is malformed", stage="validation")
    attempt_key = f"w21experimentattempt:{experiment_id}:{case_id}"
    attempt = context["store"].get_metadata("artifacts", attempt_key)
    attempt_digest = None
    if isinstance(attempt, Mapping):
        attempt_digest = hashlib.sha256(json.dumps(
            {key: value for key, value in attempt.items() if key != "sha256"},
            sort_keys=True, allow_nan=False,
        ).encode("utf-8")).hexdigest()
    if (not isinstance(attempt, Mapping)
            or attempt.get("kind") != "w21experiment_case_attempt"
            or attempt.get("attempt_id") != attempt_id
            or attempt.get("experiment_id") != experiment_id
            or attempt.get("run_id") != run_id
            or attempt.get("case_id") != case_id
            or type(attempt.get("case_ordinal")) is not int
            or type(case_proof.get("case_ordinal")) is not int
            or attempt.get("case_ordinal") != case_proof.get("case_ordinal")
            or attempt.get("study") != case_proof.get("study")
            or attempt.get("project_id") != project_id
            or attempt.get("session_id") != model_ref.get("session_id")
            or attempt.get("model_ref") != model_ref
            or attempt.get("model_revision") != context["revision"]
            or attempt.get("producer") != context["producer"]
            or attempt.get("design_sha256") != case_proof.get("design_sha256")
            or attempt.get("parameters") != case_proof.get("planned_parameters")
            or attempt.get("units") != case_proof.get("units")
            or attempt.get("metric_definition_refs") != [
                {"metric_id": row.get("metric_id"), "version": row.get("version"),
                 "definition_sha256": row.get("definition_sha256")}
                for row in metric_records if isinstance(row, Mapping)
            ]
            or attempt.get("sha256") != attempt_digest
            or attempt.get("sha256") != case_proof.get("attempt_sha256")):
        raise ExecutionContractError("INVALID_METRIC_CASE_BINDING", "experiment case attempt is absent, tampered, or bound to a different case", stage="validation")

    if (case_proof.get("parameters") != attempt.get("parameters")
            or case_proof.get("case_ordinal") != attempt.get("case_ordinal")
            or case_proof.get("study") != attempt.get("study")
            or case_proof.get("dataset") is None
            or case_proof.get("solution") is None
            or case_proof.get("validation_status") != "PASS"
            or not isinstance(case_proof.get("solve_result_sha256"), str)
            or not isinstance(case_proof.get("solution_indices"), Mapping)
            or case_proof["solution_indices"].get("binding_complete") is not True
            or case_proof["solution_indices"].get("solution") != case_proof.get("solution")):
        raise ExecutionContractError("INVALID_METRIC_CASE_BINDING", "case solve or parameter evidence does not match the pre-dispatch attempt", stage="validation")

    refs = [
        {"metric_id": row.get("metric_id"), "version": row.get("version"),
         "definition_sha256": row.get("definition_sha256")}
        for row in metric_records if isinstance(row, Mapping)
    ]
    try:
        resolved_records = context["store"].read_metric_definition_versions(
            project_id, refs, require_latest=False, require_active_head=True,
        )
    except Exception as exc:
        raise ExecutionContractError("UNVERIFIED_METRIC_DEFINITION", "pinned metric definitions are no longer active or failed integrity validation", stage="validation") from exc
    if resolved_records != [dict(row) for row in metric_records]:
        raise ExecutionContractError("UNVERIFIED_METRIC_DEFINITION", "experiment metric snapshots differ from their immutable project versions", stage="validation")

    from ._metric_contract import definition_sha256, normalize_definition
    for record in resolved_records:
        normalized = normalize_definition(record.get("definition"))
        if (normalized != record.get("definition")
                or definition_sha256(normalized) != record.get("definition_sha256")):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "pinned metric semantic definition failed hash validation", stage="validation")

    parameter_names = list(case_proof.get("parameters", {}))
    parameter_readback = case_proof.get("parameter_readback")
    rows = parameter_readback.get("parameters") if isinstance(parameter_readback, Mapping) else None
    if (not isinstance(parameter_readback, Mapping) or not isinstance(rows, list)
            or parameter_readback.get("case_attempt_id") != attempt_id
            or {row.get("name") for row in rows if isinstance(row, Mapping)} != set(parameter_names)):
        raise ExecutionContractError("PARAMETER_READBACK_UNAVAILABLE", "case attempt has no complete native parameter readback", stage="validation")
    for row in rows:
        if (not isinstance(row, Mapping) or not isinstance(row.get("evaluated"), Mapping)
                or row.get("case_attempt_id") != attempt_id):
            raise ExecutionContractError("PARAMETER_READBACK_UNAVAILABLE", "case parameter readback is incomplete", stage="validation")
        evaluated = row["evaluated"]
        data = evaluated.get("data")
        name = row.get("name")
        expected_expression = repr(float(case_proof["parameters"][name])) + "[" + case_proof["units"][name] + "]" if name in case_proof["parameters"] else None
        value_readback = row.get("case_value_readback")
        expected_value = case_proof["parameters"].get(name) if isinstance(name, str) else None
        expected_unit = case_proof["units"].get(name) if isinstance(name, str) else None
        observed_value = value_readback.get("value") if isinstance(value_readback, Mapping) else None
        tolerance = value_readback.get("relative_tolerance") if isinstance(value_readback, Mapping) else None
        if (evaluated.get("kind") not in {"float64", "int64"}
                or isinstance(data, bool) or not isinstance(data, (int, float))
                or not math.isfinite(float(data))
                or not isinstance(expected_expression, str)
                or row.get("expression") != expected_expression
                or isinstance(observed_value, bool) or not isinstance(observed_value, (int, float))
                or not math.isfinite(float(observed_value))
                or isinstance(expected_value, bool) or not isinstance(expected_value, (int, float))
                or not math.isfinite(float(expected_value))
                or isinstance(tolerance, bool) or tolerance != 1e-12
                or value_readback.get("unit") != ("1" if expected_unit in {"1", "dimensionless"} else expected_unit)
                or not math.isclose(float(observed_value), float(expected_value), rel_tol=1e-12, abs_tol=0.0)
                or not isinstance(value_readback.get("source"), str)
                or not value_readback.get("source")
                or (case_proof["units"][name] not in {"1", "dimensionless"}
                    and row.get("unit") != case_proof["units"][name])):
            raise ExecutionContractError("PARAMETER_READBACK_UNAVAILABLE", "case parameter did not resolve to a finite native scalar", stage="validation")

    from ._observation_store import register_observation, resolve_observation, solution_identity
    from ._g3_results import result_evaluate
    actual_solution_identity = solution_identity(worker, model_tag, case_proof["solution"])
    solution_times = actual_solution_identity.get("times") if isinstance(actual_solution_identity, Mapping) else None
    sampled_times = case_proof["solution_indices"].get("time_values")
    if (actual_solution_identity != case_proof.get("solution_identity")
            or _default_json_sha256(actual_solution_identity) != case_proof.get("solution_identity_sha256")
            or actual_solution_identity.get("solution") != case_proof.get("solution")
            or actual_solution_identity.get("study") != case_proof.get("study")
            or not isinstance(solution_times, list)
            or not solution_times
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) for value in solution_times)
            or not isinstance(sampled_times, list) or sampled_times != solution_times
            or not all(isinstance(actual_solution_identity.get(key), str) and actual_solution_identity[key]
                       for key in ("computation_date", "computation_version"))):
        raise ExecutionContractError("CASE_SOLUTION_IDENTITY_MISMATCH", "native solution identity changed before metric evaluation", stage="post_dispatch")

    source_ref = case_proof.get("sample_observation_ref")
    try:
        source_record, source_payload = resolve_observation(
            worker, model_tag, source_ref, allow_historical=True,
        )
    except Exception as exc:
        raise ExecutionContractError("CASE_SAMPLE_OBSERVATION_UNVERIFIED", "case W21 sample bytes failed project/worker/hash verification", stage="validation") from exc
    if (not isinstance(source_record, Mapping)
            or source_record.get("kind") != "w17_observation"
            or source_record.get("project_id") != project_id
            or source_record.get("producer") != context["producer"]
            or source_record.get("model_ref") != model_ref
            or source_record.get("dataset") != case_proof["dataset"]
            or source_record.get("solution") != case_proof["solution"]
            or source_record.get("source_identity") != actual_solution_identity
            or source_payload.get("case_attempt_id") != attempt_id
            or source_record.get("sha256") != source_ref.get("sha256")):
        raise ExecutionContractError("CASE_SAMPLE_OBSERVATION_UNVERIFIED", "case W21 sample observation does not bind the same project, solution, and Worker", stage="validation")

    items: list[dict[str, Any]] = []
    for record in resolved_records:
        definition = record["definition"]
        selector = definition["solution"]
        if selector.get("dataset") != case_proof["dataset"]:
            raise ExecutionContractError("METRIC_CASE_DATASET_MISMATCH", "metric definition does not name the case's sampled dataset", stage="validation")
        if selector.get("solution") is not None and selector["solution"] != case_proof["solution"]:
            raise ExecutionContractError("METRIC_CASE_SOLUTION_MISMATCH", "metric definition selects a different solution than this case", stage="validation")
        bound_solution = {"dataset": case_proof["dataset"], "solution": case_proof["solution"]}
        for axis in ("outer", "inner"):
            if axis in selector:
                bound_solution[axis] = selector[axis]
        metric_spec = {
            "expressions": [definition["expression"]],
            "solution": bound_solution,
            "selection": definition["selection"],
            "entity_dim": definition["selection"]["entity_dimension"],
            "aggregate": definition["aggregate"],
            "complex_mode": definition["complex_mode"],
            "complex_transform_order": "before",
            "storage": "inline",
        }
        weight = definition.get("weight")
        if isinstance(weight, Mapping):
            metric_spec["weight_expression"] = weight["expression"]
        native = result_evaluate(worker, model_tag, {"spec": metric_spec}, strict_metric_evidence=True)
        status = native.get("status")
        if (not isinstance(status, Mapping) or status.get("ok") is not True
                or native.get("dataset") != case_proof["dataset"]
                or native.get("solution") != case_proof["solution"]):
            raise ExecutionContractError("CASE_METRIC_SOLUTION_MISMATCH", "native metric readback does not bind to this case's exact solution", stage="post_dispatch")
        native = copy.deepcopy(native)
        native["case_attempt_id"] = attempt_id
        native["case_solution_identity_sha256"] = case_proof["solution_identity_sha256"]
        if native.get("cleanup", {}).get("cleanup_failed"):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "native metric evaluation cleanup is uncertain", stage="post_dispatch")
        expression_units = native.get("expression_units")
        actual_unit = expression_units.get(definition["expression"]) if isinstance(expression_units, Mapping) else None
        if not isinstance(actual_unit, str) or actual_unit != definition["expected_unit"]:
            raise ExecutionContractError("UNIT_READBACK_MISMATCH", "native expression unit readback does not match the frozen metric definition", stage="post_dispatch")
        values = _selected_pairs(native)
        weight_evidence = _verified_weight_evidence(native, definition, values) if weight is not None else None
        outcomes = []
        threshold = definition.get("threshold")
        for item in values:
            value = item["value"]
            if threshold is None:
                outcome = "NOT_REQUESTED"
            elif isinstance(value, Mapping):
                raise ExecutionContractError("API_UNSUPPORTED", "threshold evaluation requires a real scalar projection", stage="validation")
            else:
                relation = threshold["relation"]
                satisfied = {"gt": value > threshold["value"], "gte": value >= threshold["value"],
                             "lt": value < threshold["value"], "lte": value <= threshold["value"]}[relation]
                outcome = "MET" if satisfied else "NOT_MET"
            outcomes.append({"outer": item["outer"], "inner": item["inner"],
                             "solnum": item["solnum"], "status": outcome})
        observation = register_observation(worker, model_tag, native)
        items.append({
            "metric_id": record["metric_id"],
            "case_attempt_id": attempt_id,
            "case_attempt_sha256": attempt["sha256"],
            "case_solution_identity_sha256": case_proof["solution_identity_sha256"],
            "definition_version": record["version"],
            "definition": copy.deepcopy(definition),
            "definition_sha256": record["definition_sha256"],
            "unit": actual_unit,
            "values": values,
            "threshold_outcomes": outcomes,
            "observation_ref": observation,
            "native_evidence": native["strict_metric_evidence"],
            **({"weight_evidence": weight_evidence} if weight_evidence is not None else {}),
            "dataset": native["dataset"],
            "solution": native["solution"],
            "aggregate": native.get("aggregate"),
            "complex_mode": native.get("complex_mode"),
            "is_complex": bool(native.get("field_array", {}).get("is_complex")) if isinstance(native.get("field_array"), Mapping) else None,
        })

    after_identity = solution_identity(worker, model_tag, case_proof["solution"])
    if after_identity != actual_solution_identity:
        raise ExecutionContractError("CASE_SOLUTION_IDENTITY_MISMATCH", "native solution changed during metric evaluation", stage="post_dispatch")
    evaluation_id = "mev_" + uuid.uuid4().hex
    binding = dict(case_proof)
    evaluation = {
        "kind": "w17_metric_evaluation",
        "schema_version": 1,
        "evaluation_id": evaluation_id,
        "project_id": project_id,
        "created_model_ref": dict(model_ref),
        "created_revision": context["revision"],
        "producer": context["producer"],
        "evaluation_source": "experiment.run_case_same_worker_callback",
        "case_binding": binding,
        "case_binding_sha256": _json_sha256(binding),
        "items": items,
        "native_status": "VERIFIED_RESULT_READBACK",
    }
    evaluation["sha256"] = _json_sha256(evaluation)
    prior = context["store"].register_artifact_if_absent(evaluation_id, evaluation)
    if prior is not None and prior != evaluation:
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "experiment metric evaluation id is already bound to different immutable evidence", stage="post_dispatch")
    return {
        "evaluation_id": evaluation_id,
        "sha256": evaluation["sha256"],
        "case_attempt_id": attempt_id,
        "case_binding_sha256": evaluation["case_binding_sha256"],
        "metric_definition_refs": [
            {"metric_id": row["metric_id"], "version": row["version"],
             "definition_sha256": row["definition_sha256"]}
            for row in resolved_records
        ],
        "observation_artifact_refs": [
            {"metric_id": item["metric_id"], "observation_id": item["observation_ref"]["observation_id"],
             "sha256": item["observation_ref"]["sha256"]}
            for item in items
        ],
        "source": evaluation["evaluation_source"],
    }


def _finite_value(value: Any) -> float | complex:
    if isinstance(value, Mapping) and set(value) == {"real", "imag"}:
        real, imag = value["real"], value["imag"]
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in (real, imag)):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "stored complex metric value is invalid", stage="validation")
        return complex(float(real), float(imag))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "stored real metric value is invalid", stage="validation")
    return float(value)


def _resolve_historical_evaluation(context: Mapping[str, Any], reference: Mapping[str, str], worker: Any) -> dict[str, Any]:
    store = context["store"]
    record = store.get_metadata("artifacts", reference["evaluation_id"])
    if (not isinstance(record, Mapping) or record.get("kind") != "w17_metric_evaluation"
            or record.get("evaluation_id") != reference["evaluation_id"]
            or record.get("project_id") != context["project_id"]
            or record.get("sha256") != reference["sha256"]
            or _json_sha256({key: value for key, value in record.items() if key != "sha256"}) != record.get("sha256")):
            raise ExecutionContractError("UNVERIFIED_METRIC_EVALUATION", "evaluation reference is missing, foreign, or hash-mismatched", stage="validation")
    producer = record.get("producer")
    operation = store.get_operation(producer) if isinstance(producer, str) else None
    if (not operation or operation.get("status") != "SUCCEEDED"
            or not _is_metric_evaluate_producer(operation, context["project_id"], record["evaluation_id"], record["sha256"])):
        raise ExecutionContractError("UNVERIFIED_METRIC_EVALUATION", "evaluation producer is not the successful project-scoped operation that published this evaluation", stage="validation")
    items = record.get("items")
    if not isinstance(items, list):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "evaluation items are malformed", stage="validation")
    from ._artifact_store import ArtifactStore, trusted_project_root
    root = trusted_project_root(worker)
    resolved_values: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "evaluation item is malformed", stage="validation")
        metric_id = item.get("metric_id")
        definition = item.get("definition")
        if (not isinstance(metric_id, str) or not isinstance(definition, Mapping)
                or definition_sha256(definition) != item.get("definition_sha256")):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "evaluation item has no hash-bound scientific definition", stage="validation")
        if metric_id in resolved_values:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "evaluation contains duplicate metric items", stage="validation")
        expected_solution = record.get("solution_override") or definition.get("solution")
        if not isinstance(expected_solution, Mapping):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "evaluation has no exact requested solution binding", stage="validation")
        ref = item.get("observation_ref")
        if not isinstance(ref, Mapping) or set(ref) != {"observation_id", "sha256"}:
            raise ExecutionContractError("UNVERIFIED_OBSERVATION", "evaluation has no exact registered observation reference", stage="validation")
        obs = store.get_metadata("artifacts", ref["observation_id"])
        if (not isinstance(obs, Mapping) or obs.get("kind") != "w17_observation"
                or obs.get("project_id") != context["project_id"]
                or obs.get("model_ref") != record.get("created_model_ref")
                or obs.get("sample_revision") != record.get("created_revision")
                or obs.get("producer") != producer or obs.get("sha256") != ref["sha256"]):
            raise ExecutionContractError("UNVERIFIED_OBSERVATION", "observation does not bind to this project, producer, model, revision, and hash", stage="validation")
        artifact = obs.get("artifact")
        if not isinstance(artifact, Mapping):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "observation artifact descriptor is malformed", stage="validation")
        if artifact.get("sha256") != ref["sha256"]:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "observation descriptor hash differs from its registered reference", stage="validation")
        path = ArtifactStore(root).resolve_safe_path(artifact.get("file_path"), allow_overwrite=True)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "observation artifact bytes do not match the registered hash", stage="validation")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "observation artifact is not valid JSON", stage="validation") from exc
        metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
        field_array = metadata.get("field_array") if isinstance(metadata, Mapping) else None
        expression = definition.get("expression")
        expression_units = metadata.get("expression_units") if isinstance(metadata, Mapping) else None
        evidence = metadata.get("strict_metric_evidence") if isinstance(metadata, Mapping) else None
        if (not isinstance(metadata, Mapping) or not isinstance(field_array, Mapping)
                or metadata.get("expressions") != [expression]
                or metadata.get("dataset") != item.get("dataset")
                or metadata.get("dataset") != expected_solution.get("dataset")
                or metadata.get("solution") != item.get("solution")
                or ("solution" in expected_solution and metadata.get("solution") != expected_solution["solution"])
                or metadata.get("aggregate") != item.get("aggregate")
                or metadata.get("aggregate") != definition.get("aggregate")
                or metadata.get("complex_mode") != item.get("complex_mode")
                or metadata.get("complex_mode") != definition.get("complex_mode", "real")
                or item.get("unit") != definition.get("expected_unit")
                or metadata.get("is_complex") is not item.get("is_complex")
                or field_array.get("is_complex") is not item.get("is_complex")
                or not isinstance(expression_units, Mapping)
                or expression_units != {expression: item.get("unit")}
                or not isinstance(evidence, Mapping) or evidence.get("status") != "VERIFIED"
                or evidence.get("selection_source") != "actual_transient_numerical_feature_readback"
                or evidence.get("selection_membership_identical") is not True
                or not isinstance(evidence.get("selection_features"), list)
                or not evidence.get("selection_features")
                or evidence.get("requested_solution") != expected_solution
                or evidence != item.get("native_evidence")
                or evidence.get("expression_unit_readback") != expression_units):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "observation artifact does not bind to the evaluation definition and native readbacks", stage="validation")
        selection_features = evidence["selection_features"]
        if any(not isinstance(feature, Mapping) or not isinstance(feature.get("entities"), list)
               or not feature["entities"] or any(isinstance(entity, bool) or not isinstance(entity, int) or entity < 1
                                                  for entity in feature["entities"])
               for feature in selection_features):
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "native selection readback has no exact positive entity membership", stage="validation")
        if definition["selection"]["kind"] == "explicit":
            primary = [feature for feature in selection_features if feature.get("role") == "primary"]
            expected_entities = definition["selection"]["entities"]
            if len(primary) != 1 or primary[0]["entities"] != expected_entities:
                raise ExecutionContractError("INTEGRITY_COMPROMISED", "native primary selection membership differs from the immutable metric definition", stage="validation")
        result_for_recomputation = {
            "field_array": field_array,
            "strict_metric_evidence": evidence,
        }
        values = _selected_pairs(result_for_recomputation)
        saved_values = item.get("values")
        if saved_values != values:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "evaluation values differ from the authorized observation artifact", stage="validation")
        if definition.get("weight") is not None:
            weight_evidence = _verified_weight_evidence(
                {"cleanup": metadata.get("cleanup"), "strict_metric_evidence": evidence},
                definition,
                values,
            )
            if item.get("weight_evidence") != weight_evidence:
                raise ExecutionContractError("INTEGRITY_COMPROMISED", "evaluation weight evidence differs from its authorized observation artifact", stage="validation")
        elif "weight_evidence" in item:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "unweighted metric contains unauthorized weight evidence", stage="validation")
        resolved_values[metric_id] = values
    resolved = dict(record)
    # This transient view is built only from the project-authorized, producer-
    # bound observation bytes above. Comparison never consumes caller values or
    # the cached result vector without re-deriving it from those bytes.
    resolved["_artifact_values"] = resolved_values
    return resolved


def _is_metric_evaluate_producer(operation: Mapping[str, Any], project_id: str, evaluation_id: str, sha256: str) -> bool:
    metadata = operation.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    outer = metadata.get("arguments")
    execution = metadata.get("execution")
    nested_operation = operation.get("operation")
    nested_args: Mapping[str, Any] | None = None
    if nested_operation in {"registry_call", "operation_call"} and isinstance(outer, Mapping):
        nested_operation = outer.get("operation_id")
        nested_args = outer.get("arguments") if isinstance(outer.get("arguments"), Mapping) else None
    if nested_operation != "metric.evaluate":
        return False
    claims = []
    for candidate in (execution, outer, nested_args):
        if isinstance(candidate, Mapping) and "project_id" in candidate:
            claims.append(candidate.get("project_id"))
    if not claims or not all(claim == project_id for claim in claims):
        return False
    result = operation.get("result")
    for _ in range(3):
        if not isinstance(result, Mapping):
            return False
        if result.get("evaluation_id") == evaluation_id and result.get("sha256") == sha256:
            return True
        result = result.get("data")
    return False


def metric_compare(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = normalize_arguments("metric.compare", arguments)
    context = _context("metric.compare")
    if any("experiment_id" in case for case in args["cases"]):
        raise ExecutionContractError("UNVERIFIED_METRIC_ASSOCIATION", "W21 case records have no persisted association to this metric definition; raw case values cannot substitute", stage="validation")
    evaluations = [_resolve_historical_evaluation(context, case, worker) for case in args["cases"]]
    output = []
    for metric_id in args["metric_ids"]:
        tolerance = args["tolerances"][metric_id]
        vectors = []
        definition_hashes = []
        observed_units = []
        complex_flags = []
        aggregates = []
        complex_modes = []
        weight_evidence_rows = []
        for evaluation in evaluations:
            matches = [item for item in evaluation["items"] if item.get("metric_id") == metric_id]
            if len(matches) != 1:
                raise ExecutionContractError("UNVERIFIED_METRIC_EVALUATION", "evaluation does not contain exactly this metric id", stage="validation")
            item = matches[0]
            definition_hashes.append(item.get("definition_sha256"))
            observed_units.append(item.get("unit"))
            complex_flags.append(item.get("is_complex"))
            aggregates.append(item.get("aggregate"))
            complex_modes.append(item.get("complex_mode"))
            weight_evidence_rows.append(item.get("weight_evidence"))
            vector = evaluation.get("_artifact_values", {}).get(metric_id)
            if not isinstance(vector, list) or not vector:
                raise ExecutionContractError("INTEGRITY_COMPROMISED", "authorized observation artifact has no recomputed metric tuples", stage="validation")
            vectors.append(vector)
        if (len(set(definition_hashes)) != 1 or not isinstance(definition_hashes[0], str)
                or len(set(observed_units)) != 1 or not isinstance(observed_units[0], str)
                or len(set(complex_flags)) != 1 or type(complex_flags[0]) is not bool
                or len(set(aggregates)) != 1 or len(set(complex_modes)) != 1):
            raise ExecutionContractError("UNVERIFIED_METRIC_EVALUATION", "comparison cases do not share one immutable definition and native unit/complex semantics", stage="validation")
        if tolerance["unit"] != observed_units[0]:
            raise ExecutionContractError("UNIT_READBACK_MISMATCH", "comparison tolerance unit does not match the native unit readback", stage="validation")
        tuple_keys = [
            [(item.get("outer"), item.get("inner"), item.get("solnum")) for item in vector]
            for vector in vectors
        ]
        if any(keys != tuple_keys[0] for keys in tuple_keys[1:]):
            raise ExecutionContractError("SOLUTION_AXIS_MISMATCH", "stored metric evaluations have different exact outer/inner/solnum tuples", stage="validation")
        if complex_flags[0] and tolerance.get("complex_distance") != "modulus":
            raise ExecutionContractError("API_UNSUPPORTED", "complex comparison requires complex_distance=modulus", stage="validation")
        if not complex_flags[0] and "complex_distance" in tolerance:
            raise ExecutionContractError("INVALID_REQUEST", "complex_distance is only valid when native result readback is complex", stage="validation")
        rows = []
        for index, key in enumerate(tuple_keys[0]):
            values = [_finite_value(vector[index]["value"]) for vector in vectors]
            baseline = values[0]
            comparisons = []
            for value in values[1:]:
                delta = abs(value - baseline)
                scale = max(abs(value), abs(baseline))
                relative_allowance = tolerance["relative"] * scale
                allowed = tolerance["absolute"] + relative_allowance
                if not all(math.isfinite(float(number)) for number in (delta, scale, relative_allowance, allowed)):
                    raise ExecutionContractError(
                        "COMPARISON_OVERFLOW",
                        "finite inputs produced a non-finite comparison delta or tolerance bound",
                        stage="validation",
                    )
                comparisons.append({"absolute_delta": float(delta), "allowed_delta": float(allowed), "status": "WITHIN_TOLERANCE" if delta <= allowed else "OUTSIDE_TOLERANCE"})
            rows.append({"outer": key[0], "inner": key[1], "solnum": key[2], "values": [
                {"real": float(value.real), "imag": float(value.imag)} if isinstance(value, complex) else float(value)
                for value in values
            ], "comparisons": comparisons})
        compared = {"metric_id": metric_id, "unit": observed_units[0], "definition_sha256": definition_hashes[0], "aggregate": aggregates[0], "complex_mode": complex_modes[0], "is_complex": complex_flags[0], "tuple_results": rows}
        if any(item is not None for item in weight_evidence_rows):
            if not all(isinstance(item, Mapping) for item in weight_evidence_rows):
                raise ExecutionContractError("UNVERIFIED_METRIC_EVALUATION", "weighted comparison is missing per-case native weight evidence", stage="validation")
            first_weight = weight_evidence_rows[0]
            first_sampling = first_weight.get("minimum", {}).get("sampling") if isinstance(first_weight.get("minimum"), Mapping) else None
            first_unit = first_weight.get("unit_evidence", {}).get("roi_context_dimensionality") if isinstance(first_weight.get("unit_evidence"), Mapping) else None
            for item in weight_evidence_rows[1:]:
                sampling = item.get("minimum", {}).get("sampling") if isinstance(item.get("minimum"), Mapping) else None
                unit = item.get("unit_evidence", {}).get("roi_context_dimensionality") if isinstance(item.get("unit_evidence"), Mapping) else None
                if sampling != first_sampling or unit != first_unit:
                    raise ExecutionContractError("UNVERIFIED_METRIC_EVALUATION", "weighted comparison cases do not share the same verified sampling and unit semantics", stage="validation")
            compared["weight_validation"] = {
                "status": "VERIFIED",
                "unit_evidence": first_weight["unit_evidence"],
                "sampling": first_sampling,
                "per_case": [
                    {
                        "evaluation_id": evaluation["evaluation_id"],
                        "minimum": item["minimum"]["by_solution"],
                        "denominator": item["denominator"]["by_solution"],
                    }
                    for evaluation, item in zip(evaluations, weight_evidence_rows)
                ],
            }
        output.append(compared)
    return {"comparisons": output, "case_count": len(evaluations), "source": "authorized_immutable_metric_evaluations", "historical_resolution": "project_producer_hash_and_observation_artifact_verified"}


OPERATIONS = {
    "metric.define": metric_define,
    "metric.list": metric_list,
    "metric.evaluate": metric_evaluate,
    "metric.remove": metric_remove,
    "metric.compare": metric_compare,
}
