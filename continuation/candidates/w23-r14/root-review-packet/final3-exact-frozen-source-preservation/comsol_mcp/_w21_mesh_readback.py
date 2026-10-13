"""Managed Worker bridge for bounded current-mesh evidence.

This is an internal stage reader, not a public MCP operation or an admission
producer.  It routes through the ordinary managed ``mesh.inspect`` READ path,
then persists only the primitive's compact hashes/counts and the exact Worker
request identities.  Historical mesh identity, frame, DOF and mapping remain
unverified.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ._execution_contract import ExecutionContractError, model_ref_from_mapping
from ._stage_contract import sha256_json
from ._w21_mesh_evidence import CURRENT_MESH_CAPTURE_ONLY, _validated_snapshot
from ._w21_stage_backend import stage_attempt_binding


MESH_READBACK_CONTRACT = "w21-stage-current-mesh-readback/v1"
_PRE_ATTEMPT_STATES = {"ADMITTED"}
_POST_ATTEMPT_STATES = {"RUNNING", "SUCCEEDED_PARTIAL", "ACCEPTED"}
_MESH_READ_METHODS = {
    "getSDim", "getNumVertex", "getTypes", "getNumElem", "getVertex",
    "getElem", "getElemEntity",
}
_PATH_READ_METHODS = {"component", "mesh", "tags", "geom"}


def _fail(code: str, message: str, *, stage: str = "pre_dispatch") -> None:
    raise ExecutionContractError(code, message, stage=stage)


def _digest(value: Any) -> str:
    try:
        return sha256_json(value)
    except Exception as exc:
        raise ExecutionContractError("MESH_EVIDENCE_NOT_CANONICAL", "mesh artifact is not finite canonical JSON") from exc


def _worker_generation(worker: Any) -> int:
    value = getattr(worker, "generation", None)
    if callable(value):
        value = value()
    if type(value) is not int or value < 1:
        _fail("EXECUTION_STATE_UNKNOWN", "managed Worker generation is unavailable")
    return value


def _request_hash(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _worker_event_record(event: Mapping[str, Any], *, model_ref: Mapping[str, Any],
                         worker_generation: int, child_operation_id: str,
                         operation: str, readphase: str, expected_revision: int) -> dict[str, Any]:
    """Return bounded request evidence without copying mesh arrays from replies."""
    kind = event.get("kind")
    metadata = event.get("metadata")
    request_id = event.get("request_id")
    request_hash = event.get("request_hash")
    phase = event.get("phase")
    if (kind not in {"call", "model", "model_snapshot"}
            or phase not in {"submitted", "observed", "unknown", "unresponsive"}
            or event.get("operation_id") != child_operation_id
            or not isinstance(metadata, Mapping)
            or metadata.get("type") != kind
            or metadata.get("request_id") != request_id
            or not isinstance(request_id, str) or not request_id
            or not _request_hash(request_hash)):
        _fail("WORKER_MESH_BINDING_MISMATCH", "mesh Worker event lacks its exact managed request identity",
              stage="post_dispatch")
    sidecar = event.get("w21_stage_output_binding")
    if (not isinstance(sidecar, Mapping)
            or sidecar.get("model_ref") != dict(model_ref)
            or sidecar.get("model_tag") != model_ref.get("model_tag")
            or sidecar.get("worker_generation") != worker_generation
            or sidecar.get("child_operation_id") != child_operation_id
            or sidecar.get("operation") != operation
            or sidecar.get("readphase") != readphase):
        _fail("WORKER_MESH_BINDING_MISMATCH", "mesh Worker event is not bound to the current attempt read",
              stage="post_dispatch")

    normalized_metadata: dict[str, Any] = {
        "type": kind,
        "request_id": request_id,
    }
    if kind == "call":
        handle = metadata.get("handle")
        generation = metadata.get("generation")
        method = metadata.get("method")
        args = metadata.get("args")
        if (not isinstance(handle, str) or not handle
                or generation != worker_generation
                or not isinstance(method, str) or not method
                or not isinstance(args, list)):
            _fail("WORKER_MESH_BINDING_MISMATCH", "mesh Worker call metadata is incomplete",
                  stage="post_dispatch")
        if method not in _MESH_READ_METHODS | _PATH_READ_METHODS:
            _fail("WORKER_MESH_METHOD_REFUSED", "strict mesh capture emitted a non-read method",
                  stage="post_dispatch")
        normalized_metadata.update({
            "handle": handle,
            "generation": generation,
            "method": method,
            "args": list(args),
        })
    else:
        tag = metadata.get("tag")
        if tag != model_ref.get("model_tag"):
            _fail("WORKER_MESH_BINDING_MISMATCH", "mesh Worker command names a different model",
                  stage="post_dispatch")
        normalized_metadata["tag"] = tag

    reply = event.get("reply")
    reply_summary: dict[str, Any] | None = None
    if isinstance(reply, Mapping):
        reply_summary = {"status": reply.get("status")}
        if "result" in reply:
            reply_summary["result_sha256"] = _digest(reply["result"])
        failure = reply.get("failure")
        if isinstance(failure, Mapping):
            reply_summary["failure_code"] = failure.get("code")
            reply_summary["execution_state_unknown"] = failure.get("execution_state_unknown") is True

    return {
        "phase": phase,
        "request_id": request_id,
        "request_hash": request_hash,
        "kind": kind,
        "operation_id": child_operation_id,
        "status": event.get("status"),
        "metadata": normalized_metadata,
        "reply": reply_summary,
        "w21_stage_output_binding": {
            "model_ref": dict(model_ref), "model_tag": model_ref.get("model_tag"),
            "worker_generation": worker_generation,
            "child_operation_id": child_operation_id,
            "operation": operation, "readphase": readphase,
            "expected_revision": expected_revision,
        },
    }


def _validate_worker_sequence(events: list[dict[str, Any]], *, model_ref: Mapping[str, Any],
                              worker_generation: int, child_operation_id: str,
                              receiver_handle: str, content: Mapping[str, Any]) -> list[dict[str, Any]]:
    requests: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("phase") in {"unknown", "unresponsive"}:
            _fail("EXECUTION_STATE_UNKNOWN", "mesh Worker request outcome is unresolved", stage="post_dispatch")
        requests.setdefault(event["request_id"], []).append(event)
    if not requests:
        _fail("EXECUTION_STATE_UNKNOWN", "mesh snapshot has no captured Worker RPC events", stage="post_dispatch")

    calls: list[dict[str, Any]] = []
    for request_id, rows in requests.items():
        if len(rows) != 2 or {row.get("phase") for row in rows} != {"submitted", "observed"}:
            _fail("EXECUTION_STATE_UNKNOWN", "mesh Worker RPC is missing a unique submit/observe pair",
                  stage="post_dispatch")
        submitted = next(row for row in rows if row["phase"] == "submitted")
        observed = next(row for row in rows if row["phase"] == "observed")
        if (submitted.get("request_hash") != observed.get("request_hash")
                or submitted.get("kind") != observed.get("kind")
                or observed.get("status") != "SUCCEEDED"
                or (isinstance(observed.get("reply"), Mapping)
                    and observed["reply"].get("status") not in (None, "SUCCEEDED"))):
            _fail("EXECUTION_STATE_UNKNOWN", "mesh Worker submit and observed response do not agree",
                  stage="post_dispatch")
        if submitted["kind"] == "call":
            call = submitted["metadata"]
            method = call["method"]
            if method in _MESH_READ_METHODS | {"geom"}:
                if call.get("handle") != receiver_handle or call.get("generation") != worker_generation:
                    _fail("WORKER_MESH_BINDING_MISMATCH", "MeshSequence read used a different receiver or Worker generation",
                          stage="post_dispatch")
            calls.append(call)

    method_rows = [row for row in calls if row.get("handle") == receiver_handle]
    if not any(row.get("method") == "geom" and row.get("args") == [] for row in method_rows):
        _fail("EXECUTION_STATE_UNKNOWN", "resolved MeshSequence.geom() association was not captured",
              stage="post_dispatch")
    for method in ("getSDim", "getNumVertex", "getTypes"):
        if not any(row.get("method") == method for row in method_rows):
            _fail("EXECUTION_STATE_UNKNOWN", f"MeshSequence {method} read was not captured", stage="post_dispatch")

    expected_types = content.get("types")
    vertex_count = content.get("vertex_count")
    if (not isinstance(expected_types, list) or type(vertex_count) is not int or vertex_count < 0):
        _fail("MESH_SNAPSHOT_INVALID", "mesh snapshot content summary is malformed", stage="post_dispatch")
    vertex_ranges: list[tuple[int, int]] = []
    connectivity_ranges: dict[str, list[tuple[int, int]]] = {}
    entity_ranges: dict[str, list[tuple[int, int]]] = {}
    for row in method_rows:
        method, args = row.get("method"), row.get("args")
        if method == "getNumElem" and (len(args) != 1 or not isinstance(args[0], str)):
            _fail("WORKER_MESH_BINDING_MISMATCH", "MeshSequence.getNumElem arguments are malformed",
                  stage="post_dispatch")
        if method == "getVertex":
            if (len(args) != 2 or any(type(item) is not int for item in args)
                    or args[0] < 0 or args[1] < 1 or args[1] > 1024):
                _fail("WORKER_MESH_BINDING_MISMATCH", "MeshSequence.getVertex block arguments are malformed",
                      stage="post_dispatch")
            vertex_ranges.append((args[0], args[0] + args[1]))
        if method in {"getElem", "getElemEntity"}:
            if (len(args) != 3 or not isinstance(args[0], str)
                    or any(type(item) is not int for item in args[1:])
                    or args[1] < 0 or args[2] < 1 or args[2] > 1024):
                _fail("WORKER_MESH_BINDING_MISMATCH", f"MeshSequence.{method} block arguments are malformed",
                      stage="post_dispatch")
            target = connectivity_ranges if method == "getElem" else entity_ranges
            target.setdefault(args[0], []).append((args[1], args[1] + args[2]))

    def exact_coverage(ranges: list[tuple[int, int]], expected: int) -> bool:
        cursor = 0
        for start, end in sorted(ranges):
            if start != cursor or end <= start:
                return False
            cursor = end
        return cursor == expected

    if not exact_coverage(vertex_ranges, vertex_count):
        _fail("EXECUTION_STATE_UNKNOWN", "captured vertex blocks do not cover the complete mesh once",
              stage="post_dispatch")
    expected_names: set[str] = set()
    for row in expected_types:
        if not isinstance(row, Mapping) or not isinstance(row.get("type"), str):
            _fail("MESH_SNAPSHOT_INVALID", "mesh element type summary is malformed", stage="post_dispatch")
        name, count = row["type"], row.get("element_count")
        if name in expected_names or type(count) is not int or count < 0:
            _fail("MESH_SNAPSHOT_INVALID", "mesh element counts are malformed", stage="post_dispatch")
        expected_names.add(name)
        if not exact_coverage(connectivity_ranges.get(name, []), count):
            _fail("EXECUTION_STATE_UNKNOWN", f"captured {name} connectivity blocks are incomplete or duplicated",
                  stage="post_dispatch")
        if not exact_coverage(entity_ranges.get(name, []), count):
            _fail("EXECUTION_STATE_UNKNOWN", f"captured {name} entity blocks are incomplete or duplicated",
                  stage="post_dispatch")
    if set(connectivity_ranges) != expected_names or set(entity_ranges) != expected_names:
        _fail("EXECUTION_STATE_UNKNOWN", "captured element block types differ from the complete mesh summary",
              stage="post_dispatch")
    return [{key: row.get(key) for key in ("request_id", "request_hash", "kind", "phase", "metadata", "reply")}
            for row in events]


def _persist(backend: Any, *, job_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    digest = _digest(dict(payload))
    artifact_id = f"w21-current-mesh:{digest}"
    record = {**dict(payload), "sha256": digest}
    backend.store.persist_artifact(artifact_id, record)
    return {"artifact_id": artifact_id, "sha256": digest, "job_id": job_id}


def produce_stage_mesh_snapshot_readback(backend: Any, *, project_id: str, model_ref: Any,
                                         attempt_id: str, phase: str, model_revision: int,
                                         component: str, mesh: str, geometry: str,
                                         event_callback: Any = None) -> dict[str, Any]:
    """Capture one exact attempt's current MeshSequence through managed READ."""
    if phase not in {"pre-stage", "post-stage"}:
        _fail("INVALID_REQUEST", "mesh snapshot phase must be pre-stage or post-stage")
    for label, value in (("project_id", project_id), ("attempt_id", attempt_id),
                         ("component", component), ("mesh", mesh), ("geometry", geometry)):
        if not isinstance(value, str) or not value.strip():
            _fail("INVALID_REQUEST", f"{label} must be a non-empty string")
    if type(model_revision) is not int or model_revision < 0:
        _fail("REVISION_CONFLICT", "mesh snapshot requires a non-negative observed model revision")
    if backend.service is None or backend.worker is None or backend.store is None:
        _fail("ENGINE_UNRESPONSIVE", "managed service, Worker, and operation store are required")

    try:
        if not isinstance(model_ref, Mapping):
            raise ValueError("full ModelRef required")
        ref = model_ref_from_mapping(dict(model_ref)).as_dict()
    except Exception as exc:
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "mesh snapshot requires a full ModelRef") from exc
    if dict(model_ref) != ref:
        _fail("MODEL_IDENTITY_MISMATCH", "mesh snapshot ModelRef is not canonical")
    if ref["session_id"] != backend.service.ledger.session_id:
        _fail("MODEL_IDENTITY_MISMATCH", "mesh snapshot session differs from the managed service")
    project_binding = backend.model_project_binding(ref)
    if (not isinstance(project_binding, Mapping)
            or project_binding.get("attribution") != "PROJECT_BOUND"
            or project_binding.get("project_id") != project_id):
        _fail("PROJECT_IDENTITY_MISMATCH", "ModelRef is not bound to the requested project")

    state = backend.service.ledger._state_for(model_ref_from_mapping(ref))
    if (state.ref.as_dict() != ref or state.dirty
            or state.external_event_counter != state.observed_external_event_counter
            or state.revision != model_revision):
        _fail("REVISION_CONFLICT", "mesh snapshot must bind the clean current managed model revision")

    attempt = backend.store.get_stage_attempt(project_id, ref, attempt_id)
    if not isinstance(attempt, Mapping):
        _fail("STAGE_ATTEMPT_STATE_UNKNOWN", "mesh snapshot attempt is not durably registered")
    immutable_binding = stage_attempt_binding(attempt)
    if attempt.get("model_ref") != ref or attempt.get("project_id") != project_id:
        _fail("MODEL_IDENTITY_MISMATCH", "durable stage attempt belongs to another project or ModelRef")
    expected_revision = attempt.get("expected_revision")
    if type(expected_revision) is not int or expected_revision < 0:
        _fail("STAGE_ATTEMPT_STATE_UNKNOWN", "durable stage attempt revision is malformed")
    if phase == "pre-stage":
        if attempt.get("status") not in _PRE_ATTEMPT_STATES or attempt.get("engine_dispatched") is not False:
            _fail("STAGE_ATTEMPT_STATE_UNKNOWN", "pre-stage mesh snapshot requires an undispatched ADMITTED attempt")
        if model_revision != expected_revision:
            _fail("REVISION_CONFLICT", "pre-stage mesh revision must equal the immutable attempt revision")
    else:
        if (attempt.get("status") not in _POST_ATTEMPT_STATES
                or attempt.get("engine_dispatched") is not True
                or model_revision < expected_revision):
            _fail("STAGE_ATTEMPT_STATE_UNKNOWN", "post-stage mesh snapshot requires a dispatched non-unknown attempt")

    job = backend.store.operation_job(attempt["operation_id"])
    if not isinstance(job, Mapping) or not isinstance(job.get("job_id"), str) or not job["job_id"]:
        _fail("STAGE_ATTEMPT_STATE_UNKNOWN", "stage attempt has no durable operation job for Worker observations")
    worker_generation = _worker_generation(backend.worker)
    operation = "mesh.inspect"
    readphase = phase
    child_operation_id = f"w21-mesh-{uuid4().hex}"
    child_request_id = f"w21-mesh-request-{uuid4().hex}"
    execution = {
        "project_id": project_id,
        "session_id": ref["session_id"],
        "model_ref": ref,
        "expected_revision": model_revision,
        "request_id": child_request_id,
        "idempotency_key": f"{attempt_id}:mesh:{phase}:{uuid4().hex}",
    }
    path = {"segments": [
        {"collection": "component", "tag": component},
        {"collection": "mesh", "tag": mesh},
    ]}
    snapshot_binding = {
        "project_id": project_id,
        "model_ref": ref,
        "revision": model_revision,
        "attempt_id": attempt_id,
        "phase": phase,
        "component": component,
        "mesh": mesh,
        "geometry": geometry,
    }
    private_mode: dict[str, Any] = {"binding": snapshot_binding, "resolved": None}
    raw_events: list[dict[str, Any]] = []
    compact_events: list[dict[str, Any]] = []
    snapshot: Mapping[str, Any] | None = None
    outcome_status = "FAILED"
    outcome_error: dict[str, Any] | None = None

    def capture(event: Any) -> None:
        if not isinstance(event, Mapping):
            _fail("EXECUTION_STATE_UNKNOWN", "managed mesh read emitted a malformed Worker event",
                  stage="post_dispatch")
        event_copy = dict(event)
        event_copy["w21_stage_output_binding"] = {
            "model_ref": ref, "model_tag": ref["model_tag"],
            "worker_generation": worker_generation,
            "child_operation_id": child_operation_id,
            "operation": operation, "readphase": readphase,
        }
        raw_events.append(event_copy)
        compact = _worker_event_record(
            event_copy, model_ref=ref, worker_generation=worker_generation,
            child_operation_id=child_operation_id, operation=operation,
            readphase=readphase, expected_revision=model_revision,
        )
        compact_events.append(compact)
        if callable(event_callback):
            event_callback(event_copy, readphase=readphase, operation=operation,
                           child_operation_id=child_operation_id,
                           expected_revision=model_revision)
        else:
            # The Worker callback is synchronous: submitted evidence reaches
            # the existing job event store before the request is sent.
            backend.store.add_event(job["job_id"], "worker_request", event_copy)

    mode_context = getattr(backend, "_stage_mesh_snapshot_context", None)
    if mode_context is None:
        _fail("EXECUTION_STATE_UNKNOWN", "managed backend lacks its private mesh snapshot context")
    if not callable(getattr(backend.worker, "operation_context", None)):
        _fail("EXECUTION_STATE_UNKNOWN", "managed Worker cannot bind mesh READ requests to the child operation")
    mode_token = mode_context.set(private_mode)
    try:
        # G3 READ dispatches do not use the G2 ``invoke`` wrapper that normally
        # installs this Worker context. Bind here so each actual Worker RPC
        # emits submitted/observed (or UNKNOWN) evidence under the child
        # operation id before and after transport.
        with backend.context(child_operation_id, capture):
            reply = backend.invoke(operation, {"path": path}, execution, child_operation_id, capture)
    except Exception as exc:
        outcome_status = "UNKNOWN" if (
            any(item.get("phase") in {"unknown", "unresponsive"} for item in compact_events)
            or getattr(exc, "code", None) == "EXECUTION_STATE_UNKNOWN"
            or getattr(exc, "execution_state_unknown", False)
        ) else "FAILED"
        outcome_error = {"code": getattr(exc, "code", type(exc).__name__), "type": type(exc).__name__}
        if outcome_status == "UNKNOWN":
            # ExecutionService cannot freeze this READ through its normal
            # envelope path when the managed callback raises before returning.
            # Preserve the unresolved Worker outcome in the ledger so later
            # dependent writes fail closed; a clean validation/identity error
            # remains non-dirty.
            backend.service._freeze_after_unknown_read(
                model_ref_from_mapping(ref),
                {"success": False, "execution_state_unknown": True, "error": outcome_error},
            )
        raise
    finally:
        mode_context.reset(mode_token)

    if not isinstance(reply, Mapping):
        outcome_status = "UNKNOWN"
        outcome_error = {
            "code": "EXECUTION_STATE_UNKNOWN",
            "type": "non_mapping_managed_reply",
        }
        # A malformed return can hide whether the managed callback completed.
        # Freeze the bound model and persist this as UNKNOWN, never as a clean
        # FAILED capture. The service's ordinary READ outcome hook only runs
        # when its callback returns an envelope.
        backend.service._freeze_after_unknown_read(
            model_ref_from_mapping(ref),
            {"success": False, "execution_state_unknown": True, "error": outcome_error},
        )
        try:
            _persist(backend, job_id=job["job_id"], payload={
                "schema": MESH_READBACK_CONTRACT, "capture_status": outcome_status,
                "claim_scope": CURRENT_MESH_CAPTURE_ONLY,
                "project_id": project_id, "model_ref": ref,
                "attempt_binding": immutable_binding, "attempt_status": attempt.get("status"),
                "observed_revision": model_revision, "phase": phase,
                "child_operation_id": child_operation_id, "child_request_id": child_request_id,
                "worker_generation": worker_generation, "worker_requests": compact_events,
                "error": outcome_error,
            })
        finally:
            _fail(str(outcome_error.get("code") or "EXECUTION_STATE_UNKNOWN"),
                  "managed mesh.inspect did not complete with a bound successful READ result",
                  stage="post_dispatch")

    if reply.get("success") is not True or not isinstance(reply.get("execution"), Mapping):
        error = reply.get("error")
        outcome_error = {
            "code": error.get("code") if isinstance(error, Mapping) else "EXECUTION_STATE_UNKNOWN",
            "type": "managed_mesh_inspect_failure",
        }
        outcome_status = "UNKNOWN" if (
            reply.get("execution_state_unknown") is True
            or outcome_error.get("code") == "EXECUTION_STATE_UNKNOWN"
            or any(item.get("phase") in {"unknown", "unresponsive"} for item in compact_events)
        ) else "FAILED"
        try:
            _persist(backend, job_id=job["job_id"], payload={
                "schema": MESH_READBACK_CONTRACT, "capture_status": outcome_status,
                "claim_scope": CURRENT_MESH_CAPTURE_ONLY,
                "project_id": project_id, "model_ref": ref,
                "attempt_binding": immutable_binding, "attempt_status": attempt.get("status"),
                "observed_revision": model_revision, "phase": phase,
                "child_operation_id": child_operation_id, "child_request_id": child_request_id,
                "worker_generation": worker_generation, "worker_requests": compact_events,
                "error": outcome_error,
            })
        finally:
            _fail(str(outcome_error.get("code") or "EXECUTION_STATE_UNKNOWN"),
                  "managed mesh.inspect did not complete with a bound successful READ result",
                  stage="post_dispatch")

    execution_result = reply["execution"]
    detail = reply.get("data")
    if isinstance(detail, Mapping) and isinstance(detail.get("data"), Mapping):
        # ExecutionService's G3 domain envelope has a nested operation data
        # member; preserve only that exact published layer.
        detail = detail["data"]
    if (not isinstance(detail, Mapping)
            or detail.get("kind") != "mesh_sequence_current_snapshot"
            or detail.get("status") != "SUCCEEDED"
            or detail.get("capture_status") != CURRENT_MESH_CAPTURE_ONLY
            or not isinstance(detail.get("mesh_snapshot"), Mapping)):
        _fail("EXECUTION_STATE_UNKNOWN", "mesh.inspect returned no current-only complete snapshot", stage="post_dispatch")
    if (execution_result.get("model_ref") != ref
            or execution_result.get("session_id") != ref["session_id"]
            or execution_result.get("revision") != model_revision
            or execution_result.get("dirty") is not False
            or execution_result.get("project_id") != project_id
            or execution_result.get("request_id") != child_request_id
            or execution_result.get("operation_id") != child_operation_id
            or execution_result.get("request_hash_status") != "NOT_APPLICABLE_READ_NO_WRITE_TICKET"
            or "request_hash" in execution_result):
        _fail("MODEL_IDENTITY_MISMATCH", "mesh.inspect execution envelope changed the model/project/revision binding",
              stage="post_dispatch")
    final_project = backend.model_project_binding(ref)
    state_after = backend.service.ledger._state_for(model_ref_from_mapping(ref))
    if (not isinstance(final_project, Mapping)
            or final_project.get("project_id") != project_id
            or final_project.get("attribution") != "PROJECT_BOUND"
            or state_after.ref.as_dict() != ref
            or state_after.revision != model_revision
            or state_after.dirty
            or state_after.external_event_counter != state_after.observed_external_event_counter):
        _fail("MODEL_IDENTITY_MISMATCH", "managed project/model state changed during the mesh READ", stage="post_dispatch")

    resolved = private_mode.get("resolved")
    if (not isinstance(resolved, Mapping)
            or resolved.get("component") != component
            or resolved.get("mesh") != mesh
            or resolved.get("geometry") != geometry
            or not isinstance(resolved.get("receiver_handle"), str)
            or not resolved.get("receiver_handle")):
        _fail("WORKER_MESH_BINDING_MISMATCH", "strict mesh reader did not return its actual resolved sequence identity",
              stage="post_dispatch")
    snapshot = detail["mesh_snapshot"]
    worker_requests = _validate_worker_sequence(
        compact_events, model_ref=ref, worker_generation=worker_generation,
        child_operation_id=child_operation_id, receiver_handle=resolved["receiver_handle"],
        content=snapshot.get("content") if isinstance(snapshot.get("content"), Mapping) else {},
    )
    try:
        validated_binding, validated_content = _validated_snapshot(snapshot)
    except Exception as exc:
        raise ExecutionContractError(
            getattr(exc, "code", "MESH_SNAPSHOT_INVALID"),
            "mesh.inspect snapshot failed complete primitive validation",
            stage="post_dispatch",
        ) from exc
    if (not isinstance(snapshot, Mapping)
            or snapshot.get("binding") != snapshot_binding
            or validated_binding != snapshot_binding
            or dict(validated_content) != dict(snapshot.get("content", {}))):
        _fail("WORKER_MESH_BINDING_MISMATCH",
              "mesh.inspect snapshot is not bound to the exact current stage attempt",
              stage="post_dispatch")
    if _worker_generation(backend.worker) != worker_generation:
        _fail("EXECUTION_STATE_UNKNOWN", "Worker generation changed during current mesh capture", stage="post_dispatch")
    outcome_status = "CAPTURED"

    payload = {
        "schema": MESH_READBACK_CONTRACT,
        "capture_status": outcome_status,
        "claim_scope": CURRENT_MESH_CAPTURE_ONLY,
        "historical_mesh": "UNVERIFIED",
        "source_target_mapping": "UNVERIFIED",
        "frame_identity": "UNVERIFIED",
        "dof_identity": "UNVERIFIED",
        "project_id": project_id,
        "model_ref": ref,
        "attempt_binding": immutable_binding,
        "attempt_status": attempt.get("status"),
        "observed_revision": model_revision,
        "phase": phase,
        "child_operation_id": child_operation_id,
        "child_request_id": child_request_id,
        "managed_execution": {
            "model_ref": ref, "session_id": ref["session_id"],
            "project_id": project_id, "revision": model_revision,
            "request_id": child_request_id,
            "operation_id": child_operation_id,
            "request_hash_scope": "NOT_APPLICABLE_READ_NO_WRITE_TICKET",
            "revision_witness": "service-inspect-no-ticket",
        },
        "worker_generation": worker_generation,
        "resolved_mesh_sequence": {
            "path": resolved.get("path"), "component": component, "mesh": mesh,
            "geometry": geometry, "receiver_handle": resolved["receiver_handle"],
        },
        "worker_requests": worker_requests,
        "mesh_snapshot": dict(snapshot),
    }
    artifact_ref = _persist(backend, job_id=job["job_id"], payload=payload)
    return {
        "contract": MESH_READBACK_CONTRACT,
        "producer": "managed-backend-current-mesh-readback",
        "status": CURRENT_MESH_CAPTURE_ONLY,
        "binding": snapshot_binding,
        "attempt_binding": immutable_binding,
        "model_revision": model_revision,
        "worker_generation": worker_generation,
        "artifact_ref": artifact_ref,
        "mesh_snapshot": dict(snapshot),
        "historical_mesh": "UNVERIFIED",
        "source_target_mapping": "UNVERIFIED",
        "frame_identity": "UNVERIFIED",
        "dof_identity": "UNVERIFIED",
    }
