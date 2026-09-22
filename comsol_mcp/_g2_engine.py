"""COMSOL-facing implementation for the bounded G2 operation set."""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping
from uuid import uuid4

from ._atomic_save import AtomicSaveError, atomic_save
from ._execution_contract import ExecutionContractError, PreWriteRefusal, canonical_project_path
from ._g2_code import describe_source, execution_result
from ._g2_contract import (
    NodePath, engine_value_spec, property_schema_from_engine, resolve_node_path,
    signature_spec, typed_value_from_engine, validate_property_set,
    validate_typed_value,
)
from ._g2_transactions import INVARIANT_TYPES, TransactionRecord, run_transaction, transaction_fingerprint


def _model(worker: Any, model_tag: str) -> Any:
    try:
        return worker.client().model(model_tag)
    except Exception as exc:
        raise ExecutionContractError("NODE_NOT_FOUND", f"bound model {model_tag!r} is unavailable") from exc


def _call(node: Any, method: str, *args: Any) -> Any:
    try:
        return getattr(node, method)(*args)
    except AttributeError as exc:
        raise ExecutionContractError("API_UNSUPPORTED", f"selected node does not expose {method}()") from exc
    except ExecutionContractError:
        raise
    except Exception as exc:
        # The worker's reply carries the engine's own exception text; keep a
        # bounded copy so an engine refusal stays diagnosable instead of being
        # flattened into a bare exception-class name.
        detail = str(exc)
        if len(detail) > 600:
            detail = detail[:600] + "…(truncated)"
        raise ExecutionContractError("ENGINE_CALL_FAILED", f"COMSOL {method} call failed: {type(exc).__name__}: {detail}") from exc


# ---------------------------------------------------------------------------
# R02: complete node discovery, per-level pagination, errors and budgets.
# ---------------------------------------------------------------------------

_CURSOR_VERSION = 1

# Authoritative child-collection candidates for the installed COMSOL 6.4
# API surface (verified by javap of the installed
# apiplugins/com.comsol.api_1.0.0.jar: Model, ModelNode/Component, Material,
# Results, GeomSequence, MeshSequence, Physics, Study, SolverSequence).  A
# node exposes a subset; an absent accessor means "this node does not support
# this collection" and is omitted, while every other probe failure is
# reported in ``errors`` and never silently turned into an empty list.
COLLECTION_CANDIDATES: tuple[dict[str, str], ...] = (
    {"collection": "component", "method": "component"},
    {"collection": "geom", "method": "geom"},
    {"collection": "mesh", "method": "mesh"},
    {"collection": "physics", "method": "physics"},
    {"collection": "material", "method": "material"},
    {"collection": "propertyGroup", "method": "propertyGroup"},
    {"collection": "func", "method": "func"},
    {"collection": "variable", "method": "variable"},
    {"collection": "selection", "method": "selection"},
    {"collection": "coordSystem", "method": "coordSystem"},
    {"collection": "cpl", "method": "cpl"},
    {"collection": "pair", "method": "pair"},
    {"collection": "multiphysics", "method": "multiphysics"},
    {"collection": "feature", "method": "feature"},
    {"collection": "study", "method": "study"},
    {"collection": "sol", "method": "sol"},
    {"collection": "dataset", "method": "dataset"},
    {"collection": "numerical", "method": "numerical"},
    {"collection": "table", "method": "table"},
    {"collection": "export", "method": "export"},
    {"collection": "view", "method": "view"},
    {"collection": "extraDim", "method": "extraDim"},
)

_SEARCH_BUDGET_LIMITS = {"max_nodes": (1, 100_000), "max_seconds": (0.0, 600.0), "max_rpc": (1, 1_000_000)}
_DEFAULT_SEARCH_BUDGET = {"max_nodes": 2000, "max_seconds": 20.0, "max_rpc": 5000}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _worker_generation(worker: Any) -> int | None:
    try:
        return int(worker.generation)
    except Exception:
        return None


def _cursor_context(worker: Any, model_tag: str, model_revision: int | None, kind: str, scope_hash: str) -> dict[str, Any]:
    return {"v": _CURSOR_VERSION, "kind": kind, "model_tag": str(model_tag),
            "worker_generation": _worker_generation(worker), "model_revision": model_revision,
            "scope_hash": scope_hash}


def _cursor_encode(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _cursor_decode(cursor: Any) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor.strip():
        raise ExecutionContractError("INVALID_CURSOR", "cursor must be a non-empty string")
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except Exception as exc:  # malformed base64 or JSON
        raise ExecutionContractError("INVALID_CURSOR", "cursor is not a valid continuation token") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ExecutionContractError("INVALID_CURSOR", "cursor has an unsupported format or version")
    return payload


def _cursor_verify(payload: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    """Reject a cursor that belongs to another model epoch or another query.

    A stale cursor is never silently restarted from the first item: the caller
    must re-issue the request deliberately.
    """
    for field in ("kind", "model_tag", "worker_generation", "model_revision", "scope_hash"):
        if payload.get(field) != expected.get(field):
            raise ExecutionContractError("CURSOR_STALE", f"cursor is stale or belongs to another query ({field} changed)")


def _worker_failure_code(exc: BaseException) -> str | None:
    """Extract the structured worker failure code from an exception chain."""
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        failure = getattr(cause, "failure", None)
        if isinstance(failure, Mapping) and isinstance(failure.get("code"), str):
            return str(failure["code"])
        reply = getattr(cause, "reply", None)
        if isinstance(reply, Mapping) and isinstance(reply.get("code"), str):
            return str(reply["code"])
        cause = cause.__cause__
    return None


def _worker_failure_unknown(exc: BaseException) -> bool:
    """Read an explicit unknown-state signal from a worker failure chain.

    The Java worker can return a normal engine error code together with
    ``execution_state_unknown=true`` when the engine may have started the
    operation.  Callers must preserve that bit even when a later readback is
    readable; the readback describes the observation, not whether the failed
    operation completed.  This helper only consumes structured fields (or the
    explicit ``EXECUTION_STATE_UNKNOWN`` code), never exception class names or
    human-readable messages.
    """
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if getattr(cause, "code", None) == "EXECUTION_STATE_UNKNOWN":
            return True
        if getattr(cause, "execution_state_unknown", None) is True:
            return True
        for name in ("failure", "reply", "details"):
            value = getattr(cause, name, None)
            if isinstance(value, Mapping):
                if value.get("execution_state_unknown") is True:
                    return True
                if value.get("engine_state_unknown") is True:
                    return True
                if value.get("code") == "EXECUTION_STATE_UNKNOWN":
                    return True
        cause = cause.__cause__
    return False


def _unsupported_collection(exc: ExecutionContractError) -> bool:
    """True when the node simply does not expose the probed accessor."""
    if exc.code == "API_UNSUPPORTED":
        return True
    return _worker_failure_code(exc) in {"METHOD_NOT_APPLICABLE"}


def _parse_search_budget(value: Any) -> dict[str, Any]:
    budget = dict(_DEFAULT_SEARCH_BUDGET)
    if value is None:
        return budget
    if not isinstance(value, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "budget must be an object")
    unknown = sorted(set(value) - set(_SEARCH_BUDGET_LIMITS))
    if unknown:
        raise ExecutionContractError("INVALID_REQUEST", f"budget has unsupported fields: {', '.join(unknown)}")
    for name, (low, high) in _SEARCH_BUDGET_LIMITS.items():
        if name not in value:
            continue
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not low <= item <= high:
            raise ExecutionContractError("INVALID_REQUEST", f"budget.{name} is outside its supported range")
        budget[name] = int(item) if name != "max_seconds" else float(item)
    return budget


def _collect_children(worker: Any, node: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return (rows, errors, rpc_calls) for one node.

    Production workers expose ``probe_children`` (one engine-side batch).
    Fixture/plain workers fall back to per-collection proxy probing; that path
    is intentionally more chatty and reports its actual call count.
    """
    batch = getattr(worker, "probe_children", None)
    if callable(batch):
        return _children_from_batch_reply(batch(node, list(COLLECTION_CANDIDATES)))
    return _probe_children_via_node(node)


def _normalise_child_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    collection = row.get("collection")
    if not isinstance(collection, str) or not collection:
        return None
    if row.get("accessor") is True:
        return {"collection": collection, "tag": None, "accessor": True}
    tag = row.get("tag")
    if not isinstance(tag, str) or not tag:
        return None
    return {"collection": collection, "tag": tag}


def _normalise_child_errors(value: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return errors
    for row in value:
        if isinstance(row, Mapping):
            errors.append({"collection": str(row.get("collection", "")), "code": str(row.get("code", "PROBE_FAILED")),
                           "message": str(row.get("message", ""))})
    return errors


def _children_from_batch_reply(reply: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if not isinstance(reply, Mapping) or not isinstance(reply.get("children"), list) or not isinstance(reply.get("errors", []), list):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker children probe returned an invalid reply")
    rows = [row for row in (_normalise_child_row(item) for item in reply["children"]) if row is not None]
    return rows, _normalise_child_errors(reply.get("errors")), 1


def _probe_children_via_node(node: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    rpc = 0
    for spec in COLLECTION_CANDIDATES:
        collection, method = spec["collection"], spec["method"]
        rpc += 1
        try:
            container = _call(node, method)
        except ExecutionContractError as exc:
            if not _unsupported_collection(exc):
                errors.append({"collection": collection, "code": _worker_failure_code(exc) or exc.code, "message": str(exc)})
            continue
        if container is None:
            errors.append({"collection": collection, "code": "COLLECTION_PROBE_FAILED", "message": "accessor returned null"})
            continue
        rpc += 1
        try:
            tags = _call(container, "tags")
        except ExecutionContractError as exc:
            if _unsupported_collection(exc):
                # A zero-arg accessor that returns a node (for example a Work
                # Plane's inner geometry) rather than a tag container.
                rows.append({"collection": collection, "tag": None, "accessor": True})
            else:
                errors.append({"collection": collection, "code": "TAGS_PROBE_FAILED", "message": str(exc)})
            continue
        for tag in list(tags or []):
            rows.append({"collection": collection, "tag": str(tag)})
    return rows, errors, rpc


def _safe_node_field(node: Any, method: str) -> str | None:
    try:
        value = _call(node, method)
    except Exception:
        return None
    return None if value is None else str(value)


def _matches_query(node: Any, query: Mapping[str, Any], path_tag: str | None = None) -> tuple[bool, str | None, str | None, str | None]:
    tag = _safe_node_field(node, "tag")
    if (tag is None or not tag) and path_tag:
        tag = path_tag
    type_id = _safe_node_field(node, "getType")
    label = _safe_node_field(node, "label")
    if "tag" in query and query.get("tag") != tag:
        return False, tag, type_id, label
    wanted_type = query.get("type_id", query.get("type"))
    if ("type_id" in query or "type" in query) and wanted_type != type_id:
        return False, tag, type_id, label
    if "label" in query and query.get("label") != label:
        return False, tag, type_id, label
    return True, tag, type_id, label


def _segment_for_row(row: Mapping[str, Any]) -> dict[str, str]:
    if row.get("accessor"):
        return {"accessor": str(row["collection"])}
    return {"collection": str(row["collection"]), "tag": str(row["tag"])}


def _walk_from_reply(reply: Any) -> dict[str, Any]:
    """Validate the worker `walk` reply before it reaches the wire result."""
    if not isinstance(reply, Mapping):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker walk returned an invalid reply")
    raw_matches, raw_errors = reply.get("matches"), reply.get("errors", [])
    visited, complete, truncated = reply.get("visited"), reply.get("complete"), reply.get("truncated_reason")
    if not isinstance(raw_matches, list) or not isinstance(raw_errors, list):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker walk reply has invalid lists")
    if isinstance(visited, bool) or not isinstance(visited, int) or visited < 0 or not isinstance(complete, bool):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker walk reply has invalid counters")
    if truncated is not None and not isinstance(truncated, str):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker walk reply has an invalid truncation reason")
    results: list[dict[str, Any]] = []
    for row in raw_matches:
        if not isinstance(row, Mapping) or not isinstance(row.get("segments"), list):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker walk match row is invalid")
        segments: list[dict[str, Any]] = []
        for segment in row["segments"]:
            if not isinstance(segment, Mapping):
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker walk segment is invalid")
            if set(segment) == {"accessor"}:
                segments.append({"accessor": str(segment["accessor"])})
            elif set(segment) == {"collection", "tag"}:
                segments.append({"collection": str(segment["collection"]), "tag": str(segment["tag"])})
            else:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "worker walk segment has an unsupported shape")
        results.append({"path": {"segments": segments}, "tag": row.get("tag"),
                        "type_id": row.get("type_id"), "label": row.get("label")})
    return {"results": results, "errors": _normalise_child_errors(raw_errors), "visited": visited,
            "complete": complete, "truncated_reason": truncated}


def _walk_fixture(worker: Any, node: Any, query: Mapping[str, Any], budget: Mapping[str, Any], skip_visited: int, limit: int) -> dict[str, Any]:
    """Deterministic BFS for fixture/plain workers without a batch walk.

    Semantics match the worker `walk` command: one visit per node, children of
    a node sorted by (collection, tag), a visit counter that re-walks the
    prefix for a resumed cursor, and budgets that only charge *new* nodes.
    """
    queue: deque[tuple[Any, list[dict[str, Any]]]] = deque([(node, [])])
    visited = 0
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    truncated: str | None = None
    complete = False
    rpc = 0
    started = time.monotonic()
    while queue:
        if visited - skip_visited >= budget["max_nodes"]:
            truncated = "node_budget"; break
        if time.monotonic() - started > budget["max_seconds"]:
            truncated = "time_budget"; break
        if rpc >= budget["max_rpc"]:
            truncated = "rpc_budget"; break
        current, segments = queue.popleft()
        visited += 1
        if visited > skip_visited:
            path_tag = segments[-1].get("tag") if segments and isinstance(segments[-1], Mapping) else None
            matched, tag, type_id, label = _matches_query(current, query, path_tag=path_tag)
            if matched:
                results.append({"path": {"segments": list(segments)}, "tag": tag, "type_id": type_id, "label": label})
                if len(results) >= limit:
                    truncated = "match_limit"; break
        rows, probe_errors, probe_rpc = _probe_children_via_node(current)
        rpc += probe_rpc
        for row in probe_errors:
            if len(errors) < 50:
                errors.append(row)
        for row in rows:
            if len(queue) + visited - skip_visited > budget["max_nodes"]:
                break
            try:
                child = _call(current, row["collection"]) if row.get("accessor") else _call(current, row["collection"], row["tag"])
            except ExecutionContractError as exc:
                if len(errors) < 50:
                    errors.append({"collection": row["collection"], "code": "CHILD_RESOLVE_FAILED", "message": str(exc)})
                continue
            queue.append((child, segments + [_segment_for_row(row)]))
    else:
        complete = True
    return {"results": results, "errors": errors, "visited": visited, "complete": complete, "truncated_reason": truncated}


def inspect_node(worker: Any, model_tag: str, path: Mapping[str, Any], *, include_values: bool = False) -> dict[str, Any]:
    model = _model(worker, model_tag)
    node = resolve_node_path(model, path)
    result: dict[str, Any] = {"path": NodePath.from_wire(path).as_dict()}
    for field, method in (("tag", "tag"), ("label", "label"), ("type_id", "getType")):
        try: result[field] = str(_call(node, method))
        except ExecutionContractError: result[field] = None
    result["properties"] = property_schema_from_engine(node)["properties"]
    if include_values:
        # Reuse the authoritative typed getter path.  Generic PropFeature.get
        # is intentionally not used: it can collapse an empty vector and an
        # expression into an ambiguous JSON value.
        result["values"] = property_get(worker, model_tag, path,
                                        [str(prop["name"]) for prop in result["properties"]])["properties"]
    return result


def children_node(worker: Any, model_tag: str, path: Mapping[str, Any], *, cursor: str | None = None,
                  limit: int = 100, model_revision: int | None = None) -> dict[str, Any]:
    """List every child of one node with per-level pagination.

    One worker batch per call on the production path.  ``complete`` is true
    only when this page is the last one and every collection probe succeeded;
    ``errors`` lists probe failures explicitly instead of turning them into an
    empty list.  A cursor is bound to the model epoch (worker generation and
    managed revision) and to the exact children list; a stale cursor is
    rejected, never restarted from the first child.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ExecutionContractError("INVALID_REQUEST", "limit must be between 1 and 500")
    model = _model(worker, model_tag)
    node = resolve_node_path(model, path)
    parsed = NodePath.from_wire(path)
    rows, errors, rpc_calls = _collect_children(worker, node)
    rows.sort(key=lambda row: (row["collection"], row.get("tag") or ""))
    list_hash = _sha256_json(rows)
    scope_hash = _sha256_json(parsed.as_dict())
    offset = 0
    if cursor is not None:
        payload = _cursor_decode(cursor)
        _cursor_verify(payload, _cursor_context(worker, model_tag, model_revision, "children", scope_hash))
        if payload.get("list_sha256") != list_hash:
            raise ExecutionContractError("CURSOR_STALE", "the node's children changed since the cursor was issued")
        offset = payload.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= len(rows):
            raise ExecutionContractError("INVALID_CURSOR", "cursor offset is invalid")
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    next_cursor = None
    if next_offset < len(rows):
        payload = _cursor_context(worker, model_tag, model_revision, "children", scope_hash)
        payload.update({"list_sha256": list_hash, "offset": next_offset, "total": len(rows)})
        next_cursor = _cursor_encode(payload)
    complete = next_cursor is None and not errors
    return {"path": parsed.as_dict(), "children": page, "next_cursor": next_cursor,
            "total": len(rows), "complete": complete, "truncated": next_cursor is not None,
            "incomplete": bool(errors), "errors": errors, "rpc_calls": rpc_calls}


def find_nodes(worker: Any, model_tag: str, query: Mapping[str, Any], *, root: Mapping[str, Any] | None = None,
               limit: int = 100, cursor: str | None = None, model_revision: int | None = None,
               budget: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Search the node tree with a deterministic, resumable walk.

    The production worker executes the walk inside one engine-side call
    (``walk_nodes``); fixture/plain workers use an equivalent Python BFS.  A
    resumed cursor re-walks the already-visited prefix (re-validated against
    the model epoch) and continues with new nodes only, so budgets are not
    consumed twice.  Search interruption due to probe errors is reported as
    ``errors`` with ``complete=false``; it is never reported as NOT_FOUND.
    """
    if not isinstance(query, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "query must be an object")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ExecutionContractError("INVALID_REQUEST", "limit must be between 1 and 500")
    parsed_budget = _parse_search_budget(budget)
    root = root or {"segments": []}
    parsed_root = NodePath.from_wire(root)
    scope_hash = _sha256_json({"query": dict(query), "root": parsed_root.as_dict()})
    skip_visited = 0
    if cursor is not None:
        payload = _cursor_decode(cursor)
        _cursor_verify(payload, _cursor_context(worker, model_tag, model_revision, "find", scope_hash))
        skip_visited = payload.get("visited")
        if isinstance(skip_visited, bool) or not isinstance(skip_visited, int) or skip_visited < 0:
            raise ExecutionContractError("INVALID_CURSOR", "cursor visit counter is invalid")
    model = _model(worker, model_tag)
    node = resolve_node_path(model, parsed_root)
    walker = getattr(worker, "walk_nodes", None)
    if callable(walker):
        reply = walker(node, query=dict(query), candidates=list(COLLECTION_CANDIDATES),
                       max_nodes=parsed_budget["max_nodes"], max_seconds=parsed_budget["max_seconds"],
                       skip_visited=skip_visited, limit=limit)
        data = _walk_from_reply(reply)
    else:
        data = _walk_fixture(worker, node, dict(query), parsed_budget, skip_visited, limit)
    base_segments = parsed_root.as_dict()["segments"]
    results = data["results"]
    if base_segments:
        results = [{"path": {"segments": base_segments + row["path"]["segments"]}, "tag": row.get("tag"),
                    "type_id": row.get("type_id"), "label": row.get("label")} for row in results]
    complete = bool(data["complete"]) and not data["errors"]
    next_cursor = None
    if data["truncated_reason"] is not None:
        payload = _cursor_context(worker, model_tag, model_revision, "find", scope_hash)
        payload.update({"visited": data["visited"]})
        next_cursor = _cursor_encode(payload)
    return {"results": results, "count": len(results), "complete": complete,
            "truncated": data["truncated_reason"] is not None, "truncated_reason": data["truncated_reason"],
            "incomplete": not complete, "next_cursor": next_cursor, "errors": data["errors"],
            "visited": data["visited"], "budget": parsed_budget,
            "status": "SUCCEEDED" if complete else "INCOMPLETE"}


def property_schema(worker: Any, model_tag: str, path: Mapping[str, Any], name: str | None = None) -> dict[str, Any]:
    return property_schema_from_engine(resolve_node_path(_model(worker, model_tag), path), name)


def property_get(worker: Any, model_tag: str, path: Mapping[str, Any], names: list[str]) -> dict[str, Any]:
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
        raise ExecutionContractError("INVALID_REQUEST", "names must be a non-empty array of strings")
    node = resolve_node_path(_model(worker, model_tag), path)
    # A full ``properties()`` enumeration is one remote Worker round trip per
    # metadata field for every property.  ``property_get`` is often called
    # once per candidate property, so enumerating the complete table here can
    # exhaust the local TCP ephemeral-port pool before the actual getter is
    # reached.  Query only the requested properties, with one schema request
    # per distinct name, while preserving duplicate names in the response.
    schemas = {
        name: property_schema_from_engine(node, name)
        for name in dict.fromkeys(names)
    }
    values = []
    for name in names:
        row = schemas.get(name)
        if not row or row.get("metadata_status") != "KNOWN":
            raise ExecutionContractError("API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {name!r}")
        getter, kind, rank = row.get("getter"), row.get("kind"), row.get("shape_rank")
        if not isinstance(getter, str) or not isinstance(kind, str) or not isinstance(rank, int):
            raise ExecutionContractError("API_UNSUPPORTED", f"property {name!r} has unsupported value metadata")
        raw = _call(node, getter, name)
        typed = typed_value_from_engine(raw, kind=kind)
        if len(typed["shape"]) != rank:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"getter {getter} returned a value with an unexpected rank")
        typed["java_signature"] = row.get("java_signature")
        values.append({"name": name, "value": typed})
    return {"path": NodePath.from_wire(path).as_dict(), "properties": values}


def _schemas_for(node: Any, properties: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # As in ``property_get``, keep the metadata query bounded by the caller's
    # requested names.  This also ensures a multi-property setter does not
    # perform an unrelated whole-node enumeration before validating input.
    by_name = {
        name: property_schema_from_engine(node, name)
        for name in dict.fromkeys(
            item.get("name") for item in properties if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        )
    }
    out: dict[str, dict[str, Any]] = {}
    for item in properties:
        row = by_name.get(item["name"], {})
        if row.get("metadata_status") == "KNOWN":
            out[item["name"]] = {
                "kind": row.get("kind"), "shape_rank": row.get("shape_rank"),
                "java_signature": row.get("java_signature"), "getter": row.get("getter"),
                "allowed_values": row.get("allowed_values"),
                "metadata_status": "KNOWN",
            }
        else:
            out[item["name"]] = {"metadata_status": "UNKNOWN"}
    return out


# COMSOL stores a PropFeature expression as Java String text and has no
# engine-side "expression" kind.  The recorded comparison rule therefore
# treats expression/string as one textual semantic kind and still requires
# byte-equal text: a COMSOL-normalized expression (whitespace, units) is
# reported as a mismatch so the caller can reconcile, never silently
# accepted.  Numeric comparisons are exact unless the caller passes an
# explicit ``numeric_tolerance``; no implicit tolerance exists.
_TEXTUAL_KINDS = frozenset({"string", "expression"})


def _typed_readback_comparison(requested: Mapping[str, Any], returned: Mapping[str, Any], *, numeric_tolerance: float | None = None) -> dict[str, Any]:
    """Return a small record describing why a typed round trip matched.

    The only cross-kind equivalence is expression<->string, which mirrors the
    engine's storage representation and is recorded in the result instead of
    being an implicit widening.
    """
    requested_kind, returned_kind = requested.get("kind"), returned.get("kind")
    record: dict[str, Any] = {"requested_kind": requested_kind, "returned_kind": returned_kind}
    if requested.get("shape") != returned.get("shape"):
        return {**record, "matched": False, "rule": "shape_mismatch"}
    if requested_kind == returned_kind:
        rule = "exact"
    elif requested_kind in _TEXTUAL_KINDS and returned_kind in _TEXTUAL_KINDS:
        rule = "exact_text_expression_string_mapping"
    else:
        return {**record, "matched": False, "rule": "kind_mismatch"}
    if requested.get("data") == returned.get("data"):
        return {**record, "matched": True, "rule": rule}
    requested_data, returned_data = requested.get("data"), returned.get("data")
    if (rule == "exact" and requested_kind == "float64" and numeric_tolerance is not None
            and isinstance(requested_data, (int, float)) and not isinstance(requested_data, bool)
            and isinstance(returned_data, (int, float)) and not isinstance(returned_data, bool)
            and math.isfinite(float(requested_data)) and math.isfinite(float(returned_data))
            and abs(float(requested_data) - float(returned_data)) <= float(numeric_tolerance)):
        return {**record, "matched": True, "rule": "explicit_numeric_tolerance", "numeric_tolerance": float(numeric_tolerance)}
    return {**record, "matched": False, "rule": rule + "_data_mismatch"}


def _typed_readback_matches(requested: Mapping[str, Any], returned: Mapping[str, Any]) -> bool:
    """Compare the parts of a scalar/vector property contract we can prove."""
    return bool(_typed_readback_comparison(requested, returned)["matched"])


def property_set(worker: Any, model_tag: str, path: Mapping[str, Any], properties: list[dict[str, Any]]) -> dict[str, Any]:
    node = resolve_node_path(_model(worker, model_tag), path)
    schemas = _schemas_for(node, properties)
    for item in properties:
        row = schemas.get(item.get("name")) if isinstance(item, Mapping) else None
        if not row or row.get("metadata_status") != "KNOWN":
            # An unknown property can only be written when the caller carries
            # an explicit, supported Java signature.  This keeps unknown
            # metadata fail-closed while still allowing a versioned adapter
            # to operate on a property absent from a particular COMSOL build.
            raw_value = item.get("value") if isinstance(item, Mapping) else None
            signature = raw_value.get("java_signature") if isinstance(raw_value, Mapping) else None
            sig = signature_spec(signature)
            if sig is None or sig["kind"] != raw_value.get("kind"):
                label = item.get("name") if isinstance(item, Mapping) else "<invalid>"
                raise PreWriteRefusal("API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {label!r}")
            schemas[item["name"]] = {"kind": sig["kind"], "shape_rank": sig["rank"],
                                      "java_signature": signature, "metadata_status": "EXPLICIT"}
    validated = validate_property_set(properties, schemas=schemas)
    for item in validated:
        if item["value"]["kind"] == "int64":
            raise PreWriteRefusal("API_UNSUPPORTED", "COMSOL PropFeature has no verified int64 setter overload")
        row = schemas[item["name"]]
        # A caller-supplied signature is not a substitute for authoritative
        # property metadata.  Without a verified getter we cannot prove the
        # write's target or read back its resulting shape, so reject before the
        # first setter call rather than mutating and discovering that later.
        if not isinstance(row.get("getter"), str) or not row.get("getter"):
            raise PreWriteRefusal("API_UNSUPPORTED", f"property {item['name']!r} has no authoritative readback getter")
        if not item["value"].get("java_signature"):
            item["value"]["java_signature"] = row.get("java_signature")
    applied, failed, not_executed = [], [], []
    execution_state_unknown = False
    for index, item in enumerate(validated):
        dispatch_started = False
        try:
            dispatch_started = True
            _call(node, "set", item["name"], item["value"])
            row = schemas[item["name"]]
            getter = row.get("getter")
            if not getter:
                raise PreWriteRefusal("API_UNSUPPORTED", "property readback getter is unavailable")
            readback = _call(node, getter, item["name"])
            typed_readback = typed_value_from_engine(readback, kind=row.get("kind"))
            typed_readback["java_signature"] = row.get("java_signature")
            comparison = _typed_readback_comparison(item["value"], typed_readback)
            if not comparison["matched"]:
                execution_state_unknown = True
                failed.append({"name": item["name"], "error": "property readback did not match requested kind/shape/data",
                               "readback": typed_readback, "comparison": comparison, "partial_change": True,
                               "execution_state_unknown": True})
                not_executed.extend(validated[index + 1:])
                break
            applied.append({"name": item["name"], "value": item["value"], "readback": typed_readback,
                            "readback_match": True, "comparison": comparison})
        except Exception as exc:
            execution_state_unknown |= dispatch_started
            failed.append({"name": item["name"], "error": str(exc),
                           "partial_change": bool(applied) or dispatch_started,
                           "execution_state_unknown": dispatch_started})
            not_executed.extend(validated[index + 1:])
            break
    success = not failed
    return {"success": success, "data": {"path": NodePath.from_wire(path).as_dict(), "applied": applied, "failed": failed, "not_executed": not_executed},
            "error": None if success else {"code": "EXECUTION_STATE_UNKNOWN" if execution_state_unknown else "ENGINE_CALL_FAILED",
                                             "message": "one or more property assignments failed", "safe_retry": False},
            "partial_change": bool(applied) or execution_state_unknown, "execution_state_unknown": execution_state_unknown,
            "applied": applied}


# ---------------------------------------------------------------------------
# R03: indexed/keyed writes require a real positional readback.
# ---------------------------------------------------------------------------

# PropFeature's indexed getters, verified by javap against the installed
# COMSOL 6.4.0.293 apiplugins/com.comsol.api_1.0.0.jar:
# getString/getDouble/getInt/getBoolean(String, int) read the value stored at
# an index position.  Keyed properties are read with
# getEntryKeys(String) -> String[] plus getEntryKeyIndex(String, String) -> int:
# that pair, never the request payload, is the authority for which key exists
# and at which position the indexed getter reads.
_INDEXED_GETTERS = {"boolean": "getBoolean", "int32": "getInt", "float64": "getDouble",
                    "string": "getString", "expression": "getString"}


def _typed_readback(node: Any, getter: str, name: str, kind: str | None, *args: Any) -> dict[str, Any]:
    return typed_value_from_engine(_call(node, getter, name, *args), kind=kind)


def _flatten_positions(value: Any, prefix: tuple[int, ...] = ()) -> dict[tuple[int, ...], Any]:
    """Map every scalar of a rectangular value to its positional index."""
    positions: dict[tuple[int, ...], Any] = {}
    if isinstance(value, list):
        for index, item in enumerate(value):
            positions.update(_flatten_positions(item, prefix + (index,)))
        return positions
    if prefix:
        positions[prefix] = value
    return positions


def _indexed_shape_change(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any] | None:
    before_shape, after_shape = list(before.get("shape") or []), list(after.get("shape") or [])
    return None if before_shape == after_shape else {"before": before_shape, "after": after_shape}


def _indexed_write_verification(before: Mapping[str, Any], after: Mapping[str, Any], indices: list[int],
                                requested: Mapping[str, Any], kind: str | None) -> dict[str, Any]:
    """Compare a ``setIndex`` write with the engine's own positional readback.

    The record always names the target positions, the observed target value,
    every changed/removed non-target position and any engine-side shape change.
    A target that cannot be located in the observed value is a failure, never a
    silent pass.
    """
    record: dict[str, Any] = {
        "scope": "indexed_element_and_non_target_positions",
        "indices": list(indices), "requested": dict(requested),
        "before": dict(before), "after": dict(after),
        "shape_change": _indexed_shape_change(before, after),
        "appended_positions": [], "removed_positions": [], "changed_positions": [],
        "target_positions": [], "target_comparison": None,
        "observed": None, "expected": dict(requested), "unchanged": None,
        "status": "FAIL", "reason": "not_evaluated",
    }
    shape_after = list(after.get("shape") or [])
    data_after = after.get("data")
    requested_rank = len(requested.get("shape") or [])

    def not_locatable(reason: str, detail: str) -> dict[str, Any]:
        record.update(reason=reason, detail=detail, observed=dict(after))
        return record

    if len(indices) == 2:
        if len(shape_after) != 2 or not isinstance(data_after, list):
            return not_locatable("target_not_locatable", "matrix cell write read back a non-matrix value")
        row_index, column_index = indices
        if not (0 <= row_index < len(data_after) and 0 <= column_index < len(data_after[row_index])):
            return not_locatable("target_not_locatable", "observed matrix does not contain the requested cell")
        observed_raw: Any = data_after[row_index][column_index]
        target_positions = {(row_index, column_index)}
    elif requested_rank == 1:
        if len(shape_after) != 2 or not isinstance(data_after, list):
            return not_locatable("target_not_locatable", "row assignment read back a non-matrix value")
        row_index = indices[0]
        if not 0 <= row_index < len(data_after):
            return not_locatable("target_not_locatable", "observed matrix does not contain the requested row")
        observed_raw = list(data_after[row_index])
        target_positions = {(row_index, column) for column in range(len(data_after[row_index]))}
    else:
        if len(shape_after) != 1 or not isinstance(data_after, list):
            return not_locatable("target_not_locatable", "vector element write read back a non-vector value")
        row_index = indices[0]
        if not 0 <= row_index < len(data_after):
            return not_locatable("target_not_locatable", "observed vector does not contain the requested index")
        observed_raw = data_after[row_index]
        target_positions = {(row_index,)}

    observed = typed_value_from_engine(observed_raw, kind=kind)
    comparison = _typed_readback_comparison(requested, observed)
    record["observed"] = observed
    record["target_comparison"] = comparison
    record["target_positions"] = sorted(list(position) for position in target_positions)
    positions_before = _flatten_positions(before.get("data"))
    positions_after = _flatten_positions(data_after)
    record["appended_positions"] = sorted(list(position) for position in positions_after if position not in positions_before)
    record["removed_positions"] = sorted(list(position) for position in positions_before if position not in positions_after)
    record["changed_positions"] = sorted(
        list(position) for position, value in positions_before.items()
        if position in positions_after and position not in target_positions and positions_after[position] != value
    )
    record["unchanged"] = not record["changed_positions"] and not record["removed_positions"]
    if not comparison["matched"]:
        record.update(reason="target_element_mismatch")
        return record
    if record["changed_positions"] or record["removed_positions"]:
        record.update(reason="non_target_positions_changed")
        return record
    record.update(status="PASS", reason=None)
    return record


def _indexed_envelope(*, path: Mapping[str, Any], name: str, indices: list[int], value: Mapping[str, Any],
                      readback: dict[str, Any] | None, verification: Mapping[str, Any] | None,
                      verification_scope: Mapping[str, Any], error: Mapping[str, Any] | None,
                      dispatch_error: bool) -> dict[str, Any]:
    """Uniform applied/failed/not_executed envelope for a single indexed write."""
    verified = bool(verification and verification.get("status") == "PASS")
    unknown = bool(dispatch_error)
    row = {"name": name, "indices": list(indices), "value": value, "readback": readback,
           "readback_match": verified, "verification": verification, "partial_change": True}
    applied = [row] if verified else []
    failed = [] if verified else [{**row, "error": (error or {}).get("message", "indexed write was not verified"),
                                   "execution_state_unknown": unknown}]
    return {
        "success": verified,
        "data": {"path": NodePath.from_wire(path).as_dict(), "name": name, "indices": list(indices),
                 "value": value, "readback": readback, "applied": applied, "failed": failed,
                 "not_executed": [], "verification": verification, "verification_scope": dict(verification_scope),
                 "shape_change": (verification or {}).get("shape_change") if verification else None},
        "error": None if verified else dict(error or {"code": "VERIFICATION_FAILED",
                                                     "message": "indexed write was not verified by engine readback",
                                                     "safe_retry": False}),
        "partial_change": not verified,
        "execution_state_unknown": unknown,
        "applied": applied,
    }


def property_index_set(worker: Any, model_tag: str, path: Mapping[str, Any], name: str, indices: list[int], value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(indices, list) or not indices or not all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in indices):
        raise PreWriteRefusal("INVALID_REQUEST", "indices must be non-negative integers")
    node = resolve_node_path(_model(worker, model_tag), path)
    row = property_schema_from_engine(node, name)
    if row.get("metadata_status") != "KNOWN":
        supplied = value.get("java_signature") if isinstance(value, Mapping) else None
        if signature_spec(supplied) is None:
            raise PreWriteRefusal("API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {name!r}")
        row = {"kind": signature_spec(supplied)["kind"], "shape_rank": None,
               "java_signature": supplied, "metadata_status": "EXPLICIT"}
    row_rank = row.get("shape_rank")
    if isinstance(row_rank, int) and ((row_rank == 0) or (row_rank == 1 and len(indices) == 2) or (row_rank == 2 and len(indices) not in (1, 2))):
        raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", f"indexed assignment does not match property rank {row_rank}")
    expected_rank = 0 if len(indices) == 2 else (max(0, int(row_rank) - 1) if isinstance(row_rank, int) else None)
    expected = {"kind": row.get("kind"), "shape_rank": expected_rank}
    typed = validate_typed_value(value, expected=expected)
    if typed["kind"] == "int64":
        raise PreWriteRefusal("API_UNSUPPORTED", "COMSOL PropFeature has no verified int64 indexed setter overload")
    if not typed.get("java_signature"):
        base_signatures = {
            ("boolean", 0): "boolean", ("int32", 0): "int", ("int64", 0): "long",
            ("float64", 0): "double", ("string", 0): "java.lang.String",
            ("expression", 0): "java.lang.String",
            ("boolean", 1): "boolean[]", ("int32", 1): "int[]", ("int64", 1): "long[]",
            ("float64", 1): "double[]", ("string", 1): "java.lang.String[]",
            ("expression", 1): "java.lang.String[]",
        }
        typed["java_signature"] = base_signatures.get((typed["kind"], len(typed["shape"])))
    if not typed.get("java_signature"):
        raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", "indexed assignment has no supported Java signature")
    if not isinstance(row.get("getter"), str) or not row.get("getter"):
        raise PreWriteRefusal("API_UNSUPPORTED", f"property {name!r} has no authoritative readback getter")
    if len(indices) not in (1, 2):
        raise PreWriteRefusal("INVALID_REQUEST", "COMSOL setIndex accepts one vector or two matrix indices")
    if len(indices) == 2 and typed["shape"]:
        raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", "matrix cell assignment requires a scalar typed value")
    getter = row.get("getter")
    verification_scope = {
        "scope": "engine_positional_readback", "authoritative_readback": True,
        "compared": ["target_positions", "non_target_positions", "shape_change"],
        "engine": "PropFeature value getter of the same property (whole-property snapshot before and after setIndex)",
        "kind_rule": "kind/shape/data comparison shared with node.property_set; expression<->string is an explicit textual rule and numeric tolerance is never implicit",
    }
    # Read the complete property before dispatching: a failure here proves no
    # setter was called, so an unsupported/unreadable property stays a
    # write-time rejection instead of a post-write unknown.
    before = _typed_readback(node, getter, name, row.get("kind"))
    try:
        # PropFeature.setIndex(name, value, index[, secondIndex]) is the actual
        # COMSOL signature.  The index comes after the typed value, unlike the
        # legacy request's {indices,value} field order.
        if len(indices) == 1:
            _call(node, "setIndex", name, typed, indices[0])
        else:
            _call(node, "setIndex", name, typed, indices[0], indices[1])
    except Exception as exc:
        return _indexed_envelope(
            path=path, name=name, indices=indices, value=typed, readback=None, verification=None,
            verification_scope=verification_scope, dispatch_error=True,
            error={"code": "EXECUTION_STATE_UNKNOWN", "safe_retry": False,
                   "message": f"setIndex was dispatched but the call failed: {type(exc).__name__}"})
    try:
        after = _typed_readback(node, getter, name, row.get("kind"))
    except Exception as exc:
        return _indexed_envelope(
            path=path, name=name, indices=indices, value=typed, readback=None, verification=None,
            verification_scope=verification_scope, dispatch_error=True,
            error={"code": "EXECUTION_STATE_UNKNOWN", "safe_retry": False,
                   "message": f"the write was dispatched but its readback failed: {type(exc).__name__}"})
    verification = _indexed_write_verification(before, after, indices, typed, row.get("kind"))
    error = None if verification["status"] == "PASS" else {
        "code": "VERIFICATION_FAILED", "safe_retry": False,
        "message": f"setIndex on {name!r} was not confirmed by engine readback: {verification['reason']}",
    }
    return _indexed_envelope(path=path, name=name, indices=indices, value=typed, readback=after,
                             verification=verification, verification_scope=verification_scope,
                             error=error, dispatch_error=False)


def _entry_indexed_getter(kind: str | None) -> str:
    getter = _INDEXED_GETTERS.get(str(kind))
    if getter is None:
        raise PreWriteRefusal("API_UNSUPPORTED", f"no authoritative indexed getter exists for kind {kind!r}")
    return getter


def _read_entry_table(node: Any, name: str, kind: str | None) -> dict[str, dict[str, Any]]:
    """Read every keyed entry through getEntryKeys + getEntryKeyIndex + getter.

    Any missing piece of that authoritative path raises before the caller
    writes, so an unreadable keyed property is rejected instead of reported as
    verified from the request payload.
    """
    getter = _entry_indexed_getter(kind)
    keys = _call(node, "getEntryKeys", name)
    if not isinstance(keys, (list, tuple)) or not all(isinstance(item, str) for item in keys):
        raise PreWriteRefusal("ENGINE_CALL_FAILED", f"getEntryKeys({name!r}) did not return a key list")
    table: dict[str, dict[str, Any]] = {}
    for key in keys:
        index = _call(node, "getEntryKeyIndex", name, key)
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise PreWriteRefusal("ENGINE_CALL_FAILED", f"getEntryKeyIndex({name!r}, {key!r}) returned {index!r}")
        table[key] = {"entry_index": index, "value": _typed_readback(node, getter, name, kind, index)}
    return table


def property_entry_set(worker: Any, model_tag: str, path: Mapping[str, Any], name: str, key: str, value: Mapping[str, Any],
                       *, allow_unverified: bool = False) -> dict[str, Any]:
    if not isinstance(name, str) or not name or not isinstance(key, str): raise PreWriteRefusal("INVALID_REQUEST", "name and key are required")
    node = resolve_node_path(_model(worker, model_tag), path)
    row = property_schema_from_engine(node, name)
    if row.get("metadata_status") != "KNOWN":
        signature = value.get("java_signature") if isinstance(value, Mapping) else None
        spec = signature_spec(signature)
        if spec is None or spec["rank"] != 0:
            raise PreWriteRefusal("API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {name!r}")
        expected = {"kind": spec["kind"], "shape_rank": 0}
    else:
        expected = {"kind": row.get("kind"), "shape_rank": 0}
    if not isinstance(row.get("getter"), str) or not row.get("getter"):
        raise PreWriteRefusal("API_UNSUPPORTED", f"property {name!r} has no authoritative readback getter")
    typed = validate_typed_value(value, expected=expected)
    if typed["kind"] == "int64":
        raise PreWriteRefusal("API_UNSUPPORTED", "COMSOL PropFeature has no verified int64 entry setter overload")
    if not typed.get("java_signature"):
        typed["java_signature"] = {"boolean": "boolean", "int32": "int", "int64": "long",
                                    "float64": "double", "string": "java.lang.String",
                                    "expression": "java.lang.String"}.get(typed["kind"])
    kind = row.get("kind")
    if allow_unverified:
        # Explicitly requested unverified dispatch: the caller accepts that the
        # stored entry is not read back.  It is never reported as verified and
        # cannot be reached from the public registry schema.
        _call(node, "setEntry", name, key, typed)
        return {"success": True, "data": {"path": NodePath.from_wire(path).as_dict(), "name": name, "key": key,
                                          "value": typed, "readback": None, "applied": [], "failed": [],
                                          "not_executed": [], "verification": {"status": "NOT_RUN", "checked": []},
                                          "verification_scope": {"scope": "engine_dispatch_only",
                                                                 "authoritative_readback": False,
                                                                 "mode": "unverified_dispatch",
                                                                 "reason": "no authoritative keyed readback path was used"}},
                "error": None, "partial_change": False, "execution_state_unknown": False, "applied": []}
    verification_scope = {
        "scope": "engine_entry_readback", "authoritative_readback": True,
        "compared": ["key_present", "key_index", "entry_value", "other_entries"],
        "engine": "PropFeature getEntryKeys + getEntryKeyIndex + the indexed getter of the same property",
        "kind_rule": "kind/shape/data comparison shared with node.property_set; expression<->string is an explicit textual rule and numeric tolerance is never implicit",
    }
    # Fail closed before the write: without the authoritative key/index/getter
    # path there is no way to prove which entry a setEntry call reached.
    before = _read_entry_table(node, name, kind)
    if key in before:
        cross_check = before[key]["entry_index"]
        if isinstance(cross_check, bool) or not isinstance(cross_check, int) or cross_check < 0:
            raise PreWriteRefusal("ENGINE_CALL_FAILED", f"entry index for key {key!r} is invalid")
    try:
        _call(node, "setEntry", name, key, typed)
        after = _read_entry_table(node, name, kind)
    except Exception as exc:
        return _entry_envelope(
            path=path, name=name, key=key, value=typed, kind=kind, before=before, after=None, verification=None,
            verification_scope=verification_scope, dispatch_error=True,
            error={"code": "EXECUTION_STATE_UNKNOWN", "safe_retry": False,
                   "message": f"setEntry was dispatched but the entry could not be read back: {type(exc).__name__}"})
    verification = _entry_write_verification(before, after, key, typed)
    error = None if verification["status"] == "PASS" else {
        "code": "VERIFICATION_FAILED", "safe_retry": False,
        "message": f"setEntry on {name!r}[{key!r}] was not confirmed by engine readback: {verification['reason']}",
    }
    return _entry_envelope(path=path, name=name, key=key, value=typed, kind=kind, before=before, after=after,
                           verification=verification, verification_scope=verification_scope, error=error,
                           dispatch_error=False)


def _entry_write_verification(before: Mapping[str, Mapping[str, Any]], after: Mapping[str, Mapping[str, Any]],
                              key: str, requested: Mapping[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "scope": "keyed_entry_and_other_entries",
        "keys_before": list(before), "keys_after": list(after),
        "key_added": key not in before,
        "key_present": key in after,
        "key_index": after[key]["entry_index"] if key in after else None,
        "observed": after[key]["value"] if key in after else None,
        "expected": dict(requested),
        "changed_entries": [], "unexpected_new_keys": [],
        "status": "FAIL", "reason": "not_evaluated",
    }
    if key not in after:
        record.update(reason="entry_key_missing_after_write")
        return record
    expected_index = record["keys_after"].index(key)
    if record["key_index"] != expected_index:
        # Two authoritative APIs disagree about the stored position; the value
        # that the indexed getter returned then describes an unknown entry.
        record.update(reason="entry_index_inconsistent", consistent_index=expected_index)
        return record
    comparison = _typed_readback_comparison(requested, record["observed"])
    record["value_comparison"] = comparison
    record["changed_entries"] = sorted(
        name for name, row in before.items()
        if name != key and (name not in after or after[name]["value"] != row["value"])
    )
    record["unexpected_new_keys"] = sorted(name for name in after if name not in before and name != key)
    record["other_entries_unchanged"] = not record["changed_entries"] and not record["unexpected_new_keys"]
    if not comparison["matched"]:
        record.update(reason="entry_value_mismatch")
        return record
    if not record["other_entries_unchanged"]:
        record.update(reason="other_entries_changed")
        return record
    record.update(status="PASS", reason=None)
    return record


def _entry_envelope(*, path: Mapping[str, Any], name: str, key: str, value: Mapping[str, Any], kind: str | None,
                    before: Mapping[str, Mapping[str, Any]] | None, after: Mapping[str, Mapping[str, Any]] | None,
                    verification: Mapping[str, Any] | None, verification_scope: Mapping[str, Any],
                    error: Mapping[str, Any] | None, dispatch_error: bool) -> dict[str, Any]:
    verified = bool(verification and verification.get("status") == "PASS")
    unknown = bool(dispatch_error)
    readback = after[key]["value"] if isinstance(after, Mapping) and key in after else None
    row = {"name": name, "key": key, "value": value, "readback": readback, "readback_match": verified,
           "verification": verification, "partial_change": True}
    applied = [row] if verified else []
    failed = [] if verified else [{**row, "error": (error or {}).get("message", "keyed write was not verified"),
                                   "execution_state_unknown": unknown}]
    return {
        "success": verified,
        "data": {"path": NodePath.from_wire(path).as_dict(), "name": name, "key": key, "value": value,
                 "readback": readback, "applied": applied, "failed": failed, "not_executed": [],
                 "verification": verification, "verification_scope": dict(verification_scope)},
        "error": None if verified else dict(error or {"code": "VERIFICATION_FAILED",
                                                     "message": "keyed write was not verified by engine readback",
                                                     "safe_retry": False}),
        "partial_change": not verified,
        "execution_state_unknown": unknown,
        "applied": applied,
    }


def create_checkpoint(worker: Any, model_tag: str, destination: Path, label: str, *, include_solution: bool = False) -> dict[str, Any]:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    model = _model(worker, model_tag)
    started_ns = time.monotonic_ns()
    final = destination
    # Checkpoints are immutable evidence.  Preserve a complete artifact when
    # a label collides and publish a fresh unique path instead of overwriting.
    if final.exists():
        final = destination.with_name(f"{destination.stem}-{uuid4().hex[:12]}{destination.suffix}")
    save_count = 1
    try:
        published = atomic_save(final, lambda temporary: model.save(str(temporary)), final.parent, overwrite=False)
    except AtomicSaveError as exc:
        raise ExecutionContractError("ARTIFACT_MISSING", "COMSOL checkpoint save failed") from exc
    if not final.is_file() or final.stat().st_size <= 0 or not published.get("checkpoint", {}).get("verified"):
        raise ExecutionContractError("ARTIFACT_MISSING", "checkpoint save produced no complete file")
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    elapsed_ms = round((time.monotonic_ns() - started_ns) / 1_000_000, 3)
    return {"checkpoint_id": f"checkpoint-{digest[:20]}", "path": str(final), "label": label,
            "sha256": digest, "size": final.stat().st_size, "save_count": save_count,
            "save_elapsed_ms": elapsed_ms, "include_solution_requested": bool(include_solution),
            "solution_scope": "included_by_COMSOL_save_unverified",
            "restore_scope": {"model_settings": True, "solution": "included_by_COMSOL_save_unverified",
                              "external_dependencies": False, "gui_state": "unknown"}}


def execute_transaction(worker: Any, model_tag: str, actions: list[dict[str, Any]], *, runner: Any, invariants: list[dict[str, Any]] | None = None, checkpoint_id: str | None = None,
                        model_ref: Mapping[str, Any] | None = None, revision: int | None = None,
                        pre_revision: int | None = None) -> dict[str, Any]:
    """Apply the actions, then verify the declared invariants against the model.

    ``success`` requires both a completed action set and no failing *required*
    invariant.  A required invariant that fails keeps the applied actions, the
    checkpoint reference and the recovery option visible; it never masquerades
    as an atomic rollback.
    """
    record = run_transaction(actions, runner=runner, invariants=invariants, checkpoint_id=checkpoint_id,
                             model_ref=model_ref, revision=revision, pre_revision=pre_revision,
                             invariant_verifier=_invariant_verifier(worker, model_tag))
    executed = record.execution_status == "SUCCEEDED"
    success = executed and record.verification_status != "FAILED"
    return {"success": success, "data": record.as_dict(),
            "error": None if success else record.error,
            "partial_change": record.execution_status == "PARTIAL" or (executed and record.verification_status == "FAILED"),
            "execution_state_unknown": record.execution_state_unknown,
            "transaction_id": record.transaction_id}


# ---------------------------------------------------------------------------
# R04: live evaluation of the typed invariant vocabulary.
# ---------------------------------------------------------------------------

# Selection readback is authoritative through com.comsol.model.Selection:
# ``int[] entities()`` and ``int[] entities(int)`` were verified with javap on
# 2026-09-20 against the installed COMSOL 6.4.0.293 apiplugins jar
# (com.comsol.api_1.0.0.jar), where SelectionFeature extends ...Selection and
# the worker method allowlist already exposes "entities".  A node without that
# accessor fails the check with evidence instead of being assumed non-empty.
_SELECTION_DIMENSIONS = (0, 1, 2, 3)


def _invariant_verifier(worker: Any, model_tag: str) -> Any:
    def verify(declaration: Mapping[str, Any], _index: int) -> dict[str, Any]:
        return evaluate_invariant(worker, model_tag, declaration)
    return verify


def evaluate_invariant(worker: Any, model_tag: str, declaration: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one declared invariant against the live bound model."""
    if not isinstance(declaration, Mapping):
        raise ExecutionContractError("INVALID_INVARIANT", "invariant must be an object")
    kind = declaration.get("type")
    if kind not in INVARIANT_TYPES:
        raise ExecutionContractError("INVARIANT_UNSUPPORTED", f"unsupported invariant type {kind!r}")
    path = declaration.get("path")
    expected: dict[str, Any] = {"path": path}
    try:
        node = resolve_node_path(_model(worker, model_tag), path)
    except ExecutionContractError as exc:
        if kind == "node_exists" and exc.code == "NODE_NOT_FOUND":
            return {"status": "FAIL", "reason": "node_not_found",
                    "observed": {"error": exc.code, "message": str(exc)}, "expected": expected}
        return {"status": "NOT_RUN", "reason": "node_resolution_unavailable",
                "observed": {"error": exc.code, "message": str(exc)}, "expected": expected}

    if kind == "node_exists":
        return {"status": "PASS", "reason": None,
                "observed": {"tag": _safe_node_field(node, "tag"), "type_id": _safe_node_field(node, "getType")},
                "expected": expected}

    if kind == "node_type":
        expected = {**expected, "type_id": declaration.get("type_id")}
        observed_type = _safe_node_field(node, "getType")
        if observed_type is None:
            return {"status": "NOT_RUN", "reason": "type_readback_unavailable", "observed": {}, "expected": expected}
        if observed_type != declaration.get("type_id"):
            return {"status": "FAIL", "reason": "node_type_mismatch",
                    "observed": {"type_id": observed_type}, "expected": expected}
        return {"status": "PASS", "reason": None, "observed": {"type_id": observed_type}, "expected": expected}

    if kind in {"property_equals", "property_tolerance"}:
        name = declaration.get("name")
        expected = {**expected, "name": name}
        schema = property_schema_from_engine(node, name)
        if schema.get("metadata_status") != "KNOWN":
            return {"status": "FAIL", "reason": "property_metadata_unknown",
                    "observed": {"metadata_status": schema.get("metadata_status"), "value_type": schema.get("value_type")},
                    "expected": expected}
        raw = _call(node, schema["getter"], name)
        observed = typed_value_from_engine(raw, kind=schema.get("kind"))
        if kind == "property_equals":
            requested = validate_typed_value(declaration.get("value"))
            comparison = _typed_readback_comparison(requested, observed)
            status = "PASS" if comparison["matched"] else "FAIL"
            # ``observed``/``expected`` are both TypedValue objects so the
            # evidence is directly comparable; the declaration that names the
            # property lives in the durable record's ``invariants``.
            return {"status": status, "reason": None if status == "PASS" else "value_mismatch",
                    "observed": observed, "expected": requested, "comparison": comparison}
        if schema.get("kind") not in {"float64", "int32"}:
            return {"status": "FAIL", "reason": "property_not_numeric", "observed": observed,
                    "expected": {"kind": schema.get("kind"), "value": declaration.get("value")}}
        if isinstance(raw, (list, tuple)):
            return {"status": "FAIL", "reason": "property_not_scalar", "observed": observed,
                    "expected": {"value": declaration.get("value"), "tolerance": declaration.get("tolerance")}}
        target, tolerance = float(declaration["value"]), float(declaration["tolerance"])
        delta = abs(float(observed["data"]) - target)
        status = "PASS" if delta <= tolerance else "FAIL"
        return {"status": status, "reason": None if status == "PASS" else "value_outside_tolerance",
                "observed": {**observed, "target": target, "tolerance": tolerance, "delta": delta},
                "expected": {"value": target, "tolerance": tolerance, "kind": schema.get("kind")}}

    if kind in {"selection_non_empty", "selection_dimension"}:
        if kind == "selection_non_empty" and declaration.get("selection_name") is not None:
            names = {_safe_node_field(node, "tag"), _safe_node_field(node, "label")}
            expected = {**expected, "selection_name": declaration["selection_name"]}
            if declaration["selection_name"] not in names:
                return {"status": "FAIL", "reason": "selection_name_mismatch",
                        "observed": {"tag": None if names == {None} else sorted(name for name in names if name)},
                        "expected": expected}
        try:
            entities = _call(node, "entities")
        except ExecutionContractError as exc:
            return {"status": "FAIL", "reason": "selection_readback_unavailable",
                    "observed": {"error": exc.code, "message": str(exc)}, "expected": expected}
        if not isinstance(entities, (list, tuple)):
            raise ExecutionContractError("ENGINE_CALL_FAILED", "Selection.entities() did not return an entity list")
        entities = [int(entity) for entity in entities]
        if kind == "selection_non_empty":
            status = "PASS" if entities else "FAIL"
            return {"status": status, "reason": None if status == "PASS" else "empty_selection",
                    "observed": {"entities": entities, "count": len(entities)}, "expected": expected}
        dimension = int(declaration["dimension"])
        expected = {**expected, "dimension": dimension}
        in_dimension = _call(node, "entities", dimension)
        if not isinstance(in_dimension, (list, tuple)):
            raise ExecutionContractError("ENGINE_CALL_FAILED", "Selection.entities(dimension) did not return an entity list")
        in_dimension = [int(entity) for entity in in_dimension]
        other_dimensions: dict[str, Any] = {}
        for other in _SELECTION_DIMENSIONS:
            if other == dimension:
                continue
            try:
                other_dimensions[str(other)] = [int(entity) for entity in (_call(node, "entities", other) or [])]
            except ExecutionContractError as exc:
                other_dimensions[str(other)] = {"unreadable": exc.code}
        conflicting = {dim: rows for dim, rows in other_dimensions.items() if isinstance(rows, list) and rows}
        observed = {"dimension": dimension, "entities": in_dimension,
                    "other_dimension_entities": other_dimensions}
        if not in_dimension:
            return {"status": "FAIL", "reason": "empty_selection", "observed": observed, "expected": expected}
        if conflicting:
            return {"status": "FAIL", "reason": "selection_spans_other_dimensions",
                    "observed": observed, "expected": expected}
        return {"status": "PASS", "reason": None, "observed": observed, "expected": expected}

    raise ExecutionContractError("INVARIANT_UNSUPPORTED", f"unsupported invariant type {kind!r}")
