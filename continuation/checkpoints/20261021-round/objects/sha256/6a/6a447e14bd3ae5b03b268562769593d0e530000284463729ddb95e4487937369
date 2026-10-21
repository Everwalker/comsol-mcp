"""Bounded JSON/CSV parameter-table import and export for the managed G3 path.

The module only reads and writes through the already-bound Worker model and
the project-scoped ArtifactStore. It never evaluates expressions locally.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from ._execution_contract import ExecutionContractError
from ._g3_common import (
    accessor_container,
    call_probe,
    collection_spec,
    expression_names,
    expression_write,
    operation_arguments,
    tag_list,
    validate_tag,
)
from ._g3_w13 import _validate_name, parameter_list

FORMAT_VERSION = "comsol-mcp-parameter-table/1"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 4096
MAX_GROUPS = 128
MAX_FIELD_BYTES = {
    "group": 63,
    "name": 64,
    "expression": 16384,
    "description": 8192,
    "unit": 128,
}
CSV_HEADER = ("group", "name", "expression", "description", "unit", "unit_status")
_IDENTITY_FIELDS = {
    "project_id", "session_id", "model_ref", "expected_revision",
    "idempotency_key", "request_id", "revision",
}
_PRIVATE_FIELDS = {"_managed_context", "_registered_artifact"}


def _fail(code: str, message: str, *, stage: str = "validation") -> ExecutionContractError:
    return ExecutionContractError(code, message, stage=stage)


def _args(arguments: Any, allowed: tuple[str, ...], required: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(arguments, Mapping):
        raise _fail("INVALID_REQUEST", "parameter table arguments must be an object")
    public = {key: value for key, value in arguments.items() if key not in _PRIVATE_FIELDS}
    try:
        return operation_arguments(public, (*allowed, *_IDENTITY_FIELDS), required, "arguments")
    except ExecutionContractError as exc:
        if exc.stage == "validation":
            raise
        raise ExecutionContractError(exc.code, str(exc), stage="validation", details=exc.details) from exc


def _context(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    context = arguments.get("_managed_context")
    if not isinstance(context, Mapping):
        raise _fail("RUNTIME_CONFIGURATION_REQUIRED", "managed parameter I/O context is unavailable")
    required = ("project_root", "project_id", "operation_id", "request_id", "model_ref",
                "source_revision", "host_identity", "engine_host_identity", "operation_store")
    if any(key not in context for key in required):
        raise _fail("RUNTIME_CONFIGURATION_REQUIRED", "managed parameter I/O context is incomplete")
    return context


def _check_managed_guards(context: Mapping[str, Any], worker: Any, model_tag: str, *, after_read: bool = False) -> None:
    ref = context.get("_model_ref_object")
    service = context.get("_execution_service")
    expected = context.get("source_revision")
    try:
        if ref is not None and service is not None:
            current = service.ledger._state_for(ref).revision
            if current != expected:
                raise _fail(
                    "EXECUTION_STATE_UNKNOWN" if after_read else "REVISION_CONFLICT",
                    "managed model revision changed during parameter I/O",
                    stage="post_dispatch" if after_read else "pre_dispatch",
                )
            # ModelRef.generation counts a particular model's incarnations;
            # Worker.generation is its connection epoch. They are separate
            # identities (a new model at epoch 2 legitimately has generation 1).
            # The production backend captures the original Worker/epoch in its
            # private callback context, before any I/O getter or setter.
            epoch = context.get("_source_worker_epoch")
            generation = getattr(worker, "generation", None)
            if (context.get("_source_worker") is not worker or type(epoch) is not int
                    or epoch < 1 or type(generation) is not int or generation != epoch):
                raise _fail(
                    "EXECUTION_STATE_UNKNOWN" if after_read else "MODEL_REF_STALE",
                    "Worker connection epoch or original receiver changed during parameter I/O",
                    stage="post_dispatch" if after_read else "validation",
                )
            ref_tag = getattr(ref, "model_tag", None)
            if isinstance(ref_tag, str) and ref_tag != model_tag:
                raise _fail("MODEL_REF_STALE", "bound model tag no longer matches the managed ModelRef",
                            stage="validation")
    except ExecutionContractError:
        raise
    except Exception as exc:
        raise _fail("MODEL_REF_STALE", "managed model identity could not be revalidated",
                    stage="post_dispatch" if after_read else "validation") from exc


def _safe_text(value: Any, field: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str):
        raise _fail("INVALID_REQUEST", f"{field} must be a string")
    limit = maximum if maximum is not None else MAX_FIELD_BYTES[field]
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise _fail("INVALID_REQUEST", f"{field} is not valid UTF-8 text") from exc
    if size > limit or "\x00" in value:
        raise _fail("INVALID_REQUEST", f"{field} exceeds its supported text bound")
    return value


def _row(raw: Any, index: int, *, csv_mode: bool = False) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise _fail("INVALID_REQUEST", f"rows[{index}] must be an object")
    required = set(CSV_HEADER)
    if set(raw) != required:
        raise _fail("INVALID_REQUEST", f"rows[{index}] has unsupported or missing fields")
    group = _safe_text(raw.get("group"), "group")
    name = _validate_name(
        _safe_text(raw.get("name"), "name", maximum=MAX_FIELD_BYTES["name"]),
        f"rows[{index}].name",
    )
    expression = _safe_text(raw.get("expression"), "expression")
    description = _safe_text(raw.get("description"), "description")
    unit = raw.get("unit")
    if unit is not None:
        unit = _safe_text(unit, "unit")
    status = raw.get("unit_status")
    if not isinstance(status, str) or status not in {"VERIFIED", "NO_UNIT_RETURNED"}:
        raise _fail("INVALID_REQUEST", f"rows[{index}].unit_status must declare a verified unit or successful null")
    if (status == "NO_UNIT_RETURNED") != (unit is None):
        raise _fail("INVALID_REQUEST", f"rows[{index}] unit and unit_status are inconsistent")
    return {
        "group": validate_tag(group, f"rows[{index}].group"),
        "name": name,
        "expression": expression,
        "description": description,
        "unit": unit,
        "unit_status": status,
    }


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


_MAX_JSON_NESTING = 64


def _bounded_json_loads(value: str) -> Any:
    """Parse a JSON value only after a string-aware nesting bound check."""
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_JSON_NESTING:
                raise ValueError("JSON nesting exceeds the supported limit")
        elif character in "]}":
            depth -= 1
    return json.loads(value, object_pairs_hook=_json_pairs)


def _csv_unit(value: str, index: int) -> str | None:
    try:
        result = _bounded_json_loads(value)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise _fail("INVALID_REQUEST", f"CSV rows[{index}].unit must be a JSON string or null") from exc
    if result is not None and not isinstance(result, str):
        raise _fail("INVALID_REQUEST", f"CSV rows[{index}].unit must be a JSON string or null")
    return result


def _parse_json(raw: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        payload = _bounded_json_loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise _fail("INVALID_REQUEST", "registered JSON parameter table is malformed UTF-8 or JSON") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"schema", "groups", "rows"}:
        raise _fail("INVALID_REQUEST", "JSON parameter table must contain only schema, groups, and rows")
    if payload.get("schema") != FORMAT_VERSION:
        raise _fail("INVALID_REQUEST", "JSON parameter table schema version is unsupported")
    groups = payload.get("groups")
    rows = payload.get("rows")
    if (not isinstance(groups, list) or len(groups) > MAX_GROUPS
            or not all(isinstance(group, str) for group in groups)):
        raise _fail("INVALID_REQUEST", "JSON groups must be a bounded string array")
    clean_groups = [validate_tag(_safe_text(group, "group"), "group") for group in groups]
    if len(set(clean_groups)) != len(clean_groups):
        raise _fail("INVALID_REQUEST", "JSON groups must not contain duplicates")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise _fail("INVALID_REQUEST", f"JSON rows must not exceed {MAX_ROWS}")
    clean_rows = [_row(item, index) for index, item in enumerate(rows)]
    if any(item["group"] not in set(clean_groups) for item in clean_rows):
        raise _fail("INVALID_REQUEST", "every JSON row group must be declared in groups")
    return clean_groups, clean_rows


def _parse_csv(raw: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        source = raw.decode("utf-8", errors="strict")
        reader = csv.reader(io.StringIO(source, newline=""), strict=True)
        header = next(reader, None)
        if tuple(header or ()) != CSV_HEADER:
            raise _fail("INVALID_REQUEST", "CSV header must be group,name,expression,description,unit,unit_status")
        rows: list[dict[str, Any]] = []
        groups: list[str] = []
        for index, values in enumerate(reader):
            if index >= MAX_ROWS:
                raise _fail("INVALID_REQUEST", f"CSV rows must not exceed {MAX_ROWS}")
            if len(values) != len(CSV_HEADER):
                raise _fail("INVALID_REQUEST", f"CSV row {index + 2} must contain exactly six columns")
            group_cell, name, expression, description, unit_cell, unit_status = values
            try:
                group = _bounded_json_loads(group_cell)
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                raise _fail("INVALID_REQUEST", f"CSV row {index + 2} group must be a JSON string cell") from exc
            if not isinstance(group, str):
                raise _fail("INVALID_REQUEST", f"CSV row {index + 2} group must be a JSON string cell")
            row = _row({
                "group": group, "name": name, "expression": expression,
                "description": description, "unit": _csv_unit(unit_cell, index),
                "unit_status": unit_status,
            }, index, csv_mode=True)
            rows.append(row)
            if row["group"] not in groups:
                groups.append(row["group"])
    except UnicodeDecodeError as exc:
        raise _fail("INVALID_REQUEST", "registered CSV parameter table is not valid UTF-8") from exc
    except csv.Error as exc:
        raise _fail("INVALID_REQUEST", "registered CSV parameter table is malformed") from exc
    if not rows:
        raise _fail("INVALID_REQUEST", "CSV parameter table must contain at least one parameter row")
    return groups, rows


def _read_registered_source(source: Any, artifact_id: str) -> bytes:
    from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact

    if not isinstance(source, Mapping) or source.get("record_schema_version") != 2:
        raise _fail("ARTIFACT_IDENTITY_MISMATCH", "parameter import requires a schema-v2 registered artifact")
    if source.get("artifact_id") != artifact_id or source.get("sha256") != artifact_id:
        raise _fail("ARTIFACT_IDENTITY_MISMATCH", "registered parameter source digest does not match artifact_id")
    if source.get("role") not in {"parameter_source", "parameter_table"}:
        raise _fail("ARTIFACT_ROLE_MISMATCH", "parameter import requires a parameter_source or parameter_table artifact")
    path = source.get("path")
    if not isinstance(path, Path):
        raise _fail("ARTIFACT_IDENTITY_MISMATCH", "registered parameter source has no verified local path")
    handle, before = _open_pinned_artifact(path)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    try:
        recorded = source.get("file_identity")
        if isinstance(recorded, Mapping):
            current = {
                "device": int(before.st_dev), "inode": int(before.st_ino),
                "size": int(before.st_size),
                "mtime_ns": int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000))),
            }
            if any(recorded.get(key) != value for key, value in current.items()):
                raise _fail("ARTIFACT_IDENTITY_MISMATCH", "registered parameter source identity changed")
        if before.st_size > MAX_FILE_BYTES:
            raise _fail("INVALID_REQUEST", "registered parameter source exceeds the 8 MiB limit")
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise _fail("INVALID_REQUEST", "registered parameter source exceeds the 8 MiB limit")
            chunks.append(chunk)
            digest.update(chunk)
        _assert_pinned_artifact_stable(path, before, os.fstat(handle.fileno()))
    finally:
        handle.close()
    if digest.hexdigest() != artifact_id or size != source.get("size"):
        raise _fail("ARTIFACT_HASH_MISMATCH", "registered parameter source bytes changed during import")
    return b"".join(chunks)


def _parameter_groups(worker: Any, model_tag: str) -> tuple[Any, list[str]]:
    container = accessor_container(worker, model_tag, {"segments": []}, "param")
    group_probe = call_probe(container, "group")
    if not group_probe["ok"] or group_probe["value"] is None:
        raise _fail("GROUP_ATTRIBUTION_UNVERIFIED", "COMSOL parameter group tags could not be verified")
    groups = tag_list(container, collection_spec("param"))
    if not groups or len(groups) > MAX_GROUPS:
        raise _fail("GROUP_ATTRIBUTION_UNVERIFIED", "COMSOL parameter group list is empty or exceeds the bound")
    return container, groups


def _duplicate_guard(rows: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["group"], row["name"])
        if key in seen:
            raise _fail("INVALID_REQUEST", "parameter table contains duplicate (group, name) rows")
        seen.add(key)


def _import_table(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = _args(arguments, ("artifact_id", "format", "group"), ("artifact_id", "format"))
    artifact_id = args.get("artifact_id")
    if not isinstance(artifact_id, str) or re.fullmatch(r"[0-9a-f]{64}", artifact_id) is None:
        raise _fail("ARTIFACT_NOT_FOUND", "artifact_id must be a lowercase registered SHA-256 digest")
    fmt = args.get("format")
    if not isinstance(fmt, str) or fmt not in {"json", "csv"}:
        raise _fail("INVALID_REQUEST", "format must be json or csv")
    context = _context(arguments)
    source = arguments.get("_registered_artifact")
    try:
        raw = _read_registered_source(source, artifact_id)
        groups, rows = _parse_json(raw) if fmt == "json" else _parse_csv(raw)
        if not rows:
            raise _fail("INVALID_REQUEST", "parameter table must contain at least one row")
        _duplicate_guard(rows)
        override = args.get("group")
        if override is not None:
            override = validate_tag(override, "group")
            if len(groups) != 1:
                raise _fail("INVALID_REQUEST", "group override is allowed only for a single-group source table")
            groups = [override]
            rows = [{**row, "group": override} for row in rows]
            _duplicate_guard(rows)

        container, actual_groups = _parameter_groups(worker, model_tag)
        missing = [group for group in groups if group not in actual_groups]
        if missing:
            raise _fail("NODE_NOT_FOUND", "one or more parameter table groups do not exist in the target model")
        # Resolve every named target before the first setter. The fixed default
        # tag is an ordinary verified group tag, not the top-level fallback.
        from ._g3_common import _call
        targets = {tag: _call(container, "group", tag) for tag in groups}
        _check_managed_guards(context, worker, model_tag)
    except ExecutionContractError as exc:
        if exc.stage == "validation":
            raise
        raise ExecutionContractError(exc.code, str(exc), stage="validation", details=exc.details) from exc
    source_registration = None
    if isinstance(source, Mapping):
        source_registration = {
            "schema_version": source.get("record_schema_version"),
            "role": source.get("role"),
            "classification": source.get("classification"),
            "sha256": source.get("sha256"),
            "size": source.get("size"),
            "relative_path": source.get("relative_path"),
        }

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        target = targets[row["group"]]
        try:
            written = expression_write(
                target, row["name"], row["expression"], row["description"],
                label="parameter import", unit_node=container,
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, ExecutionContractError) else "EXECUTION_STATE_UNKNOWN"
            failed.append({
                "group": row["group"], "name": row["name"], "stage": "expression_description_readback",
                "code": code, "message": "parameter setter or exact expression/description readback failed",
                "unit_status": "UNVERIFIED",
                "error": {"code": code, "message": "parameter setter or exact expression/description readback failed",
                          "safe_retry": False},
            })
            not_executed.extend({"group": item["group"], "name": item["name"]} for item in rows[index + 1:])
            return {
                "data_schema_version": 1, "operation": "parameter.import",
                "status": "EXECUTION_STATE_UNKNOWN", "ok": False, "partial_change": True,
                "execution_state_unknown": True, "source_artifact_id": artifact_id,
                "source_sha256": artifact_id, "source_format": fmt,
                "source_revision": context.get("source_revision"),
                "artifact_registration": source_registration,
                "source_groups": groups, "group_override": override,
                "row_count": len(rows), "applied": applied, "failed": failed,
                "not_executed": not_executed, "unit_semantics": "NO_UNIT_CLAIM_AFTER_UNVERIFIED_SETTER",
            }
        applied.append({
            "group": row["group"], "name": row["name"], "expression": row["expression"],
            "description": row["description"], "expression_readback": written.get("readback"),
            "description_readback": written.get("description_readback"),
            "unit": row["unit"], "unit_status": "PENDING_READBACK",
        })

    # Check units only after every expression was installed, so forward
    # references and inter-row dependencies are evaluated by COMSOL as a set.
    for index, row in enumerate(rows):
        probe = call_probe(container, "evaluateUnit", row["name"])
        if not probe["ok"]:
            applied[index]["unit_status"] = "UNVERIFIED"
            failed.append({
                "group": row["group"], "name": row["name"], "stage": "unit_readback",
                "code": "UNIT_READBACK_UNVERIFIED", "message": "COMSOL evaluateUnit readback failed",
                "unit_status": "UNVERIFIED",
                "error": {"code": "UNIT_READBACK_UNVERIFIED",
                          "message": "COMSOL evaluateUnit readback failed", "safe_retry": False},
            })
            continue
        actual = probe["value"]
        if actual is not None and not isinstance(actual, str):
            applied[index]["unit_status"] = "UNVERIFIED"
            failed.append({
                "group": row["group"], "name": row["name"], "stage": "unit_readback",
                "code": "UNIT_READBACK_UNVERIFIED", "message": "COMSOL evaluateUnit returned a non-string value",
                "unit_status": "UNVERIFIED",
                "error": {"code": "UNIT_READBACK_UNVERIFIED",
                          "message": "COMSOL evaluateUnit returned a non-string value", "safe_retry": False},
            })
            continue
        matches = (
            row["unit_status"] == "NO_UNIT_RETURNED" and actual is None
        ) or (
            row["unit_status"] == "VERIFIED" and isinstance(actual, str) and actual == row["unit"]
        )
        applied[index]["unit_status"] = (
            "NO_UNIT_RETURNED" if actual is None else ("VERIFIED" if matches else "MISMATCH")
        )
        if not matches:
            failed.append({
                "group": row["group"], "name": row["name"], "stage": "unit_readback",
                "code": "UNIT_MISMATCH", "message": "COMSOL unit readback did not match the declared unit",
                "declared_unit": row["unit"], "actual_unit": actual,
                "unit_status": applied[index]["unit_status"],
                "error": {"code": "UNIT_MISMATCH",
                          "message": "COMSOL unit readback did not match the declared unit",
                          "safe_retry": False},
            })

    partial = bool(failed)
    return {
        "data_schema_version": 1, "operation": "parameter.import",
        "status": "PARTIAL_FAILURE" if partial else "APPLIED",
        "ok": not partial, "partial_change": partial, "execution_state_unknown": False,
        "source_artifact_id": artifact_id, "source_sha256": artifact_id,
        "source_format": fmt, "source_revision": context.get("source_revision"),
        "artifact_registration": source_registration,
        "source_groups": groups, "group_override": override,
        "row_count": len(rows), "applied": applied, "failed": failed,
        "not_executed": not_executed,
        "unit_semantics": "exact evaluateUnit text; null is NO_UNIT_RETURNED; no conversion",
    }


def _projection(worker: Any, model_tag: str) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        data = parameter_list(worker, model_tag, {})
    except ExecutionContractError as exc:
        if exc.stage == "validation":
            raise
        raise ExecutionContractError(exc.code, str(exc), stage="validation", details=exc.details) from exc
    if data.get("group_attribution") != "model.param().group().tags()":
        raise _fail("GROUP_ATTRIBUTION_UNVERIFIED", "COMSOL parameter groups could not be completely attributed")
    group_rows = data.get("groups")
    if not isinstance(group_rows, list) or len(group_rows) > MAX_GROUPS:
        raise _fail("UNVERIFIED", "COMSOL parameter group enumeration is incomplete")
    groups: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    aggregate_bytes = 0
    for group_row in group_rows:
        if not isinstance(group_row, Mapping) or not isinstance(group_row.get("group"), str):
            raise _fail("GROUP_ATTRIBUTION_UNVERIFIED", "COMSOL returned a parameter group without a verified tag")
        group = validate_tag(group_row["group"], "group")
        groups.append(group)
        errors = group_row.get("errors")
        entries = group_row.get("parameters")
        if errors or not isinstance(entries, list):
            raise _fail("UNVERIFIED", "COMSOL parameter enumeration or getter readback is incomplete")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise _fail("UNVERIFIED", "COMSOL returned a malformed parameter row")
            name = _validate_name(entry.get("name"), "name")
            expression = entry.get("expression")
            description = entry.get("description")
            unit = entry.get("unit")
            if (not isinstance(expression, str) or not isinstance(description, str)
                    or entry.get("description_error") is not None
                    or entry.get("unit_error") is not None
                    or (unit is not None and not isinstance(unit, str))):
                raise _fail("UNVERIFIED", "COMSOL expression, description, or unit getter readback is incomplete")
            row = _row({
                "group": group, "name": name, "expression": expression,
                "description": description, "unit": unit,
                "unit_status": "NO_UNIT_RETURNED" if unit is None else "VERIFIED",
            }, len(rows))
            key = (group, name)
            if key in seen:
                raise _fail("UNVERIFIED", "COMSOL parameter enumeration contains duplicate group/name rows")
            seen.add(key)
            aggregate_bytes += sum(
                len(row[field].encode("utf-8")) for field in ("group", "name", "expression", "description")
            ) + (len(row["unit"].encode("utf-8")) if row["unit"] is not None else 4) + 128
            if aggregate_bytes > MAX_FILE_BYTES:
                raise _fail("INVALID_REQUEST", "complete parameter table exceeds the 8 MiB aggregate output limit")
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise _fail("INVALID_REQUEST", f"COMSOL parameter table exceeds {MAX_ROWS} rows")
    if not groups or len(set(groups)) != len(groups):
        raise _fail("GROUP_ATTRIBUTION_UNVERIFIED", "COMSOL parameter groups are empty or duplicated")
    return groups, sorted(rows, key=lambda row: (row["group"], row["name"]))


def _verified_projection(worker: Any, model_tag: str) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        return _projection(worker, model_tag)
    except ExecutionContractError as exc:
        if exc.stage == "validation":
            raise
        raise ExecutionContractError(exc.code, str(exc), stage="validation", details=exc.details) from exc


def _serialize(fmt: str, groups: list[str], rows: list[dict[str, Any]]) -> bytes:
    if fmt == "json":
        payload = {
            "schema": FORMAT_VERSION,
            "groups": groups,
            "rows": rows,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    else:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_ALL)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow([
                json.dumps(row["group"], ensure_ascii=False),
                row["name"], row["expression"], row["description"],
                json.dumps(row["unit"], ensure_ascii=False),
                row["unit_status"],
            ])
        raw = stream.getvalue().encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        raise _fail("INVALID_REQUEST", "complete parameter table exceeds the 8 MiB output limit")
    return raw


def _export_table(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = _args(arguments, ("format", "destination"), ("format", "destination"))
    fmt = args.get("format")
    if not isinstance(fmt, str) or fmt not in {"json", "csv"}:
        raise _fail("INVALID_REQUEST", "format must be json or csv")
    destination = args.get("destination")
    if not isinstance(destination, str) or not destination.strip():
        raise _fail("INVALID_DESTINATION", "destination must be a project-relative path")
    lexical = Path(destination)
    if lexical.is_absolute() or any(part in {"..", "."} for part in lexical.parts):
        raise _fail("ACCESS_VIOLATION", "destination must stay inside the authorized project")
    context = _context(arguments)
    from ._artifact_store import (
        ArtifactStore,
        publish_parameter_table_bytes_no_clobber,
        register_project_artifact,
    )
    root = Path(context["project_root"])
    store = ArtifactStore(root)
    try:
        output_path = store.resolve_safe_path(destination, allow_overwrite=False)
    except ExecutionContractError as exc:
        if exc.stage == "validation":
            raise
        raise ExecutionContractError(exc.code, str(exc), stage="validation", details=exc.details) from exc
    if output_path.exists():
        raise _fail("DESTINATION_EXISTS", "parameter export destination already exists")
    if not output_path.parent.is_dir():
        raise _fail("INVALID_DESTINATION", "parameter export destination directory must already exist")
    _check_managed_guards(context, worker, model_tag)
    groups, rows = _verified_projection(worker, model_tag)
    _check_managed_guards(context, worker, model_tag, after_read=True)
    verify_groups, verify_rows = _verified_projection(worker, model_tag)
    _check_managed_guards(context, worker, model_tag, after_read=True)
    if groups != verify_groups or rows != verify_rows:
        raise _fail("EXECUTION_STATE_UNKNOWN", "parameter model changed while the export snapshot was being verified",
                    stage="post_dispatch")
    raw = _serialize(fmt, groups, rows)
    digest = hashlib.sha256(raw).hexdigest()
    published = False
    registration = None
    try:
        _check_managed_guards(context, worker, model_tag, after_read=True)
        publication = publish_parameter_table_bytes_no_clobber(root, destination, raw)
        published = True
        if publication.get("sha256") != digest or publication.get("size_bytes") != len(raw):
            raise _fail("ARTIFACT_IDENTITY_MISMATCH", "published parameter table failed digest/size verification",
                        stage="post_dispatch")
        registration = register_project_artifact(
            root, context["operation_store"], destination,
            project_id=str(context["project_id"]),
            role="parameter_source", classification="project_private",
            host_identity=str(context["host_identity"]),
            engine_host_identity=str(context["engine_host_identity"]),
            request_id=str(context["request_id"]),
            registering_operation_id=str(context["operation_id"]),
            artifact_type="application/vnd.comsol-mcp.parameter-table",
            format_version=FORMAT_VERSION,
            provenance_fields={
                "source_model_ref": dict(context["model_ref"]),
                "source_revision": context.get("source_revision"),
                "producer_operation_id": context.get("operation_id"),
                "producer_job_id": context.get("job_id"),
                "parameter_table_format": fmt,
                "parameter_table_schema": FORMAT_VERSION,
            },
        )
        _check_managed_guards(context, worker, model_tag, after_read=True)
    except Exception as exc:
        if not published:
            # A failed post-commit digest/directory sync check can raise after
            # the final name exists. Claim the commit only when the path's
            # exact bytes match the payload we just prepared.
            try:
                from ._artifact_store import _hash_regular_artifact
                observed_digest, observed_identity = _hash_regular_artifact(output_path)
                published = observed_digest == digest and observed_identity["size"] == len(raw)
            except Exception:
                published = False
        if not published:
            if isinstance(exc, ExecutionContractError) and exc.stage is None:
                raise ExecutionContractError(exc.code, str(exc), stage="validation", details=exc.details) from exc
            raise
        code = exc.code if isinstance(exc, ExecutionContractError) else "ARTIFACT_REGISTRATION_UNKNOWN"
        return {
            "data_schema_version": 1, "operation": "parameter.export",
            "status": "PARTIAL_FAILURE", "ok": False, "partial_change": True,
            "execution_state_unknown": True,
            "format": fmt, "format_version": FORMAT_VERSION,
            "destination": destination, "relative_path": destination,
            "sha256": digest, "size_bytes": len(raw),
            "source_revision": context.get("source_revision"), "groups": groups,
            "row_count": len(rows),
            "artifact_id": registration.get("artifact_id") if isinstance(registration, Mapping) else None,
            "artifact_registration": ({
                "schema_version": registration.get("schema_version"),
                "role": registration.get("role"),
                "classification": registration.get("classification"),
                "sha256": registration.get("sha256"),
                "size": registration.get("size"),
                "relative_path": registration.get("path"),
            } if isinstance(registration, Mapping) else None),
            "applied": [{"destination": destination, "sha256": digest}],
            "failed": [{"stage": "artifact_registration", "code": code,
                        "message": "parameter bytes were published but registration or final identity verification failed",
                        "error": {"code": code,
                                  "message": "parameter bytes were published but registration or final identity verification failed",
                                  "safe_retry": False}}],
            "not_executed": [], "unit_semantics": "exact evaluateUnit text; null is NO_UNIT_RETURNED",
            "description_semantics": "exact COMSOL descr() text, including empty strings",
        }
    return {
        "data_schema_version": 1, "operation": "parameter.export",
        "status": "APPLIED", "ok": True, "partial_change": False,
        "execution_state_unknown": False,
        "format": fmt, "format_version": FORMAT_VERSION,
        "destination": destination, "artifact_id": registration["artifact_id"],
        "sha256": digest, "size_bytes": len(raw), "relative_path": registration["path"],
        "source_revision": context.get("source_revision"), "groups": groups,
        "row_count": len(rows), "artifact_registration": {
            "schema_version": registration["schema_version"],
            "role": registration["role"], "classification": registration["classification"],
            "sha256": registration["sha256"], "size": registration["size"],
            "relative_path": registration["path"],
        },
        "applied": [{"destination": destination, "artifact_id": registration["artifact_id"]}],
        "failed": [], "not_executed": [],
        "unit_semantics": "exact evaluateUnit text; null is NO_UNIT_RETURNED; no conversion",
        "description_semantics": "exact COMSOL descr() text, including empty strings",
    }


OPERATIONS = {
    "parameter.import": _import_table,
    "parameter.export": _export_table,
}
