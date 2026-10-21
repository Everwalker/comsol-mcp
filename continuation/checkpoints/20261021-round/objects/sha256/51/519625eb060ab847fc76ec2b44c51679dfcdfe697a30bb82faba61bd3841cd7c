"""Evidence reports, evidence bundles, and authorized artifact publication.

The handlers in this module are G3 callback functions.  They accept only the
backend-installed ``_managed_context`` capability; paths, destination
registrations, and authorization verifiers never come from the request.  Local
exports use the existing ArtifactStore pin/stream/no-clobber publisher.  Host
publication is fail-closed until the backend installs both a registered
destination resolver and a scope-aware authorization provider.

The software tests exercise these handlers with a synthetic Worker and
provider.  They are not COMSOL-native or external-host publication evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import ExecutionContractError, PreWriteRefusal

REPORT_SCHEMA = "comsol-mcp-model-report"
REPORT_FORMAT = "comsol-model-report/1"
BUNDLE_SCHEMA = "comsol-mcp-evidence-bundle"
BUNDLE_FORMAT = "comsol-evidence-bundle/1"
PUBLISH_DESTINATION_SCHEMA = "comsol-mcp-publish-destination/1"
MAX_ARTIFACT_ROWS = 512
MAX_SELECTED_ARTIFACTS = 128
MAX_REPORT_JOBS = 256
MAX_EVENTS_PER_JOB = 512
MAX_BUNDLE_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}$")
_ENVELOPE_FIELDS = frozenset({
    "project_id", "session_id", "model_ref", "expected_revision",
    "idempotency_key", "request_id", "correlation_id", "trace_id",
})

_REGISTRATION_SCHEMA = {
    "type": "object", "required": ["path", "sha256", "size_bytes", "artifact_id", "role",
                                    "published", "registration_path", "registration_sha256"],
    "properties": {
        "path": {"type": "string"}, "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "size_bytes": {"type": "integer", "minimum": 1},
        "artifact_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "role": {"type": "string"}, "published": {"type": "boolean"},
        "registration_path": {"type": "string"},
        "registration_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }, "additionalProperties": False,
}
DATA_SCHEMAS = {
    "export.report": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["data_schema_version", "operation", "status", "report_status",
                     "scientific_acceptance", "destination", "sha256", "size_bytes",
                     "artifact_registration", "report", "scratch_cleanup_verified", "safe_retry"],
        "properties": {
            "data_schema_version": {"type": "integer", "const": 1},
            "operation": {"const": "export.report"}, "status": {"const": "APPLIED"},
            "report_status": {"enum": ["PASS", "UNVERIFIED"]},
            "scientific_acceptance": {"const": "UNVERIFIED"}, "destination": {"type": "string"},
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "size_bytes": {"type": "integer", "minimum": 1},
            "artifact_registration": _REGISTRATION_SCHEMA, "report": {"type": "object"},
            "scratch_cleanup_verified": {"type": "boolean"}, "safe_retry": {"const": False},
        }, "additionalProperties": False,
    },
    "export.evidence_bundle": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["data_schema_version", "operation", "status", "bundle_status",
                     "missing_required_categories", "destination", "sha256", "size_bytes",
                     "artifact_registration", "manifest", "portability_status",
                     "scratch_cleanup_verified", "safe_retry"],
        "properties": {
            "data_schema_version": {"type": "integer", "const": 1},
            "operation": {"const": "export.evidence_bundle"}, "status": {"const": "APPLIED"},
            "bundle_status": {"enum": ["COMPLETE", "INCOMPLETE"]},
            "missing_required_categories": {"type": "array", "items": {"type": "string"}},
            "destination": {"type": "string"},
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "size_bytes": {"type": "integer", "minimum": 1},
            "artifact_registration": _REGISTRATION_SCHEMA, "manifest": {"type": "object"},
            "portability_status": {"const": "UNVERIFIED"},
            "scratch_cleanup_verified": {"type": "boolean"}, "safe_retry": {"const": False},
        }, "additionalProperties": False,
    },
    "artifact.publish": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["data_schema_version", "operation", "status", "project_id", "destination_ref",
                     "destination_registration_sha256", "published", "count",
                     "authorization_scope_sha256", "safe_retry"],
        "properties": {
            "data_schema_version": {"type": "integer", "const": 1},
            "operation": {"const": "artifact.publish"}, "status": {"const": "PUBLISHED"},
            "project_id": {"type": "string", "minLength": 1},
            "destination_ref": {"type": "string", "minLength": 1},
            "destination_registration_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "published": {"type": "array", "items": {"type": "object"}},
            "count": {"type": "integer", "minimum": 1},
            "authorization_scope_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "safe_retry": {"const": False},
        }, "additionalProperties": False,
    },
}


def _body_context(arguments: Mapping[str, Any], allowed: set[str]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise PreWriteRefusal("INVALID_REQUEST", "export arguments must be an object")
    context = arguments.get("_managed_context")
    if not isinstance(context, Mapping):
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "export requires backend-created managed context")
    body = {key: value for key, value in arguments.items()
            if key != "_managed_context" and key not in _ENVELOPE_FIELDS}
    unknown = set(body) - allowed
    if unknown:
        raise PreWriteRefusal("INVALID_REQUEST", f"unsupported export fields: {sorted(unknown)}")
    return body, context


def _require_context(context: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if context.get(key) is None]
    if missing:
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "managed context lacks " + ", ".join(missing))


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _project_context(context: Mapping[str, Any]):
    from ._artifact_store import ArtifactStore, _assert_no_symlink_components

    _require_context(context, "project_root", "project_id", "operation_store", "backend")
    raw_root = Path(str(context["project_root"])).expanduser()
    if not raw_root.is_absolute() or raw_root.is_symlink():
        raise PreWriteRefusal("RUNTIME_CONFIGURATION_REQUIRED", "managed project root must be absolute and non-symlink")
    _assert_no_symlink_components(raw_root, field="export project root")
    root = raw_root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise PreWriteRefusal("RUNTIME_CONFIGURATION_REQUIRED", "managed project root is not a regular directory")
    _assert_no_symlink_components(root, field="export project root")
    return root, ArtifactStore(root)


def _bound_model_state(context: Mapping[str, Any], model_tag: str):
    from ._g3_managed_models import _model_state

    service, ref, state = _model_state(context, model_tag)
    binding_fn = getattr(context["backend"], "model_project_binding", None)
    if not callable(binding_fn):
        raise PreWriteRefusal("PROJECT_IDENTITY_MISMATCH", "backend cannot verify the model's durable project binding")
    try:
        binding = binding_fn(ref.as_dict())
    except Exception as exc:
        raise PreWriteRefusal("PROJECT_IDENTITY_MISMATCH", "model project binding could not be read",
                              details={"cause_type": type(exc).__name__}) from exc
    if binding != {"attribution": "PROJECT_BOUND", "project_id": context["project_id"]}:
        raise PreWriteRefusal("PROJECT_IDENTITY_MISMATCH", "model is not bound to the admitted project")
    return service, ref, state


def _strict_definition(value: Any, allowed: set[str], *, required: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PreWriteRefusal("INVALID_REQUEST", "definition must be an object")
    definition = dict(value)
    unknown = set(definition) - allowed
    if unknown:
        raise PreWriteRefusal("INVALID_REQUEST", f"unsupported definition fields: {sorted(unknown)}")
    missing = required - set(definition)
    if missing:
        raise PreWriteRefusal("INVALID_REQUEST", f"definition is missing fields: {sorted(missing)}")
    try:
        raw = _json_bytes(definition)
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PreWriteRefusal("INVALID_REQUEST", "definition must be finite JSON data") from exc
    if len(raw) > 64 * 1024:
        raise PreWriteRefusal("INVALID_REQUEST", "definition exceeds the 64 KiB bound")
    return definition


def _destination(context: Mapping[str, Any], value: Any, suffix: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise PreWriteRefusal("INVALID_REQUEST", "destination must be a nonempty project-relative path")
    pure = PurePosixPath(value)
    if (pure.is_absolute() or pure.as_posix() != value or "\\" in value
            or any(part in {"", ".", ".."} for part in pure.parts)):
        raise PreWriteRefusal("ACCESS_VIOLATION", "destination must use canonical project-relative POSIX syntax")
    if Path(value).suffix.casefold() != suffix:
        raise PreWriteRefusal("INVALID_REQUEST", f"destination suffix must be {suffix}")
    root, store = _project_context(context)
    try:
        target = store.resolve_safe_path(value, allow_overwrite=False)
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=exc.details) from exc
    from ._artifact_store import _assert_no_symlink_components
    _assert_no_symlink_components(target.parent, field="export destination parent")
    if not target.parent.is_dir():
        raise PreWriteRefusal("INVALID_DESTINATION", "export destination parent must already exist")
    return target.relative_to(root).as_posix()


def _write_private(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _hash_file(path: Path) -> tuple[str, int, dict[str, int]]:
    from ._artifact_store import _hash_regular_artifact
    digest, identity = _hash_regular_artifact(path)
    return digest, int(identity["size"]), identity


def _publish_and_register(context: Mapping[str, Any], source: Path, destination: str, *,
                          role: str, artifact_type: str, format_version: str,
                          provenance: Mapping[str, Any]) -> dict[str, Any]:
    from ._artifact_store import publish_project_file_no_clobber, register_project_artifact
    _require_context(context, "host_identity", "engine_host_identity", "operation_id", "request_id")
    root, _store = _project_context(context)
    digest, size, _identity = _hash_file(source)
    if size <= 0:
        raise ExecutionContractError("ARTIFACT_EMPTY", "generated export is empty", stage="post_dispatch")
    published = publish_project_file_no_clobber(root, destination, source,
                                                expected_sha256=digest, expected_size=size)
    registration = register_project_artifact(
        root, context["operation_store"], destination,
        project_id=str(context["project_id"]), role=role, classification="project_local",
        host_identity=str(context["host_identity"]),
        engine_host_identity=str(context["engine_host_identity"]),
        request_id=str(context["request_id"]), registering_operation_id=str(context["operation_id"]),
        artifact_type=artifact_type, format_version=format_version,
        provenance_fields=dict(provenance),
    )
    return {"path": destination, "sha256": digest, "size_bytes": size,
            "artifact_id": registration["artifact_id"], "role": role,
            "published": bool(published), "registration_path": registration["path"],
            "registration_sha256": registration["sha256"]}


def _cleanup(scratch: Path) -> bool:
    try:
        shutil.rmtree(scratch)
        return not scratch.exists()
    except OSError:
        return False


def _failure_receipt(root: Path, scratch: Path, operation: str, destination: str,
                     stage: str, error: BaseException, *, artifacts: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    from ._artifact_store import _assert_no_symlink_components

    relative = scratch.relative_to(root).as_posix()
    receipt = {
        "schema": "comsol-mcp-export-failure-receipt/1", "operation": operation,
        "destination": destination, "stage": stage, "cause_type": type(error).__name__,
        "cause_code": getattr(error, "code", None), "safe_retry": False,
        "artifacts": [dict(row) for row in artifacts],
    }
    path = scratch / "failure_receipt.json"
    try:
        _assert_no_symlink_components(scratch, field="export recovery directory")
        _write_private(path, _json_bytes(receipt))
        state = "WRITTEN"
    except Exception as receipt_error:
        state = "WRITE_FAILED:" + type(receipt_error).__name__
    return {"recovery_artifact": {"directory": relative,
                                  "receipt": f"{relative}/failure_receipt.json",
                                  "receipt_status": state},
            "partial_change": True, "execution_state_unknown": True, "safe_retry": False}


def _safe_job_projection(context: Mapping[str, Any], model_ref: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project only durable operation/job/event fields safe for local evidence."""
    store = context["operation_store"]
    project_id = str(context["project_id"])
    try:
        page = store.list_jobs(offset=0, limit=MAX_REPORT_JOBS + 1, project_id=project_id)
        jobs = list(page)
        total = int(getattr(page, "total", len(jobs)))
    except Exception as exc:
        return [], {"status": "UNVERIFIED", "reason": "durable_job_read_failed:" + type(exc).__name__,
                    "complete": False}
    if len(jobs) > MAX_REPORT_JOBS:
        jobs = jobs[:MAX_REPORT_JOBS]
    output: list[dict[str, Any]] = []
    events_total = 0
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        operation = job.get("operation")
        result = job.get("result")
        metadata = job.get("metadata")
        execution = (result.get("execution") if isinstance(result, Mapping) else None)
        outer = metadata.get("execution") if isinstance(metadata, Mapping) else None
        candidate_refs = [
            metadata.get("model_ref") if isinstance(metadata, Mapping) else None,
            outer.get("model_ref") if isinstance(outer, Mapping) else None,
            operation.get("metadata", {}).get("arguments", {}).get("model_ref")
            if isinstance(operation, Mapping) and isinstance(operation.get("metadata"), Mapping)
            and isinstance(operation.get("metadata", {}).get("arguments"), Mapping) else None,
            execution.get("model_ref") if isinstance(execution, Mapping) else None,
        ]
        if not any(item == dict(model_ref) for item in candidate_refs):
            continue
        op_name = operation.get("operation") if isinstance(operation, Mapping) else job.get("operation_id")
        status = str(job.get("status") or "UNKNOWN")
        success = isinstance(result, Mapping) and result.get("success") is True and status == "SUCCEEDED"
        event_rows: list[dict[str, Any]] = []
        truncated = False
        try:
            raw_events = store.events(str(job.get("job_id")), 0, MAX_EVENTS_PER_JOB + 1)
            if len(raw_events) > MAX_EVENTS_PER_JOB:
                raw_events = raw_events[:MAX_EVENTS_PER_JOB]
                truncated = True
            for event in raw_events:
                metadata_raw = event.get("metadata") if isinstance(event, Mapping) else None
                try:
                    raw_digest = hashlib.sha256(_json_bytes(metadata_raw)).hexdigest()
                except Exception:
                    raw_digest = None
                event_rows.append({"event": str(event.get("event", "UNKNOWN")),
                                   "created_at": event.get("created_at"),
                                   "metadata_sha256": raw_digest,
                                   "metadata_projection": _event_projection(metadata_raw)})
        except Exception:
            truncated = True
        events_total += len(event_rows)
        output.append({
            "job_id": job.get("job_id"), "operation_id": job.get("operation_id"),
            "operation": op_name, "status": status,
            "operation_request_hash": operation.get("request_hash") if isinstance(operation, Mapping) else None,
            "result_success": result.get("success") if isinstance(result, Mapping) else None,
            "result_code": (result.get("error", {}).get("code") if isinstance(result, Mapping)
                            and isinstance(result.get("error"), Mapping) else None),
            "result_data_status": result.get("data", {}).get("status") if isinstance(result, Mapping)
            and isinstance(result.get("data"), Mapping) else None,
            "events": event_rows, "events_truncated": truncated,
            "provenance_status": "PASS" if success else "UNKNOWN",
            "scope": "durable_operation_store_status_only_not_scientific_acceptance",
        })
    coverage = {"status": "PASS" if total <= MAX_REPORT_JOBS else "UNVERIFIED",
                "complete": total <= MAX_REPORT_JOBS,
                "project_job_count": total, "model_bound_job_count": len(output),
                "event_count": events_total,
                "reason": None if total <= MAX_REPORT_JOBS else "project_job_page_limit_reached"}
    return output, coverage


def _event_projection(value: Any, *, _depth: int = 0) -> Any:
    """Remove secrets and paths from event metadata while retaining a digest."""
    safe_keys = frozenset({
        "operation", "operation_id", "request_id", "idempotency_key_sha256", "model_ref",
        "schema_version", "session_id", "server_instance_id", "model_tag", "generation",
        "revision", "status", "state", "code", "stage", "event", "job_id",
        "worker_instance_id", "worker_epoch", "runtime_id", "server_instance_id",
        "operation_request_hash", "result_code", "result_status", "cause_type",
        "safe_retry", "partial_change", "execution_state_unknown", "source_sha256",
        "artifact_id", "sha256", "size_bytes", "count", "completed_count",
        "success", "accepted", "at", "created_at", "attempt", "step_index",
    })
    if _depth > 6:
        return "[DEPTH_LIMIT]"
    if isinstance(value, Mapping):
        result = {}
        for key, item in sorted(value.items(), key=lambda row: str(row[0])):
            name = str(key)
            folded = name.casefold()
            if name in safe_keys:
                result[name] = _event_projection(item, _depth=_depth + 1)
            elif any(word in folded for word in ("password", "secret", "token", "authorization", "source_code", "java_code")):
                result[name] = "[REDACTED]"
            elif folded.endswith("_path") or folded in {"path", "filepath", "file_path", "absolute_path", "resolved_path"}:
                result[name] = "[PATH_REDACTED]"
        return result
    if isinstance(value, (list, tuple)):
        return [_event_projection(item, _depth=_depth + 1) for item in value[:128]]
    if isinstance(value, str):
        if len(value) > 512:
            return value[:512] + "…[TRUNCATED]"
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            return "[PATH_REDACTED]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[UNSUPPORTED_VALUE]"


def _registered_rows(context: Mapping[str, Any], *, max_rows: int = MAX_ARTIFACT_ROWS) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from ._artifact_store import local_artifact_host_identity, local_engine_host_identity
    from ._g3_managed_models import _resolve_registered

    store = context["operation_store"]
    try:
        raw_rows = store.list_metadata("artifacts")
    except Exception as exc:
        return [], {"status": "UNVERIFIED", "complete": False,
                    "reason": "artifact_metadata_unavailable:" + type(exc).__name__}
    if not isinstance(raw_rows, list):
        return [], {"status": "UNVERIFIED", "complete": False, "reason": "artifact_metadata_malformed"}
    project_rows = [row for row in raw_rows if isinstance(row, Mapping)
                    and row.get("schema_version") == 2
                    and row.get("project_id") == context["project_id"]]
    total = len(project_rows)
    if total > max_rows:
        project_rows = project_rows[:max_rows]
    resolved_rows: list[dict[str, Any]] = []
    for row in project_rows:
        artifact_id = row.get("artifact_id")
        if not isinstance(artifact_id, str) or not _SHA256.fullmatch(artifact_id):
            resolved_rows.append({"artifact_id": artifact_id, "status": "UNVERIFIED",
                                  "reason": "artifact_id_malformed"})
            continue
        try:
            resolved = _resolve_registered(context, artifact_id, max_size=MAX_ARTIFACT_BYTES)
            current = store.get_metadata("artifacts", artifact_id)
            if (not isinstance(current, Mapping) or current.get("schema_version") != 2
                    or current.get("project_id") != context["project_id"]
                    or current.get("classification") != "project_local"
                    or current.get("sha256") != artifact_id
                    or current.get("role") != row.get("role")):
                raise PreWriteRefusal("ARTIFACT_IDENTITY_MISMATCH", "artifact row changed during report resolution")
            resolved_rows.append({"artifact_id": artifact_id, "sha256": resolved["sha256"],
                                  "size_bytes": resolved["size"], "role": current.get("role"),
                                  "artifact_type": current.get("artifact_type"),
                                  "format_version": current.get("format_version"),
                                  "status": "PASS", "scope": "project_local_registered_bytes_hash_verified"})
        except Exception as exc:
            resolved_rows.append({"artifact_id": artifact_id, "sha256": row.get("sha256"),
                                  "size_bytes": row.get("size"), "role": row.get("role"),
                                  "status": "UNVERIFIED", "reason": getattr(exc, "code", type(exc).__name__)})
    coverage = {"status": "PASS" if total <= max_rows else "UNVERIFIED",
                "complete": total <= max_rows, "project_registered_artifact_count": total,
                "verified_count": sum(row.get("status") == "PASS" for row in resolved_rows),
                "reason": None if total <= max_rows else "artifact_inventory_limit_reached"}
    return resolved_rows, coverage


def _observation_rows(context: Mapping[str, Any], model_ref: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from ._artifact_store import ArtifactStore, _assert_no_symlink_components, _hash_regular_artifact
    root, artifact_store = _project_context(context)
    try:
        rows = context["operation_store"].list_metadata("artifacts")
    except Exception as exc:
        return [], {"status": "UNVERIFIED", "complete": False,
                    "reason": "observation_metadata_unavailable:" + type(exc).__name__}
    observations = [row for row in rows if isinstance(row, Mapping) and row.get("kind") == "w17_observation"
                    and row.get("project_id") == context["project_id"]]
    output = []
    complete = len(observations) <= MAX_ARTIFACT_ROWS
    for record in observations[:MAX_ARTIFACT_ROWS]:
        identity_status = record.get("model_ref") == dict(model_ref)
        artifact = record.get("artifact")
        status, reason = "UNVERIFIED", None
        size = None
        try:
            if not isinstance(artifact, Mapping):
                raise PreWriteRefusal("INTEGRITY_COMPROMISED", "observation file descriptor is missing")
            relative = artifact.get("file_path")
            expected_sha = record.get("sha256")
            expected_size = artifact.get("byte_size")
            if not isinstance(relative, str) or not _SHA256.fullmatch(str(expected_sha)):
                raise PreWriteRefusal("INTEGRITY_COMPROMISED", "observation descriptor is malformed")
            if isinstance(expected_size, bool) or not isinstance(expected_size, int) or not 0 <= expected_size <= MAX_ARTIFACT_BYTES:
                raise PreWriteRefusal("ARTIFACT_SIZE_LIMIT", "observation exceeds the local evidence limit")
            path = artifact_store.resolve_safe_path(relative, allow_overwrite=True)
            _assert_no_symlink_components(path, field="observation artifact")
            digest, info = _hash_regular_artifact(path)
            size = info["size"]
            if digest != expected_sha or size != expected_size or artifact.get("sha256") != expected_sha:
                raise PreWriteRefusal("INTEGRITY_COMPROMISED", "observation bytes differ from their durable digest")
            producer = context["operation_store"].get_operation(str(record.get("producer")))
            if (not isinstance(producer, Mapping) or producer.get("status") != "SUCCEEDED"
                    or producer.get("project_id") != context["project_id"]):
                raise PreWriteRefusal("UNVERIFIED_OBSERVATION", "observation producer is not a successful project operation")
            status = "PASS" if identity_status else "UNVERIFIED"
            if not identity_status:
                reason = "observation_belongs_to_historical_or_other_model_ref"
        except Exception as exc:
            reason = getattr(exc, "code", type(exc).__name__)
        output.append({"observation_id": record.get("observation_id"), "sha256": record.get("sha256"),
                       "size_bytes": size, "producer_operation_id": record.get("producer"),
                       "model_ref": record.get("model_ref"), "revision": record.get("revision"),
                       "status": status, "reason": reason,
                       "acceptance_scope": "registered_observation_bytes_and_producer_only_not_numerical_or_physical_acceptance"})
    coverage = {"status": "PASS" if complete else "UNVERIFIED", "complete": complete,
                "project_observation_count": len(observations),
                "current_model_count": sum(row.get("status") == "PASS" for row in output),
                "reason": None if complete else "observation_inventory_limit_reached"}
    return output, coverage


def _current_model_evidence(worker: Any, model_tag: str, context: Mapping[str, Any], definition: Mapping[str, Any]) -> dict[str, Any]:
    from ._g3_managed_models import tree as model_tree

    _service, ref, state = _bound_model_state(context, model_tag)
    raw_snapshot = None
    snapshot_error = None
    try:
        snapshot_fn = getattr(worker, "backend_snapshot", None) or getattr(worker, "model_snapshot", None)
        if not callable(snapshot_fn):
            raise RuntimeError("worker_model_snapshot_unavailable")
        raw_snapshot = snapshot_fn(model_tag)
        if isinstance(raw_snapshot, Mapping):
            raw_snapshot = dict(raw_snapshot)
        else:
            raise RuntimeError("worker_model_snapshot_malformed")
        if raw_snapshot.get("model_tag", raw_snapshot.get("tag")) != model_tag:
            raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "Worker snapshot returned another model tag")
    except Exception as exc:
        snapshot_error = getattr(exc, "code", type(exc).__name__)
    include_tree = definition.get("include_tree", True)
    if type(include_tree) is not bool:
        raise PreWriteRefusal("INVALID_REQUEST", "definition.include_tree must be boolean")
    tree_result = None
    tree_error = None
    if include_tree:
        try:
            tree_result = model_tree(worker, model_tag, {
                "_managed_context": context,
                "path": {"segments": []},
                "depth": int(definition.get("tree_depth", 12)),
                "limit": int(definition.get("tree_limit", 500)),
            })
            if not isinstance(tree_result, Mapping):
                raise RuntimeError("model_tree_returned_malformed_value")
            tree_result = dict(tree_result)
        except Exception as exc:
            tree_error = getattr(exc, "code", type(exc).__name__)
    ref_map = ref.as_dict()
    artifacts, artifact_coverage = _registered_rows(context)
    observations, observation_coverage = _observation_rows(context, ref_map)
    jobs, job_coverage = _safe_job_projection(context, ref_map)
    checks = {
        "managed_model_ref": {"status": "PASS", "model_ref": ref_map,
                              "revision": state.revision, "dirty": state.dirty},
        "worker_snapshot": {"status": "PASS" if raw_snapshot is not None else "UNVERIFIED",
                            "fingerprint": raw_snapshot.get("fingerprint") if raw_snapshot else None,
                            "fingerprint_scope": raw_snapshot.get("fingerprint_scope") if raw_snapshot else None,
                            "external_event_counter": raw_snapshot.get("external_event_counter") if raw_snapshot else None,
                            "worker_instance_id": raw_snapshot.get("worker_instance_id") if raw_snapshot else None,
                            "server_instance_id": raw_snapshot.get("server_instance_id") if raw_snapshot else None,
                            "reason": snapshot_error},
        "model_tree": {"status": ("PASS" if isinstance(tree_result, Mapping) and tree_result.get("complete") is True
                                  and not tree_result.get("errors") else "UNVERIFIED"),
                       "complete": tree_result.get("complete") if tree_result else False,
                       "truncated": tree_result.get("truncated") if tree_result else None,
                       "count": tree_result.get("count") if tree_result else None,
                       "errors": tree_result.get("errors") if tree_result else [tree_error],
                       "scope": "actual paginated model tree; no result fields inferred"},
        "registered_artifacts": artifact_coverage,
        "registered_observations": observation_coverage,
        "durable_execution_history": job_coverage,
    }
    overall = "UNVERIFIED" if any(row.get("status") != "PASS" for row in checks.values()) else "PASS"
    return {"model": {"model_ref": ref_map, "revision": state.revision,
                       "fingerprint": raw_snapshot.get("fingerprint") if raw_snapshot else None,
                       "comsol_version": _runtime_version(worker),
                       "runtime_identity": _runtime_identity(worker, context)},
            "model_tree": tree_result,
            "registered_artifacts": artifacts,
            "registered_observations": observations,
            "execution_history": jobs,
            "checks": checks,
            "status": overall,
            "acceptance_scope": "source and integrity evidence only; no numerical, physical, license, or native acceptance is implied"}


def _runtime_version(worker: Any) -> str | None:
    try:
        getter = getattr(worker, "getComsolVersion", None)
        value = getter() if callable(getter) else None
        return str(value)[:256] if value else None
    except Exception:
        return None


def _runtime_identity(worker: Any, context: Mapping[str, Any]) -> dict[str, Any]:
    try:
        meta = worker.runtime_metadata()
        if isinstance(meta, Mapping):
            return {key: meta.get(key) for key in ("server", "instance_id", "generation", "connected")}
    except Exception:
        pass
    identity = context.get("runtime_identity")
    return dict(identity) if isinstance(identity, Mapping) else {"status": "UNVERIFIED"}


def _report_document(worker: Any, model_tag: str, context: Mapping[str, Any], definition: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"title", "include_tree", "tree_depth", "tree_limit", "artifact_ids"}
    unknown = set(definition) - allowed
    if unknown:
        raise PreWriteRefusal("INVALID_REQUEST", f"unsupported report definition fields: {sorted(unknown)}")
    title = definition.get("title", "COMSOL model evidence report")
    if not isinstance(title, str) or not title.strip() or len(title) > 256:
        raise PreWriteRefusal("INVALID_REQUEST", "definition.title must be a nonempty string of at most 256 characters")
    depth = definition.get("tree_depth", 12)
    limit = definition.get("tree_limit", 500)
    if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= 32:
        raise PreWriteRefusal("INVALID_REQUEST", "definition.tree_depth must be an integer from 0 through 32")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise PreWriteRefusal("INVALID_REQUEST", "definition.tree_limit must be an integer from 1 through 500")
    artifact_ids = definition.get("artifact_ids", [])
    if (not isinstance(artifact_ids, list) or len(artifact_ids) > MAX_SELECTED_ARTIFACTS
            or any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)):
        raise PreWriteRefusal("INVALID_REQUEST", "definition.artifact_ids must be up to 128 unique lowercase SHA-256 identifiers")
    evidence = _current_model_evidence(worker, model_tag, context,
                                       {**definition, "tree_depth": depth, "tree_limit": limit})
    if artifact_ids:
        selected = set(artifact_ids)
        actual = {row.get("artifact_id") for row in evidence["registered_artifacts"]}
        missing = sorted(selected - actual)
        if missing:
            raise PreWriteRefusal("ARTIFACT_NOT_FOUND", "one or more report artifact_ids are not registered in this project",
                                  details={"missing_artifact_ids": missing})
        evidence["registered_artifacts"] = [row for row in evidence["registered_artifacts"]
                                             if row.get("artifact_id") in selected]
    now = context.get("created_at")
    document = {
        "schema": REPORT_SCHEMA, "version": 1, "format_version": REPORT_FORMAT,
        "title": title, "generated_by": {"operation_id": context["operation_id"],
                                          "request_id": context["request_id"],
                                          "project_id": context["project_id"],
                                          "source_revision": context["source_revision"],
                                          "generated_at": now},
        **evidence,
        "status": evidence["status"],
        "scientific_acceptance": "UNVERIFIED",
    }
    return document


def export_report(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, {"definition", "destination"})
    root, _store = _project_context(context)
    definition = _strict_definition(body.get("definition"),
                                    {"title", "include_tree", "tree_depth", "tree_limit", "artifact_ids"})
    destination = _destination(context, body.get("destination"), ".json")
    scratch = Path(tempfile.mkdtemp(prefix="model-report-", dir=root))
    report_path = scratch / "report.json"
    stage = "collect_evidence"
    publish_attempted = False
    try:
        report = _report_document(worker, model_tag, context, definition)
        report_bytes = _json_bytes(report) + b"\n"
        stage = "write_report"
        _write_private(report_path, report_bytes)
        digest, size, _identity = _hash_file(report_path)
        stage = "publish_report"
        publish_attempted = True
        receipt = _publish_and_register(
            context, report_path, destination, role="model_report", artifact_type="application/json",
            format_version=REPORT_FORMAT,
            provenance={"source_model_ref": context["model_ref"],
                        "source_revision": context["source_revision"],
                        "report_sha256": digest, "report_status": report["status"]},
        )
        cleaned = _cleanup(scratch)
        return {"data_schema_version": 1, "operation": "export.report", "status": "APPLIED",
                "report_status": report["status"], "scientific_acceptance": "UNVERIFIED",
                "destination": destination, "sha256": digest, "size_bytes": size,
                "artifact_registration": receipt, "report": report,
                "scratch_cleanup_verified": cleaned, "safe_retry": False}
    except BaseException as exc:
        if publish_attempted or stage != "collect_evidence":
            recovery = _failure_receipt(root, scratch, "export.report", destination, stage, exc)
            if isinstance(exc, ExecutionContractError):
                exc.details.update(recovery)
                exc.details["safe_retry"] = False
                exc.safe_retry = False
                raise
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "report export failed after staging began; inspect recovery receipt",
                                         stage="post_dispatch", details={**recovery, "cause_type": type(exc).__name__}) from exc
        _cleanup(scratch)
        raise


def _resolve_v2_artifact(context: Mapping[str, Any], artifact_id: str) -> dict[str, Any]:
    from ._artifact_store import resolve_registered_artifact
    root, _store = _project_context(context)
    _require_context(context, "host_identity", "engine_host_identity")
    record = context["operation_store"].get_metadata("artifacts", artifact_id)
    if (not isinstance(record, Mapping) or record.get("schema_version") != 2
            or record.get("project_id") != context["project_id"]
            or record.get("classification") != "project_local"
            or record.get("artifact_id") != artifact_id or record.get("sha256") != artifact_id
            or not isinstance(record.get("role"), str)):
        raise PreWriteRefusal("ARTIFACT_SCOPE_MISMATCH", "bundle source must be an exact schema-v2 project-local registered artifact")
    try:
        resolved = resolve_registered_artifact(
            root, context["operation_store"], artifact_id,
            project_id=str(context["project_id"]),
            current_host_identity=str(context["host_identity"]),
            current_engine_host_identity=str(context["engine_host_identity"]),
            # The resolver accepts a server id for legacy rows. This helper
            # has already required schema-v2 and rejects every legacy record.
            current_server_instance_id="project-scope-schema-v2",
            max_size_bytes=MAX_ARTIFACT_BYTES,
        )
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=exc.details) from exc
    return {"artifact_id": artifact_id, "path": Path(resolved["path"]),
            "project_relative_path": str(resolved["relative_path"]), "sha256": resolved["sha256"],
            "size_bytes": int(resolved["size"]), "role": record["role"],
            "artifact_type": record.get("artifact_type"), "format_version": record.get("format_version"),
            "provenance": record.get("provenance", {})}


def _resolve_observation(context: Mapping[str, Any], record: Mapping[str, Any], model_ref: Mapping[str, Any]) -> dict[str, Any]:
    from ._artifact_store import ArtifactStore, _assert_no_symlink_components, _hash_regular_artifact
    root, store = _project_context(context)
    if (record.get("kind") != "w17_observation" or record.get("project_id") != context["project_id"]
            or record.get("model_ref") != dict(model_ref)):
        raise PreWriteRefusal("ARTIFACT_SCOPE_MISMATCH", "only current ModelRef project observations enter this evidence bundle")
    descriptor = record.get("artifact")
    if not isinstance(descriptor, Mapping):
        raise PreWriteRefusal("INTEGRITY_COMPROMISED", "observation artifact descriptor is missing")
    relative = descriptor.get("file_path")
    expected_sha = record.get("sha256")
    expected_size = descriptor.get("byte_size")
    if not isinstance(relative, str) or not isinstance(expected_sha, str) or not _SHA256.fullmatch(expected_sha):
        raise PreWriteRefusal("INTEGRITY_COMPROMISED", "observation descriptor is malformed")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or not 0 <= expected_size <= MAX_ARTIFACT_BYTES:
        raise PreWriteRefusal("ARTIFACT_SIZE_LIMIT", "observation file exceeds the bundle size limit")
    path = store.resolve_safe_path(relative, allow_overwrite=True)
    _assert_no_symlink_components(path, field="bundle observation source")
    digest, identity = _hash_regular_artifact(path)
    if digest != expected_sha or identity["size"] != expected_size or descriptor.get("sha256") != expected_sha:
        raise PreWriteRefusal("INTEGRITY_COMPROMISED", "observation bytes differ from their durable hash")
    producer = context["operation_store"].get_operation(str(record.get("producer")))
    if (not isinstance(producer, Mapping) or producer.get("status") != "SUCCEEDED"
            or producer.get("project_id") != context["project_id"]):
        raise PreWriteRefusal("UNVERIFIED_OBSERVATION", "observation producer is not a successful project operation")
    return {"artifact_id": str(record["observation_id"]), "path": path,
            "project_relative_path": str(relative), "sha256": digest,
            "size_bytes": identity["size"], "role": "rawdata_observation",
            "artifact_type": "application/json", "format_version": "comsol-w17-observation/1",
            "provenance": {"producer_operation_id": record.get("producer"),
                           "source_model_ref": dict(model_ref), "source_revision": record.get("revision")}}


def _artifact_category(row: Mapping[str, Any]) -> str:
    role = str(row.get("role") or "").casefold()
    media = str(row.get("artifact_type") or "").casefold()
    path = Path(str(row.get("project_relative_path") or ""))
    if role == "code_recipe" or role.endswith("_recipe") or role == "recipe":
        return "recipe"
    if role == "rawdata_observation" or any(value in role for value in ("raw", "data", "observation", "sample")):
        return "rawdata"
    if "plot" in role or "image/png" in media or path.suffix.casefold() in {".png", ".svg", ".jpg", ".jpeg"}:
        return "plots"
    if "log" in role:
        return "logs"
    return "other"


def _zip_pinned_file(archive: zipfile.ZipFile, member_name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact
    path = Path(row["path"])
    with archive.open(member_name, "w", force_zip64=True) as output:
        source, before = _open_pinned_artifact(path)
        copied = 0
        digest = hashlib.sha256()
        try:
            if int(before.st_size) != int(row["size_bytes"]):
                raise ExecutionContractError("ARTIFACT_CHANGED", "source size changed before bundle streaming",
                                             stage="post_dispatch")
            for block in iter(lambda: source.read(1024 * 1024), b""):
                copied += len(block)
                digest.update(block)
                output.write(block)
            _assert_pinned_artifact_stable(path, before, os.fstat(source.fileno()))
        finally:
            source.close()
    if copied != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
        raise ExecutionContractError("ARTIFACT_HASH_MISMATCH", "source bytes changed during evidence bundle streaming",
                                     stage="post_dispatch")
    return {"path": member_name, "sha256": digest.hexdigest(), "size_bytes": copied}


def _bundle_jobs(context: Mapping[str, Any], model_ref: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    jobs, coverage = _safe_job_projection(context, model_ref)
    rows = []
    for job in jobs:
        for event in job.get("events", []):
            rows.append({"job_id": job.get("job_id"), "operation_id": job.get("operation_id"),
                         "operation": job.get("operation"), "job_status": job.get("status"),
                         **event})
    complete = coverage.get("complete") is True and bool(rows)
    return jobs, rows, complete


def export_evidence_bundle(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, {"definition", "destination"})
    root, store = _project_context(context)
    definition = _strict_definition(body.get("definition"), {"artifact_ids", "include_solution", "max_total_bytes"})
    include_solution = definition.get("include_solution", True)
    if type(include_solution) is not bool:
        raise PreWriteRefusal("INVALID_REQUEST", "definition.include_solution must be boolean")
    total_limit = definition.get("max_total_bytes", MAX_BUNDLE_TOTAL_BYTES)
    if isinstance(total_limit, bool) or not isinstance(total_limit, int) or not 1024 <= total_limit <= MAX_BUNDLE_TOTAL_BYTES:
        raise PreWriteRefusal("INVALID_REQUEST", "definition.max_total_bytes must be an integer from 1024 through 1 GiB")
    artifact_ids = definition.get("artifact_ids", [])
    if (not isinstance(artifact_ids, list) or len(artifact_ids) > MAX_SELECTED_ARTIFACTS
            or any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)):
        raise PreWriteRefusal("INVALID_REQUEST", "definition.artifact_ids must be up to 128 unique lowercase SHA-256 identifiers")
    destination = _destination(context, body.get("destination"), ".zip")
    _service, ref, state = _bound_model_state(context, model_tag)
    selected = [_resolve_v2_artifact(context, artifact_id) for artifact_id in artifact_ids]
    rows_by_id = {row["artifact_id"]: row for row in selected}
    observations: list[dict[str, Any]] = []
    for record in context["operation_store"].list_metadata("artifacts"):
        if isinstance(record, Mapping) and record.get("kind") == "w17_observation" and record.get("project_id") == context["project_id"]:
            if record.get("model_ref") == ref.as_dict():
                observations.append(_resolve_observation(context, record, ref.as_dict()))
    # Add all same-model observations as raw data; their immutable IDs prevent
    # duplicate members and no caller-supplied path is ever consulted.
    for row in observations:
        rows_by_id.setdefault(row["artifact_id"], row)
    selected_rows = list(rows_by_id.values())
    if any(row["size_bytes"] > MAX_ARTIFACT_BYTES for row in selected_rows):
        raise PreWriteRefusal("ARTIFACT_SIZE_LIMIT", "one evidence artifact exceeds 512 MiB")
    byte_total = sum(int(row["size_bytes"]) for row in selected_rows)
    if byte_total > total_limit:
        raise PreWriteRefusal("ARTIFACT_SIZE_LIMIT", "selected evidence exceeds max_total_bytes")
    _require_context(context, "operation_id", "request_id")
    scratch = Path(tempfile.mkdtemp(prefix="evidence-bundle-", dir=root))
    mph_path = scratch / "model.mph"
    zip_path = scratch / "bundle.zip"
    model = worker.client().model(model_tag).java
    before_live_path = str(model._call("getFilePath") or "")
    source_snapshot_before = None
    save_dispatched = False
    publication_attempted = False
    stage = "preflight"
    keep_scratch = False
    try:
        try:
            snapshot_fn = getattr(worker, "backend_snapshot", None) or getattr(worker, "model_snapshot", None)
            source_snapshot_before = snapshot_fn(model_tag) if callable(snapshot_fn) else None
        except Exception:
            source_snapshot_before = None
        stage = "save_bound_model_copy"
        save_dispatched = True
        save_receipt = model._call("saveModelPackage", str(mph_path), include_solution)
        if not mph_path.is_file() or mph_path.stat().st_size <= 0:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL saveCopy produced no nonempty MPH",
                                         stage="post_dispatch", details={"safe_retry": False, "save_receipt": save_receipt})
        mph_sha, mph_size, _mph_identity = _hash_file(mph_path)
        after_live_path = str(model._call("getFilePath") or "")
        if before_live_path != after_live_path:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "saveCopy changed the bound model live filepath",
                                         stage="post_dispatch", details={"before_live_filepath": bool(before_live_path),
                                                                          "after_live_filepath": bool(after_live_path),
                                                                          "safe_retry": False})
        source_snapshot_after = None
        try:
            source_snapshot_after = snapshot_fn(model_tag) if callable(snapshot_fn) else None
        except Exception:
            source_snapshot_after = None
        if (isinstance(source_snapshot_before, Mapping) and isinstance(source_snapshot_after, Mapping)
                and source_snapshot_before.get("fingerprint") != source_snapshot_after.get("fingerprint")):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "saveCopy changed the bound model fingerprint",
                                         stage="post_dispatch", details={"safe_retry": False})
        model_row = {"path": mph_path, "sha256": mph_sha, "size_bytes": mph_size,
                     "artifact_id": None, "role": "model_mph_copy", "artifact_type": "application/vnd.comsol.mph",
                     "project_relative_path": None, "provenance": {"source_model_ref": ref.as_dict(),
                                                                     "source_revision": state.revision,
                                                                     "include_solution": include_solution}}
        all_rows = [model_row, *selected_rows]
        member_rows = []
        categories: dict[str, list[dict[str, Any]]] = {key: [] for key in ("mph", "recipe", "rawdata", "plots", "other")}
        stage = "write_bundle_archive"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                             allowZip64=True) as archive:
            for row in all_rows:
                category = "mph" if row["role"] == "model_mph_copy" else _artifact_category(row)
                suffix = Path(str(row.get("project_relative_path") or "model.mph")).suffix.lower()
                if not suffix or not re.fullmatch(r"\.[a-z0-9]{1,16}", suffix):
                    suffix = ".bin"
                name = f"evidence/{category}/{row['artifact_id'] or 'bound-model'}{suffix}"
                member = _zip_pinned_file(archive, name, row)
                descriptor = {"artifact_id": row.get("artifact_id"), "role": row["role"],
                              "artifact_type": row.get("artifact_type"), "format_version": row.get("format_version"),
                              **member, "source_scope": "bound_model_savecopy" if category == "mph" else "registered_project_artifact"}
                member_rows.append(descriptor)
                categories.setdefault(category, []).append(descriptor)
            jobs, log_rows, logs_present = _bundle_jobs(context, ref.as_dict())
            logs_bytes = _json_bytes({"schema": "comsol-mcp-export-log-projection/1", "jobs": jobs,
                                      "events": log_rows, "redaction": "secrets and absolute paths omitted; event metadata hashes retained"}) + b"\n"
            archive.writestr("logs/operations.json", logs_bytes)
            log_member = {"path": "logs/operations.json", "sha256": hashlib.sha256(logs_bytes).hexdigest(),
                          "size_bytes": len(logs_bytes)}
            member_rows.append({"role": "durable_operation_log_projection", **log_member})
            categories["logs"] = [log_member] if logs_present else []
            report = _report_document(worker, model_tag, context,
                                      {"title": "Evidence bundle source report", "include_tree": True,
                                       "tree_depth": 12, "tree_limit": 500})
            report_bytes = _json_bytes(report) + b"\n"
            archive.writestr("report.json", report_bytes)
            report_member = {"path": "report.json", "sha256": hashlib.sha256(report_bytes).hexdigest(),
                             "size_bytes": len(report_bytes)}
            member_rows.append({"role": "source_report", **report_member})
            categories["other"].append(report_member)
            required = {"mph": bool(categories["mph"]), "recipe": bool(categories["recipe"]),
                        "rawdata": bool(categories["rawdata"]), "plots": bool(categories["plots"]),
                        "logs": logs_present, "provenance": True}
            missing = sorted(key for key, present in required.items() if not present)
            bundle_status = "COMPLETE" if not missing else "INCOMPLETE"
            provenance = {
                "schema": "comsol-mcp-evidence-provenance/1", "project_id": context["project_id"],
                "model_ref": ref.as_dict(), "revision": state.revision,
                "source_fingerprint": (source_snapshot_before.get("fingerprint")
                                        if isinstance(source_snapshot_before, Mapping) else None),
                "operation_id": context["operation_id"], "request_id": context["request_id"],
                "runtime_identity": _runtime_identity(worker, context), "comsol_version": _runtime_version(worker),
                "selected_registered_artifact_ids": list(artifact_ids),
                "included_observation_ids": [row["artifact_id"] for row in observations],
                "save_copy": {"include_solution": include_solution, "live_filepath_unchanged": True,
                              "fingerprint_unchanged": (source_snapshot_before.get("fingerprint") == source_snapshot_after.get("fingerprint")
                                                         if isinstance(source_snapshot_before, Mapping) and isinstance(source_snapshot_after, Mapping)
                                                         else None)},
                "bundle_status": bundle_status,
                "missing_required_categories": missing,
                "acceptance_scope": "byte/hash provenance; no native, numerical, or physical acceptance implied",
            }
            provenance_bytes = _json_bytes(provenance) + b"\n"
            archive.writestr("provenance.json", provenance_bytes)
            provenance_member = {"path": "provenance.json", "sha256": hashlib.sha256(provenance_bytes).hexdigest(),
                                 "size_bytes": len(provenance_bytes)}
            member_rows.append({"role": "provenance", **provenance_member})
            manifest = {"schema": BUNDLE_SCHEMA, "version": 1, "format_version": BUNDLE_FORMAT,
                        "bundle_status": bundle_status, "model": {"model_ref": ref.as_dict(),
                                                                     "source_revision": state.revision,
                                                                     "include_solution": include_solution,
                                                                     "mph_sha256": mph_sha, "mph_size_bytes": mph_size},
                        "members": member_rows, "required_categories": required,
                        "missing_required_categories": missing,
                        "portability_status": "UNVERIFIED",
                        "external_code_and_runtime_libraries": "UNVERIFIED",
                        "absolute_paths_in_manifest": False}
            archive.writestr("manifest.json", _json_bytes(manifest) + b"\n")
        package_sha, package_size, _package_identity = _hash_file(zip_path)
        stage = "publish_bundle"
        publication_attempted = True
        receipt = _publish_and_register(
            context, zip_path, destination, role="evidence_bundle", artifact_type="application/zip",
            format_version=BUNDLE_FORMAT,
            provenance={"source_model_ref": ref.as_dict(), "source_revision": state.revision,
                        "include_solution": include_solution, "bundle_status": bundle_status,
                        "missing_required_categories": missing},
        )
        cleaned = _cleanup(scratch)
        return {"data_schema_version": 1, "operation": "export.evidence_bundle", "status": "APPLIED",
                "bundle_status": bundle_status, "missing_required_categories": missing,
                "destination": destination, "sha256": package_sha, "size_bytes": package_size,
                "artifact_registration": receipt, "manifest": manifest,
                "portability_status": "UNVERIFIED", "scratch_cleanup_verified": cleaned,
                "safe_retry": False}
    except BaseException as exc:
        if save_dispatched:
            keep_scratch = True
            recovery = _failure_receipt(root, scratch, "export.evidence_bundle", destination, stage, exc,
                                        artifacts=[{"model_mph": str(mph_path.relative_to(root)),
                                                    "exists": mph_path.is_file()}])
            if isinstance(exc, ExecutionContractError):
                exc.details.update(recovery)
                exc.details["safe_retry"] = False
                exc.safe_retry = False
                raise
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "evidence bundle failed after saveCopy; inspect retained recovery files",
                                         stage="post_dispatch", details={**recovery, "cause_type": type(exc).__name__}) from exc
        raise
    finally:
        if not keep_scratch:
            _cleanup(scratch)


def _validate_publish_provider(context: Mapping[str, Any], project_id: str, destination_ref: str):
    resolver = context.get("host_publish_destination_resolver")
    verifier = context.get("host_authorization_scope_verifier")
    if not callable(resolver) or not callable(verifier):
        raise PreWriteRefusal("PUBLISH_UNAVAILABLE", "no trusted registered destination and scoped host authorization providers are installed")
    try:
        destination = resolver(project_id, destination_ref)
    except Exception as exc:
        raise PreWriteRefusal("DESTINATION_STATE_UNKNOWN", "registered publish destination could not be resolved",
                              details={"cause_type": type(exc).__name__}) from exc
    if (not isinstance(destination, Mapping) or destination.get("schema") != PUBLISH_DESTINATION_SCHEMA
            or destination.get("project_id") != project_id or destination.get("destination_ref") != destination_ref
            or not isinstance(destination.get("registration_sha256"), str)
            or not _SHA256.fullmatch(destination["registration_sha256"])
            or not callable(destination.get("publish_chunks"))):
        raise PreWriteRefusal("DESTINATION_IDENTITY_MISMATCH", "destination resolver did not return an exact registered host destination")
    return destination, verifier


def artifact_publish(_worker: Any, _model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, {"artifact_ids", "destination_ref", "authorization_ref"})
    _require_context(context, "project_id", "operation_store", "project_root", "backend")
    project_id = context["project_id"]
    artifact_ids = body.get("artifact_ids")
    destination_ref = body.get("destination_ref")
    authorization_ref = body.get("authorization_ref")
    if (not isinstance(artifact_ids, list) or not 1 <= len(artifact_ids) <= MAX_SELECTED_ARTIFACTS
            or any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)):
        raise PreWriteRefusal("INVALID_REQUEST", "artifact_ids must contain 1..128 unique lowercase SHA-256 identifiers")
    if (not isinstance(destination_ref, str) or not destination_ref.strip() or len(destination_ref) > 256
            or any(ord(ch) < 32 for ch in destination_ref)):
        raise PreWriteRefusal("INVALID_REQUEST", "destination_ref must be a registered destination identifier")
    if (not isinstance(authorization_ref, str) or not authorization_ref.strip() or len(authorization_ref) > 512
            or any(ord(ch) < 32 for ch in authorization_ref)):
        raise PreWriteRefusal("AUTHORIZATION_REQUIRED", "artifact.publish requires a bounded authorization_ref")
    permission_check = context.get("host_permission_check")
    if not callable(permission_check):
        raise PreWriteRefusal("PERMISSION_STATE_UNKNOWN", "backend did not install the host-control permission checker")
    try:
        if permission_check(project_id, "host_control") is not True:
            raise PreWriteRefusal("PERMISSION_DENIED", "artifact.publish requires effective host_control permission")
    except PreWriteRefusal:
        raise
    except Exception as exc:
        raise PreWriteRefusal("PERMISSION_STATE_UNKNOWN", "host-control permission could not be verified",
                              details={"cause_type": type(exc).__name__}) from exc
    destination, verifier = _validate_publish_provider(context, project_id, destination_ref)
    root, _store = _project_context(context)
    artifacts = [_resolve_v2_artifact(context, artifact_id) for artifact_id in artifact_ids]
    scope = {"project_id": project_id, "action": "artifact.publish",
             "destination_ref": destination_ref,
             "destination_registration_sha256": destination["registration_sha256"],
             "artifacts": [{"artifact_id": row["artifact_id"], "sha256": row["sha256"],
                            "size_bytes": row["size_bytes"]} for row in artifacts]}
    try:
        accepted = verifier(project_id, "artifact.publish", scope, authorization_ref)
    except Exception as exc:
        raise PreWriteRefusal("AUTHORIZATION_STATE_UNKNOWN", "publish authorization provider failed",
                              details={"cause_type": type(exc).__name__}) from exc
    if accepted is not True:
        raise PreWriteRefusal("AUTHORIZATION_REQUIRED", "authorization does not match this exact project/destination/artifact scope")
    provider_publish = destination["publish_chunks"]
    receipts: list[dict[str, Any]] = []
    for row in artifacts:
        from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact
        path = row["path"]
        source, before = _open_pinned_artifact(path)
        sent = 0
        digest = hashlib.sha256()

        def chunks():
            nonlocal sent
            try:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    sent += len(block)
                    digest.update(block)
                    yield block
                _assert_pinned_artifact_stable(path, before, os.fstat(source.fileno()))
            finally:
                source.close()

        try:
            target_name = f"{row['artifact_id']}{Path(row['project_relative_path']).suffix.lower()}"
            provider_receipt = provider_publish(
                destination, target_name, chunks(), expected_sha256=row["sha256"],
                expected_size=row["size_bytes"], artifact_id=row["artifact_id"],
            )
            if (sent != row["size_bytes"] or digest.hexdigest() != row["sha256"]
                    or not isinstance(provider_receipt, Mapping)
                    or provider_receipt.get("status") != "PUBLISHED"
                    or provider_receipt.get("destination_ref") != destination_ref
                    or provider_receipt.get("artifact_id") != row["artifact_id"]
                    or provider_receipt.get("sha256") != row["sha256"]
                    or provider_receipt.get("size_bytes") != row["size_bytes"]
                    or provider_receipt.get("no_clobber") is not True
                    or provider_receipt.get("readback_verified") is not True):
                raise ExecutionContractError("PUBLISH_READBACK_MISMATCH", "host provider did not prove exact no-clobber hash/size readback",
                                             stage="post_dispatch", details={"safe_retry": False})
            public_receipt = {key: provider_receipt.get(key) for key in (
                "status", "destination_ref", "artifact_id", "sha256", "size_bytes",
                "no_clobber", "readback_verified", "receipt_id",
            ) if key in provider_receipt}
            receipts.append({"artifact_id": row["artifact_id"], "sha256": row["sha256"],
                             "size_bytes": row["size_bytes"], "status": "PUBLISHED",
                             "provider_receipt": public_receipt})
        except BaseException as exc:
            source.close()
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "host publication stopped after dispatch; inspect exact provider receipts",
                                         stage="post_dispatch", details={"destination_ref": destination_ref,
                                                                          "completed_count": len(receipts),
                                                                          "completed": receipts,
                                                                          "failed_artifact_id": row["artifact_id"],
                                                                          "cause_type": type(exc).__name__,
                                                                          "cause_code": getattr(exc, "code", None),
                                                                          "partial_change": bool(receipts) or sent > 0,
                                                                          "safe_retry": False}) from exc
    return {"data_schema_version": 1, "operation": "artifact.publish", "status": "PUBLISHED",
            "project_id": project_id, "destination_ref": destination_ref,
            "destination_registration_sha256": destination["registration_sha256"],
            "published": receipts, "count": len(receipts),
            "authorization_scope_sha256": hashlib.sha256(_json_bytes(scope)).hexdigest(),
            "safe_retry": False}


DISPATCH = {
    "export.report": export_report,
    "export.evidence_bundle": export_evidence_bundle,
    "artifact.publish": artifact_publish,
}

# G3 aggregation consumes the same operation-to-handler table used by the
# local direct test harness above.
OPERATIONS = DISPATCH
