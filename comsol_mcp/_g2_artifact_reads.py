"""Project-scoped, host-only reads for registered artifacts.

This module deliberately does not resolve a Worker, enqueue engine work, or
write OperationStore rows. The existing registration resolver remains the
authority for the one-file content and filesystem identity check.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError


_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_CURSOR_SCHEMA = "comsol-mcp-artifact-list-cursor/v1"
_CURSOR_FIELDS = frozenset({
    "schema", "project_id", "scope_sha256", "filter_sha256", "after_artifact_id",
})
_IDENTITY_FIELDS = frozenset({"session_id", "model_ref", "expected_revision", "idempotency_key"})
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _invalid_cursor() -> ExecutionContractError:
    return ExecutionContractError("INVALID_CURSOR", "artifact list cursor is invalid for this project or filter")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _filter_hash(filters: Mapping[str, str]) -> str:
    return hashlib.sha256(_canonical_json(dict(filters))).hexdigest()


def _scope_hash(*, project_root_identity: str, host_identity: str, engine_host_identity: str) -> str:
    # The cursor binds to host/root scope without carrying reversible path or
    # endpoint strings in its base64-encoded payload.
    return hashlib.sha256(_canonical_json({
        "project_root_identity": project_root_identity,
        "host_identity": host_identity,
        "engine_host_identity": engine_host_identity,
    })).hexdigest()


def _encode_cursor(
    *, project_id: str, project_root_identity: str, host_identity: str,
    engine_host_identity: str, filter_sha256: str, after_artifact_id: str,
) -> str:
    payload = {
        "schema": _CURSOR_SCHEMA,
        "project_id": project_id,
        "scope_sha256": _scope_hash(
            project_root_identity=project_root_identity,
            host_identity=host_identity,
            engine_host_identity=engine_host_identity,
        ),
        "filter_sha256": filter_sha256,
        "after_artifact_id": after_artifact_id,
    }
    return base64.urlsafe_b64encode(_canonical_json(payload)).decode("ascii").rstrip("=")


def _decode_cursor(
    token: Any, *, project_id: str, project_root_identity: str,
    host_identity: str, engine_host_identity: str, filter_sha256: str,
) -> str:
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise _invalid_cursor()
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise _invalid_cursor() from exc
    expected_scope = _scope_hash(
        project_root_identity=project_root_identity,
        host_identity=host_identity,
        engine_host_identity=engine_host_identity,
    )
    if (not isinstance(payload, dict) or set(payload) != _CURSOR_FIELDS
            or payload.get("schema") != _CURSOR_SCHEMA
            or payload.get("project_id") != project_id
            or payload.get("scope_sha256") != expected_scope
            or payload.get("filter_sha256") != filter_sha256
            or not isinstance(payload.get("after_artifact_id"), str)
            or not _ARTIFACT_ID_RE.fullmatch(payload["after_artifact_id"])):
        raise _invalid_cursor()
    # A cursor is only a continuation hint, never an authorization token.
    # Scope and permission are rechecked on every request and in the SQL page.
    return payload["after_artifact_id"]


def _normalize_filters(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - {"role", "classification"}:
        raise ExecutionContractError("INVALID_REQUEST", "artifact.list filter accepts only role and classification")
    filters: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(item, str) or not item.strip() or len(item) > 96:
            raise ExecutionContractError("INVALID_REQUEST", f"artifact.list filter {name} must be a nonempty string of at most 96 characters")
        filters[name] = item
    return filters


def _metadata_path_like(value: str) -> bool:
    """Reject filesystem-shaped declarations while allowing MIME/namespaced values."""
    if (value.startswith(("/", "\\", "~/", "~\\"))
            or re.search(r"(?:^|[\s=])[A-Za-z]:[\\/]", value)
            or re.search(r"(?:^|[\s=])/(?:[^/\s]+(?:/|$))", value)
            or re.search(r"(?:^|[\s=])\\\\[^\\]+\\", value)
            or value.casefold().startswith("file:")):
        return True
    return any(part in {".", ".."} for part in re.split(r"[/\\]+", value))


def _safe_metadata_text(value: Any, *, field: str, maximum: int, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if (not isinstance(value, str) or not value.strip()
            or len(value) > maximum or _CONTROL_CHARS_RE.search(value)
            or _metadata_path_like(value.strip())):
        raise ExecutionContractError("ARTIFACT_STATE_UNKNOWN", f"project artifact {field} metadata is malformed")
    return value


def _validate_read_metadata(record: Mapping[str, Any]) -> tuple[str, str, str | None, str | None]:
    role = _safe_metadata_text(record.get("role"), field="role", maximum=96, required=True)
    classification = _safe_metadata_text(record.get("classification"), field="classification", maximum=96, required=True)
    artifact_type = _safe_metadata_text(record.get("artifact_type"), field="type", maximum=128)
    format_version = _safe_metadata_text(record.get("format_version"), field="version", maximum=128)
    assert isinstance(role, str) and isinstance(classification, str)
    return role, classification, artifact_type, format_version


def _type_hint(record: Mapping[str, Any]) -> dict[str, Any]:
    declared = _safe_metadata_text(record.get("artifact_type"), field="type", maximum=128)
    if declared is not None:
        return {"value": declared, "status": "DECLARED"}
    path = record.get("path")
    if isinstance(path, str):
        extension = PurePosixPath(path).suffix.lower()
        if extension:
            return {"value": extension, "status": "DECLARED_SUFFIX_ONLY"}
    return {"value": None, "status": "NOT_RECORDED"}


def _validate_inspect_record(record: Mapping[str, Any], artifact_id: str) -> None:
    """Reject partial schema-v2 rows before the legacy-compatible resolver.

    The resolver tolerates absent identity fields for older metadata formats;
    this endpoint is specifically for immutable schema-v2 registrations, where
    missing size or pinned inode data must not be upgraded to a verified read.
    """
    path = record.get("path")
    size = record.get("size")
    file_identity = record.get("file_identity")
    valid_path = False
    if isinstance(path, str):
        portable = PurePosixPath(path)
        valid_path = (
            not portable.is_absolute()
            and portable.as_posix() == path
            and len(portable.parts) == 3
            and portable.parts[:2] == ("g2_artifacts", "registered")
            and re.fullmatch(re.escape(artifact_id) + r"(?:\.[a-z0-9]{1,16})?", portable.name) is not None
        )
    required_identity_fields = ("device", "inode", "size", "mtime_ns")
    if (type(record.get("schema_version")) is not int or record.get("schema_version") != 2
            or record.get("artifact_id") != artifact_id or record.get("sha256") != artifact_id
            or not valid_path
            or isinstance(size, bool) or not isinstance(size, int) or size < 0
            or not isinstance(file_identity, Mapping)
            or any(isinstance(file_identity.get(key), bool) or not isinstance(file_identity.get(key), int)
                   or file_identity[key] < 0 for key in required_identity_fields)
            or file_identity.get("size") != size
            or any(not isinstance(record.get(key), str) or not record[key]
                   for key in ("role", "classification"))):
        raise ExecutionContractError("ARTIFACT_STATE_UNKNOWN", "project artifact metadata identity is malformed")


def _producer_job(store: Any, record: Mapping[str, Any], *, project_id: str) -> dict[str, Any]:
    provenance = record.get("provenance")
    operation_id = provenance.get("registering_operation_id") if isinstance(provenance, Mapping) else None
    request_id = provenance.get("request_id") if isinstance(provenance, Mapping) else None
    source_path = provenance.get("source_project_relative_path") if isinstance(provenance, Mapping) else None
    if not all(isinstance(value, str) and value for value in (operation_id, request_id, source_path)):
        return {"job_id": None, "status": "NOT_RECORDED"}
    try:
        operation = store.get_operation(operation_id)
        job = store.operation_job(operation_id)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"job_id": None, "status": "UNVERIFIABLE"}
    if not isinstance(operation, Mapping) or not isinstance(job, Mapping):
        return {"job_id": None, "status": "NOT_RECORDED"}
    metadata = operation.get("metadata")
    if not isinstance(metadata, Mapping):
        return {"job_id": None, "status": "UNVERIFIABLE"}
    action = operation.get("operation")
    arguments = metadata.get("arguments")
    if action in {"registry_call", "operation_call"} and isinstance(arguments, Mapping):
        nested_arguments = arguments.get("arguments")
        if arguments.get("operation_id") != "artifact.register" or not isinstance(nested_arguments, Mapping):
            return {"job_id": None, "status": "NOT_RECORDED"}
        arguments = nested_arguments
    elif action != "artifact.register":
        return {"job_id": None, "status": "NOT_RECORDED"}
    execution = metadata.get("execution")
    if (not isinstance(arguments, Mapping) or not isinstance(execution, Mapping)
            or operation.get("request_id") != request_id
            or execution.get("project_id") != project_id
            or arguments.get("project_id") != project_id
            or arguments.get("path") != source_path
            or arguments.get("role") != record.get("role")
            or arguments.get("classification") != record.get("classification")):
        return {"job_id": None, "status": "UNVERIFIABLE"}
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return {"job_id": None, "status": "UNVERIFIABLE"}
    operation_status, job_status = operation.get("status"), job.get("status")
    status = operation_status if operation_status == job_status and isinstance(operation_status, str) else "UNKNOWN"
    return {"job_id": job_id, "status": status}


def dispatch(daemon: Any, operation: str, arguments: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    """Execute one admitted host-only artifact read after project authorization."""
    from ._artifact_store import (
        local_artifact_host_identity,
        local_engine_host_identity,
        project_root_identity as identify_project_root,
        resolve_registered_artifact,
    )
    from ._g2_registry import validate_call

    validate_call(operation, arguments)
    if _IDENTITY_FIELDS.intersection(arguments):
        names = ", ".join(sorted(_IDENTITY_FIELDS.intersection(arguments)))
        raise ExecutionContractError("INVALID_REQUEST", f"{operation} does not accept nested model identity fields: {names}")
    project_id = arguments.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise ExecutionContractError("PROJECT_IDENTITY_REQUIRED", f"{operation} requires project_id")
    if execution.get("project_id") is not None and execution.get("project_id") != project_id:
        raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", f"{operation} project_id differs from the execution envelope")
    body_request_id = arguments.get("request_id")
    outer_request_id = execution.get("request_id")
    if body_request_id is not None and outer_request_id is not None and body_request_id != outer_request_id:
        raise ExecutionContractError("INVALID_REQUEST", f"{operation} request_id differs from the execution envelope")

    # Permission is deliberately checked before any artifact metadata query.
    project = daemon.project_authority.authorize_operation(project_id, "inspect")
    project_root = Path(project["workspace"])
    root_identity = identify_project_root(project_root)
    host_identity = local_artifact_host_identity()
    configured_backend = daemon._default_backend
    engine_host_identity = local_engine_host_identity(getattr(configured_backend, "endpoint_key", None))
    request_id = outer_request_id if outer_request_id is not None else body_request_id

    if operation == "artifact.list":
        filters = _normalize_filters(arguments.get("filter"))
        filter_sha256 = _filter_hash(filters)
        raw_limit = arguments.get("limit", 100)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or not 1 <= raw_limit <= 200:
            raise ExecutionContractError("INVALID_REQUEST", "artifact.list limit must be an integer from 1 to 200")
        after_artifact_id = None
        if "cursor" in arguments:
            after_artifact_id = _decode_cursor(
                arguments["cursor"], project_id=project_id,
                project_root_identity=root_identity, host_identity=host_identity,
                engine_host_identity=engine_host_identity, filter_sha256=filter_sha256,
            )
        try:
            records, has_more = daemon.store.list_project_artifact_page(
                project_id=project_id, project_root_identity=root_identity,
                host_identity=host_identity, engine_host_identity=engine_host_identity,
                filters=filters, after_artifact_id=after_artifact_id, limit=raw_limit,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExecutionContractError("ARTIFACT_STATE_UNKNOWN", "project artifact metadata could not be read safely") from exc
        items = []
        for record in records:
            size = record.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ExecutionContractError("ARTIFACT_STATE_UNKNOWN", "project artifact metadata identity is malformed")
            role, classification, _artifact_type, version = _validate_read_metadata(record)
            items.append({
                "artifact_id": record["artifact_id"],
                "sha256": record["sha256"],
                "size_bytes": size,
                "role": role,
                "classification": classification,
                "record_schema_version": 2,
                "type_hint": _type_hint(record),
                "version_status": "DECLARED" if version is not None else "NOT_RECORDED",
                "producer_job_status": "NOT_LOOKED_UP",
                "verification_status": "NOT_CHECKED",
            })
        next_cursor = None
        if has_more and items:
            next_cursor = _encode_cursor(
                project_id=project_id, project_root_identity=root_identity,
                host_identity=host_identity, engine_host_identity=engine_host_identity,
                filter_sha256=filter_sha256, after_artifact_id=items[-1]["artifact_id"],
            )
        data = {
            "data_schema_version": 1,
            "project_id": project_id,
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "limit": raw_limit,
            "verification_scope": "METADATA_ONLY",
        }
        if request_id is not None:
            data["request_id"] = request_id
        return {"success": True, "data": data}

    artifact_id = arguments.get("artifact_id")
    if not isinstance(artifact_id, str) or not _ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise ExecutionContractError("ARTIFACT_NOT_FOUND", "artifact_id is not registered in the current managed project")
    try:
        record = daemon.store.get_project_artifact_metadata(
            artifact_id, project_id=project_id,
            project_root_identity=root_identity, host_identity=host_identity,
            engine_host_identity=engine_host_identity,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ExecutionContractError("ARTIFACT_STATE_UNKNOWN", "project artifact metadata is malformed") from exc
    if record is None:
        raise ExecutionContractError("ARTIFACT_NOT_FOUND", "artifact_id is not registered in the current managed project")
    role, classification, _artifact_type, format_version = _validate_read_metadata(record)
    _validate_inspect_record(record, artifact_id)
    try:
        resolved = resolve_registered_artifact(
            project_root, daemon.store, artifact_id, project_id=project_id,
            current_host_identity=host_identity,
            current_engine_host_identity=engine_host_identity,
            current_server_instance_id="",
        )
    except ExecutionContractError as exc:
        # Resolver details are useful internally but can contain the absolute
        # project path. Preserve its public typed failure without disclosing it.
        if exc.code == "ACCESS_VIOLATION":
            raise ExecutionContractError("ACCESS_VIOLATION", "registered artifact failed a safe access check", stage=exc.stage) from exc
        if exc.code == "ARTIFACT_NOT_FOUND":
            raise ExecutionContractError("ARTIFACT_NOT_FOUND", "registered artifact content is unavailable", stage=exc.stage) from exc
        raise
    data = {
        "data_schema_version": 1,
        "project_id": project_id,
        "artifact_id": artifact_id,
        "sha256": resolved["sha256"],
        "size_bytes": resolved["size"],
        "relative_path": resolved["relative_path"],
        "role": role,
        "classification": classification,
        "record_schema_version": 2,
        "type_hint": _type_hint(record),
        "format_version": format_version if isinstance(format_version, str) else None,
        "format_version_status": "DECLARED" if isinstance(format_version, str) and format_version else "NOT_RECORDED",
        "producer_job": _producer_job(daemon.store, record, project_id=project_id),
        "file_integrity": {
            "status": "VERIFIED_CONTENT_AND_PINNED_IDENTITY",
            "checks": ["sha256", "registered_size", "registered_file_identity", "no_symlink_components"],
        },
        "package_status": "NOT_VERIFIED",
    }
    if request_id is not None:
        data["request_id"] = request_id
    return {"success": True, "data": data}
