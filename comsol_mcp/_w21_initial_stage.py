"""Private managed preflight for the registered W21 initial-state stage."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ._execution_contract import ExecutionContractError, model_ref_from_mapping
from ._stage_contract import sha256_json
from ._w21_stage_backend import (
    ADMISSION_CONTRACT, initial_stage_managed_action_plan,
    is_registered_initial_state_stage, is_w21_initial_stage_runner_profile,
)


def produce_initial_stage_preflight(
    backend: Any, *, binding: Mapping[str, Any], plan: Mapping[str, Any], stage: Mapping[str, Any],
    event_callback: Any = None, authorize_callback: Any = None,
) -> dict[str, Any]:
    """Read the registered target Study through a managed READ ticket.

    Solver, Variables, dataset, solution tuple, and mesh metadata may be
    created by the first Study.run.  This preflight intentionally proves only
    the facts available before that run and never tries to synthesize them.
    """
    if not is_registered_initial_state_stage(plan, stage):
        raise ExecutionContractError(
            "STAGE_PROFILE_UNVERIFIED",
            "the stored plan row is not the unique first initial-state stage",
            stage="validation",
        )
    ref = binding.get("model_ref") if isinstance(binding, Mapping) else None
    project_id = binding.get("project_id") if isinstance(binding, Mapping) else None
    if (not isinstance(ref, Mapping) or not isinstance(project_id, str) or not project_id
            or not isinstance(binding.get("attempt_id"), str) or not binding.get("attempt_id")
            or type(binding.get("expected_revision")) is not int):
        raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "initial-stage preflight has no exact attempt binding")
    if backend.service is None or backend.worker is None or backend.store is None:
        raise ExecutionContractError("ENGINE_UNRESPONSIVE", "managed Worker/service/store is unavailable")
    model_ref = model_ref_from_mapping(dict(ref))
    state = backend.service.ledger._state_for(model_ref)
    expected_revision = binding["expected_revision"]
    if state.dirty or state.revision != expected_revision:
        raise ExecutionContractError("REVISION_CONFLICT", "initial-stage preflight revision is not the clean attempt revision")
    project_binding = backend.model_project_binding(dict(ref))
    if (not isinstance(project_binding, Mapping)
            or project_binding.get("attribution") != "PROJECT_BOUND"
            or project_binding.get("project_id") != project_id):
        raise ExecutionContractError("PROJECT_IDENTITY_MISMATCH", "initial-stage ModelRef is not bound to this project")

    segments = stage.get("study_target", {}).get("segments") if isinstance(stage.get("study_target"), Mapping) else None
    if (not isinstance(segments, list) or len(segments) != 1
            or not isinstance(segments[0], Mapping)
            or segments[0].get("collection") != "study"
            or not isinstance(segments[0].get("tag"), str) or not segments[0]["tag"]):
        raise ExecutionContractError("STAGE_TARGET_UNSUPPORTED", "initial stage must name one explicit Study tag")
    study_tag = segments[0]["tag"]
    action_plan = initial_stage_managed_action_plan(plan, stage)
    if not isinstance(action_plan, list) or not action_plan or action_plan[0] != "study.inspect:preflight":
        raise ExecutionContractError("STAGE_PROFILE_UNVERIFIED", "initial managed-action plan is incomplete", stage="pre_dispatch")
    runner_profile = is_w21_initial_stage_runner_profile(plan, stage)
    action_cap = 12 if runner_profile else len(action_plan)
    # Check the first observation budget before its managed Worker dispatch.
    if action_cap < 1 or len(action_plan) > action_cap:
        raise ExecutionContractError("STAGE_PROFILE_UNVERIFIED", "initial managed-action budget exceeds its frozen cap", stage="pre_dispatch")

    worker_generation = getattr(backend.worker, "generation", None)
    if callable(worker_generation):
        worker_generation = worker_generation()
    if type(worker_generation) is not int or worker_generation < 1:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "initial-stage Worker generation is unavailable")

    child_operation_id = f"w21-initial-preflight-{uuid4().hex}"
    child_request_id = f"w21-initial-preflight-request-{uuid4().hex}"
    readphase = "initial-stage-study-preflight"
    operation = "study.inspect"
    execution = {
        "project_id": project_id, "session_id": ref.get("session_id"), "model_ref": dict(ref),
        "expected_revision": expected_revision, "request_id": child_request_id,
        "idempotency_key": f"{binding['attempt_id']}:initial-preflight",
    }
    arguments = {"path": {"segments": [{"collection": "study", "tag": study_tag}]}}
    if callable(authorize_callback):
        authorize_callback(operation, dict(arguments), execution)

    events: list[dict[str, Any]] = []

    def capture(event: Any) -> None:
        if not isinstance(event, Mapping):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "initial-stage Worker event is malformed", stage="post_dispatch")
        copied = dict(event)
        copied["w21_stage_output_binding"] = {
            "model_ref": dict(ref), "model_tag": ref.get("model_tag"),
            "worker_generation": worker_generation,
            "child_operation_id": child_operation_id,
            "operation": operation, "readphase": readphase,
        }
        events.append(copied)
        if copied.get("operation_id") != child_operation_id:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "initial preflight Worker event has a different operation id", stage="post_dispatch")
        if callable(event_callback):
            event_callback(copied, readphase=readphase, operation=operation,
                           child_operation_id=child_operation_id,
                           expected_revision=expected_revision)

    reply = backend.invoke(operation, arguments, execution, child_operation_id, capture)
    if not isinstance(reply, Mapping) or reply.get("success") is not True:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "managed study.inspect preflight did not complete")
    data = reply.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("data"), Mapping):
        data = data["data"]
    if not isinstance(data, Mapping) or data.get("study") != study_tag:
        raise ExecutionContractError("STAGE_PROFILE_UNVERIFIED", "study.inspect did not resolve the exact registered Study")

    managed = reply.get("execution")
    now = backend.service.ledger._state_for(model_ref)
    if (not isinstance(managed, Mapping) or managed.get("model_ref") != dict(ref)
            or managed.get("session_id") != ref.get("session_id")
            or managed.get("project_id") not in (None, project_id)
            or managed.get("revision") != expected_revision or now.revision != expected_revision
            or now.dirty or managed.get("dirty") is True):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "initial preflight changed or lost the managed model binding")

    worker_requests: list[dict[str, Any]] = []
    for event in events:
        if event.get("phase") != "submitted":
            continue
        metadata = event.get("metadata")
        sidecar = event.get("w21_stage_output_binding")
        request_id, request_hash = event.get("request_id"), event.get("request_hash")
        kind = event.get("kind")
        if (not isinstance(metadata, Mapping)
                or not isinstance(request_id, str) or not request_id
                or not isinstance(request_hash, str) or len(request_hash) != 64
                or any(char not in "0123456789abcdef" for char in request_hash)
                or not isinstance(sidecar, Mapping)
                or sidecar.get("model_ref") != dict(ref)
                or sidecar.get("worker_generation") != worker_generation
                or sidecar.get("child_operation_id") != child_operation_id
                or sidecar.get("operation") != operation
                or sidecar.get("readphase") != readphase
                or kind not in {"call", "model", "model_snapshot"}
                or metadata.get("type") != kind or metadata.get("request_id") != request_id
                or (kind == "call" and (
                    not isinstance(metadata.get("handle"), str) or not metadata.get("handle")
                    or metadata.get("generation") != worker_generation
                    or not isinstance(metadata.get("method"), str)
                    or not isinstance(metadata.get("args"), list)))
                or (kind in {"model", "model_snapshot"} and metadata.get("tag") != ref.get("model_tag"))):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "initial preflight Worker request is not exactly bound", stage="post_dispatch")
        worker_requests.append({
            "worker_request_id": request_id, "worker_request_hash": request_hash,
            "worker_kind": kind,
            "worker_model_tag": metadata.get("tag") if kind in {"model", "model_snapshot"} else None,
            "worker_receiver": metadata.get("handle") if kind == "call" else None,
            "worker_method": metadata.get("method") if kind == "call" else None,
            "worker_generation": worker_generation, "phase": "submitted",
        })
    if not worker_requests:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "study.inspect returned without a submitted Worker read")

    revision_chain = [{
        "operation": operation, "readphase": readphase,
        "child_operation_id": child_operation_id, "child_request_id": child_request_id,
        "request_id": managed.get("request_id"), "managed_operation_id": managed.get("operation_id"),
        "managed_request_id": managed.get("request_id"), "request_hash": managed.get("request_hash"),
        "revision_witness": "service-inspect-no-ticket",
        "expected_revision": expected_revision, "revision": expected_revision,
        "effect": "READ", "worker_requests": worker_requests,
    }]
    record_payload = {
        "schema_version": 1, "kind": "w21_initial_stage_preflight",
        "binding": dict(binding), "plan_id": plan.get("plan_id"),
        "plan_sha256": plan.get("sha256"), "stage_id": stage.get("stage_id"),
        "study_tag": study_tag, "study_readback": dict(data),
        "managed_read": {"model_ref": dict(ref), "project_id": project_id,
                         "revision": expected_revision, "request_id": child_request_id,
                         "operation_id": child_operation_id},
        "worker_generation": worker_generation, "worker_requests": worker_requests,
        "revision_chain": revision_chain,
        "managed_internal_action_plan": action_plan,
        "internal_read_cap": 12 if runner_profile else None,
    }
    artifact_hash = sha256_json(record_payload)
    artifact_id = f"w21-initial-stage-preflight:{binding['attempt_id']}"
    backend.store.persist_artifact(artifact_id, {**record_payload, "sha256": artifact_hash})
    return {
        "contract": ADMISSION_CONTRACT,
        "producer": "managed-backend-initial-stage-preflight",
        "status": "READY_FOR_INITIAL_STATE_SOLVE",
        "initial_state_route": "registered_first_stage",
        "binding": dict(binding),
        "facts": {
            "study_target_binding": "VERIFIED",
            "source_attempt_binding": "NOT_APPLICABLE",
            "target_field_identity": "POST_SOLVE_REQUIRED",
            "source_target_units": "POST_SOLVE_REQUIRED",
            "source_target_mesh": "POST_SOLVE_REQUIRED",
            "frame_identity": "POST_SOLVE_REQUIRED",
            "history_identity": "NOT_APPLICABLE",
        },
        "study_readback": {"study": study_tag, "path": data.get("path"),
                            "status": "VERIFIED", "source": "managed study.inspect"},
        "revision_chain": revision_chain,
        "managed_internal_action_plan": action_plan,
        "planned_internal_read_count": len(action_plan),
        "internal_read_cap": 12 if runner_profile else None,
        "evidence_refs": [{"artifact_id": artifact_id, "sha256": artifact_hash}],
    }


__all__ = ["produce_initial_stage_preflight"]
