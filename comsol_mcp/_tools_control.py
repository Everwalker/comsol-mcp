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


def job_status(job_id: str) -> dict:
    """Read durable job status without contacting the engine or resubmitting work."""
    return dispatch("job_status", {"job_id": job_id}, {})


def job_log(job_id: str, offset: int = 0, limit: int = 100) -> dict:
    """Read a bounded page of persisted job events, including observed engine logs."""
    return dispatch("job_log", {"job_id": job_id, "offset": offset, "limit": limit}, {})


def job_result(job_id: str) -> dict:
    """Retrieve the original job result; never starts a replacement computation."""
    return dispatch("job_result", {"job_id": job_id}, {})


def job_reconcile(job_id: str) -> dict:
    """Reconcile the original job with worker evidence without replaying its mutations."""
    return dispatch("job_reconcile", {"job_id": job_id}, {})


def register(registry) -> None:
    for function in (session_health, model_inspect, model_adopt, job_status, job_log, job_result, job_reconcile):
        registry.add_tool(function)
