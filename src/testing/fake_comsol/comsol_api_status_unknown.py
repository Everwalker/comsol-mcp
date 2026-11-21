"""Status contract for diagnostic UNKNOWN outcomes.

The public status must never stay UNKNOWN merely because a failure lacks a
domain outcome. This module keeps the original engine_error facts intact while
projecting an honest public status (FAILED / RECOVERED) for consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from typing import Any

STATUS_UNKNOWN = "UNKNOWN"
STATUS_FAILED = "FAILED"
STATUS_RECOVERED = "RECOVERED"

# Retry is a projection, not an executed operation. A projection claim must
# never imply that a retry already ran or that a recovery happened.
RETRY_PROJECTION_CLAIM = "retry projected (not executed)"

# Only a result that actually completed may be reported as success.
_TERMINAL_SUCCESS_STATUSES = frozenset({"completed", "success"})


def _is_incomplete_attempt(result: dict[str, Any]) -> bool:
    """An attempt without a success terminal status is incomplete."""
    status = str(result.get("status") or "").strip().lower()
    if status in _TERMINAL_SUCCESS_STATUSES:
        return False
    detail = str(result.get("error_detail") or "")
    return True


def _has_completed_attempt(results: list[dict[str, Any]]) -> bool:
    for result in results:
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "").strip().lower()
        if status in _TERMINAL_SUCCESS_STATUSES:
            return True
    return False


def _has_recovered_attempt(results: list[dict[str, Any]]) -> bool:
    """True only when a re-check attempt actually completed successfully."""
    if len(results) < 2:
        return False
    return _has_completed_attempt(results[1:])


def reconcile_status(
    status: str,
    results: list[dict[str, Any]],
    message: str = "",
) -> tuple[str, str]:
    """Replace UNKNOWN only where facts support it; preserve genuine UNKNOWN.

    - An error with no completed attempt projects FAILED (the run did not
      complete) while keeping the diagnostic facts in the message.
    - A run whose re-check completed projects RECOVERED.
    - A run with no error and no completed attempt keeps UNKNOWN, because
      nothing in the evidence supports FAILED or RECOVERED.
    """
    if status != STATUS_UNKNOWN:
        return status, message
    if _has_recovered_attempt(results):
        return STATUS_RECOVERED, message
    if _has_incomplete_attempt(results) and not _has_completed_attempt(results):
        return STATUS_FAILED, message
    return status, message


def attach_diagnostic_engine_error(
    result: dict[str, Any],
    failure_type: str,
    stage: str,
    attempted_operations: list[str],
    allowed_operations: list[str],
    safety_flags: dict[str, bool],
) -> dict[str, Any]:
    """Attach the diagnostic domain outcome while keeping facts intact.

    The diagnostic block is a distinct layer from the public claim layer:
    raw facts (failure_type, stages, attempts, safety flags) live in
    ``domain_outcome.engine_error``; the public retry projection lives in
    the claim layer and never mutates those facts.
    """
    outcome = dict(result)
    domain_outcome = dict(outcome.get("domain_outcome") or {})
    diagnostic: dict[str, Any] = {
        "failure_type": failure_type,
        "stage": stage,
        "attempted_operations": list(attempted_operations),
        "allowed_operations": list(allowed_operations),
    }
    diagnostic.update(dict(safety_flags))
    domain_outcome["engine_error"] = diagnostic
    outcome["domain_outcome"] = domain_outcome
    return outcome


def project_retry_projection(outcome: dict[str, Any]) -> dict[str, Any]:
    """Project the retry layer onto the public claim fields.

    Retry is never executed here: no model operation is attempted and no
    recovery is implied. The projection only marks that a retry was
    projected and normalizes the public status away from UNKNOWN.
    """
    projected = dict(outcome)
    claim = dict(projected.get("claim") or {})
    claim["retry_projection"] = RETRY_PROJECTION_CLAIM
    claim["retry_executed"] = False
    claim["recovery_implied"] = False
    projected["claim"] = claim
    status, message = reconcile_status(
        str(projected.get("status") or ""),
        list(projected.get("results") or []),
        str(projected.get("message") or ""),
    )
    projected["status"] = status
    projected["message"] = message
    projected["status_source"] = "public_projection"
    return projected


def reconcile_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Single canonical entry point: facts stay, public layer is projected."""
    reconciled = dict(outcome)
    status, message = reconcile_status(
        str(reconciled.get("status") or ""),
        list(reconciled.get("results") or []),
        str(reconciled.get("message") or ""),
    )
    reconciled["status"] = status
    reconciled["message"] = message
    return project_retry_projection(reconciled)
