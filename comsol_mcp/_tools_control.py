"""Persistent execution status tools; these never need an idle COMSOL server."""
from __future__ import annotations

from comsol_mcp._control_client import dispatch


def session_health() -> dict:
    """Read cached service/worker liveness without entering COMSOL."""
    return dispatch("session_health", {}, {})


def model_inspect(refresh: bool = False) -> dict:
    """Inspect bound model identity/revision; refresh explicitly reconciles observed changes."""
    return dispatch("model_inspect", {"refresh": refresh}, {})


def model_adopt(model_tag: str) -> dict:
    """Bind an existing server model by tag without loading, creating or removing a model."""
    return dispatch("model_adopt", {"model_tag": model_tag}, {})


def job_list(offset: int = 0, limit: int = 50, status: str | None = None, project_id: str | None = None) -> dict:
    """List and filter durable jobs with bounded pagination."""
    payload: dict = {"offset": offset, "limit": limit}
    if status is not None:
        payload["status"] = status
    if project_id is not None:
        payload["project_id"] = project_id
    return dispatch("job_list", payload, {})


def job_status(job_id: str) -> dict:
    """Read durable job status without contacting the engine or resubmitting work."""
    return dispatch("job_status", {"job_id": job_id}, {})


def job_log(job_id: str, offset: int = 0, limit: int = 100) -> dict:
    """Read a bounded page of persisted job events, including observed engine logs."""
    return dispatch("job_log", {"job_id": job_id, "offset": offset, "limit": limit}, {})


def job_result(job_id: str) -> dict:
    """Retrieve the original job result; never starts a replacement computation."""
    return dispatch("job_result", {"job_id": job_id}, {})


def job_wait(job_id: str, timeout_s: float | None = 30.0, poll_interval_s: float = 0.05) -> dict:
    """Wait for a job to reach a terminal state without blocking the engine queue."""
    return dispatch("job_wait", {"job_id": job_id, "timeout_s": timeout_s, "poll_interval_s": poll_interval_s}, {})


def job_cancel(job_id: str, reason: str = "cancelled by user", force_stop: bool = False, server_scope: dict | None = None) -> dict:
    """Request cancellation of a queued or running job."""
    payload: dict = {"job_id": job_id, "reason": reason, "force_stop": force_stop}
    if server_scope is not None:
        payload["server_scope"] = server_scope
    return dispatch("job_cancel", payload, {})


def job_reconcile(job_id: str) -> dict:
    """Reconcile the original job with worker evidence without replaying its mutations."""
    return dispatch("job_reconcile", {"job_id": job_id}, {})


def register(registry) -> None:
    for function in (
        session_health, model_inspect, model_adopt,
        job_list, job_status, job_log, job_result, job_wait, job_cancel, job_reconcile,
    ):
        registry.add_tool(function)
