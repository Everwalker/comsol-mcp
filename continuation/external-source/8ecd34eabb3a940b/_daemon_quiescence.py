"""Conservative readback for retiring the current daemon Worker.

OperationStore is the durable source of queued/running/unknown work and direct
Worker request observations.  This module intentionally avoids executor private
fields, and it treats every project handled by the daemon's current single
ManagedBackend Worker as part of the Worker shutdown scope.
"""
from __future__ import annotations

from typing import Any, Mapping


_TERMINAL_JOBS = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"}
_ACTIVE_JOBS = {"QUEUED", "RUNNING"}
_UNKNOWN_JOBS = {"UNKNOWN", "RECONCILING"}
_TERMINAL_WORKER = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
_PAGE_SIZE = 1000
_MAX_ROWS = 1_000_000


def _unknown(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "success": False,
        "data": {
            "schema_version": 1,
            "status": "UNKNOWN",
            "worker_status": "UNKNOWN",
            "server_lane_status": "UNOWNED_OR_UNKNOWN",
            "safe_to_retire_worker": False,
            "safe_to_stop_server": False,
        },
        "error": {"code": code, "message": message, **details},
    }


def _all_jobs(store: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    while True:
        page = store.list_jobs(offset=len(result), limit=_PAGE_SIZE)
        result.extend(page)
        if len(result) >= page.total:
            return result
        if not page or len(result) > _MAX_ROWS:
            raise RuntimeError("durable job inventory pagination did not complete")


def _binding_fields(job: Mapping[str, Any]) -> tuple[Any, Any]:
    metadata = job.get("metadata")
    if not isinstance(metadata, Mapping):
        return None, None
    execution = metadata.get("execution")
    if not isinstance(execution, Mapping):
        execution = {}
    return execution.get("project_id", metadata.get("project_id")), execution.get("session_id", metadata.get("session_id"))


def _request_inventory(store: Any, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    submitted: dict[tuple[str, str], int] = {}
    observed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    ambiguous: list[dict[str, str]] = []
    unknown_outcome = 0

    for job in jobs:
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            ambiguous.append({"job_id": str(job_id), "reason": "missing_job_id"})
            continue
        offset = 0
        while True:
            events = store.events(job_id, offset=offset, limit=_PAGE_SIZE)
            for event in events:
                if event.get("event") != "worker_request":
                    continue
                metadata = event.get("metadata")
                if not isinstance(metadata, dict):
                    ambiguous.append({"job_id": job_id, "reason": "metadata_not_object"})
                    continue
                request_id = metadata.get("request_id")
                phase = metadata.get("phase")
                if not isinstance(request_id, str) or not request_id:
                    ambiguous.append({"job_id": job_id, "reason": "missing_request_id"})
                    continue
                key = (job_id, request_id)
                if phase == "submitted":
                    submitted[key] = submitted.get(key, 0) + 1
                elif phase == "observed":
                    reply = metadata.get("reply") if isinstance(metadata.get("reply"), dict) else {}
                    failure = reply.get("failure") if isinstance(reply.get("failure"), dict) else {}
                    status = reply.get("status", metadata.get("status"))
                    unknown = failure.get("execution_state_unknown") is True
                    observed.setdefault(key, []).append({"status": status, "unknown": unknown})
                    unknown_outcome += int(unknown)
                elif phase in {"unknown", "unresponsive"}:
                    unknown_outcome += 1
                else:
                    ambiguous.append({"job_id": job_id, "reason": "unrecognized_worker_event_phase"})
            if len(events) < _PAGE_SIZE:
                break
            offset += len(events)
            if offset > _MAX_ROWS:
                raise RuntimeError("Worker request event inventory exceeded its safe scan limit")

    pending = [key for key in submitted if key not in observed]
    orphan = [key for key in observed if key not in submitted]
    duplicate_submitted = [key for key, count in submitted.items() if count != 1]
    duplicate_observed = [key for key, values in observed.items() if len(values) != 1]
    nonterminal = [key for key, values in observed.items()
                   if any(value["status"] not in _TERMINAL_WORKER for value in values)]
    safe = not (pending or orphan or duplicate_submitted or duplicate_observed
                or nonterminal or ambiguous or unknown_outcome)
    return {
        "submitted": sum(submitted.values()),
        "observed": sum(len(values) for values in observed.values()),
        "pending": len(pending),
        "orphan_observed": len(orphan),
        "nonterminal_observed": len(nonterminal),
        "unknown_outcome": unknown_outcome,
        "ambiguous": len(ambiguous),
        "pending_keys": [list(key) for key in sorted(pending)],
        "orphan_keys": [list(key) for key in sorted(orphan)],
        "duplicate_submitted_keys": [list(key) for key in sorted(duplicate_submitted)],
        "duplicate_observed_keys": [list(key) for key in sorted(duplicate_observed)],
        "nonterminal_keys": [list(key) for key in sorted(nonterminal)],
        "ambiguous_events": ambiguous,
        "safe_to_cleanup": safe,
        "request_keys_scanned": len(set(submitted) | set(observed)),
    }


def control_daemon_quiescence_readback(daemon: Any, project_id: str, session_id: str) -> dict[str, Any]:
    """Return exact current-Worker status with cross-project durable coverage.

    All accepted engine operations in this daemon currently route through the
    same ManagedBackend/Worker. Thus the requested project/session is an
    attribution view; Worker retirement uses the union over the full SQLite
    ledger. Process-level COMSOL server identity is not held here and remains
    explicitly unverified.
    """
    if not isinstance(project_id, str) or not project_id or not isinstance(session_id, str) or not session_id:
        return _unknown("INVALID_BINDING", "project_id and session_id are required")
    if daemon.closed.is_set():
        return _unknown("DAEMON_CLOSED", "daemon is closing or closed")
    try:
        project = daemon.project_authority.get_project(project_id)
    except Exception:
        return _unknown("PROJECT_BINDING_UNKNOWN", "project authority could not verify the requested project")

    with daemon.lock:
        generation_before = daemon._activity_generation
        worker = daemon.backend.worker
        identity = dict(daemon.backend.worker_identity or {})
        service = daemon.backend.service
        bound_session_id = getattr(getattr(service, "ledger", None), "session_id", None)
    if worker is None or service is None or bound_session_id != session_id:
        return _unknown("WORKER_BINDING_UNAVAILABLE", "requested session is not bound to the live daemon Worker",
                        project_id=project_id, session_id=session_id,
                        project_workspace=project.get("workspace"))
    instance_id = identity.get("worker_instance_id")
    epoch = identity.get("connection_epoch")
    if not isinstance(instance_id, str) or not instance_id or type(epoch) is not int or epoch < 1:
        return _unknown("WORKER_IDENTITY_UNKNOWN", "live Worker instance and connection epoch are unavailable",
                        project_id=project_id, session_id=session_id)

    try:
        jobs = _all_jobs(daemon.store)
        requests = _request_inventory(daemon.store, jobs)
    except Exception as exc:
        return _unknown("DURABLE_INVENTORY_UNKNOWN", "durable job or Worker-request inventory failed",
                        project_id=project_id, session_id=session_id, cause_type=type(exc).__name__)

    target_rows = [row for row in jobs if _binding_fields(row) == (project_id, session_id)]
    worker_counts = {state: sum(str(row.get("status", "")).upper() == state for row in jobs)
                     for state in ("QUEUED", "RUNNING", "UNKNOWN", "RECONCILING")}
    session_counts = {state: sum(str(row.get("status", "")).upper() == state for row in target_rows)
                      for state in ("QUEUED", "RUNNING", "UNKNOWN", "RECONCILING")}
    unclassified = [row for row in jobs if str(row.get("status", "")).upper()
                    not in _TERMINAL_JOBS | _ACTIVE_JOBS | _UNKNOWN_JOBS]

    with daemon.lock:
        stable = (
            generation_before == daemon._activity_generation
            and daemon.backend.worker is worker
            and daemon.backend.worker_identity == identity
            and getattr(getattr(daemon.backend.service, "ledger", None), "session_id", None) == bound_session_id
            and not daemon.closed.is_set()
        )

    active = bool(worker_counts["QUEUED"] or worker_counts["RUNNING"])
    durable_unknown = bool(worker_counts["UNKNOWN"] or worker_counts["RECONCILING"] or unclassified)
    # A submitted Worker request with a durable QUEUED/RUNNING job is positive
    # evidence of busy work, not an unknown outcome. It still blocks retirement
    # through ``safe_to_cleanup``. A pending request without an active durable
    # job, or contradictory/ambiguous event history, remains UNKNOWN.
    request_unknown = bool(
        requests["orphan_observed"]
        or requests["duplicate_submitted_keys"]
        or requests["duplicate_observed_keys"]
        or requests["nonterminal_observed"]
        or requests["unknown_outcome"]
        or requests["ambiguous"]
        or (requests["pending"] and not active)
    )
    worker_status = "UNKNOWN" if not stable or durable_unknown or request_unknown else (
        "BUSY" if active or requests["pending"] else "QUIESCENT")
    # This daemon has no trusted COMSOL OS-process/listener identity. The
    # separate runner must prove those facts before it can stop an owned server.
    server_status = (
        "BUSY" if active or requests["pending"] else
        ("UNKNOWN" if request_unknown or durable_unknown or not stable else "UNOWNED_OR_UNKNOWN")
    )
    return {
        "success": True,
        "data": {
            "schema_version": 1,
            "status": worker_status,
            "worker_status": worker_status,
            "server_lane_status": server_status,
            "scope": "exact-daemon-worker+all-projects+durable-direct-rpc",
            "binding": {
                "project_id": project_id,
                "session_id": session_id,
                "worker_instance_id": instance_id,
                "connection_epoch": epoch,
                "worker_object_identity": f"python-object:{id(worker)}",
            },
            "counts": {
                "worker_jobs": worker_counts,
                "requested_project_session_jobs": session_counts,
                "direct_rpc": {key: requests[key] for key in (
                    "submitted", "observed", "pending", "orphan_observed", "nonterminal_observed",
                    "unknown_outcome", "ambiguous")},
                "unclassified_durable_jobs": len(unclassified),
            },
            "direct_rpc_inventory": requests,
            "checks": {
                "exact_worker_binding": True,
                "all_projects_in_daemon_ledger_scanned": True,
                "snapshot_stable": stable,
                "all_accepted_jobs_terminal": not active,
                "no_durable_unknown_or_unclassified_jobs": not durable_unknown,
                "all_direct_rpc_requests_observed_once_and_terminal": requests["safe_to_cleanup"],
                "server_process_identity_attested": False,
            },
            "safe_to_retire_worker": worker_status == "QUIESCENT",
            "safe_to_stop_server": False,
            "limitations": [
                "all projects in this daemon currently share one ManagedBackend Worker",
                "server process ownership/listener absence requires separate exact OS evidence",
            ],
        },
    }
