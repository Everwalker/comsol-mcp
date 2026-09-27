"""F16 project-scoped metric definitions, native evaluations, and comparisons."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping
from typing import Any

from ._execution_contract import ExecutionContractError
from ._metric_contract import definition_sha256, normalize_arguments


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
    if any(row["definition"].get("weight") for row in selected_definitions):
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "weighted metrics remain unavailable until native dimensionless-unit, nonnegative-field, and positive-denominator readback is implemented",
            stage="validation",
        )
    from ._g3_results import result_evaluate
    from ._observation_store import register_observation
    items: list[dict[str, Any]] = []
    for definition_record in selected_definitions:
        definition = definition_record["definition"]
        solution = args.get("solution", definition["solution"])
        native = result_evaluate(worker, model_tag, {
            "spec": {
                "expressions": [definition["expression"]],
                "solution": solution,
                "selection": definition["selection"],
                "entity_dim": definition["selection"]["entity_dimension"],
                "aggregate": definition["aggregate"],
                "complex_mode": definition["complex_mode"],
                "complex_transform_order": "before",
                "storage": "inline",
            },
        }, strict_metric_evidence=True)
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
        output.append({"metric_id": metric_id, "unit": observed_units[0], "definition_sha256": definition_hashes[0], "aggregate": aggregates[0], "complex_mode": complex_modes[0], "is_complex": complex_flags[0], "tuple_results": rows})
    return {"comparisons": output, "case_count": len(evaluations), "source": "authorized_immutable_metric_evaluations", "historical_resolution": "project_producer_hash_and_observation_artifact_verified"}


OPERATIONS = {
    "metric.define": metric_define,
    "metric.list": metric_list,
    "metric.evaluate": metric_evaluate,
    "metric.remove": metric_remove,
    "metric.compare": metric_compare,
}
