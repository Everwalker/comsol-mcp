"""Closed, read-only W23 Port/BMA discovery helpers for the R21 runner.

The native caller supplies only public G2 ``node.*`` operation receipts. This
module validates those receipts and builds an explicit evidence graph; it does
not set properties, run studies/solvers, save models, or assign physical meaning
to a direction sign.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from uuid import UUID
from pathlib import Path
from typing import Any, Mapping, Sequence

from comsol_mcp._g2_contract import NodePath
from tools import w23_te_science_protocol as protocol


MAX_DIRECTION_CALLS = 192
MAX_CHILDREN_PER_LIST = 128
MAX_ROOT_ENTITIES = 32
MAX_FEATURES_PER_PARENT = 64
MAX_SOLVER_TREE_DEPTH = 4
MAX_SOLVER_TREE_NODES = 96

PORT_PROPERTIES = (
    "PortType", "PortName", "PortOrientation", "PortExcitation", "Pin",
    "PortModeNumber", "Thetap", "PortSlit", "SlitType",
)
BMA_PROPERTIES = ("PortName", "modeFreq")
STUDY_STEP_PROPERTIES = ("study", "studystep")
STORE_SOLUTION_PROPERTIES = ("sol",)
DATASET_PROPERTIES = ("solution",)
ALLOWED_PROPERTIES = frozenset(
    (*PORT_PROPERTIES, *BMA_PROPERTIES, *STUDY_STEP_PROPERTIES,
     *STORE_SOLUTION_PROPERTIES, *DATASET_PROPERTIES)
)
READONLY_OPERATION_IDS = frozenset({
    "node.children", "node.inspect", "node.property_schema", "node.property_get",
})
_ALLOWED_COLLECTIONS = frozenset({"component", "physics", "feature", "study", "sol", "dataset"})
_ALLOWED_ACCESSORS = frozenset({"physics", "feature", "result", "dataset"})


class DirectionDiscoveryError(ValueError):
    """A read-only discovery response is incomplete or ambiguous."""


def node_path(segments: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    path = {"segments": [dict(row) for row in segments]}
    NodePath.from_wire(path)
    if not _safe_path(path):
        raise DirectionDiscoveryError("node path is outside the R21 read-only scope")
    return path


def _safe_path(raw: Any) -> bool:
    try:
        parsed = NodePath.from_wire(raw)
    except Exception:
        return False
    if len(parsed.segments) > MAX_SOLVER_TREE_DEPTH + 2:
        return False
    rows = [row.as_dict() for row in parsed.segments]
    if not rows:
        return True
    if rows[0] == {"collection": "component", "tag": "comp1"}:
        if len(rows) == 1:
            return True
        if len(rows) == 2 and rows[1].get("collection") == "physics":
            return True
        if len(rows) == 3 and rows[1].get("collection") == "physics" and rows[2].get("collection") == "feature":
            return True
        return False
    if len(rows) == 1 and rows[0].get("collection") in {"study", "sol"}:
        return True
    if rows[0].get("collection") == "study":
        return len(rows) == 2 and rows[1].get("collection") == "feature"
    if rows[0].get("collection") == "sol":
        return 1 <= len(rows) <= MAX_SOLVER_TREE_DEPTH + 1 and all(
            row.get("collection") == "feature" for row in rows[1:]
        )
    if rows[0] == {"accessor": "result"}:
        return (len(rows) == 1 or
                (len(rows) == 2 and rows[1].get("collection") == "dataset"))
    return False


def _bounded_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 128 and not any(c in value for c in "/\\\x00")


def _valid_read_arguments(operation_id: str, arguments: Any) -> bool:
    if not isinstance(arguments, Mapping):
        return False
    if operation_id == "node.children":
        if set(arguments) not in ({"path", "limit"}, {"path", "limit", "collection"}):
            return False
        if (not _safe_path(arguments.get("path"))
                or type(arguments.get("limit")) is not int
                or arguments["limit"] != MAX_CHILDREN_PER_LIST):
            return False
        return ("collection" not in arguments
                or (isinstance(arguments["collection"], str)
                    and arguments["collection"] in _ALLOWED_COLLECTIONS))
    if operation_id == "node.inspect":
        return (set(arguments) == {"path", "include_properties"}
                and _safe_path(arguments.get("path"))
                and arguments.get("include_properties") is False)
    if operation_id == "node.property_schema":
        return (set(arguments) == {"path", "name"} and _safe_path(arguments.get("path"))
                and _bounded_text(arguments.get("name"))
                and arguments["name"] in ALLOWED_PROPERTIES)
    if operation_id == "node.property_get":
        names = arguments.get("names")
        return (set(arguments) == {"path", "names"} and _safe_path(arguments.get("path"))
                and isinstance(names, list) and len(names) == 1
                and _bounded_text(names[0]) and names[0] in ALLOWED_PROPERTIES)
    return False


def _allowed_direction_operation_request(request: Mapping[str, Any]) -> bool:
    """Validate the closed inner operation request before it is wrapped for dispatch."""
    if not isinstance(request, Mapping) or set(request) != {"operation", "arguments"}:
        return False
    if request.get("operation") != "operation_call":
        return False
    body = request.get("arguments")
    if not isinstance(body, Mapping) or set(body) != {"operation_id", "arguments"}:
        return False
    operation_id = body.get("operation_id")
    return operation_id in READONLY_OPERATION_IDS and _valid_read_arguments(operation_id, body.get("arguments"))


def _valid_uuid_request_token(value: Any, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    suffix = value[len(prefix):]
    try:
        return str(UUID(suffix)) == suffix
    except (ValueError, AttributeError, TypeError):
        return False


def _valid_model_ref_identity(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "session_id", "server_instance_id", "model_tag", "generation"}:
        return False
    return (type(value.get("schema_version")) is int and value.get("schema_version") == 1
            and all(isinstance(value.get(key), str) and value.get(key)
                    for key in ("session_id", "server_instance_id", "model_tag"))
            and type(value.get("generation")) is int and value["generation"] >= 1)


def valid_science_execution_envelope(execution: Any, *, expected_project_id: str,
                                     expected_model_ref: Mapping[str, Any] | None = None,
                                     expected_revision: int | None = None,
                                     require_model_context: bool) -> bool:
    """Validate the complete envelope emitted by run_native_w23_te_science._dispatch."""
    if not isinstance(execution, Mapping) or not isinstance(expected_project_id, str) or not expected_project_id:
        return False
    base = {"project_id", "rpc_timeout_s", "execution_timeout_s", "queue_timeout_s",
            "idempotency_key", "request_id"}
    model = {"session_id", "model_ref", "expected_revision"}
    if require_model_context:
        if set(execution) != base | model:
            return False
    elif set(execution) != base:
        return False
    timeout = execution.get("rpc_timeout_s")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout)) or not 1.0 <= float(timeout) <= 120.0):
        return False
    queue_timeout = execution.get("queue_timeout_s")
    if (isinstance(queue_timeout, bool) or not isinstance(queue_timeout, (int, float))
            or float(queue_timeout) != 60.0 or execution.get("execution_timeout_s") is not None
            or execution.get("project_id") != expected_project_id):
        return False
    if (not _valid_uuid_request_token(execution.get("idempotency_key"), "w23-science-idem-")
            or not _valid_uuid_request_token(execution.get("request_id"), "w23-science-req-")):
        return False
    if not require_model_context:
        return True
    model_ref = execution.get("model_ref")
    revision = execution.get("expected_revision")
    session_id = execution.get("session_id")
    if (not _valid_model_ref_identity(model_ref) or not isinstance(expected_model_ref, Mapping)
            or dict(model_ref) != dict(expected_model_ref)
            or not isinstance(session_id, str) or session_id != model_ref.get("session_id")
            or type(revision) is not int or revision < 0
            or type(expected_revision) is not int or revision != expected_revision):
        return False
    return True


def allowed_direction_dispatch(request: Mapping[str, Any], *,
                               expected_project_id: str,
                               expected_model_ref: Mapping[str, Any],
                               expected_revision: int) -> bool:
    """Permit only the exact production operation_call envelope for a bound read."""
    if not isinstance(request, Mapping) or set(request) != {"operation", "arguments", "execution"}:
        return False
    if request.get("operation") != "operation_call":
        return False
    if not valid_science_execution_envelope(
            request.get("execution"), expected_project_id=expected_project_id,
            expected_model_ref=expected_model_ref, expected_revision=expected_revision,
            require_model_context=True):
        return False
    return _allowed_direction_operation_request(
        {"operation": request["operation"], "arguments": request["arguments"]})


def operation_call(operation_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    request = {"operation": "operation_call", "arguments": {
        "operation_id": operation_id, "arguments": dict(arguments),
    }}
    if not _allowed_direction_operation_request(request):
        raise DirectionDiscoveryError(f"read request is outside the exact R21 allowlist: {operation_id}")
    return request["arguments"]


def children_arguments(path: Mapping[str, Any], *, collection: str | None = None) -> dict[str, Any]:
    value = {"path": dict(path), "limit": MAX_CHILDREN_PER_LIST}
    if collection is not None:
        value["collection"] = collection
    if not _valid_read_arguments("node.children", value):
        raise DirectionDiscoveryError("children path, limit, or collection is outside the R21 scope")
    return value


def inspect_arguments(path: Mapping[str, Any]) -> dict[str, Any]:
    value = {"path": dict(path), "include_properties": False}
    if not _valid_read_arguments("node.inspect", value):
        raise DirectionDiscoveryError("inspect path is outside the R21 scope")
    return value


def property_schema_arguments(path: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = {"path": dict(path), "name": name}
    if not _valid_read_arguments("node.property_schema", value):
        raise DirectionDiscoveryError(f"property-schema request is outside R21 allowlist: {name!r}")
    return value


def property_get_arguments(path: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = {"path": dict(path), "names": [name]}
    if not _valid_read_arguments("node.property_get", value):
        raise DirectionDiscoveryError(f"property-get request is outside R21 allowlist: {name!r}")
    return value


def schema_read_decision(row: Any, name: str) -> dict[str, Any]:
    """Fail closed unless a named property is explicitly present and typed."""
    if not isinstance(row, Mapping) or row.get("name") != name:
        return {"status": "AMBIGUOUS_SCHEMA_ROW", "should_get": False, "reason": "exact named schema row absent"}
    present = row.get("has_property")
    if type(present) is not bool:
        return {"status": "PROPERTY_PRESENCE_UNKNOWN", "should_get": False,
                "reason": "hasProperty did not return a JSON boolean"}
    if present is False:
        return {"status": "PROPERTY_ABSENT", "should_get": False,
                "reason": "hasProperty returned false"}
    metadata = row.get("metadata_status")
    value_type, getter, kind, rank = (row.get("value_type"), row.get("getter"),
                                     row.get("kind"), row.get("shape_rank"))
    if (metadata != "KNOWN" or not isinstance(value_type, str) or not value_type
            or not isinstance(getter, str) or not getter
            or not isinstance(kind, str) or type(rank) is not int or rank < 0):
        return {"status": "PROPERTY_TYPE_UNKNOWN", "should_get": False,
                "reason": "authoritative value type/getter/rank is unavailable"}
    allowed = row.get("allowed_values")
    if allowed is not None and (not isinstance(allowed, list)
                                or not all(isinstance(item, str) for item in allowed)
                                or len(set(allowed)) != len(allowed)):
        return {"status": "AMBIGUOUS_ALLOWED_VALUES", "should_get": False,
                "reason": "finite enum values are not a unique string list"}
    return {"status": "PROPERTY_PRESENT_TYPED", "should_get": True,
            "value_type": value_type, "getter": getter, "kind": kind,
            "shape_rank": rank, "allowed_values": allowed}


def typed_get_observation(response: Any, name: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one public typed getter result without coercing its value."""
    decision = schema_read_decision(schema, name)
    if decision.get("should_get") is not True:
        raise DirectionDiscoveryError("typed getter cannot follow absent/ambiguous property schema")
    if not isinstance(response, Mapping) or not isinstance(response.get("properties"), list):
        raise DirectionDiscoveryError("node.property_get returned no properties array")
    rows = response["properties"]
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("name") == name]
    if len(matches) != 1 or len(rows) != 1:
        raise DirectionDiscoveryError("node.property_get did not return exactly the requested property")
    value = matches[0].get("value")
    if (not isinstance(value, Mapping) or set(value) - {"kind", "shape", "data", "java_signature", "unit"}
            or value.get("kind") != decision["kind"]
            or not isinstance(value.get("shape"), list)
            or len(value["shape"]) != decision["shape_rank"]):
        raise DirectionDiscoveryError("typed getter result differs from authoritative schema kind/rank")
    return {"status": "TYPED_VALUE_CAPTURED", "name": name,
            "schema_decision": decision, "typed_value": dict(value)}


def exact_children(body: Any, *, path: Mapping[str, Any], collection: str,
                   maximum: int, requested_collection: str | None = None) -> list[dict[str, Any]]:
    if (not isinstance(body, Mapping) or body.get("path") != dict(path)
            or body.get("complete") is not True or body.get("truncated") is not False
            or body.get("next_cursor") is not None or body.get("errors") != []):
        raise DirectionDiscoveryError("node.children is incomplete, truncated, or contains errors")
    if ((requested_collection is None and "collection_filter" in body)
            or (requested_collection is not None
                and body.get("collection_filter") != requested_collection)):
        raise DirectionDiscoveryError("node.children response collection filter differs from the request")
    rows = body.get("children")
    if not isinstance(rows, list) or len(rows) > maximum:
        raise DirectionDiscoveryError("node.children count is absent or exceeds the R21 bound")
    for row in rows:
        if not isinstance(row, Mapping):
            raise DirectionDiscoveryError("node.children returned a malformed child row")
        if row.get("collection") == collection and not (
                isinstance(row.get("tag"), str) and row.get("tag") and row.get("accessor") is not True):
            raise DirectionDiscoveryError(f"node.children returned an ambiguous {collection} row")
    selected = [dict(row) for row in rows if isinstance(row, Mapping)
                and row.get("collection") == collection and isinstance(row.get("tag"), str)]
    tags = [row["tag"] for row in selected]
    if len(tags) != len(set(tags)):
        raise DirectionDiscoveryError(f"duplicate {collection} tags make discovery ambiguous")
    return selected


def exact_accessor(body: Any, *, path: Mapping[str, Any], collection: str,
                   maximum: int, requested_collection: str | None = None) -> dict[str, Any]:
    if (not isinstance(body, Mapping) or body.get("path") != dict(path)
            or body.get("complete") is not True or body.get("truncated") is not False
            or body.get("next_cursor") is not None or body.get("errors") != []):
        raise DirectionDiscoveryError("node.children accessor page is incomplete or contains errors")
    if ((requested_collection is None and "collection_filter" in body)
            or (requested_collection is not None
                and body.get("collection_filter") != requested_collection)):
        raise DirectionDiscoveryError("node.children accessor collection filter differs from the request")
    rows = body.get("children")
    if not isinstance(rows, list) or len(rows) > maximum:
        raise DirectionDiscoveryError("node.children accessor count exceeds the R21 bound")
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("collection") == collection]
    if (len(matches) != 1 or matches[0].get("accessor") is not True
            or matches[0].get("tag") is not None):
        raise DirectionDiscoveryError(f"node.children did not return one exact {collection} accessor")
    return dict(matches[0])


def type_for_inspect(body: Any, *, path: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(body, Mapping) or body.get("path") != dict(path):
        raise DirectionDiscoveryError("node.inspect returned a different path")
    if (body.get("properties_requested") is not False
            or body.get("properties_status") != "NOT_REQUESTED"
            or "properties" in body or "values" in body):
        raise DirectionDiscoveryError("node.inspect did not confirm identity-only properties omission")
    tag, type_id = body.get("tag"), body.get("type_id")
    if not _bounded_text(tag) or not _bounded_text(type_id):
        raise DirectionDiscoveryError("node.inspect omitted exact tag/type identity")
    return tag, type_id


def _physics_identifier_observation(body: Mapping[str, Any]) -> dict[str, Any]:
    status = body.get("identifier_status")
    reason = body.get("identifier_unavailable_reason")
    identifier = body.get("identifier")
    if status == "OBSERVED":
        if not _bounded_text(identifier) or reason is not None:
            raise DirectionDiscoveryError("node.inspect returned a malformed Physics identifier")
        return {"identifier": identifier, "identifier_status": "OBSERVED",
                "identifier_unavailable_reason": None}
    if status == "UNAVAILABLE":
        if identifier is not None or reason not in {"API_UNSUPPORTED", "INVALID_OR_EMPTY_READBACK"}:
            raise DirectionDiscoveryError("node.inspect returned a malformed unavailable Physics identifier")
        return {"identifier": None, "identifier_status": "UNAVAILABLE",
                "identifier_unavailable_reason": reason}
    if status == "AMBIGUOUS":
        # Ambiguous public identity evidence cannot be reduced to a convenient
        # candidate. Keep this a hard producer failure so no graph or runner
        # gate can accidentally treat one value as authoritative.
        raise DirectionDiscoveryError("node.inspect returned ambiguous Physics identifier evidence")
    raise DirectionDiscoveryError("node.inspect omitted Physics identifier availability status")


def typed_text(observation: Mapping[str, Any]) -> str | None:
    value = observation.get("typed_value")
    if not isinstance(value, Mapping) or value.get("kind") not in {"string", "expression"}:
        return None
    data = value.get("data")
    shape = value.get("shape")
    return data if shape == [] and isinstance(data, str) else None


def _schema_payload(body: Any, name: str) -> dict[str, Any]:
    if isinstance(body, Mapping) and body.get("name") == name:
        return dict(body)
    if isinstance(body, Mapping) and isinstance(body.get("properties"), list):
        rows = [row for row in body["properties"] if isinstance(row, Mapping) and row.get("name") == name]
        if len(rows) == 1 and len(body["properties"]) == 1:
            return dict(rows[0])
    raise DirectionDiscoveryError(f"node.property_schema did not return one exact row for {name!r}")


def capture_property(call, path: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Read schema first and issue one typed getter only when fully described."""
    schema = _schema_payload(call("node.property_schema", property_schema_arguments(path, name)), name)
    decision = schema_read_decision(schema, name)
    record: dict[str, Any] = {"schema": schema, "decision": decision, "typed_get": None}
    if decision.get("should_get") is True:
        response = call("node.property_get", property_get_arguments(path, name))
        record["typed_get"] = typed_get_observation(response, name, schema)
    return record


def _inspect(call, path: Mapping[str, Any]) -> dict[str, Any]:
    body = call("node.inspect", inspect_arguments(path))
    tag, type_id = type_for_inspect(body, path=path)
    result: dict[str, Any] = {"tag": tag, "type_id": type_id}
    segments = path.get("segments")
    if (isinstance(segments, list) and segments and isinstance(segments[-1], Mapping)
            and segments[-1].get("collection") == "physics"):
        result.update(_physics_identifier_observation(body))
    elif any(field in body for field in ("identifier", "identifier_status", "identifier_unavailable_reason")):
        raise DirectionDiscoveryError("node.inspect returned unexpected Physics identifier metadata")
    return result


def _solver_feature_order(body: Mapping[str, Any], path: Mapping[str, Any],
                         rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, int]]:
    """Validate an explicit ModelEntityList.index witness; never infer from tags."""
    raw = body.get("collection_order")

    def unverified(reason: str, validation_failure: str | None = None) -> tuple[dict[str, Any], dict[str, int]]:
        evidence: dict[str, Any] = {
            "order_status": "UNVERIFIED", "path": dict(path),
            "sample_atomicity": (raw.get("sample_atomicity") if isinstance(raw, Mapping) else None),
            "provenance_failure": (raw.get("provenance_failure") if isinstance(raw, Mapping) else None),
            "validation_failure": validation_failure or reason,
        }
        if isinstance(raw, Mapping):
            evidence["source_order_status"] = raw.get("order_status")
        return evidence, {}

    if not isinstance(raw, Mapping):
        return unverified("node.children omitted collection_order")
    if raw.get("order_status") != "VERIFIED_STABLE_TWO_SAMPLES":
        return unverified("source did not verify feature order")
    if (raw.get("schema") != "COMSOL_MODEL_ENTITY_LIST_INDEX_ORDER_WITNESS_V1"
            or raw.get("path") != dict(path) or raw.get("collection") != "feature"
            or raw.get("max_members") != 500
            or raw.get("sample_atomicity") != "TWO_STABLE_SAMPLES_NOT_ATOMIC_TO_OTHER_CLIENTS"
            or raw.get("receiver_reused_for_all_reads") is not True
            or raw.get("provenance_failure") is not None):
        return unverified("order witness header/receiver/path is not exact")
    receiver = raw.get("receiver")
    if (not isinstance(receiver, Mapping)
            or receiver.get("interface") != "com.comsol.model.ModelEntityList"
            or receiver.get("method") != "index"
            or receiver.get("parameters") != ["java.lang.String"]
            or receiver.get("returns") != "int"
            or receiver.get("runtime_version") != "6.4.0.293"):
        return unverified("order witness has no exact typed ModelEntityList.index(String):int receiver")
    tags_before = raw.get("tags_before")
    tags_after = raw.get("tags_after")
    if (not isinstance(tags_before, list) or len(tags_before) > 500
            or not all(_bounded_text(tag) for tag in tags_before)
            or len(set(tags_before)) != len(tags_before)
            or tags_after != tags_before):
        return unverified("actual feature tags are malformed, over limit, or changed")
    child_tags = [row.get("tag") for row in rows]
    if (len(child_tags) != len(tags_before)
            or not all(_bounded_text(tag) for tag in child_tags)
            or len(set(child_tags)) != len(child_tags)
            or set(child_tags) != set(tags_before)):
        return unverified("order witness tags do not equal the exact complete node.children tags")
    samples = raw.get("index_samples")
    if not isinstance(samples, list) or len(samples) != 2:
        return unverified("order witness does not contain exactly two index samples")
    normalized_samples: list[list[dict[str, Any]]] = []
    for sample in samples:
        if not isinstance(sample, list) or len(sample) != len(tags_before):
            return unverified("an index sample has the wrong size")
        normalized: list[dict[str, Any]] = []
        for tag, item in zip(tags_before, sample):
            if (not isinstance(item, Mapping) or set(item) != {"tag", "index"}
                    or item.get("tag") != tag or type(item.get("index")) is not int
                    or item["index"] < 0):
                return unverified("index sample contains a missing, mistyped, or negative index")
            normalized.append({"tag": tag, "index": item["index"]})
        values = [item["index"] for item in normalized]
        if len(set(values)) != len(values) or sorted(values) != list(range(len(tags_before))):
            return unverified("index sample is not a unique contiguous 0..n-1 permutation")
        normalized_samples.append(normalized)
    first, second = normalized_samples
    if first != second:
        return unverified("the two index samples differ")
    ordered_tags = [item["tag"] for item in sorted(first, key=lambda item: item["index"])]
    if raw.get("ordered_tags") != ordered_tags or raw.get("tag_indices") != first:
        return unverified("derived ordered tags do not match the exact index sample")
    index_by_tag = {item["tag"]: item["index"] for item in first}
    evidence = {
        "order_status": "VERIFIED_STABLE_TWO_SAMPLES",
        "path": dict(path),
        "receiver": dict(receiver),
        "tags_before": list(tags_before),
        "index_samples": normalized_samples,
        "tags_after": list(tags_after),
        "ordered_tags": ordered_tags,
        "tag_indices": first,
        "sample_atomicity": raw["sample_atomicity"],
    }
    return evidence, index_by_tag


def _feature_rows(call, path: Mapping[str, Any], *, with_solver_order: bool = False):
    body = call("node.children", children_arguments(path, collection="feature"))
    rows = exact_children(body, path=path, collection="feature",
                          maximum=MAX_FEATURES_PER_PARENT,
                          requested_collection="feature")
    if with_solver_order:
        return rows, _solver_feature_order(body, path, rows)
    return rows


def _result_dataset_rows(call):
    """Filtered result children are tagged datasets, as returned by the public API."""
    result_path = node_path([{"accessor": "result"}])
    return exact_children(
        call("node.children", children_arguments(result_path, collection="dataset")),
        path=result_path, collection="dataset", maximum=MAX_ROOT_ENTITIES,
        requested_collection="dataset")


def _capture_named_properties(call, path: Mapping[str, Any], names: Sequence[str]) -> dict[str, Any]:
    return {name: capture_property(call, path, name) for name in names}


def _ordered_store_solution_links(steps: Sequence[Mapping[str, Any]],
                                  all_steps: Sequence[Mapping[str, Any]],
                                  store_rows: Sequence[Mapping[str, Any]],
                                  eigenvalue_rows: Sequence[Mapping[str, Any]],
                                  feature_orders: Mapping[str, Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    """Bind a StoreSolution only inside a proven public solver-feature interval."""
    linked: list[Mapping[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    for step in steps:
        solver_tag = step["solver_tag"]
        order = feature_orders.get(solver_tag, {})
        common = {"solver_tag": solver_tag, "study_step_tag": step["tag"],
                  "study_step_index": step.get("collection_order_index"),
                  "feature_order_status": order.get("order_status", "UNVERIFIED"),
                  "sample_atomicity": order.get("sample_atomicity")}
        if (order.get("order_status") != "VERIFIED_STABLE_TWO_SAMPLES"
                or type(step.get("collection_order_index")) is not int):
            intervals.append({**common, "status": "ORDER_UNPROVEN",
                              "provenance_failure": order.get("validation_failure") or order.get("provenance_failure")})
            continue

        step_index = step["collection_order_index"]
        members = [row for row in all_steps
                   if row.get("solver_tag") == solver_tag and row.get("depth") == 1
                   and type(row.get("collection_order_index")) is int]
        next_steps = sorted((row for row in members
                             if row["collection_order_index"] > step_index),
                            key=lambda row: row["collection_order_index"])
        next_step = next_steps[0] if next_steps else None
        sequence_end = len(order.get("ordered_tags", []))
        interval_end = (next_step["collection_order_index"] if next_step is not None
                        else sequence_end)
        interval_eigenvalues = sorted((row for row in eigenvalue_rows
                                       if row.get("solver_tag") == solver_tag and row.get("depth") == 1
                                       and type(row.get("collection_order_index")) is int
                                       and step_index < row["collection_order_index"] < interval_end),
                                      key=lambda row: row["collection_order_index"])
        interval_stores = sorted((row for row in store_rows
                                  if row.get("solver_tag") == solver_tag and row.get("depth") == 1
                                  and row.get("type_id") == "StoreSolution"
                                  and type(row.get("collection_order_index")) is int
                                  and step_index < row["collection_order_index"] < interval_end),
                                 key=lambda row: row["collection_order_index"])
        interval_copies = [row for row in store_rows
                           if row.get("solver_tag") == solver_tag and row.get("depth") == 1
                           and row.get("type_id") == "CopySolution"
                           and type(row.get("collection_order_index")) is int
                           and step_index < row["collection_order_index"] < interval_end]
        store = interval_stores[0] if len(interval_stores) == 1 else None
        eigenvalues_before_store = ([row for row in interval_eigenvalues
                                     if row["collection_order_index"] < store["collection_order_index"]]
                                    if store is not None else [])
        nearest_preceding_steps = ([row for row in members
                                    if row["collection_order_index"] < store["collection_order_index"]]
                                   if store is not None else [])
        nearest_preceding = (max(nearest_preceding_steps,
                                 key=lambda row: row["collection_order_index"])
                             if nearest_preceding_steps else None)
        proven = (store is not None and bool(eigenvalues_before_store)
                  and not interval_copies and nearest_preceding is not None
                  and nearest_preceding.get("tag") == step.get("tag")
                  and step_index < eigenvalues_before_store[0]["collection_order_index"]
                  and eigenvalues_before_store[-1]["collection_order_index"] < store["collection_order_index"]
                  and (next_step is None or store["collection_order_index"] < next_step["collection_order_index"])
                  and store["collection_order_index"] < sequence_end)
        evidence = {
            **common,
            "next_study_step_tag": next_step.get("tag") if next_step is not None else None,
            "next_study_step_index": next_step.get("collection_order_index") if next_step is not None else None,
            "sequence_end_index": sequence_end,
            "eigenvalue_interval": [{"tag": row["tag"], "index": row["collection_order_index"]}
                                    for row in interval_eigenvalues],
            "store_solution_candidates": [{"tag": row["tag"], "index": row["collection_order_index"]}
                                           for row in interval_stores],
            "copy_solution_candidates": [{"tag": row["tag"], "index": row["collection_order_index"]}
                                          for row in interval_copies],
            "nearest_preceding_study_step_tag": (nearest_preceding.get("tag")
                                                   if nearest_preceding is not None else None),
            "nearest_preceding_study_step_index": (nearest_preceding.get("collection_order_index")
                                                    if nearest_preceding is not None else None),
            "store_solution_tag": store.get("tag") if store is not None else None,
            "store_solution_index": store.get("collection_order_index") if store is not None else None,
            "status": "ORDERED_STORE_LINK_PROVEN" if proven else "UNRESOLVED_ORDER_INTERVAL_OR_CANDIDATE",
        }
        intervals.append(evidence)
        if proven and store is not None:
            linked.append(store)

    status = ("ORDERED_STORE_LINK_PROVEN" if len(steps) == 1 and len(linked) == 1
              and len(intervals) == 1 and intervals[0].get("status") == "ORDERED_STORE_LINK_PROVEN"
              else "UNRESOLVED_ORDER_OR_AMBIGUITY")
    return linked, {
        "status": status,
        "interpretation": "documented solver feature sequence inferred from exact ModelEntityList.index order; not atomic to other clients and not scientific field-value acceptance",
        "study_step_intervals": intervals,
    }


def _datasets_for_store_solution(store: Mapping[str, Any],
                                 solution_datasets: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    sol_ref = typed_text(store["properties"]["sol"].get("typed_get") or {})
    if sol_ref is None:
        return []
    return [row for row in solution_datasets
            if typed_text(row["properties"]["solution"].get("typed_get") or {}) == sol_ref]


def _bma_solution_binding_status(steps: Sequence[Mapping[str, Any]],
                                 linked_stores: Sequence[Mapping[str, Any]],
                                 dataset_links: Sequence[Mapping[str, Any]],
                                 order_link_evidence: Mapping[str, Any]) -> str:
    return ("UNIQUE_EXACT_LINK" if len(steps) == len(linked_stores) == len(dataset_links) == 1
            and order_link_evidence.get("status") == "ORDERED_STORE_LINK_PROVEN"
            else "UNRESOLVED_ZERO_OR_AMBIGUOUS_LINK")


def discover_direction_graph(call) -> dict[str, Any]:
    """Enumerate exact public tags/types and read-only Port/BMA/solution links.

    ``call`` must dispatch one allowlisted public ``node.*`` operation and
    return its successful unwrapped result body. It is deliberately injected
    so offline tests can exercise graph resolution without claiming COMSOL
    runtime evidence.
    """
    root = node_path([])
    components = exact_children(
        call("node.children", children_arguments(root, collection="component")),
        path=root, collection="component", maximum=MAX_ROOT_ENTITIES,
        requested_collection="component")
    studies = exact_children(
        call("node.children", children_arguments(root, collection="study")),
        path=root, collection="study", maximum=MAX_ROOT_ENTITIES,
        requested_collection="study")
    solvers = exact_children(
        call("node.children", children_arguments(root, collection="sol")),
        path=root, collection="sol", maximum=MAX_ROOT_ENTITIES,
        requested_collection="sol")
    comp1 = [row for row in components if row["tag"] == "comp1"]
    if len(comp1) != 1:
        raise DirectionDiscoveryError("saved model does not expose exactly one component tag comp1")
    component = node_path([{"collection": "component", "tag": "comp1"}])

    physics_rows = exact_children(
        call("node.children", children_arguments(component, collection="physics")),
        path=component, collection="physics", maximum=MAX_ROOT_ENTITIES,
        requested_collection="physics")
    physics_inventory = []
    for row in physics_rows:
        path = node_path([{"collection": "component", "tag": "comp1"},
                          {"collection": "physics", "tag": row["tag"]}])
        actual = _inspect(call, path)
        if actual["tag"] != row["tag"]:
            raise DirectionDiscoveryError("physics child tag differs from exact node.inspect identity")
        physics_inventory.append({**actual, "path": path})
    emw = [row for row in physics_inventory if row["type_id"] == "ElectromagneticWaves"]
    if len(emw) != 1:
        raise DirectionDiscoveryError("saved model does not expose exactly one ElectromagneticWaves physics interface")
    emw_row = emw[0]
    physics_tag = emw_row["tag"]
    physics_path = emw_row["path"]
    namespace = correlate_physics_identity(
        physics_tag, emw_row.get("identifier"), emw_row.get("identifier_status"),
        physics_identifier_inventory=physics_inventory)

    port_features = _feature_rows(call, physics_path)
    ports = []
    for row in port_features:
        path = node_path([{"collection": "component", "tag": "comp1"},
                          {"collection": "physics", "tag": physics_tag},
                          {"collection": "feature", "tag": row["tag"]}])
        actual = _inspect(call, path)
        if actual["tag"] != row["tag"]:
            raise DirectionDiscoveryError("Port feature tag differs from exact node.inspect identity")
        if actual["type_id"] == "Port":
            ports.append({**actual, "path": path,
                          "properties": _capture_named_properties(call, path, PORT_PROPERTIES)})

    bma_features = []
    for study in studies:
        study_path = node_path([{"collection": "study", "tag": study["tag"]}])
        rows = _feature_rows(call, study_path)
        for row in rows:
            path = node_path([{"collection": "study", "tag": study["tag"]},
                              {"collection": "feature", "tag": row["tag"]}])
            actual = _inspect(call, path)
            if actual["tag"] != row["tag"]:
                raise DirectionDiscoveryError("study feature tag differs from exact node.inspect identity")
            if actual["type_id"] == "BoundaryModeAnalysis":
                bma_features.append({"study_tag": study["tag"], **actual, "path": path,
                                     "properties": _capture_named_properties(call, path, BMA_PROPERTIES)})

    solver_sequence_inventory = []
    solver_nodes = []
    solver_feature_orders: dict[str, dict[str, Any]] = {}
    for solver in solvers:
        start = node_path([{"collection": "sol", "tag": solver["tag"]}])
        actual_solver = _inspect(call, start)
        if actual_solver["tag"] != solver["tag"]:
            raise DirectionDiscoveryError("solver-sequence tag differs from exact node.inspect identity")
        root_rows, (feature_order, root_index_by_tag) = _feature_rows(
            call, start, with_solver_order=True)
        solver_feature_orders[solver["tag"]] = feature_order
        solver_sequence_inventory.append({**actual_solver, "path": start,
                                          "feature_order": feature_order})
        stack = [(start, 0, root_rows, root_index_by_tag)]
        visited = set()
        while stack:
            parent_path, depth, pending_rows, order_index_by_tag = stack.pop()
            path_key = json.dumps(parent_path, sort_keys=True)
            if path_key in visited:
                raise DirectionDiscoveryError("solver feature graph contains a duplicate path")
            visited.add(path_key)
            rows = pending_rows if pending_rows is not None else _feature_rows(call, parent_path)
            for row in rows:
                child_segments = parent_path["segments"] + [{"collection": "feature", "tag": row["tag"]}]
                child_path = node_path(child_segments)
                actual = _inspect(call, child_path)
                if actual["tag"] != row["tag"]:
                    raise DirectionDiscoveryError("solver feature tag differs from exact node.inspect identity")
                properties = None
                if actual["type_id"] == "StudyStep":
                    properties = _capture_named_properties(call, child_path, STUDY_STEP_PROPERTIES)
                elif actual["type_id"] in {"StoreSolution", "CopySolution"}:
                    properties = _capture_named_properties(call, child_path, STORE_SOLUTION_PROPERTIES)
                solver_node = {**actual, "solver_tag": solver["tag"], "path": child_path,
                               "depth": depth + 1, "properties": properties}
                if depth == 0:
                    solver_node["collection_order_status"] = feature_order["order_status"]
                    solver_node["collection_order_index"] = order_index_by_tag.get(row["tag"])
                solver_nodes.append(solver_node)
                if len(solver_nodes) > MAX_SOLVER_TREE_NODES:
                    raise DirectionDiscoveryError("solver feature graph exceeds the frozen node limit")
                if depth + 1 < MAX_SOLVER_TREE_DEPTH:
                    stack.append((child_path, depth + 1, None, {}))

    datasets = _result_dataset_rows(call)
    dataset_inventory = []
    for row in datasets:
        path = node_path([{"accessor": "result"},
                          {"collection": "dataset", "tag": row["tag"]}])
        actual = _inspect(call, path)
        if actual["tag"] != row["tag"]:
            raise DirectionDiscoveryError("dataset tag differs from exact node.inspect identity")
        properties = (_capture_named_properties(call, path, DATASET_PROPERTIES)
                      if actual["type_id"] == "Solution" else None)
        dataset_inventory.append({**actual, "path": path, "properties": properties})

    study_steps = [row for row in solver_nodes
                   if row["type_id"] == "StudyStep" and row.get("properties") is not None]
    store_rows = [row for row in solver_nodes
                  if row["type_id"] in {"StoreSolution", "CopySolution"} and row.get("properties") is not None]
    eigenvalue_rows = [row for row in solver_nodes if row["type_id"] == "Eigenvalue"]
    solution_datasets = [row for row in dataset_inventory
                         if row["type_id"] == "Solution" and row.get("properties") is not None]
    bindings = []
    for bma in bma_features:
        bma_tag = bma["tag"]
        steps = [row for row in study_steps
                 if typed_text(row["properties"]["study"].get("typed_get") or {}) == bma["study_tag"]
                 and typed_text(row["properties"]["studystep"].get("typed_get") or {}) == bma_tag]
        candidate_stores = [row for row in store_rows
                            if any(row["solver_tag"] == step["solver_tag"] for step in steps)]
        linked_stores, order_link_evidence = _ordered_store_solution_links(
            steps, study_steps, store_rows, eigenvalue_rows, solver_feature_orders)
        candidate_datasets = [dataset for store in candidate_stores
                              for dataset in _datasets_for_store_solution(store, solution_datasets)]
        dataset_links = [dataset for store in linked_stores
                         for dataset in _datasets_for_store_solution(store, solution_datasets)]
        bindings.append({"study_tag": bma["study_tag"], "bma_tag": bma_tag,
                         "port_name": typed_text(bma["properties"]["PortName"].get("typed_get") or {}),
                         "mode_frequency_raw": bma["properties"]["modeFreq"],
                         "study_step_matches": steps,
                         "store_solution_candidates": candidate_stores,
                         "store_solution_matches": linked_stores,
                         "dataset_candidates": candidate_datasets,
                         "dataset_matches": dataset_links,
                         "order_link_evidence": order_link_evidence,
                         "status": _bma_solution_binding_status(
                             steps, linked_stores, dataset_links, order_link_evidence)})
    port_name_matches = {
        port_name: [row for row in ports
                    if typed_text(row["properties"]["PortName"].get("typed_get") or {}) == port_name]
        for port_name in ("1", "2")
    }
    port_binding_status = {
        name: ("UNIQUE_EXACT_PORT_NAME" if len(rows) == 1 else "UNRESOLVED_ZERO_OR_AMBIGUOUS_PORT_NAME")
        for name, rows in port_name_matches.items()
    }
    return {
        "schema": "W23_R31_PUBLIC_DIRECTION_GRAPH_V1",
        "status": "PASS_READONLY_METADATA_AND_ORDER_WITNESS_OR_UNVERIFIED_NOT_SCIENCE_ACCEPTANCE",
        "physics_inventory": physics_inventory,
        "electromagnetic_waves": {**emw_row, "namespace_correlation": namespace},
        "ports": ports,
        "port_name_matches": port_name_matches,
        "port_name_binding_status": port_binding_status,
        "bma_features": bma_features,
        "solver_sequences": solver_sequence_inventory,
        "solver_features": solver_nodes,
        "solution_datasets": dataset_inventory,
        "bma_solution_bindings": bindings,
        "limits": {"Study.run": 0, "solver.run": 0, "result.at_points": 0,
                   "node.property_set": 0, "model.save": 0, "code.execute_java": 0},
        "native_scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY",
    }


def correlate_physics_tag(tag: str, *, variable_prefix: str = "emw") -> dict[str, Any]:
    if not _bounded_text(tag) or not _bounded_text(variable_prefix):
        raise DirectionDiscoveryError("physics tag or field namespace is malformed")
    return {
        "physics_tag": tag,
        "physics_type": "ElectromagneticWaves",
        "observed_field_variable_prefix": variable_prefix,
        "tag_prefix_equal": tag == variable_prefix,
        "status": "TAG_NAMESPACE_MATCH" if tag == variable_prefix else "TAG_NAMESPACE_MISMATCH_UNRESOLVED",
        "evidence_scope": "exact public Physics tag/getType readback compared with preserved result expression prefix; no field values evaluated",
    }


def correlate_physics_identity(tag: str, identifier: Any, identifier_status: Any, *,
                               variable_prefix: str = "emw",
                               physics_identifier_inventory: Sequence[Mapping[str, Any]] | None = None
                               ) -> dict[str, Any]:
    """Correlate documented Physics Name/Scope metadata without reading fields.

    COMSOL 6.4 Table 2-3 maps ``Physics.identifier()`` to the Physics Name;
    the accompanying documentation describes Scope as the expression access
    form. This is metadata correlation only. The Physics tag remains a separate
    identity and is never substituted for the frozen expression prefix.
    """
    tag_comparison = correlate_physics_tag(tag, variable_prefix=variable_prefix)
    if identifier_status == "OBSERVED" and _bounded_text(identifier):
        observed_identifier = identifier
        alias_matches_tag = identifier == tag
    elif identifier_status == "UNAVAILABLE" and identifier is None:
        observed_identifier = None
        alias_matches_tag = None
    else:
        raise DirectionDiscoveryError("Physics identifier status/value pair is malformed")
    if physics_identifier_inventory is None:
        inventory_rows: list[Mapping[str, Any]] = [{
            "identifier": observed_identifier, "identifier_status": identifier_status,
        }]
    elif (isinstance(physics_identifier_inventory, Sequence)
          and not isinstance(physics_identifier_inventory, (str, bytes))
          and all(isinstance(row, Mapping) for row in physics_identifier_inventory)):
        inventory_rows = list(physics_identifier_inventory)
    else:
        raise DirectionDiscoveryError("Physics identifier inventory is malformed")
    inventory_complete = bool(inventory_rows) and all(
        row.get("identifier_status") == "OBSERVED" and _bounded_text(row.get("identifier"))
        for row in inventory_rows)
    identifiers = [row.get("identifier") for row in inventory_rows
                   if row.get("identifier_status") == "OBSERVED" and _bounded_text(row.get("identifier"))]
    duplicate_identifiers = sorted({value for value in identifiers if identifiers.count(value) > 1})
    expected_prefix_count = identifiers.count(variable_prefix)
    if identifier_status != "OBSERVED" or observed_identifier is None:
        status = "IDENTIFIER_UNRESOLVED"
        prefix_equal = None
        inventory_status = "INCOMPLETE"
    elif not inventory_complete:
        status = "IDENTIFIER_INVENTORY_INCOMPLETE_UNRESOLVED"
        prefix_equal = None
        inventory_status = "INCOMPLETE"
    elif duplicate_identifiers or expected_prefix_count > 1:
        status = "IDENTIFIER_SCOPE_AMBIGUOUS_UNRESOLVED"
        prefix_equal = None
        inventory_status = "AMBIGUOUS"
    elif observed_identifier != variable_prefix:
        status = "IDENTIFIER_SCOPE_MISMATCH_UNRESOLVED"
        prefix_equal = False
        inventory_status = "COMPLETE_UNIQUE"
    else:
        status = "IDENTIFIER_SCOPE_MATCH"
        prefix_equal = True
        inventory_status = "COMPLETE_UNIQUE"
    return {
        "physics_tag": tag,
        "physics_type": "ElectromagneticWaves",
        "physics_identifier": observed_identifier,
        "physics_identifier_status": identifier_status,
        "physics_identifier_semantics": "DOCUMENTED_NAME_SCOPE_METADATA_CORRELATION_ONLY",
        "physics_identifier_api_deprecated": True,
        "physics_identifier_tag_alias_equal": alias_matches_tag,
        "physics_identifier_inventory_status": inventory_status,
        "physics_identifier_duplicate_values": duplicate_identifiers,
        "observed_field_variable_prefix": variable_prefix,
        "tag_prefix_equal": tag == variable_prefix,
        "tag_comparison_status": tag_comparison["status"],
        "identifier_prefix_equal": prefix_equal,
        "status": status,
        "documentation_basis": {
            "runtime_version": "6.4.0.293",
            "title": "About Name, Identifier, and Tag Properties",
            "doc_id": 4074,
            "chunk_id": 16684,
            "sha256": "fa285b31b9809fb527b3c40fad66cf6bcac26cb557e90afb5582adeb66b75f94",
            "deprecated": True,
            "use": "Table 2-3 maps deprecated Physics.identifier() to Physics Name; Scope is documented as expression access. Metadata correlation only.",
        },
        "evidence_scope": "documented Physics Name/Scope metadata compared with the frozen result-expression prefix; tag is retained separately; no field values evaluated; no scientific acceptance",
    }


EXPECTED_W23_FIELD_PREFIX = "emw"


def physics_namespace_is_resolved(namespace: Mapping[str, Any], *,
                                  expected_prefix: str = EXPECTED_W23_FIELD_PREFIX) -> bool:
    """Accept only an exact, bounded, complete witness for frozen W23 prefix emw."""
    documentation = namespace.get("documentation_basis") if isinstance(namespace, Mapping) else None
    identifier = namespace.get("physics_identifier") if isinstance(namespace, Mapping) else None
    field_prefix = namespace.get("observed_field_variable_prefix") if isinstance(namespace, Mapping) else None
    tag = namespace.get("physics_tag") if isinstance(namespace, Mapping) else None
    return (
        isinstance(namespace, Mapping)
        and expected_prefix == EXPECTED_W23_FIELD_PREFIX
        and _bounded_text(expected_prefix)
        and namespace.get("status") == "IDENTIFIER_SCOPE_MATCH"
        and namespace.get("physics_type") == "ElectromagneticWaves"
        and namespace.get("physics_identifier_status") == "OBSERVED"
        and _bounded_text(identifier)
        and _bounded_text(field_prefix)
        and _bounded_text(tag)
        and identifier == expected_prefix
        and field_prefix == expected_prefix
        and namespace.get("physics_identifier_semantics") == "DOCUMENTED_NAME_SCOPE_METADATA_CORRELATION_ONLY"
        and namespace.get("physics_identifier_api_deprecated") is True
        and namespace.get("physics_identifier_tag_alias_equal") is (identifier == tag)
        and namespace.get("identifier_prefix_equal") is True
        and namespace.get("physics_identifier_inventory_status") == "COMPLETE_UNIQUE"
        and namespace.get("physics_identifier_duplicate_values") == []
        and isinstance(documentation, Mapping)
        and documentation.get("runtime_version") == "6.4.0.293"
        and documentation.get("doc_id") == 4074
        and documentation.get("chunk_id") == 16684
        and documentation.get("sha256") == "fa285b31b9809fb527b3c40fad66cf6bcac26cb557e90afb5582adeb66b75f94"
        and documentation.get("deprecated") is True
    )


def validate_direction_limits(plan: Mapping[str, Any]) -> None:
    if (plan.get("mode") != "direction_discovery_only"
            or plan.get("max_public_operation_calls") != MAX_DIRECTION_CALLS
            or plan.get("max_children_per_list") != MAX_CHILDREN_PER_LIST
            or plan.get("allowed_operation_call_ids") != sorted(READONLY_OPERATION_IDS)
            or plan.get("Study.run") != 0 or plan.get("solver.run") != 0
            or plan.get("result.at_points") != 0):
        raise DirectionDiscoveryError("direction discovery call plan differs from the closed R21 source-only budget")


def offline_signed_poynting_report(*, raw_fields: Mapping[int, Mapping[str, Path]],
                                   expected_hashes: Mapping[str, str],
                                   prior_report: Path,
                                   expected_prior_sha256: str) -> dict[str, Any]:
    """Recompute separate global Px and outward n.S from exact frozen R20 exports."""
    report_bytes = prior_report.read_bytes()
    prior_sha = hashlib.sha256(report_bytes).hexdigest()
    if prior_sha != expected_prior_sha256:
        raise DirectionDiscoveryError("prior frozen R20 direction report hash mismatch")
    prior = json.loads(report_bytes)
    prior_files = prior.get("raw_EH_mode_and_signal_Poynting", {}).get("field_files")
    if not isinstance(prior_files, list) or len(prior_files) != 4:
        raise DirectionDiscoveryError("R20 direction report lacks exactly four frozen field arrays")
    prior_by_path = {row.get("field_file"): row for row in prior_files if isinstance(row, Mapping)}
    result: dict[str, Any] = {
        "schema": "W23_R21_OFFLINE_SIGNED_POYNTING_REPLAY_V1",
        "status": "PASS_DIRECTION_OBSERVABLES_RECOMPUTED_NOT_SCIENTIFIC_ACCEPTANCE",
        "source_report": {"path": str(prior_report), "sha256": prior_sha},
        "formula": "Px = 0.5 Re(Ey*conjugate(Hz) - Ez*conjugate(Hy)); outward n.S = native_nx * global_Px; report them separately",
        "quadrature": {"method": "composite Simpson", "samples": [protocol.SAVED_MPH_SAMPLE_COUNT, protocol.SAVED_MPH_REFINED_SAMPLE_COUNT],
                       "relative_tolerance_preserved": protocol.INTEGRAL_RELATIVE_TOLERANCE},
        "nonchanges": {"H_only_flip": False, "tolerance_change": False, "grid_densification": False,
                       "field_or_normal_sign_change": False},
        "observations": [],
        "native_model_execution": False,
        "study_run": 0,
        "solver_run": 0,
        "scientific_acceptance": "NOT_ACCEPTED_DIAGNOSTIC_READBACK_ONLY",
    }
    if (protocol.SAVED_MPH_SAMPLE_COUNT != 641 or protocol.SAVED_MPH_REFINED_SAMPLE_COUNT != 1281
            or protocol.INTEGRAL_RELATIVE_TOLERANCE != 1e-3):
        raise DirectionDiscoveryError("frozen R20 grids/Simpson tolerance differ from the source plan")
    for count in (protocol.SAVED_MPH_SAMPLE_COUNT, protocol.SAVED_MPH_REFINED_SAMPLE_COUNT):
        if count not in raw_fields or set(raw_fields[count]) != {"input", "output"}:
            raise DirectionDiscoveryError(f"R20 field inputs are incomplete for {count} samples")
        loaded: dict[str, Mapping[str, Any]] = {}
        refs: list[dict[str, Any]] = []
        for plane, path in raw_fields[count].items():
            if not path.is_file() or path.is_symlink():
                raise DirectionDiscoveryError(f"frozen R20 field file missing or non-regular: {path}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = expected_hashes.get(str(path))
            prior_row = prior_by_path.get(str(path))
            if (not isinstance(expected, str) or digest != expected or not isinstance(prior_row, Mapping)
                    or prior_row.get("field_file_sha256") != digest or prior_row.get("sample_count") != count):
                raise DirectionDiscoveryError(f"frozen R20 field hash/count mismatch: {path}")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("sample_count") != count or raw.get("science_acceptance") != "NOT_EVALUATED":
                raise DirectionDiscoveryError("R20 field receipt sample count or acceptance marker changed")
            loaded[plane] = raw
            refs.append({"plane": plane, "path": str(path), "bytes": path.stat().st_size, "sha256": digest})
        recomputed = protocol.independent_integrals(loaded["output"], loaded["input"], field_scheme="emw_down")
        output_nx = recomputed["output_native_normal_x"]
        input_nx = recomputed["incident_native_normal_x"]
        observations = [
            {"plane": "input", "quantity": "BMA_mode1", "global_Px_W_per_m": recomputed["incident_reference_power"],
             "native_nx": input_nx, "outward_n_dot_S_W_per_m": input_nx * recomputed["incident_reference_power"]},
            {"plane": "output", "quantity": "signal", "global_Px_W_per_m": recomputed["signal_power"],
             "native_nx": output_nx, "outward_n_dot_S_W_per_m": output_nx * recomputed["signal_power"]},
            {"plane": "output", "quantity": "BMA_mode2", "global_Px_W_per_m": recomputed["reference_mode_power"],
             "native_nx": output_nx, "outward_n_dot_S_W_per_m": output_nx * recomputed["reference_mode_power"]},
        ]
        for row in observations:
            matching = next(r for r in prior_files
                            if r.get("plane") == row["plane"] and r.get("sample_count") == count)
            is_signal = row["quantity"] == "signal"
            prior_global = ("signal_signed_global_x_power_W_per_m" if is_signal
                            else "signed_global_x_mode_power_W_per_m")
            prior_outward = ("signal_port_normal_dot_power_W_per_m" if is_signal
                             else "port_normal_dot_mode_power_W_per_m")
            if (not math.isclose(row["global_Px_W_per_m"], float(matching[prior_global]),
                                 rel_tol=2e-10, abs_tol=1e-12)
                    or not math.isclose(row["outward_n_dot_S_W_per_m"], float(matching[prior_outward]),
                                       rel_tol=2e-10, abs_tol=1e-12)):
                raise DirectionDiscoveryError("offline direction observables differ from frozen R20 report")
        result["observations"].append({"sample_count": count, "input_files": refs,
                                      "integrals": recomputed, "signed_direction_observables": observations,
                                      "scope": "global Px and outward n.S are separate diagnostics; direction reports do not identify excitation coefficient phase"})
    return result

