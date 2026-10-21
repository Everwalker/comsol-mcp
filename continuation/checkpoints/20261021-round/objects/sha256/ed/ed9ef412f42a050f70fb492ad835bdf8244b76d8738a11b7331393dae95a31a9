"""Managed model lifecycle and trusted Java recipe operations.

The module is deliberately an adapter over the existing Worker, revision
ledger, ArtifactStore and G3 dispatch table.  It does not create a second
queue or accept request-supplied managed context.  Session-level operations
(``model.list/create/load``) are callable by the control-plane route while
model-level operations follow the ordinary G3 callback contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import ctypes
import errno
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import re
import stat
import sys
import tempfile
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, PreWriteRefusal

MODEL_SAVE_FORMAT = "application/vnd.comsol.mph"
MODEL_SAVE_ROLE = "saved_model"
MODEL_SAVED_BASELINE_SCHEMA = "comsol-mcp.model-saved-baseline/1"
MODEL_RECIPE_SCHEMA = "comsol-mcp-code-recipe"
MODEL_RECIPE_FORMAT = "comsol-mcp-code-recipe/1"
MODEL_RECIPE_MAX_BYTES = 256 * 1024
MODEL_TREE_MAX_DEPTH = 32
MODEL_TREE_MAX_LIMIT = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ROLES = frozenset({"java-source", "java_source", "trusted-java-source", "trusted_java_source", "code-source", "code_source"})


def _arguments(arguments: Mapping[str, Any], allowed: set[str]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise PreWriteRefusal("INVALID_REQUEST", "managed model arguments must be an object")
    context = arguments.get("_managed_context")
    if not isinstance(context, Mapping):
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "managed context must be created by the backend")
    body = {key: value for key, value in arguments.items() if key not in {
        "_managed_context", "project_id", "session_id", "model_ref", "expected_revision",
        "idempotency_key", "request_id",
    }}
    unknown = set(body) - allowed
    if unknown:
        raise PreWriteRefusal("INVALID_REQUEST", f"unsupported fields: {sorted(unknown)}")
    return body, context


def _require_context(context: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if context.get(key) is None]
    if missing:
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "managed context lacks " + ", ".join(missing))


def _model_state(context: Mapping[str, Any], model_tag: str) -> tuple[Any, Any, Any]:
    _require_context(context, "execution_service", "backend", "model_ref", "source_revision", "project_id")
    service = context["execution_service"]
    ref = context.get("_model_ref_object")
    if ref is None:
        from ._execution_contract import model_ref_from_mapping
        ref = model_ref_from_mapping(dict(context["model_ref"]))
    if ref.model_tag != model_tag or ref.as_dict() != dict(context["model_ref"]):
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "handler tag does not match the exact managed ModelRef")
    state = service.ledger._state_for(ref)
    if state.revision != context["source_revision"]:
        raise PreWriteRefusal("REVISION_CONFLICT", "managed context revision is stale")
    if state.dirty:
        raise PreWriteRefusal("REVISION_CONFLICT", "model is dirty")
    return service, ref, state


def _resolve_registered(context: Mapping[str, Any], artifact_id: Any, *, max_size: int | None = None) -> dict[str, Any]:
    from ._artifact_store import local_artifact_host_identity, local_engine_host_identity, resolve_registered_artifact
    _require_context(context, "project_root", "project_id", "operation_store", "backend")
    raw_ref = context.get("model_ref")
    ref_server_id = raw_ref.get("server_instance_id") if isinstance(raw_ref, Mapping) else None
    service = context.get("execution_service")
    server_id = ref_server_id or getattr(getattr(service, "ledger", None), "server_instance_id", None)
    if not isinstance(server_id, str) or not server_id:
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "registered artifact resolution lacks the attached server identity")
    try:
        return resolve_registered_artifact(
            context["project_root"], context["operation_store"], artifact_id,
            project_id=str(context["project_id"]),
            current_host_identity=local_artifact_host_identity(),
            current_engine_host_identity=local_engine_host_identity(context["backend"].endpoint_key),
            current_server_instance_id=server_id,
            max_size_bytes=max_size,
        )
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc


def _register_saved_model(context: Mapping[str, Any], relative_path: str, *, sha256: str, size: int) -> dict[str, Any]:
    from ._artifact_store import register_project_artifact
    existing = context["operation_store"].get_metadata("artifacts", sha256)
    if existing is not None:
        expected = {
            "schema_version": 2, "artifact_id": sha256, "sha256": sha256,
            "size": size, "project_id": str(context["project_id"]),
            "host_identity": str(context["host_identity"]),
            "engine_host_identity": str(context["engine_host_identity"]),
            "role": MODEL_SAVE_ROLE, "classification": "project_local",
            "artifact_type": MODEL_SAVE_FORMAT, "format_version": "comsol-mph-savecopy/1",
        }
        if not isinstance(existing, Mapping) or any(existing.get(key) != value for key, value in expected.items()):
            raise PreWriteRefusal("ARTIFACT_REGISTRATION_CONFLICT", "saved MPH bytes conflict with an existing project artifact identity")
        resolved = _resolve_registered(context, sha256)
        if resolved.get("sha256") != sha256 or resolved.get("size") != size:
            raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "existing saved MPH registration failed exact content readback")
        return dict(existing)
    return register_project_artifact(
        context["project_root"], context["operation_store"], relative_path,
        project_id=str(context["project_id"]), role=MODEL_SAVE_ROLE, classification="project_local",
        host_identity=str(context["host_identity"]), engine_host_identity=str(context["engine_host_identity"]),
        request_id=str(context["request_id"]), registering_operation_id=str(context["operation_id"]),
        artifact_type=MODEL_SAVE_FORMAT, format_version="comsol-mph-savecopy/1",
        provenance_fields={"source_model_ref": context["model_ref"],
                           "source_revision": context["source_revision"], "size_bytes": size},
    )


def _saved_baseline_key(model_ref: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(model_ref), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "managed-model-saved-baseline:" + hashlib.sha256(encoded).hexdigest()


def _record_saved_baseline(
    context: Mapping[str, Any], *, model_ref: Mapping[str, Any], state: str,
    saved_revision: int, operation: str, artifact_id: str | None = None,
    sha256: str | None = None, size_bytes: int | None = None,
    source_model_revision: int | None = None,
) -> dict[str, Any]:
    """Persist the saved-state boundary against this exact live ModelRef.

    This row lives in OperationStore's existing revisions table and is keyed
    by the full ModelRef digest. It records saved provenance only; it never
    revives a Worker binding after reconnect or restart.
    """
    _require_context(context, "operation_store", "project_id", "operation_id")
    if state not in {"SAVED", "UNSAVED"} or type(saved_revision) is not int or saved_revision < 0:
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline state or revision is invalid")
    if state == "SAVED":
        if (not isinstance(artifact_id, str) or _SHA256.fullmatch(artifact_id) is None
                or sha256 != artifact_id or type(size_bytes) is not int or size_bytes <= 0
                or type(source_model_revision) is not int or source_model_revision < 0):
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline artifact identity is incomplete")
    elif any(value is not None for value in (artifact_id, sha256, size_bytes, source_model_revision)):
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "unsaved baseline cannot name an artifact or source revision")
    row = {
        "schema": MODEL_SAVED_BASELINE_SCHEMA,
        "state": state,
        "model_ref": dict(model_ref),
        "project_id": str(context["project_id"]),
        "revision": saved_revision,
        "saved_revision": saved_revision,
        "source_model_revision": source_model_revision,
        "artifact_id": artifact_id,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "operation": operation,
        "operation_id": str(context["operation_id"]),
    }
    try:
        context["operation_store"].put_metadata("revisions", _saved_baseline_key(model_ref), row)
    except Exception as exc:
        raise ExecutionContractError("SAVED_STATE_UNKNOWN", "saved model baseline could not be persisted",
                                     stage="post_dispatch", details={"cause_type": type(exc).__name__,
                                                                       "safe_retry": False}) from exc
    return row


def _saved_baseline(context: Mapping[str, Any], model_ref: Mapping[str, Any], current_revision: int) -> dict[str, Any]:
    """Validate durable saved-state provenance and resolve its registered MPH."""
    store = context.get("operation_store")
    if store is None:
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "close cannot inspect durable saved-state provenance")
    try:
        record = store.get_metadata("revisions", _saved_baseline_key(model_ref))
    except Exception as exc:
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "close cannot read durable saved-state provenance",
                              details={"cause_type": type(exc).__name__}) from exc
    required = {"schema", "state", "model_ref", "project_id", "revision", "saved_revision",
                "source_model_revision", "artifact_id", "sha256", "size_bytes", "operation", "operation_id"}
    if (not isinstance(record, Mapping) or set(record) != required
            or record.get("schema") != MODEL_SAVED_BASELINE_SCHEMA
            or record.get("model_ref") != dict(model_ref)
            or record.get("project_id") != context.get("project_id")
            or type(record.get("revision")) is not int
            or type(record.get("saved_revision")) is not int
            or record.get("revision") != record.get("saved_revision")
            or record.get("saved_revision") < 0
            or record.get("state") not in {"SAVED", "UNSAVED"}
            or not isinstance(record.get("operation_id"), str) or not record.get("operation_id")):
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "exact ModelRef has no valid durable saved-state baseline")
    artifact = None
    if record["state"] == "UNSAVED":
        if (record.get("operation") != "model.create" or record.get("saved_revision") != 0
                or any(record.get(name) is not None for name in ("artifact_id", "sha256", "size_bytes", "source_model_revision"))):
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "unsaved baseline record has an invalid source")
    else:
        if (record.get("operation") not in {"model.load", "model.save", "model.clone"}
                or not isinstance(record.get("artifact_id"), str)
                or _SHA256.fullmatch(record["artifact_id"]) is None
                or record.get("sha256") != record.get("artifact_id")
                or type(record.get("size_bytes")) is not int or record["size_bytes"] <= 0
                or type(record.get("source_model_revision")) is not int
                or record["source_model_revision"] < 0):
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline record has an invalid artifact identity")
        artifact_record = store.get_metadata("artifacts", record["artifact_id"])
        if not isinstance(artifact_record, Mapping):
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline artifact registration is missing")
        try:
            resolved = _resolve_registered(context, record["artifact_id"])
        except ExecutionContractError as exc:
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline artifact no longer resolves",
                                  details={"cause_code": exc.code}) from exc
        if (resolved.get("sha256") != record["sha256"] or resolved.get("size") != record["size_bytes"]
                or artifact_record.get("sha256") != record["sha256"]):
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline artifact hash or size changed")
        artifact = {"artifact_id": record["artifact_id"], "sha256": record["sha256"],
                    "size_bytes": record["size_bytes"]}
    try:
        operation = store.get_operation(record["operation_id"])
    except Exception as exc:
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline operation cannot be read",
                              details={"cause_type": type(exc).__name__}) from exc
    if (not isinstance(operation, Mapping) or operation.get("operation_id") != record["operation_id"]
            or operation.get("operation") != record["operation"] or operation.get("status") != "SUCCEEDED"):
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "saved baseline lacks a durable successful producer operation")
    result = operation.get("result")
    execution = result.get("execution") if isinstance(result, Mapping) else None
    data = result.get("data") if isinstance(result, Mapping) else None
    if (not isinstance(result, Mapping) or result.get("success") is not True
            or not isinstance(execution, Mapping)
            or execution.get("operation_id") != record["operation_id"]
            or not isinstance(data, Mapping) or data.get("model_ref") != dict(model_ref)):
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "baseline producer result does not bind the exact ModelRef")
    if record["state"] == "SAVED":
        if (data.get("artifact_id") != record["artifact_id"]
                or data.get("sha256") != record["sha256"]
                or data.get("size_bytes") != record["size_bytes"]):
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "baseline producer result does not bind its saved artifact")
        if record["operation"] == "model.save" and data.get("source_revision") != record.get("source_model_revision"):
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "save producer result differs from its source revision")
        if record["operation"] == "model.clone" and data.get("source_model_ref") is None:
            raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "clone producer result does not bind its source ModelRef")
    return {"state": record["state"], "saved_revision": record["saved_revision"],
            "source_model_revision": record["source_model_revision"], "artifact": artifact,
            "operation_id": record["operation_id"], "operation": record["operation"],
            "revision_matches": record["saved_revision"] == current_revision}


def _owned_tags(context: Mapping[str, Any]) -> set[str] | None:
    runtime = context.get("session_context")
    tags = getattr(runtime, "owned_model_tags", None)
    return tags if isinstance(tags, set) else None


def _bind_created_model(worker: Any, tag: str, context: Mapping[str, Any], *, operation: str) -> dict[str, Any]:
    """Bind a newly created tag and persist its project identity; never adopt."""
    service, backend = context.get("execution_service"), context.get("backend")
    if service is None or backend is None:
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "model creation requires the active service and backend")
    tags = [str(value) for value in worker.client().tags()]
    if tag not in tags:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"{operation} returned a tag that is absent from ModelUtil.tags()",
                                     stage="post_dispatch", details={"tag": tag, "safe_retry": False})
    owned = _owned_tags(context)
    if owned is not None:
        owned.add(tag)
    try:
        metadata = service.bind_model(tag, ownership="mcp_owned")
        ref = metadata.get("execution", {}).get("model_ref")
        if not isinstance(ref, Mapping):
            raise RuntimeError("bind_model returned no ModelRef")
        backend._bind_model_project(ref, context["project_id"])
        backend.persist()
        return dict(ref)
    except Exception as exc:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "new model exists but its ModelRef/project binding did not verify",
                                     stage="post_dispatch", details={"tag": tag, "cause_type": type(exc).__name__,
                                                                      "safe_retry": False}) from exc


def list_models(worker: Any, _model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, set())
    _require_context(context, "execution_service", "backend", "project_id")
    service, backend = context["execution_service"], context["backend"]
    try:
        tags = worker.client().tags()
    except Exception as exc:
        raise ExecutionContractError("ENGINE_UNRESPONSIVE", "ModelUtil.tags() could not be read", stage="pre_dispatch",
                                     details={"cause_type": type(exc).__name__}) from exc
    if not isinstance(tags, (list, tuple)):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "ModelUtil.tags() returned an invalid shape", stage="post_dispatch")
    rows = []
    for raw_tag in tags:
        tag = str(raw_tag)
        state = service.ledger._models.get(tag)
        ref = None
        ownership = "foreign"
        project_binding = {"attribution": "UNATTRIBUTED", "project_id": None}
        if state is not None and not state.retired:
            ref = state.ref.as_dict()
            ownership = service.ledger.model_ownership.get(tag, "user_owned")
            project_binding = backend.model_project_binding(ref)
        rows.append({"model_tag": tag, "managed": ref is not None, "ownership": ownership,
                     "model_ref": ref, "project_binding": project_binding})
    rows.sort(key=lambda row: row["model_tag"])
    return {"status": "SUCCEEDED", "models": rows, "count": len(rows), "adopted": False}


def create_model(worker: Any, _model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"label", "dimension"})
    _require_context(context, "project_id", "execution_service", "backend", "operation_id")
    label = body.get("label")
    dimension = body.get("dimension")
    if not isinstance(label, str) or not label.strip() or len(label) > 256:
        raise PreWriteRefusal("INVALID_REQUEST", "label must be a nonempty string of at most 256 characters")
    if dimension is not None and (isinstance(dimension, bool) or not isinstance(dimension, int) or dimension not in {1, 2, 3}):
        raise PreWriteRefusal("INVALID_REQUEST", "dimension must be 1, 2, or 3")
    before = {str(tag) for tag in worker.client().tags()}
    try:
        model = worker.client().create(label.strip())
        tag = str(model.java.tag())
        if dimension is not None:
            java = model.java
            java.component().create("comp1", True)
            java.component("comp1").geom().create("geom1", dimension)
        after = {str(item) for item in worker.client().tags()}
    except Exception as exc:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "model.create was dispatched; preserve the Worker and inspect tags",
                                     stage="post_dispatch", details={"cause_type": type(exc).__name__,
                                                                      "safe_retry": False}) from exc
    if tag in before or tag not in after or len(after - before) != 1:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "model.create did not establish exactly one new tag",
                                     stage="post_dispatch", details={"new_tags": sorted(after - before), "safe_retry": False})
    ref = _bind_created_model(worker, tag, context, operation="model.create")
    _record_saved_baseline(context, model_ref=ref, state="UNSAVED", saved_revision=0,
                           operation="model.create")
    return {"status": "CREATED", "model_tag": tag, "model_ref": ref, "label": label.strip(),
            "dimension": dimension, "ownership": "mcp_owned", "project_id": context["project_id"]}


def load_model(worker: Any, _model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"artifact_id", "path_policy"})
    _require_context(context, "project_root", "project_id", "operation_store", "backend", "execution_service",
                     "host_identity", "engine_host_identity", "operation_id", "request_id")
    if body.get("path_policy") not in (None, {}, {"mode": "registered_artifact"}):
        raise PreWriteRefusal("INVALID_REQUEST", "model.load path_policy only permits a registered artifact")
    resolved = _resolve_registered(context, body.get("artifact_id"))
    record = context["operation_store"].get_metadata("artifacts", resolved["artifact_id"])
    _validate_loadable_mph(record, resolved, context)
    before = {str(tag) for tag in worker.client().tags()}
    try:
        model = worker.client().load(str(resolved["path"]))
        tag = str(model.java.tag())
        after = {str(item) for item in worker.client().tags()}
    except Exception as exc:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "model.load was dispatched; preserve registered artifact and inspect Worker tags",
                                     stage="post_dispatch", details={"artifact_id": resolved["artifact_id"],
                                                                      "cause_type": type(exc).__name__, "safe_retry": False}) from exc
    if tag in before or tag not in after or len(after - before) != 1:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "model.load did not establish exactly one new tag",
                                     stage="post_dispatch", details={"new_tags": sorted(after - before), "safe_retry": False})
    ref = _bind_created_model(worker, tag, context, operation="model.load")
    _record_saved_baseline(context, model_ref=ref, state="SAVED", saved_revision=0,
                           source_model_revision=0, operation="model.load",
                           artifact_id=str(resolved["artifact_id"]), sha256=str(resolved["sha256"]),
                           size_bytes=int(resolved["size"]))
    return {"status": "LOADED", "model_tag": tag, "model_ref": ref,
            "artifact_id": resolved["artifact_id"], "sha256": resolved["sha256"],
            "size_bytes": resolved["size"], "ownership": "mcp_owned", "project_id": context["project_id"]}


def _validate_loadable_mph(record: Any, resolved: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    """Admit a pinned registered MPH without treating arbitrary paths as input.

    ``artifact.register`` predates typed media metadata, so a registered .mph
    may have no ``artifact_type``/``format_version``.  In that case the pinned
    project-local registration, canonical source suffix, and bounded safe MPH
    ZIP structure are the format evidence.  If metadata is present, it must
    agree with the MPH format.  The Worker load/readback remains the final
    native-format check.
    """
    if not isinstance(record, Mapping) or record.get("schema_version") != 2:
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "model.load requires a schema-v2 registered artifact")
    if (record.get("project_id") != context.get("project_id")
            or record.get("classification") != "project_local"):
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "model.load requires a project-local registered artifact")
    if record.get("role") == "model_package":
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "model.load accepts registered MPH files; model packages require explicit extraction")
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "registered model artifact has no source provenance")
    source_path = provenance.get("source_project_relative_path")
    registered_path = record.get("path")
    if (not isinstance(source_path, str) or not source_path.lower().endswith(".mph")
            or not isinstance(registered_path, str) or not registered_path.lower().endswith(".mph")
            or Path(source_path).is_absolute() or any(part in {"", ".", ".."} for part in Path(source_path).parts)):
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "model.load requires a canonical project-local .mph registration")
    if record.get("artifact_type") not in (None, MODEL_SAVE_FORMAT):
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "registered artifact type is not COMSOL MPH")
    if record.get("format_version") not in (None, "comsol-mph-savecopy/1", "comsol-mph/1"):
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "registered MPH format version is unsupported")
    if (record.get("sha256") != resolved.get("sha256")
            or record.get("artifact_id") != resolved.get("artifact_id")
            or record.get("size") != resolved.get("size")):
        raise PreWriteRefusal("ARTIFACT_IDENTITY_MISMATCH", "resolved registered MPH identity differs from its durable record")
    try:
        # Reuse the model-package MPH reader's bounded central-directory and
        # path/special-member checks.  No FileResource temporary path is read.
        from ._g3_model_delivery import _hash_embedded_resources
        _hash_embedded_resources(Path(resolved["path"]), ())
    except (PreWriteRefusal, ExecutionContractError) as exc:
        raise PreWriteRefusal("MODEL_FORMAT_UNVERIFIED", "registered .mph bytes are not a safely readable COMSOL MPH archive",
                              details={"cause_code": getattr(exc, "code", None)}) from exc


def _registered_overwrite_target(context: Mapping[str, Any], store: Any, target: Path,
                                 relative: str) -> tuple[dict[str, Any], str, dict[str, int]]:
    """Prove an existing destination is the exact saved_model previously published here."""
    from ._artifact_store import _hash_regular_artifact

    try:
        target_digest, target_identity = _hash_regular_artifact(target)
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc
    candidates = []
    for row in context["operation_store"].list_metadata("artifacts"):
        provenance = row.get("provenance") if isinstance(row, Mapping) else None
        if (isinstance(provenance, Mapping)
                and provenance.get("source_project_relative_path") == relative
                and row.get("role") == MODEL_SAVE_ROLE
                and row.get("project_id") == context["project_id"]
                and row.get("sha256") == target_digest
                and provenance.get("source_sha256") == target_digest):
            candidates.append(dict(row))
    if len(candidates) != 1:
        raise PreWriteRefusal("OVERWRITE_UNSAFE", "destination bytes do not match one exact owned registered saved_model")
    record = candidates[0]
    if (record.get("schema_version") != 2 or record.get("artifact_id") != target_digest
            or record.get("classification") != "project_local"
            or record.get("artifact_type") != MODEL_SAVE_FORMAT
            or record.get("format_version") != "comsol-mph-savecopy/1"):
        raise PreWriteRefusal("OVERWRITE_UNSAFE", "registered overwrite target has incompatible metadata")
    resolved = _resolve_registered(context, target_digest)
    if (Path(resolved["path"]).resolve(strict=True) == target.resolve(strict=True)
            or ((resolved.get("file_identity", {}).get("device"), resolved.get("file_identity", {}).get("inode"))
                == (target_identity.get("device"), target_identity.get("inode")))):
        raise PreWriteRefusal("OVERWRITE_UNSAFE", "destination aliases the immutable registered artifact")
    return record, target_digest, target_identity


def _hash_open_handle(file_descriptor: int) -> tuple[str, int, os.stat_result]:
    digest = hashlib.sha256()
    size = 0
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    for block in iter(lambda: os.read(file_descriptor, 1024 * 1024), b""):
        size += len(block)
        digest.update(block)
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    return digest.hexdigest(), size, os.fstat(file_descriptor)


def _exchange_names(directory_fd: int, first: str, second: str) -> None:
    """Atomically exchange two names relative to one already pinned directory."""
    if sys.platform == "darwin":
        exchange = getattr(ctypes.CDLL(None, use_errno=True), "renameatx_np", None)
        if exchange is None:
            raise OSError(errno.ENOTSUP, "atomic exchange is unavailable")
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        exchange.restype = ctypes.c_int
        result = exchange(directory_fd, os.fsencode(first), directory_fd, os.fsencode(second), 0x00000002)
    elif sys.platform.startswith("linux"):
        exchange = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
        if exchange is None:
            raise OSError(errno.ENOTSUP, "atomic exchange is unavailable")
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        exchange.restype = ctypes.c_int
        result = exchange(directory_fd, os.fsencode(first), directory_fd, os.fsencode(second), 0x00000002)
    else:
        raise OSError(errno.ENOTSUP, "atomic registered overwrite is unavailable on this platform")
    if result != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, os.strerror(error), second)


def _atomic_exchange_publish(root: Path, relative: str, source: Path, *,
                             expected_sha256: str, expected_size: int,
                             old_sha256: str, old_identity: Mapping[str, int]) -> dict[str, Any]:
    """Replace one registered destination by atomic exchange, retaining old bytes until registration."""
    from ._artifact_store import (
        _assert_pinned_artifact_stable, _directory_binding_matches,
        _hash_regular_artifact, _open_directory_binding, _open_pinned_artifact,
    )

    lexical = PurePosixPath(relative)
    if (lexical.is_absolute() or not lexical.parts or lexical.as_posix() != relative
            or any(part in {"", ".", ".."} for part in lexical.parts)):
        raise PreWriteRefusal("INVALID_DESTINATION", "overwrite destination must be canonical project-relative syntax")
    if sys.platform not in {"darwin", "linux"}:
        raise PreWriteRefusal("OVERWRITE_PLATFORM_UNSUPPORTED", "safe atomic overwrite is unavailable on this platform")
    parent_parts = tuple(lexical.parts[:-1])
    leaf = lexical.parts[-1]
    descriptors, directory_identity = _open_directory_binding(root, parent_parts)
    parent_fd = descriptors[-1]
    temporary = f".model-save-{os.urandom(16).hex()}.partial"
    temporary_identity: tuple[int, int] | None = None
    swapped = False
    retained_backup = False

    def binding_matches() -> bool:
        return _directory_binding_matches(root, parent_parts, directory_identity)

    def named_stat(name: str) -> os.stat_result:
        value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode):
            raise PreWriteRefusal("OVERWRITE_UNSAFE", "overwrite path is not a regular file")
        return value

    def cleanup_temporary(expected: tuple[int, int] | None) -> bool:
        if expected is None:
            return True
        try:
            found = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if not stat.S_ISREG(found.st_mode) or (found.st_dev, found.st_ino) != expected:
            return False
        try:
            os.unlink(temporary, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError:
            return False
        try:
            os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False

    def rollback_exchange() -> bool:
        try:
            if not binding_matches() or temporary_identity is None:
                return False
            current = named_stat(leaf)
            backup = named_stat(temporary)
            if ((current.st_dev, current.st_ino) != temporary_identity
                    or (backup.st_dev, backup.st_ino) != (old_identity.get("device"), old_identity.get("inode"))):
                return False
            _exchange_names(parent_fd, temporary, leaf)
            final = named_stat(leaf)
            staged = named_stat(temporary)
            final_hash, final_identity = _hash_regular_artifact(root / relative)
            staged_hash, staged_identity = _hash_regular_artifact(root / PurePosixPath(*parent_parts, temporary))
            if ((final.st_dev, final.st_ino) != (old_identity.get("device"), old_identity.get("inode"))
                    or final_hash != old_sha256 or final_identity["size"] != old_identity.get("size")
                    or (staged.st_dev, staged.st_ino) != temporary_identity
                    or staged_hash != expected_sha256 or staged_identity["size"] != expected_size):
                return False
            return cleanup_temporary(temporary_identity)
        except (OSError, ExecutionContractError, ValueError):
            return False

    try:
        if not binding_matches():
            raise PreWriteRefusal("ACCESS_VIOLATION", "project directory binding changed before overwrite")
        old_fd = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
        try:
            old_before = os.fstat(old_fd)
            old_hash, old_size, old_after = _hash_open_handle(old_fd)
            if ((old_before.st_dev, old_before.st_ino) != (old_identity.get("device"), old_identity.get("inode"))
                    or (old_after.st_dev, old_after.st_ino) != (old_before.st_dev, old_before.st_ino)
                    or old_hash != old_sha256 or old_size != old_identity.get("size")):
                raise PreWriteRefusal("ARTIFACT_CHANGED", "registered overwrite destination changed after preflight")
        finally:
            os.close(old_fd)
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        output_fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        temporary_stat = os.fstat(output_fd)
        temporary_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
        source_handle, source_before = _open_pinned_artifact(source)
        try:
            source_hash = hashlib.sha256()
            source_size = 0
            with os.fdopen(os.dup(output_fd), "wb", closefd=True) as output:
                while True:
                    block = source_handle.read(1024 * 1024)
                    if not block:
                        break
                    source_size += len(block)
                    if source_size > expected_size:
                        raise ExecutionContractError("ARTIFACT_CHANGED", "saved MPH grew while staging overwrite")
                    source_hash.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            _assert_pinned_artifact_stable(source, source_before, os.fstat(source_handle.fileno()))
        finally:
            source_handle.close()
        if source_size != expected_size or source_hash.hexdigest() != expected_sha256:
            raise ExecutionContractError("ARTIFACT_HASH_MISMATCH", "staged MPH differs from its verified SHA/size")
        staged_hash, staged_size, staged_after = _hash_open_handle(output_fd)
        if ((staged_after.st_dev, staged_after.st_ino) != temporary_identity
                or staged_hash != expected_sha256 or staged_size != expected_size):
            raise ExecutionContractError("ARTIFACT_HASH_MISMATCH", "staged overwrite failed same-handle readback")
        os.fsync(output_fd)
        if not binding_matches():
            raise PreWriteRefusal("ACCESS_VIOLATION", "project directory binding changed before overwrite commit")
        current_old = named_stat(leaf)
        if ((current_old.st_dev, current_old.st_ino) != (old_identity.get("device"), old_identity.get("inode"))
                or current_old.st_size != old_identity.get("size")):
            raise PreWriteRefusal("ARTIFACT_CHANGED", "registered overwrite destination changed before commit")
        _exchange_names(parent_fd, temporary, leaf)
        swapped = True
        os.fsync(parent_fd)
        published = named_stat(leaf)
        backup = named_stat(temporary)
        published_hash, published_identity = _hash_regular_artifact(root / relative)
        backup_path = root / PurePosixPath(*parent_parts, temporary)
        backup_hash, backup_identity = _hash_regular_artifact(backup_path)
        if ((published.st_dev, published.st_ino) != temporary_identity
                or published_hash != expected_sha256 or published_identity["size"] != expected_size
                or (backup.st_dev, backup.st_ino) != (old_identity.get("device"), old_identity.get("inode"))
                or backup_hash != old_sha256 or backup_identity["size"] != old_identity.get("size")
                or not binding_matches()):
            if not rollback_exchange():
                retained_backup = True
                raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "atomic overwrite readback failed and rollback could not be verified",
                                             stage="post_dispatch", details={"safe_retry": False,
                                                 "destination": relative, "recovery_path": backup_path.relative_to(root).as_posix()})
            swapped = False
            raise ExecutionContractError("OVERWRITE_ROLLED_BACK", "atomic overwrite failed readback and the original bytes were restored",
                                         stage="post_dispatch", details={"safe_retry": False, "destination": relative})
        retained_backup = True
        return {"relative_path": relative, "backup_relative_path": backup_path.relative_to(root).as_posix(),
                "backup_name": temporary, "parent_parts": list(parent_parts),
                "directory_identity": [list(pair) for pair in directory_identity],
                "old_sha256": old_sha256, "old_size_bytes": int(old_identity["size"]),
                "old_identity": {"device": int(old_identity["device"]), "inode": int(old_identity["inode"])},
                "new_sha256": expected_sha256, "new_size_bytes": expected_size,
                "new_identity": {"device": temporary_identity[0], "inode": temporary_identity[1]}}
    except BaseException:
        if swapped:
            if rollback_exchange():
                swapped = False
                retained_backup = False
            else:
                retained_backup = True
                raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "overwrite failed after exchange; preserve destination and retained bytes",
                                             stage="post_dispatch", details={"safe_retry": False, "destination": relative,
                                                 "recovery_path": (root / PurePosixPath(*parent_parts, temporary)).relative_to(root).as_posix()})
        if not retained_backup:
            cleanup_temporary(temporary_identity)
        raise
    finally:
        try:
            os.close(output_fd)  # noqa: F821 - initialized on the only path that reaches the staging block
        except (OSError, UnboundLocalError):
            pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _finalize_overwrite(root: Path, receipt: Mapping[str, Any]) -> None:
    """Remove the verified old-name backup after durable artifact registration."""
    from ._artifact_store import _directory_binding_matches, _hash_regular_artifact, _open_directory_binding

    relative = receipt["relative_path"]
    parent_parts = tuple(receipt["parent_parts"])
    expected_binding = tuple(tuple(pair) for pair in receipt["directory_identity"])
    descriptors, _ = _open_directory_binding(root, parent_parts)
    parent_fd = descriptors[-1]
    backup_name = receipt["backup_name"]
    try:
        if not _directory_binding_matches(root, parent_parts, expected_binding):
            raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "project directory binding changed before old-byte cleanup",
                                         stage="post_dispatch", details={"safe_retry": False,
                                             "recovery_path": receipt["backup_relative_path"]})
        backup = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        expected = receipt["old_identity"]
        digest, identity = _hash_regular_artifact(root / PurePosixPath(*parent_parts, backup_name))
        if ((backup.st_dev, backup.st_ino) != (expected["device"], expected["inode"])
                or digest != receipt["old_sha256"] or identity["size"] != receipt["old_size_bytes"]):
            raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "retained original bytes changed before cleanup",
                                         stage="post_dispatch", details={"safe_retry": False,
                                             "recovery_path": receipt["backup_relative_path"]})
        os.unlink(backup_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        try:
            os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "retained original name remains after cleanup",
                                         stage="post_dispatch", details={"safe_retry": False,
                                             "recovery_path": receipt["backup_relative_path"]})
    except OSError as exc:
        raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "old-byte backup cleanup could not be verified",
                                     stage="post_dispatch", details={"safe_retry": False,
                                         "cause_type": type(exc).__name__,
                                         "recovery_path": receipt["backup_relative_path"]}) from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _rollback_registered_overwrite(root: Path, receipt: Mapping[str, Any]) -> bool:
    """Restore the prior registered bytes if later registration fails."""
    from ._artifact_store import _directory_binding_matches, _hash_regular_artifact, _open_directory_binding

    relative = receipt["relative_path"]
    parent_parts = tuple(receipt["parent_parts"])
    expected_binding = tuple(tuple(pair) for pair in receipt["directory_identity"])
    descriptors, _ = _open_directory_binding(root, parent_parts)
    parent_fd = descriptors[-1]
    backup_name = receipt["backup_name"]
    try:
        if not _directory_binding_matches(root, parent_parts, expected_binding):
            return False
        current = os.stat(relative.rsplit("/", 1)[-1], dir_fd=parent_fd, follow_symlinks=False)
        backup = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (receipt["new_identity"]["device"], receipt["new_identity"]["inode"])
                or (backup.st_dev, backup.st_ino) != (receipt["old_identity"]["device"], receipt["old_identity"]["inode"])):
            return False
        _exchange_names(parent_fd, backup_name, relative.rsplit("/", 1)[-1])
        os.fsync(parent_fd)
        original_hash, original_identity = _hash_regular_artifact(root / relative)
        staged_hash, staged_identity = _hash_regular_artifact(root / receipt["backup_relative_path"])
        if (original_hash != receipt["old_sha256"] or original_identity["size"] != receipt["old_size_bytes"]
                or staged_hash != receipt["new_sha256"] or staged_identity["size"] != receipt["new_size_bytes"]):
            return False
        os.unlink(backup_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        try:
            os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    except (OSError, ExecutionContractError, ValueError):
        return False
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def tree(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"path", "depth", "cursor", "limit"})
    _model_state(context, model_tag)
    depth = body.get("depth", MODEL_TREE_MAX_DEPTH)
    limit = body.get("limit", 100)
    if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= MODEL_TREE_MAX_DEPTH:
        raise PreWriteRefusal("INVALID_REQUEST", "depth must be an integer between 0 and 32")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MODEL_TREE_MAX_LIMIT:
        raise PreWriteRefusal("INVALID_REQUEST", "limit must be between 1 and 500")
    cursor = body.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4096):
        raise PreWriteRefusal("INVALID_CURSOR", "cursor must be a bounded string")
    path = body.get("path", {"segments": []})
    if not isinstance(path, Mapping):
        raise PreWriteRefusal("INVALID_REQUEST", "path must be a NodePath object")
    from ._g2_engine import find_nodes
    base_depth = len(path.get("segments", []))
    result = find_nodes(worker, model_tag, {"_managed_tree_depth": depth}, root=path, limit=limit,
                        cursor=cursor, model_revision=int(context["source_revision"]),
                        budget={"max_nodes": 100_000, "max_seconds": 600.0, "max_rpc": 1_000_000})
    rows = [row for row in result["results"]
            if len(row["path"].get("segments", [])) - base_depth <= depth]
    return {"status": result["status"], "path": path, "depth": depth, "nodes": rows,
            "count": len(rows), "complete": result["complete"], "truncated": result["truncated"],
            "next_cursor": result["next_cursor"], "errors": result["errors"],
            "model_ref": context["model_ref"], "revision": context["source_revision"]}


def save_model(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"destination", "overwrite", "include_solution"})
    _service, _ref, _state = _model_state(context, model_tag)
    _require_context(context, "project_root", "project_id", "operation_store", "host_identity",
                     "engine_host_identity", "operation_id", "request_id", "model_ref")
    destination = body.get("destination")
    overwrite = body.get("overwrite")
    include_solution = body.get("include_solution", True)
    if not isinstance(destination, str) or not destination.strip():
        raise PreWriteRefusal("INVALID_REQUEST", "destination must be nonempty and project-relative")
    if type(overwrite) is not bool or type(include_solution) is not bool:
        raise PreWriteRefusal("INVALID_REQUEST", "overwrite and include_solution must be booleans")
    destination_lexical = PurePosixPath(destination)
    if (destination_lexical.is_absolute() or destination_lexical.as_posix() != destination
            or "\\" in destination or any(part in {"", ".", ".."} for part in destination.split("/"))):
        raise PreWriteRefusal("INVALID_DESTINATION", "destination must use canonical project-relative POSIX syntax")
    root = Path(context["project_root"]).resolve(strict=True)
    from ._artifact_store import ArtifactStore
    # Validate the destination and its durable ownership before dispatch.
    store = ArtifactStore(root)
    try:
        target = store.resolve_safe_path(destination, allow_overwrite=overwrite)
    except ExecutionContractError as exc:
        if exc.code == "DESTINATION_EXISTS" and overwrite:
            target = store.resolve_safe_path(destination, allow_overwrite=True)
        else:
            raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc
    if target.is_absolute() and Path(destination).is_absolute():
        raise PreWriteRefusal("ACCESS_VIOLATION", "destination must use canonical project-relative syntax")
    if not target.parent.is_dir():
        raise PreWriteRefusal("INVALID_DESTINATION", "model.save requires an existing project-local destination directory")
    relative = target.relative_to(root).as_posix()
    existing_record = None
    old_digest = None
    old_identity = None
    if target.exists():
        if not overwrite:
            raise PreWriteRefusal("DESTINATION_EXISTS", "existing destination is not an owned registered saved_model; it cannot be overwritten")
        existing_record, old_digest, old_identity = _registered_overwrite_target(context, store, target, relative)
        if sys.platform not in {"darwin", "linux", "win32"}:
            raise PreWriteRefusal("OVERWRITE_PLATFORM_UNSUPPORTED", "safe atomic overwrite is unavailable on this platform")
    scratch = Path(tempfile.mkdtemp(prefix="model-save-", dir=root))
    source = scratch / "model.mph"
    dispatched = False
    succeeded = False
    overwrite_receipt = None
    windows_overwrite = None
    registration_succeeded = False
    stage = "preflight"
    try:
        previous_saved_baseline = context["operation_store"].get_metadata(
            "revisions", _saved_baseline_key(context["model_ref"]))
    except Exception as exc:
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "model.save cannot preserve the prior saved baseline") from exc
    try:
        live_model = worker.client().model(model_tag).java
        before_path = str(live_model._call("getFilePath") or "")
        stage = "save_copy"
        dispatched = True
        receipt = live_model._call("saveModelPackage", str(source), include_solution)
        if not source.is_file() or source.stat().st_size <= 0:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL saveCopy produced no nonempty MPH", stage="post_dispatch",
                                         details={"safe_retry": False, "receipt": receipt})
        from ._artifact_store import _hash_regular_artifact, publish_project_file_no_clobber
        sha, identity = _hash_regular_artifact(source)
        size = int(identity["size"])
        after_path = str(live_model._call("getFilePath") or "")
        if before_path != after_path:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "saveCopy changed the live model filepath",
                                         stage="post_dispatch", details={"before": before_path, "after": after_path,
                                                                          "safe_retry": False})
        if existing_record is not None:
            stage = "overwrite_publish"
            if sys.platform == "win32":
                from ._windows_model_publish import begin_registered_overwrite
                windows_overwrite = begin_registered_overwrite(
                    root, relative, source,
                    operation_store=context["operation_store"],
                    project_id=str(context["project_id"]),
                    operation_id=str(context["operation_id"]),
                    request_id=str(context["request_id"]),
                    model_ref=context["model_ref"],
                    source_revision=int(context["source_revision"]),
                    old_sha256=str(old_digest), old_identity=old_identity,
                    old_artifact_relative_path=str(existing_record["path"]),
                    new_sha256=sha, new_size_bytes=size, source_identity=identity,
                )
                overwrite_receipt = windows_overwrite.receipt
            else:
                overwrite_receipt = _atomic_exchange_publish(root, relative, source,
                    expected_sha256=sha, expected_size=size, old_sha256=str(old_digest), old_identity=old_identity)
            published = {"sha256": sha, "size_bytes": size, "reused_existing": False}
        else:
            stage = "publish"
            receipt_publish = publish_project_file_no_clobber(root, relative, source,
                                                              expected_sha256=sha, expected_size=size)
            published = {"sha256": receipt_publish["sha256"], "size_bytes": receipt_publish["size_bytes"],
                         "reused_existing": False}
        stage = "register"
        try:
            registration = _register_saved_model(context, relative, sha256=sha, size=size)
            registration_succeeded = True
        except Exception:
            if windows_overwrite is not None:
                try:
                    windows_overwrite.rollback()
                except Exception as rollback_exc:
                    raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "artifact registration failed and Windows rollback is blocked",
                                                 stage="post_dispatch", details={"safe_retry": False,
                                                     "destination": relative,
                                                     "recovery_path": overwrite_receipt.get("recovery_path"),
                                                     "journal_id": overwrite_receipt.get("journal_id"),
                                                     "rollback_cause_type": type(rollback_exc).__name__}) from rollback_exc
                windows_overwrite = None
                overwrite_receipt = None
            elif overwrite_receipt is not None and not _rollback_registered_overwrite(root, overwrite_receipt):
                raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "artifact registration failed after overwrite; the prior destination could not be restored",
                                             stage="post_dispatch", details={"safe_retry": False,
                                                 "destination": relative,
                                                 "recovery_path": overwrite_receipt["backup_relative_path"]})
            else:
                overwrite_receipt = None
            raise
        # A successful model.save runs under the existing write-ticket path,
        # which advances the managed revision once even though saveCopy leaves
        # the live model tree untouched. Bind the durable saved baseline to
        # that terminal revision; the source artifact provenance still records
        # the exact pre-save revision whose bytes COMSOL copied.
        _record_saved_baseline(context, model_ref=context["model_ref"], state="SAVED",
                               saved_revision=int(context["source_revision"]) + 1,
                               source_model_revision=int(context["source_revision"]),
                               operation="model.save", artifact_id=str(registration["artifact_id"]),
                               sha256=sha, size_bytes=size)
        if windows_overwrite is not None:
            stage = "overwrite_finalize"
            overwrite_receipt = windows_overwrite.commit()
        if overwrite_receipt is not None:
            if windows_overwrite is None:
                stage = "overwrite_finalize"
                _finalize_overwrite(root, overwrite_receipt)
                overwrite_receipt = None
        succeeded = True
        return {"status": "SAVED", "model_ref": context["model_ref"], "source_revision": context["source_revision"],
                "destination": relative, "artifact_id": registration["artifact_id"], "sha256": sha,
                "size_bytes": size, "include_solution": include_solution, "overwrite": overwrite,
                "overwrite_policy": ("OWNED_REGISTERED_TARGET_JOURNALLED_NO_CLOBBER" if sys.platform == "win32"
                                     and existing_record is not None else "OWNED_REGISTERED_TARGET_ATOMIC_EXCHANGE"),
                "atomic_new_file_publication": True,
                "atomic_path_replacement": bool(existing_record is not None and sys.platform in {"darwin", "linux"}),
                "visibility_gap_possible": bool(existing_record is not None and sys.platform == "win32"),
                "overwrite_recovery": (dict(overwrite_receipt) if existing_record is not None and sys.platform == "win32" else None),
                "publication": published,
                "live_filepath_preserved": True}
    except Exception as exc:
        if windows_overwrite is not None and not windows_overwrite.closed:
            baseline_error = None
            try:
                baseline_key = _saved_baseline_key(context["model_ref"])
                if isinstance(previous_saved_baseline, Mapping):
                    context["operation_store"].put_metadata(
                        "revisions", baseline_key, dict(previous_saved_baseline))
                else:
                    _record_saved_baseline(context, model_ref=context["model_ref"], state="UNSAVED",
                                           saved_revision=int(context["source_revision"]),
                                           operation="model.save.rollback")
            except Exception as restore_exc:
                baseline_error = restore_exc
            try:
                if baseline_error is not None:
                    from ._windows_model_publish import _set_status
                    _set_status(context["operation_store"], windows_overwrite.journal,
                                "ROLLBACK_INTENT", baseline_restore_failed=type(baseline_error).__name__)
                    raise ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "Windows overwrite baseline rollback could not be persisted",
                                                 stage="post_dispatch", details={"safe_retry": False,
                                                     "destination": relative,
                                                     "journal_id": windows_overwrite.receipt.get("journal_id"),
                                                     "baseline_restore_cause_type": type(baseline_error).__name__}) from exc
                windows_overwrite.rollback()
            except Exception as rollback_exc:
                windows_overwrite = None
                exc = ExecutionContractError("OVERWRITE_STATE_UNKNOWN", "Windows overwrite failed and exact rollback is blocked",
                                             stage="post_dispatch", details={"safe_retry": False,
                                                 "destination": relative,
                                                 "recovery_path": (overwrite_receipt or {}).get("recovery_path"),
                                                 "journal_id": (overwrite_receipt or {}).get("journal_id"),
                                                 "rollback_cause_type": type(rollback_exc).__name__})
            else:
                windows_overwrite = None
        receipt_path = scratch / "failure_receipt.json"
        if dispatched:
            payload = {"schema": "comsol-mcp-model-save-failure", "version": 1,
                       "request_id": context.get("request_id"), "operation_id": context.get("operation_id"),
                       "model_ref": context.get("model_ref"),
                       "source_revision": context.get("source_revision"), "destination": relative,
                       "stage": stage, "dispatched": True, "safe_retry": False,
                       "cause_type": type(exc).__name__, "cause_code": getattr(exc, "code", None),
                       "cause_message": str(exc)[:500]}
            # Preserve the exact recovery pointer emitted by the publisher.
            # In particular, a failed atomic rollback leaves the prior MPH at
            # that project-relative path and the operation fenced UNKNOWN.
            details = getattr(exc, "details", None)
            if isinstance(details, Mapping):
                recovery_details = {key: details[key] for key in
                                    ("destination", "recovery_path", "backup_path", "safe_retry")
                                    if key in details and isinstance(details[key], (str, bool))}
                if recovery_details:
                    payload["recovery_details"] = recovery_details
            if overwrite_receipt is not None:
                payload["overwrite_recovery"] = {"backup_path": overwrite_receipt.get("backup_relative_path"),
                                                  "old_sha256": overwrite_receipt.get("old_sha256"),
                                                  "new_sha256": overwrite_receipt.get("new_sha256"),
                                                  "registration_succeeded": registration_succeeded}
            if source.is_file():
                from ._artifact_store import _hash_regular_artifact
                digest, identity = _hash_regular_artifact(source)
                payload["retained_mph"] = {"path": source.relative_to(root).as_posix(),
                                           "sha256": digest, "size_bytes": identity["size"]}
            if target.exists():
                try:
                    from ._artifact_store import _hash_regular_artifact
                    digest, identity = _hash_regular_artifact(target)
                    payload["destination_readback"] = {"state": "VERIFIED", "sha256": digest,
                                                        "size_bytes": identity["size"]}
                except Exception as readback_exc:
                    payload["destination_readback"] = {"state": "UNKNOWN",
                                                        "cause_type": type(readback_exc).__name__}
            with receipt_path.open("xb") as handle:
                handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if isinstance(exc, ExecutionContractError):
                exc.details = {**exc.details, "recovery_directory": scratch.relative_to(root).as_posix(),
                               "failure_receipt": receipt_path.relative_to(root).as_posix(), "save_stage": stage,
                               "safe_retry": False}
                raise
        if dispatched and not isinstance(exc, ExecutionContractError):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "model.save did not complete with a verified artifact receipt",
                                         stage="post_dispatch", details={"cause_type": type(exc).__name__,
                                                                          "recovery_directory": scratch.relative_to(root).as_posix(),
                                                                          "failure_receipt": receipt_path.relative_to(root).as_posix(),
                                                                          "safe_retry": False}) from exc
        raise
    finally:
        # A failed post-save publication keeps the scratch model and receipt.
        if succeeded:
            source.unlink(missing_ok=True)
            (scratch / "failure_receipt.json").unlink(missing_ok=True)
            scratch.rmdir()


def clone_model(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"label", "include_solution"})
    _service, _ref, _state = _model_state(context, model_tag)
    _require_context(context, "project_root", "project_id", "backend", "execution_service", "operation_id")
    label = body.get("label")
    include_solution = body.get("include_solution", True)
    if not isinstance(label, str) or not label.strip() or len(label) > 256 or type(include_solution) is not bool:
        raise PreWriteRefusal("INVALID_REQUEST", "clone requires a bounded label and boolean include_solution")
    client = worker.client()
    before_tags = {str(item) for item in client.tags()}
    source = client.model(model_tag).java
    try:
        before_snapshot = worker.backend_snapshot(model_tag)
        before_path = str(source._call("getFilePath") or "")
    except Exception as exc:
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "clone source snapshot is unavailable before saveCopy",
                              details={"cause_type": type(exc).__name__}) from exc
    scratch = Path(tempfile.mkdtemp(prefix="model-clone-", dir=context["project_root"]))
    mph_path = scratch / "clone.mph"
    dispatched = False
    succeeded = False
    try:
        dispatched = True
        source._call("saveModelPackage", str(mph_path), include_solution)
        if not mph_path.is_file() or mph_path.stat().st_size <= 0:
            raise RuntimeError("saveCopy produced no nonempty clone source")
        tag = str(client.uniquetag("mcp_clone"))
        if not tag or tag in before_tags:
            raise RuntimeError("ModelUtil.uniquetag did not return a fresh clone tag")
        # The Java Worker exposes only the generic, method-allowlisted ModelUtil
        # route. This is a typed ModelUtil.loadCopy call; no Java source is run.
        submit = getattr(worker, "submit", None)
        if callable(submit):
            reply = submit("modelutil", {"method": "loadCopy", "args": [tag, str(mph_path)]},
                           request_id=str(context.get("operation_id")))
            if isinstance(reply, Mapping) and reply.get("ok") is False:
                failure = reply.get("failure") if isinstance(reply.get("failure"), Mapping) else {}
                raise ExecutionContractError(str(failure.get("code") or "MODEL_CLONE_FAILED"),
                                             str(failure.get("message") or "ModelUtil.loadCopy failed"),
                                             stage="post_dispatch", details={"safe_retry": False})
        else:
            load_copy = getattr(client, "loadCopy", None)
            if not callable(load_copy):
                raise PreWriteRefusal("WORKER_METHOD_UNAVAILABLE", "typed ModelUtil.loadCopy route is not available in this Worker")
            load_copy(tag, str(mph_path))
        clone = client.model(tag)
        clone.label(label.strip())
        after_tags = {str(item) for item in client.tags()}
        if tag in before_tags or tag not in after_tags or after_tags - before_tags != {tag}:
            raise RuntimeError("ModelUtil.loadCopy did not establish exactly one independent tag")
        after_snapshot = worker.backend_snapshot(model_tag)
        if after_snapshot != before_snapshot or str(source._call("getFilePath") or "") != before_path:
            raise RuntimeError("clone changed the source model snapshot or filepath")
        ref = _bind_created_model(worker, tag, context, operation="model.clone")
        from ._artifact_store import _hash_regular_artifact
        clone_sha, clone_identity = _hash_regular_artifact(mph_path)
        clone_size = int(clone_identity["size"])
        clone_context = {**dict(context), "model_ref": ref, "source_revision": 0}
        prior_clone_artifact = context["operation_store"].get_metadata("artifacts", clone_sha)
        if prior_clone_artifact is not None:
            # Content-addressed registration may already have these exact MPH
            # bytes from an earlier save. Reuse only the same project-bound,
            # saved-model registration after the normal resolver proves it.
            if (not isinstance(prior_clone_artifact, Mapping)
                    or prior_clone_artifact.get("schema_version") != 2
                    or prior_clone_artifact.get("artifact_id") != clone_sha
                    or prior_clone_artifact.get("sha256") != clone_sha
                    or prior_clone_artifact.get("size") != clone_size
                    or prior_clone_artifact.get("project_id") != context["project_id"]
                    or prior_clone_artifact.get("role") != MODEL_SAVE_ROLE
                    or prior_clone_artifact.get("format_version") != "comsol-mph-savecopy/1"):
                raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "clone snapshot bytes conflict with an existing artifact registration")
            resolved_clone = _resolve_registered(context, clone_sha)
            if resolved_clone.get("sha256") != clone_sha or resolved_clone.get("size") != clone_size:
                raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "existing clone snapshot artifact failed exact content readback")
            clone_artifact = prior_clone_artifact
        else:
            clone_artifact = _register_saved_model(
                clone_context, mph_path.relative_to(Path(context["project_root"])).as_posix(),
                sha256=clone_sha, size=clone_size,
            )
        _record_saved_baseline(clone_context, model_ref=ref, state="SAVED", saved_revision=0,
                               source_model_revision=0, operation="model.clone",
                               artifact_id=str(clone_artifact["artifact_id"]), sha256=clone_sha,
                               size_bytes=clone_size)
        succeeded = True
        return {"status": "CLONED", "source_model_ref": context["model_ref"], "source_tag": model_tag,
                "model_ref": ref, "model_tag": tag, "label": label.strip(),
                "include_solution": include_solution, "source_preserved": True,
                "copy_method": "ModelUtil.loadCopy", "artifact_id": clone_artifact["artifact_id"],
                "sha256": clone_sha, "size_bytes": clone_size}
    except Exception as exc:
        if dispatched:
            project_root = Path(context["project_root"])
            receipt_path = scratch / "failure_receipt.json"
            known_new_tags = None
            try:
                known_new_tags = sorted({str(item) for item in client.tags()} - before_tags)
            except Exception:
                pass
            payload = {"schema": "comsol-mcp-model-clone-failure", "version": 1,
                       "operation_id": context.get("operation_id"), "model_ref": context.get("model_ref"),
                       "source_revision": context.get("source_revision"), "source_tag": model_tag,
                       "stage": "savecopy_or_loadcopy_or_readback", "safe_retry": False,
                       "cause_type": type(exc).__name__, "cause_code": getattr(exc, "code", None),
                       "cause_message": str(exc)[:500], "known_new_tags": known_new_tags}
            if mph_path.is_file():
                from ._artifact_store import _hash_regular_artifact
                digest, identity = _hash_regular_artifact(mph_path)
                payload["retained_mph"] = {"path": mph_path.relative_to(project_root).as_posix(),
                                           "sha256": digest, "size_bytes": identity["size"]}
            with receipt_path.open("xb") as handle:
                handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if isinstance(exc, ExecutionContractError):
                exc.details = {**exc.details, "recovery_directory": scratch.relative_to(project_root).as_posix(),
                               "failure_receipt": receipt_path.relative_to(project_root).as_posix(),
                               "safe_retry": False}
                raise
        if dispatched and not isinstance(exc, (PreWriteRefusal, ExecutionContractError)):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "clone was dispatched; inspect source and new ModelUtil tags before retry",
                                         stage="post_dispatch", details={"cause_type": type(exc).__name__,
                                                                          "recovery_directory": scratch.relative_to(Path(context["project_root"])).as_posix(),
                                                                          "safe_retry": False}) from exc
        raise
    finally:
        if succeeded:
            mph_path.unlink(missing_ok=True)
            scratch.rmdir()


def close_model(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"discard_changes", "authorization_ref"})
    _require_context(context, "execution_service", "backend", "model_ref", "source_revision", "project_id")
    service, backend = context["execution_service"], context["backend"]
    from ._execution_contract import model_ref_from_mapping
    ref = model_ref_from_mapping(dict(context["model_ref"]))
    if ref.model_tag != model_tag:
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "close tag does not match the exact ModelRef")
    state = service.ledger._state_for(ref)
    if state.revision != context["source_revision"]:
        raise PreWriteRefusal("REVISION_CONFLICT", "close expected_revision is stale")
    if state.active_operation_id is not None and state.active_operation_id != context.get("close_lifecycle_ticket"):
        raise PreWriteRefusal("ENGINE_BUSY", "active model cannot be closed")
    if type(body.get("discard_changes")) is not bool:
        raise PreWriteRefusal("INVALID_REQUEST", "discard_changes must be boolean")
    owner = service.ledger.model_ownership.get(model_tag)
    binding = backend.model_project_binding(ref.as_dict())
    if owner != "mcp_owned" or binding != {"attribution": "PROJECT_BOUND", "project_id": context["project_id"]}:
        raise PreWriteRefusal("MODEL_OWNERSHIP_UNKNOWN", "only the exact project-bound MCP-owned model can be closed")
    baseline = _saved_baseline(context, ref.as_dict(), state.revision)
    uncertain = state.dirty or state.external_event_counter != state.observed_external_event_counter
    baseline_unknown = baseline["state"] not in {"SAVED", "UNSAVED"}
    unsaved = (baseline["state"] == "UNSAVED" or not baseline["revision_matches"])
    if uncertain and not body["discard_changes"]:
        raise PreWriteRefusal("MODEL_STATE_UNKNOWN", "close cannot preserve a model with an unobserved or uncertain engine change")
    if baseline_unknown and not body["discard_changes"]:
        raise PreWriteRefusal("SAVED_STATE_UNKNOWN", "close cannot prove this exact model revision was saved")
    if unsaved and not body["discard_changes"]:
        raise PreWriteRefusal("UNSAVED_CHANGES", "model has changes newer than its durable saved baseline")
    requires_authorization = uncertain or baseline_unknown or unsaved
    if requires_authorization:
        authorization = body.get("authorization_ref")
        verifier = context.get("authorization_scope_verifier")
        if not isinstance(authorization, str) or not authorization or not callable(verifier):
            raise PreWriteRefusal("AUTHORIZATION_STATE_UNKNOWN", "discarding unsaved or uncertain model state requires a scoped authorization verifier")
        try:
            accepted = verifier(context["project_id"], "model.close", ref.as_dict(), state.revision, None, authorization)
        except Exception as exc:
            raise PreWriteRefusal("AUTHORIZATION_STATE_UNKNOWN", "close authorization could not be verified",
                                  details={"cause_type": type(exc).__name__}) from exc
        if accepted is not True:
            raise PreWriteRefusal("AUTHORIZATION_REQUIRED", "close authorization does not match project/model/revision scope")
    elif body.get("authorization_ref") is not None:
        # A clean model needs no approval; an unused reference must not be
        # treated as evidence or silently consumed as a generic capability.
        raise PreWriteRefusal("INVALID_REQUEST", "authorization_ref is only meaningful for a dirty close")
    callback = context.get("close_bound_model")
    if not callable(callback):
        raise PreWriteRefusal("CONTROL_PLANE_ROUTE_REQUIRED", "model.close needs the lifecycle-aware queue finalizer")
    return callback(worker=worker, model_tag=model_tag, model_ref=ref.as_dict(),
                    expected_revision=state.revision, discard_changes=body["discard_changes"])


def _strict_json(raw: bytes, *, code: str) -> dict[str, Any]:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PreWriteRefusal(code, "strict UTF-8 JSON is required", details={"cause_type": type(exc).__name__}) from exc
    if not isinstance(value, dict):
        raise PreWriteRefusal(code, "JSON root must be an object")
    return value


def _resolve_recipe_source(context: Mapping[str, Any], source_id: Any, entrypoint: str = "") -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    resolved = _resolve_registered(context, source_id, max_size=2 * 1024 * 1024)
    record = context["operation_store"].get_metadata("artifacts", resolved["artifact_id"])
    if (not isinstance(record, Mapping) or record.get("schema_version") != 2
            or record.get("project_id") != context["project_id"] or record.get("role") not in _SOURCE_ROLES
            or Path(resolved["relative_path"]).suffix.lower() != ".java"):
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "recipe source must be a schema-v2 registered Java source artifact")
    handle, before = _open_registered_source(resolved)
    try:
        raw = handle.read(2 * 1024 * 1024 + 1)
        _check_registered_identity(resolved, before, handle, raw)
    finally:
        handle.close()
    if not raw or len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != resolved["sha256"]:
        raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "registered Java source bytes differ from its content address")
    from ._g2_code import describe_source
    try:
        description = describe_source(context["project_root"], resolved["relative_path"], entrypoint)
        if entrypoint:
            # Comments/literals and nested declarations must not force an inferred
            # entrypoint. Explicit class identity still must exist in this source.
            code = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', " ", raw.decode("utf-8"), flags=re.S)
            declared = set(re.findall(r"\b(?:class|interface|record)\s+([A-Za-z_$][\w$]*)", code))
            class_name = description["entrypoint"].split("#", 1)[0]
            package = re.search(r"\bpackage\s+([\w.]+)\s*;", code)
            prefix = package.group(1) + "." if package else ""
            if not class_name.startswith(prefix) or class_name[len(prefix):] not in declared:
                raise PreWriteRefusal("INVALID_REQUEST", "explicit recipe entrypoint class is not declared in this source")
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc
    if description["source_sha256"] != resolved["sha256"]:
        raise PreWriteRefusal("ARTIFACT_CHANGED", "source changed between registered read and entrypoint inspection")
    return resolved, dict(record), description


def _open_registered_source(resolved: Mapping[str, Any]):
    from ._artifact_store import _open_pinned_artifact
    return _open_pinned_artifact(Path(resolved["path"]))


def _check_registered_identity(resolved: Mapping[str, Any], before: os.stat_result, handle: Any, raw: bytes) -> None:
    from ._artifact_store import _assert_pinned_artifact_stable
    _assert_pinned_artifact_stable(Path(resolved["path"]), before, os.fstat(handle.fileno()))
    expected = resolved.get("file_identity")
    current = {"device": int(before.st_dev), "inode": int(before.st_ino), "size": int(before.st_size),
               "mtime_ns": int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)))}
    if current != expected or len(raw) != resolved["size"]:
        raise PreWriteRefusal("ARTIFACT_CHANGED", "registered source identity changed during its pinned read")


def _test_runtime_observation(context: Mapping[str, Any], record: Mapping[str, Any], *, expected_version: str,
                              require_current_worker: bool) -> dict[str, Any] | None:
    metadata = record.get("metadata")
    binding = metadata.get("runtime_binding") if isinstance(metadata, Mapping) else None
    result = record.get("result")
    execution = result.get("execution") if isinstance(result, Mapping) else None
    observation = execution.get("runtime_observation") if isinstance(execution, Mapping) else None
    identity = observation.get("runtime_identity") if isinstance(observation, Mapping) else None
    backend = context.get("backend")
    bound_identity = getattr(backend, "worker_identity", None)
    service = context.get("execution_service")
    if (not isinstance(observation, Mapping) or observation.get("schema") != "comsol-worker-runtime-observation/v1"
            or observation.get("status") != "OBSERVED" or observation.get("comsol_version") != expected_version
            or not isinstance(identity, Mapping) or not isinstance(binding, Mapping)
            or service is None):
        return None
    identity_fields = ("runtime_id", "worker_instance_id", "connection_epoch", "server_instance_id", "endpoint")
    if (not all(isinstance(identity.get(name), str) and identity.get(name)
                for name in ("runtime_id", "worker_instance_id", "server_instance_id", "endpoint"))
            or type(identity.get("connection_epoch")) is not int or identity["connection_epoch"] < 1):
        return None
    result_model_ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    if (binding.get("worker_instance_id") != identity.get("worker_instance_id")
            or binding.get("worker_epoch") != identity.get("connection_epoch")):
        return None
    if binding.get("endpoint") is not None and binding.get("endpoint") != identity.get("endpoint"):
        return None
    # The durable observation is historical provenance. Its runtime binding,
    # outer execution identity and exact ModelRef must agree with one another,
    # but a later recipe run may use a new Worker/session or a relocated
    # project. Only registration requires the original target to remain live.
    if (binding.get("project_id") != context.get("project_id")
            or not isinstance(binding.get("session_id"), str) or not binding.get("session_id")
            or not isinstance(result_model_ref, Mapping)
            or result_model_ref.get("session_id") != binding.get("session_id")
            or result_model_ref.get("server_instance_id") != identity.get("server_instance_id")):
        return None
    if require_current_worker:
        if not isinstance(bound_identity, Mapping):
            return None
        if any(identity.get(name) != bound_identity.get(name) for name in identity_fields):
            return None
        if (identity.get("server_instance_id") != service.ledger.server_instance_id
                or identity.get("endpoint") != getattr(backend, "endpoint_key", None)
                or binding.get("session_id") != service.ledger.session_id):
            return None
        # New producers bind installation identity separately from the catalog
        # label. Preserve original historical v1 provenance, while requiring
        # this new witness to agree with its still-live trusted attach context.
        if "installation_runtime_id" in observation:
            attached = getattr(backend, "_runtime_observation_binding", None)
            if (attached is None or observation["installation_runtime_id"] != attached.installation_runtime_id
                    or observation["comsol_version"] != attached.comsol_version):
                return None
            try:
                backend._bound_runtime_observation_identity(attached)
            except Exception:
                return None
    return {"comsol_version": expected_version,
            "runtime_identity": {name: identity.get(name) for name in identity_fields}}


def _worker_source_path_matches(source_artifact: Any, registered_relative_path: str) -> bool:
    """Bind a historical Worker path to the content-addressed relative name.

    The durable event records the absolute path seen by the old Worker. That
    absolute root may legitimately change after project relocation; its final
    registered-artifact components remain stable and are sufficient here
    because the independent host-minted execution result binds the source SHA.
    """
    if not isinstance(source_artifact, str) or not source_artifact:
        return False
    expected_posix = PurePosixPath(registered_relative_path)
    raw_posix = PurePosixPath(source_artifact)
    if raw_posix.is_absolute() and not any(part == ".." for part in raw_posix.parts):
        parts = raw_posix.parts
        expected = expected_posix.parts
        if len(parts) >= len(expected) and parts[-len(expected):] == expected:
            return True
    raw_windows = PureWindowsPath(source_artifact)
    if raw_windows.is_absolute() and not any(part == ".." for part in raw_windows.parts):
        parts = tuple(part.casefold() for part in raw_windows.parts)
        expected = tuple(part.casefold() for part in expected_posix.parts)
        if len(parts) >= len(expected) and parts[-len(expected):] == expected:
            return True
    return False


def _durable_java_test_arguments(record: Mapping[str, Any] | None,
                                 metadata: Mapping[str, Any] | None,
                                 project_id: str) -> tuple[Any, bool]:
    """Project only an exact persisted public wrapper; never caller runtime claims."""
    if not isinstance(record, Mapping) or not isinstance(metadata, Mapping):
        return None, False
    operation = record.get("operation")
    arguments = metadata.get("arguments")
    if operation == "code.execute_java":
        return arguments, metadata.get("operation") == operation
    if (operation not in {"operation_call", "registry_call"}
            or metadata.get("operation") != operation
            or not isinstance(arguments, Mapping)
            or set(arguments) != {"operation_id", "arguments"}
            or arguments.get("operation_id") != "code.execute_java"):
        return None, False
    inner = arguments.get("arguments")
    execution = metadata.get("execution")
    if (not isinstance(inner, Mapping) or not isinstance(execution, Mapping)
            or not isinstance(execution.get("project_id"), str)
            or execution.get("project_id") != project_id):
        return None, False
    # The stored execution remains authority. Optional body claims must agree,
    # including JSON types (True must not equal generation 1).
    for claim in ("project_id", "session_id", "model_ref"):
        if claim in inner:
            if claim not in execution:
                return None, False
            try:
                if json.dumps(inner[claim], sort_keys=True, allow_nan=False) != json.dumps(execution[claim], sort_keys=True, allow_nan=False):
                    return None, False
            except (TypeError, ValueError):
                return None, False
    return inner, True


def _validate_test_refs(context: Mapping[str, Any], refs: Any, *, source_sha256: str,
                        source_artifact_id: str, source_path: str, entrypoint: str,
                        expected_version: str, require_current_worker: bool) -> list[dict[str, Any]]:
    if not isinstance(refs, list) or not refs or len(refs) > 64:
        raise PreWriteRefusal("RECIPE_TEST_REQUIRED", "test_refs must contain 1 to 64 durable test operation ids")
    if any(not isinstance(ref, str) or not ref or len(ref) > 128 for ref in refs) or len(set(refs)) != len(refs):
        raise PreWriteRefusal("INVALID_REQUEST", "test_refs must be unique nonempty operation ids")
    rows = []
    store = context["operation_store"]
    for ref in refs:
        record = store.get_operation(ref)
        metadata = record.get("metadata") if isinstance(record, Mapping) else None
        stored_execution = metadata.get("execution") if isinstance(metadata, Mapping) else None
        arguments, durable_java_operation = _durable_java_test_arguments(record, metadata, context["project_id"])
        result = record.get("result") if isinstance(record, Mapping) else None
        data = result.get("data") if isinstance(result, Mapping) else None
        result_execution = result.get("execution") if isinstance(result, Mapping) else None
        worker_reply = data.get("worker") if isinstance(data, Mapping) else None
        readback = data.get("readback") if isinstance(data, Mapping) else None
        before = data.get("before") if isinstance(data, Mapping) else None
        after = data.get("after") if isinstance(data, Mapping) else None
        model_ref = result_execution.get("model_ref") if isinstance(result_execution, Mapping) else None
        source_record = store.get_metadata("artifacts", source_artifact_id)
        provenance = source_record.get("provenance") if isinstance(source_record, Mapping) else None
        path_registered = (isinstance(provenance, Mapping)
                           and isinstance(provenance.get("source_project_relative_path"), str)
                           and source_record.get("path") == source_path)
        worker_execution_observed = False
        job_id = record.get("job_id") if isinstance(record, Mapping) else None
        try:
            job_record = store.operation_job(ref) if hasattr(store, "operation_job") else None
        except Exception:
            job_record = None
        if not isinstance(job_id, str) or not job_id:
            job_id = job_record.get("job_id") if isinstance(job_record, Mapping) else None
        job_terminal = (isinstance(job_record, Mapping) and job_record.get("status") == "SUCCEEDED"
                        and job_record.get("operation_id") == ref and job_record.get("job_id") == job_id)
        if isinstance(job_id, str) and job_id:
            try:
                event_rows = store.events(job_id, offset=0, limit=1000)
            except Exception:
                event_rows = []
            execution_events = [row.get("metadata") for row in event_rows
                                if isinstance(row, Mapping) and row.get("event") == "worker_request"
                                and isinstance(row.get("metadata"), Mapping)
                                and row["metadata"].get("kind") == "code_execute"
                                and row["metadata"].get("operation_id") == record.get("operation_id")]
            submitted = [row for row in execution_events if row.get("phase") == "submitted"]
            observed = [row for row in execution_events if row.get("phase") == "observed"
                        and row.get("status") == "SUCCEEDED"]
            if len(submitted) == 1 and len(observed) == 1:
                sent, received = submitted[0], observed[0]
                sent_payload = sent.get("metadata")
                received_payload = received.get("metadata")
                worker_execution_observed = bool(
                    sent.get("request_id") == received.get("request_id")
                    and sent.get("request_hash") == received.get("request_hash")
                    and isinstance(sent.get("request_id"), str) and sent["request_id"]
                    and isinstance(sent_payload, Mapping)
                    and isinstance(received_payload, Mapping)
                    and sent_payload == received_payload
                    and _worker_source_path_matches(sent_payload.get("source_artifact"), source_path)
                    and sent_payload.get("entrypoint") == entrypoint
                    and isinstance(model_ref, Mapping)
                    and sent_payload.get("tag") == model_ref.get("model_tag")
                )
        evidence_checks = {
            "operation_terminal": job_terminal and isinstance(record, Mapping) and record.get("status") == "SUCCEEDED"
                                  and (record.get("operation") == "code.execute_java" or durable_java_operation),
            "project_and_arguments": isinstance(metadata, Mapping)
                                     and durable_java_operation
                                     and isinstance(stored_execution, Mapping)
                                     and stored_execution.get("project_id") == context["project_id"]
                                     and isinstance(arguments, Mapping)
                                     and arguments.get("source_artifact") == source_path
                                     and arguments.get("entrypoint") == entrypoint
                                     and arguments.get("mode") in {"trusted", "execute"},
            "registered_source": isinstance(source_record, Mapping)
                                and source_record.get("schema_version") == 2
                                and source_record.get("project_id") == context["project_id"]
                                and source_record.get("role") in _SOURCE_ROLES
                                and source_record.get("sha256") == source_sha256 and path_registered,
            "host_success": isinstance(result, Mapping) and result.get("success") is True
                            and isinstance(data, Mapping) and data.get("execution_success") is True
                            and data.get("source_sha256") == source_sha256
                            and data.get("entrypoint") == entrypoint,
            "worker_reply": isinstance(worker_reply, Mapping) and worker_reply.get("ok") is True
                           and isinstance(worker_reply.get("result"), Mapping),
            "readback": isinstance(readback, Mapping) and isinstance(worker_reply, Mapping)
                        and readback == worker_reply.get("result")
                        and readback.get("executed") is True and isinstance(model_ref, Mapping)
                        and readback.get("model_tag") == model_ref.get("model_tag")
                        and readback.get("source_sha256") == source_sha256
                        and readback.get("entrypoint") == entrypoint,
            "model_snapshots": isinstance(model_ref, Mapping)
                              and isinstance(before, Mapping) and before.get("model_tag") == model_ref.get("model_tag")
                              and isinstance(before.get("fingerprint"), str) and bool(before.get("fingerprint"))
                              and type(before.get("external_event_counter")) is int
                              and isinstance(after, Mapping) and after.get("model_tag") == model_ref.get("model_tag")
                              and isinstance(after.get("fingerprint"), str) and bool(after.get("fingerprint"))
                              and type(after.get("external_event_counter")) is int,
            "outer_execution_identity": isinstance(result_execution, Mapping) and isinstance(model_ref, Mapping)
                                       and result_execution.get("project_id") == context["project_id"]
                                       and result_execution.get("session_id") == model_ref.get("session_id")
                                       and result_execution.get("operation_id") == record.get("operation_id")
                                       and result_execution.get("request_id") == record.get("request_id")
                                       and type(result_execution.get("dirty")) is bool,
            "observed_worker_event_pair": worker_execution_observed,
        }
        if not all(evidence_checks.values()):
            raise PreWriteRefusal("RECIPE_TEST_UNVERIFIED", "test_ref must resolve to a durable same-project Worker execution of this registered source SHA and entrypoint",
                                  details={"test_ref": ref, "scope": "EXECUTION_SMOKE",
                                           "failed_checks": [name for name, valid in evidence_checks.items()
                                                             if not valid]})
        try:
            from ._execution_contract import model_ref_from_mapping
            bound_ref = model_ref_from_mapping(dict(model_ref))
        except Exception as exc:
            raise PreWriteRefusal("RECIPE_TEST_UNVERIFIED", "test_ref has an invalid exact ModelRef",
                                  details={"test_ref": ref}) from exc
        if require_current_worker:
            binding = context["backend"].model_project_binding(bound_ref.as_dict())
            if (binding != {"attribution": "PROJECT_BOUND", "project_id": context["project_id"]}
                    or bound_ref.server_instance_id != context["execution_service"].ledger.server_instance_id
                    or bound_ref.session_id != context["execution_service"].ledger.session_id):
                raise PreWriteRefusal("RECIPE_TEST_UNVERIFIED", "test_ref ModelRef is not bound to this project and attached server",
                                      details={"test_ref": ref})
        runtime = _test_runtime_observation(context, record, expected_version=expected_version,
                                            require_current_worker=require_current_worker)
        if runtime is None:
            raise PreWriteRefusal("RECIPE_TEST_UNVERIFIED", "test_ref has no exact observed runtime identity for the attached Worker",
                                  details={"test_ref": ref, "scope": "EXECUTION_SMOKE"})
        rows.append({"test_ref": ref, "operation_id": record["operation_id"],
                     "source_artifact": source_artifact_id, "source_sha256": source_sha256,
                     "entrypoint": entrypoint, "status": "SUCCEEDED_EXECUTION_SMOKE",
                     "scope": "EXECUTION_SMOKE", "durable": True, **runtime,
                     "model_ref": bound_ref.as_dict()})
    return rows


def _validate_input_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, Mapping) or len(json.dumps(schema, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise PreWriteRefusal("RECIPE_INVALID", "input_schema must be a JSON Schema object no larger than 64 KiB")
    try:
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(dict(schema))
        if schema.get("type") != "object":
            raise ValueError("recipe input schema root type must be object")
        copied = json.loads(json.dumps(dict(schema), sort_keys=True, allow_nan=False))
    except Exception as exc:
        raise PreWriteRefusal("RECIPE_INVALID", "input_schema is not a strict valid Draft 2020-12 object schema",
                              details={"cause_type": type(exc).__name__}) from exc
    return copied


def recipe_register(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"source_artifact", "input_schema", "test_refs", "entrypoint"})
    if "entrypoint" in body and (not isinstance(body["entrypoint"], str) or not body["entrypoint"].strip()
                                 or len(body["entrypoint"]) > 256):
        raise PreWriteRefusal("INVALID_REQUEST", "explicit recipe entrypoint must be a nonempty bounded string")
    _model_state(context, model_tag)
    _require_context(context, "project_root", "project_id", "operation_store", "host_identity",
                     "engine_host_identity", "request_id", "operation_id", "model_ref", "source_revision")
    resolved, _record, description = _resolve_recipe_source(context, body.get("source_artifact"), body.get("entrypoint", ""))
    input_schema = _validate_input_schema(body.get("input_schema"))
    from ._g3_model_delivery import _current_version
    exact_version = _current_version(worker)
    evidence = _validate_test_refs(context, body.get("test_refs"), source_sha256=resolved["sha256"],
                                   source_artifact_id=resolved["artifact_id"],
                                   source_path=resolved["relative_path"], entrypoint=description["entrypoint"],
                                   expected_version=exact_version, require_current_worker=True)
    recipe = {
        "schema": MODEL_RECIPE_SCHEMA,
        "version": 1,
        "runtime": {"comsol_versions": [exact_version]},
        "source": {"artifact_id": resolved["artifact_id"], "sha256": resolved["sha256"],
                   "entrypoint": description["entrypoint"]},
        "input_schema": input_schema,
        "test_refs": [row["test_ref"] for row in evidence],
        "accepted_test_evidence": evidence,
    }
    raw = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MODEL_RECIPE_MAX_BYTES:
        raise PreWriteRefusal("RECIPE_INVALID", "registered recipe exceeds the 256 KiB bound")
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"recipes/{digest}.json"
    from ._artifact_store import ArtifactStore, _hash_regular_artifact, publish_parameter_table_bytes_no_clobber, register_project_artifact
    store = ArtifactStore(Path(context["project_root"]))
    target = store.resolve_safe_path(relative, allow_overwrite=False)
    if target.exists():
        existing_sha, existing_identity = _hash_regular_artifact(target)
        if existing_sha != digest or existing_identity["size"] != len(raw):
            raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "content-addressed recipe filename contains different bytes")
    else:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        publish_parameter_table_bytes_no_clobber(context["project_root"], relative, raw)
    registered = register_project_artifact(
        context["project_root"], context["operation_store"], relative,
        project_id=str(context["project_id"]), role="code_recipe", classification="project_local",
        host_identity=str(context["host_identity"]), engine_host_identity=str(context["engine_host_identity"]),
        request_id=str(context["request_id"]), registering_operation_id=str(context["operation_id"]),
        artifact_type="application/json", format_version=MODEL_RECIPE_FORMAT,
        provenance_fields={"source_artifact_sha256": resolved["sha256"],
                           "test_refs": [row["test_ref"] for row in evidence],
                           "registered_model_ref": context["model_ref"],
                           "registered_source_revision": context["source_revision"]},
    )
    return {"status": "REGISTERED", "recipe_id": registered["artifact_id"],
            "recipe_artifact": {"artifact_id": registered["artifact_id"], "sha256": registered["sha256"],
                                "size_bytes": registered["size"], "role": "code_recipe"},
            "source_artifact": {"artifact_id": resolved["artifact_id"], "sha256": resolved["sha256"],
                                "entrypoint": description["entrypoint"]},
            "runtime": recipe["runtime"], "test_refs": evidence,
            "input_schema_sha256": hashlib.sha256(json.dumps(input_schema, sort_keys=True, separators=(",", ":"),
                                                       allow_nan=False).encode("utf-8")).hexdigest(),
            "immutable": True, "model_ref": context["model_ref"], "source_revision": context["source_revision"]}


def _read_code_recipe(context: Mapping[str, Any], recipe_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = _resolve_registered(context, recipe_id, max_size=MODEL_RECIPE_MAX_BYTES)
    record = context["operation_store"].get_metadata("artifacts", resolved["artifact_id"])
    if (not isinstance(record, Mapping) or record.get("schema_version") != 2
            or record.get("role") != "code_recipe" or record.get("artifact_type") != "application/json"
            or record.get("format_version") != MODEL_RECIPE_FORMAT):
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "recipe_id is not an immutable registered code_recipe artifact")
    handle, before = _open_registered_source(resolved)
    try:
        raw = handle.read(MODEL_RECIPE_MAX_BYTES + 1)
        _check_registered_identity(resolved, before, handle, raw)
    finally:
        handle.close()
    if len(raw) != resolved["size"] or hashlib.sha256(raw).hexdigest() != resolved["sha256"]:
        raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "recipe bytes differ from their registered content address")
    recipe = _strict_json(raw, code="RECIPE_INVALID")
    expected_keys = {"schema", "version", "runtime", "source", "input_schema", "test_refs", "accepted_test_evidence"}
    if set(recipe) != expected_keys or recipe.get("schema") != MODEL_RECIPE_SCHEMA or type(recipe.get("version")) is not int or recipe["version"] != 1:
        raise PreWriteRefusal("RECIPE_VERSION_UNSUPPORTED", "recipe schema, version or fields are unsupported")
    runtime = recipe.get("runtime")
    versions = runtime.get("comsol_versions") if isinstance(runtime, Mapping) else None
    if not isinstance(versions, list) or not versions or len(versions) > 16:
        raise PreWriteRefusal("RECIPE_INVALID", "recipe runtime compatibility declaration is malformed")
    source = recipe.get("source")
    if (not isinstance(source, Mapping) or set(source) != {"artifact_id", "sha256", "entrypoint"}
            or source.get("artifact_id") != source.get("sha256") or not isinstance(source.get("sha256"), str)
            or _SHA256.fullmatch(source["sha256"]) is None or not isinstance(source.get("entrypoint"), str)):
        raise PreWriteRefusal("RECIPE_INVALID", "recipe source identity is malformed")
    _validate_input_schema(recipe.get("input_schema"))
    return recipe, resolved


def recipe_run(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _arguments(arguments, {"recipe_id", "arguments"})
    _service, _ref, state = _model_state(context, model_tag)
    _require_context(context, "project_root", "project_id", "operation_store", "backend", "model_ref",
                     "host_identity", "engine_host_identity", "operation_id", "request_id", "project_authorizer")
    if "trusted_code" not in _service.ledger.permissions:
        raise PreWriteRefusal("PERMISSION_DENIED", "code.recipe_run requires trusted_code permission")
    authorize = context.get("project_authorizer")
    if not callable(authorize):
        raise PreWriteRefusal("PROJECT_AUTHORITY_UNAVAILABLE", "trusted-code project policy is unavailable")
    try:
        authorize(str(context["project_id"]), "trusted_code")
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc
    recipe, recipe_resolved = _read_code_recipe(context, body.get("recipe_id"))
    data = body.get("arguments")
    if not isinstance(data, Mapping):
        raise PreWriteRefusal("INVALID_REQUEST", "arguments must be an object")
    try:
        from jsonschema import Draft202012Validator
        errors = sorted(Draft202012Validator(recipe["input_schema"]).iter_errors(dict(data)),
                        key=lambda item: list(item.absolute_path))
    except Exception as exc:
        raise PreWriteRefusal("RECIPE_INVALID", "stored input_schema cannot be evaluated",
                              details={"cause_type": type(exc).__name__}) from exc
    if errors:
        raise PreWriteRefusal("RECIPE_ARGUMENTS_INVALID", "arguments do not satisfy the registered JSON Schema",
                              details={"validation_errors": [item.message[:240] for item in errors[:16]]})
    from ._g3_model_delivery import _current_version, _parse_version_declaration
    actual_version = _current_version(worker)
    try:
        admitted = {_parse_version_declaration(value) for value in recipe["runtime"]["comsol_versions"]}
        actual = _parse_version_declaration(actual_version)
    except (ValueError, TypeError) as exc:
        raise PreWriteRefusal("RUNTIME_VERSION_UNAVAILABLE", "recipe or attached Worker has no explicit release/build") from exc
    if actual not in admitted:
        raise PreWriteRefusal("RUNTIME_VERSION_MISMATCH", "recipe does not admit this exact COMSOL release/build",
                              details={"actual_comsol_version": actual_version,
                                       "admitted_comsol_versions": recipe["runtime"]["comsol_versions"]})
    source_id = recipe["source"]["artifact_id"]
    source, _source_record, description = _resolve_recipe_source(context, source_id, recipe["source"]["entrypoint"])
    if description["source_sha256"] != recipe["source"]["sha256"] or description["entrypoint"] != recipe["source"]["entrypoint"]:
        raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "recipe source no longer matches the registered source SHA/entrypoint")
    evidence = _validate_test_refs(context, recipe["test_refs"], source_sha256=source["sha256"],
                                   source_artifact_id=source["artifact_id"], source_path=source["relative_path"],
                                   entrypoint=description["entrypoint"], expected_version=actual_version,
                                   require_current_worker=False)
    if evidence != recipe["accepted_test_evidence"]:
        raise PreWriteRefusal("RECIPE_TEST_UNVERIFIED", "registered execution-smoke evidence differs from current durable records")
    model = worker.client().model(model_tag)
    before = worker.backend_snapshot(model_tag)
    try:
        reply = worker.execute_java(model_tag, str(source["path"]), description["entrypoint"], dict(data),
                                    request_id=str(context["operation_id"]))
    except Exception as exc:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "trusted recipe was dispatched; automatic replay is unsafe",
                                     stage="post_dispatch", details={"cause_type": type(exc).__name__,
                                                                      "safe_retry": False, "recipe_id": recipe_resolved["artifact_id"]}) from exc
    after = worker.backend_snapshot(model_tag)
    from ._g2_code import execution_result
    result = execution_result(description=description, worker_reply=reply, before=before, after=after)
    if result.get("success") is not True:
        data_result = result.get("data", {})
        data_result.update({"recipe_id": recipe_resolved["artifact_id"], "safe_retry": False,
                            "model_ref": context["model_ref"], "source_revision": state.revision})
        if result.get("error", {}).get("code") == "EXECUTION_STATE_UNKNOWN":
            data_result["execution_state_unknown"] = True
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "trusted recipe did not produce a verified successful execution",
                                     stage="post_dispatch", details={"source_sha256": description["source_sha256"],
                                                                      "safe_retry": False, "reply": result})
    return {"status": "EXECUTED", "recipe_id": recipe_resolved["artifact_id"],
            "source_artifact": recipe["source"]["artifact_id"], "source_sha256": description["source_sha256"],
            "entrypoint": description["entrypoint"], "runtime": actual_version,
            "worker_result": result["data"], "test_refs": evidence,
            "model_ref": context["model_ref"], "source_revision": state.revision,
            "execution_state_unknown": False, "safe_retry": False}


OPERATIONS = {
    "model.list": list_models,
    "model.create": create_model,
    "model.load": load_model,
    "model.tree": tree,
    "model.save": save_model,
    "model.clone": clone_model,
    "model.close": close_model,
    "code.recipe_register": recipe_register,
    "code.recipe_run": recipe_run,
}
