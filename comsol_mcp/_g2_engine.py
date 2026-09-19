"""COMSOL-facing implementation for the bounded G2 operation set."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time
from typing import Any, Mapping
from uuid import uuid4

from ._atomic_save import AtomicSaveError, atomic_save
from ._execution_contract import ExecutionContractError, canonical_project_path
from ._g2_code import describe_source, execution_result
from ._g2_contract import (
    NodePath, engine_value_spec, property_schema_from_engine, resolve_node_path,
    signature_spec, typed_value_from_engine, validate_property_set,
    validate_typed_value,
)
from ._g2_transactions import TransactionRecord, run_transaction, transaction_fingerprint


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
        raise ExecutionContractError("ENGINE_CALL_FAILED", f"COMSOL {method} call failed: {type(exc).__name__}") from exc


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


def children_node(worker: Any, model_tag: str, path: Mapping[str, Any], *, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ExecutionContractError("INVALID_REQUEST", "limit must be between 1 and 500")
    model = _model(worker, model_tag); node = resolve_node_path(model, path)
    rows: list[dict[str, Any]] = []
    # These are collection methods exposed by the persistent worker.  A node
    # may support only a subset; unsupported collections are simply omitted.
    for collection in ("component", "geom", "mesh", "physics", "feature", "study", "sol", "dataset", "result", "numerical"):
        try:
            container = _call(node, collection)
            tags = _call(container, "tags")
            for tag in list(tags or []): rows.append({"collection": collection, "tag": str(tag)})
        except Exception:
            continue
    rows.sort(key=lambda row: (row["collection"], row["tag"]))
    start = int(cursor or 0) if str(cursor or "0").isdigit() else 0
    page = rows[start:start + limit]
    return {"path": NodePath.from_wire(path).as_dict(), "children": page,
            "next_cursor": str(start + len(page)) if start + len(page) < len(rows) else None, "total": len(rows)}


def find_nodes(worker: Any, model_tag: str, query: Mapping[str, Any], *, root: Mapping[str, Any] | None = None, limit: int = 100) -> dict[str, Any]:
    if not isinstance(query, Mapping): raise ExecutionContractError("INVALID_REQUEST", "query must be an object")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500: raise ExecutionContractError("INVALID_REQUEST", "limit must be between 1 and 500")
    root = root or {"segments": []}
    model = _model(worker, model_tag)
    start_node = resolve_node_path(model, root)
    wanted_tag, wanted_type, wanted_label = query.get("tag"), query.get("type_id", query.get("type")), query.get("label")
    results: list[dict[str, Any]] = []
    queue: list[tuple[Any, list[dict[str, str]]]] = [(start_node, NodePath.from_wire(root).as_dict()["segments"])]
    while queue and len(results) < limit:
        node, segments = queue.pop(0)
        current: dict[str, Any] = {"segments": segments}
        try: tag = str(_call(node, "tag"))
        except Exception: tag = None
        try: type_id = str(_call(node, "getType"))
        except Exception: type_id = None
        try: label = str(_call(node, "label"))
        except Exception: label = None
        if ((wanted_tag is None or wanted_tag == tag) and (wanted_type is None or wanted_type == type_id) and (wanted_label is None or wanted_label == label)):
            results.append({"path": current, "tag": tag, "type_id": type_id, "label": label})
        # Use children_node's collection logic without issuing another model
        # load.  Each child is resolved from the current node via the typed
        # segment and then queued for recursive inspection.
        for child in children_node(worker, model_tag, {"segments": segments}, limit=500).get("children", []):
            child_segments = segments + [child]
            try: queue.append((resolve_node_path(model, {"segments": child_segments}), child_segments))
            except Exception: continue
    return {"results": results, "count": len(results)}


def property_schema(worker: Any, model_tag: str, path: Mapping[str, Any], name: str | None = None) -> dict[str, Any]:
    return property_schema_from_engine(resolve_node_path(_model(worker, model_tag), path), name)


def property_get(worker: Any, model_tag: str, path: Mapping[str, Any], names: list[str]) -> dict[str, Any]:
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
        raise ExecutionContractError("INVALID_REQUEST", "names must be a non-empty array of strings")
    node = resolve_node_path(_model(worker, model_tag), path)
    schema_rows = property_schema_from_engine(node).get("properties", [])
    schemas = {str(row.get("name")): row for row in schema_rows}
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
    rows = property_schema_from_engine(node).get("properties", [])
    by_name = {row.get("name"): row for row in rows}
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


def _typed_readback_matches(requested: Mapping[str, Any], returned: Mapping[str, Any]) -> bool:
    """Compare the parts of a scalar/vector property contract we can prove."""
    if requested.get("kind") != returned.get("kind") or requested.get("shape") != returned.get("shape"):
        return False
    # COMSOL may normalize expression whitespace/units in a version-specific
    # way.  G2 only treats an exact expression echo as a verified round trip;
    # callers receive the readback when it differs so they can reconcile.
    return requested.get("data") == returned.get("data")


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
                raise ExecutionContractError("API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {label!r}")
            schemas[item["name"]] = {"kind": sig["kind"], "shape_rank": sig["rank"],
                                      "java_signature": signature, "metadata_status": "EXPLICIT"}
    validated = validate_property_set(properties, schemas=schemas)
    for item in validated:
        if item["value"]["kind"] == "int64":
            raise ExecutionContractError("API_UNSUPPORTED", "COMSOL PropFeature has no verified int64 setter overload")
        row = schemas[item["name"]]
        # A caller-supplied signature is not a substitute for authoritative
        # property metadata.  Without a verified getter we cannot prove the
        # write's target or read back its resulting shape, so reject before the
        # first setter call rather than mutating and discovering that later.
        if not isinstance(row.get("getter"), str) or not row.get("getter"):
            raise ExecutionContractError("API_UNSUPPORTED", f"property {item['name']!r} has no authoritative readback getter")
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
                raise ExecutionContractError("API_UNSUPPORTED", "property readback getter is unavailable")
            readback = _call(node, getter, item["name"])
            typed_readback = typed_value_from_engine(readback, kind=row.get("kind"))
            typed_readback["java_signature"] = row.get("java_signature")
            matched = _typed_readback_matches(item["value"], typed_readback)
            if not matched:
                execution_state_unknown = True
                failed.append({"name": item["name"], "error": "property readback did not match requested kind/shape/data",
                               "readback": typed_readback, "partial_change": True,
                               "execution_state_unknown": True})
                not_executed.extend(validated[index + 1:])
                break
            applied.append({"name": item["name"], "value": item["value"], "readback": typed_readback,
                            "readback_match": True})
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


def property_index_set(worker: Any, model_tag: str, path: Mapping[str, Any], name: str, indices: list[int], value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(indices, list) or not indices or not all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in indices):
        raise ExecutionContractError("INVALID_REQUEST", "indices must be non-negative integers")
    node = resolve_node_path(_model(worker, model_tag), path)
    row = property_schema_from_engine(node, name)
    if row.get("metadata_status") != "KNOWN":
        supplied = value.get("java_signature") if isinstance(value, Mapping) else None
        if signature_spec(supplied) is None:
            raise ExecutionContractError("API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {name!r}")
        row = {"kind": signature_spec(supplied)["kind"], "shape_rank": None,
               "java_signature": supplied, "metadata_status": "EXPLICIT"}
    row_rank = row.get("shape_rank")
    if isinstance(row_rank, int) and ((row_rank == 0) or (row_rank == 1 and len(indices) == 2) or (row_rank == 2 and len(indices) not in (1, 2))):
        raise ExecutionContractError("PROPERTY_TYPE_MISMATCH", f"indexed assignment does not match property rank {row_rank}")
    expected_rank = 0 if len(indices) == 2 else (max(0, int(row_rank) - 1) if isinstance(row_rank, int) else None)
    expected = {"kind": row.get("kind"), "shape_rank": expected_rank}
    typed = validate_typed_value(value, expected=expected)
    if typed["kind"] == "int64":
        raise ExecutionContractError("API_UNSUPPORTED", "COMSOL PropFeature has no verified int64 indexed setter overload")
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
        raise ExecutionContractError("PROPERTY_TYPE_MISMATCH", "indexed assignment has no supported Java signature")
    if not isinstance(row.get("getter"), str) or not row.get("getter"):
        raise ExecutionContractError("API_UNSUPPORTED", f"property {name!r} has no authoritative readback getter")
    if len(indices) not in (1, 2):
        raise ExecutionContractError("INVALID_REQUEST", "COMSOL setIndex accepts one vector or two matrix indices")
    # PropFeature.setIndex(name, value, index[, secondIndex]) is the actual
    # COMSOL signature.  The index comes after the typed value, unlike the
    # legacy request's {indices,value} field order.
    if len(indices) == 1:
        _call(node, "setIndex", name, typed, indices[0])
    else:
        if typed["shape"]:
            raise ExecutionContractError("PROPERTY_TYPE_MISMATCH", "matrix cell assignment requires a scalar typed value")
        _call(node, "setIndex", name, typed, indices[0], indices[1])
    getter = row.get("getter")
    readback = _call(node, getter, name)
    return {"path": NodePath.from_wire(path).as_dict(), "name": name, "indices": indices,
            "value": typed, "readback": typed_value_from_engine(readback, kind=row.get("kind"))}


def property_entry_set(worker: Any, model_tag: str, path: Mapping[str, Any], name: str, key: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(name, str) or not name or not isinstance(key, str): raise ExecutionContractError("INVALID_REQUEST", "name and key are required")
    node = resolve_node_path(_model(worker, model_tag), path)
    row = property_schema_from_engine(node, name)
    if row.get("metadata_status") != "KNOWN":
        signature = value.get("java_signature") if isinstance(value, Mapping) else None
        spec = signature_spec(signature)
        if spec is None or spec["rank"] != 0:
            raise ExecutionContractError("API_UNSUPPORTED", f"authoritative value metadata is unavailable for property {name!r}")
        expected = {"kind": spec["kind"], "shape_rank": 0}
    else:
        expected = {"kind": row.get("kind"), "shape_rank": 0}
    if not isinstance(row.get("getter"), str) or not row.get("getter"):
        raise ExecutionContractError("API_UNSUPPORTED", f"property {name!r} has no authoritative readback getter")
    typed = validate_typed_value(value, expected=expected)
    if typed["kind"] == "int64":
        raise ExecutionContractError("API_UNSUPPORTED", "COMSOL PropFeature has no verified int64 entry setter overload")
    if not typed.get("java_signature"):
        typed["java_signature"] = {"boolean": "boolean", "int32": "int", "int64": "long",
                                    "float64": "double", "string": "java.lang.String",
                                    "expression": "java.lang.String"}.get(typed["kind"])
    _call(node, "setEntry", name, key, typed)
    return {"path": NodePath.from_wire(path).as_dict(), "name": name, "key": key, "value": typed}


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


def execute_transaction(worker: Any, model_tag: str, actions: list[dict[str, Any]], *, runner: Any, invariants: list[dict[str, Any]] | None = None, checkpoint_id: str | None = None) -> dict[str, Any]:
    record = run_transaction(actions, runner=runner, invariants=invariants, checkpoint_id=checkpoint_id)
    return {"success": record.status == "SUCCEEDED", "data": record.as_dict(),
            "error": None if record.status == "SUCCEEDED" else {"code": "EXECUTION_STATE_UNKNOWN" if record.status == "UNKNOWN" else "PARTIAL_FAILURE" if record.status == "PARTIAL" else "EXECUTION_FAILED",
            "message": "transaction did not apply every action", "safe_retry": False},
            "partial_change": record.status == "PARTIAL", "execution_state_unknown": record.status == "UNKNOWN",
            "transaction_id": record.transaction_id}
