"""Public-MCP control-plane cases for a bound, already-solved Chain-B model.

The caller owns the MCP host and the Chain-B model.  This helper deliberately does not
create a model, start COMSOL, or use a private worker API.  It sends one public solve
request with a short caller-side RPC timeout, then (when the response is UNKNOWN) reads
that original job through the published status/log/result/reconcile actions before it
can retry anything.  A retry is made only through :meth:`ActionClient.retry`, which
re-sends the exact recorded request under the exact same key; a second ``study.run``
request is never manufactured.

The control-plane result is accepted only when a real active job was observed by a
public status/log call and the same-key retry proof is present.  A fast solve, a missing
job id, a non-quiescent reconciliation, or a missing public route remains NOT_RUN or
BLOCKED.  Poll sleeps only separate real public observations; they never simulate a solve.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import phase4_run_mcp as protocol  # noqa: E402


_STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "UNVERIFIED"}
_ACTIVE_STATUSES = {"QUEUED", "STARTING", "RUNNING", "ACTIVE", "IN_PROGRESS"}
_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
_CONTROL_LATENCY_BUDGET_S = 2.0
_DEFAULT_POLL_TIMEOUT_S = 60.0
_DEFAULT_POLL_INTERVAL_S = 0.1
_JOB_LOG_LIMIT = 1000
_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(value: Any) -> Any:
    converter = getattr(protocol, "_json_safe", None)
    if callable(converter):
        return converter(value)
    if isinstance(value, Mapping):
        return {str(key): _safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(child) for child in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _data(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    value = payload.get("data") if isinstance(payload, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _success(payload: Mapping[str, Any] | None) -> bool:
    checker = getattr(protocol, "_success", None)
    if callable(checker):
        return bool(checker(payload))
    return isinstance(payload, Mapping) and payload.get("success") is True


def _error_code(payload: Mapping[str, Any] | None) -> str | None:
    checker = getattr(protocol, "_error_code", None)
    if callable(checker):
        return checker(payload)
    error = payload.get("error") if isinstance(payload, Mapping) else None
    return str(error.get("code")) if isinstance(error, Mapping) and error.get("code") else None


def _unknown(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    checker = getattr(protocol, "_unknown_outcome", None)
    if callable(checker):
        result = checker(payload)
        return dict(result) if isinstance(result, Mapping) else None
    return None


def _body_hash(value: Mapping[str, Any]) -> str:
    checker = getattr(protocol, "_body_sha256", None)
    if callable(checker):
        return str(checker(value))
    encoded = json.dumps(_safe(dict(value)), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    return _KEY_SAFE.sub("-", value).strip(".-")[:64] or "case"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    encoded = json.dumps(_safe(value), ensure_ascii=False, indent=2, sort_keys=True,
                         allow_nan=False) + "\n"
    temporary.write_text(encoded, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _response_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep response evidence bounded and avoid copying arbitrary result data."""
    data = _data(payload)
    row: dict[str, Any] = {
        "success": _success(payload),
        "error_code": _error_code(payload),
        "unknown": _safe(_unknown(payload)),
        "data_keys": sorted(str(key) for key in data),
    }
    for key in ("job_id", "status", "state", "queued_or_running", "rpc_wait_expired",
                "reconciled_quiescent", "quiescent", "metadata", "execution_state_unknown"):
        if key in data:
            value = data[key]
            if key == "metadata" and isinstance(value, Mapping):
                value = {name: _safe(value[name]) for name in
                         ("reconciled_quiescent", "replay_performed", "status") if name in value}
            row[key] = _safe(value)
    execution = payload.get("execution") if isinstance(payload, Mapping) else None
    if isinstance(execution, Mapping):
        row["execution"] = {
            name: _safe(execution[name])
            for name in ("operation_id", "job_id", "request_id", "idempotency_key",
                         "dispatch_stage", "status")
            if name in execution
        }
    return row


def _job_log_snapshot(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Summarize a complete public job-log page for a retry comparison.

    The control case must compare the original job's actual public events before
    and after retry.  A bounded page is evidence only when it contains fewer
    events than the requested limit (or explicitly reports no truncation); an
    exact-limit page is therefore rejected as incomplete rather than silently
    treated as unchanged.
    """
    data = _data(payload)
    events = data.get("events")
    if not isinstance(events, list):
        return {"present": False, "complete": False, "event_count": None,
                "events_sha256": None, "limit": _JOB_LOG_LIMIT}
    encoded = json.dumps(_safe(events), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    explicit_truncated = data.get("truncated")
    explicit_has_more = data.get("has_more")
    if isinstance(explicit_truncated, bool):
        complete = not explicit_truncated
    elif isinstance(explicit_has_more, bool):
        complete = not explicit_has_more
    else:
        complete = len(events) < _JOB_LOG_LIMIT
    return {
        "present": True,
        "complete": complete,
        "event_count": len(events),
        "events_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "limit": _JOB_LOG_LIMIT,
    }


def _job_id(payload: Mapping[str, Any] | None) -> str | None:
    unknown = _unknown(payload)
    if isinstance(unknown, Mapping) and isinstance(unknown.get("job_id"), str):
        if unknown["job_id"]:
            return str(unknown["job_id"])
    if not isinstance(payload, Mapping):
        return None
    data = _data(payload)
    execution = payload.get("execution")
    for source in (execution, data, payload):
        if isinstance(source, Mapping):
            value = source.get("job_id")
            if isinstance(value, str) and value:
                return value
    return None


def _needs_observation(payload: Mapping[str, Any] | None) -> bool:
    """Whether a successful envelope still describes an in-flight caller-timeout job."""
    if _unknown(payload) is not None:
        return True
    if not isinstance(payload, Mapping):
        return False
    data = _data(payload)
    rpc_wait_expired = data.get("rpc_wait_expired") is True or payload.get("rpc_wait_expired") is True
    state = _status(payload)
    return bool(_job_id(payload) and (rpc_wait_expired or state in _ACTIVE_STATUSES))


def _status(payload: Mapping[str, Any] | None) -> str | None:
    data = _data(payload)
    execution = payload.get("execution") if isinstance(payload, Mapping) else None
    nested_job = data.get("job") if isinstance(data.get("job"), Mapping) else None
    for source in (data, nested_job, execution, payload):
        if isinstance(source, Mapping):
            for name in ("status", "state"):
                value = source.get(name)
                if isinstance(value, str) and value:
                    return value.upper()
    return None


def _quiescent(payload: Mapping[str, Any] | None) -> bool | None:
    data = _data(payload)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
    for source in (data, metadata):
        for name in ("reconciled_quiescent", "quiescent"):
            if name in source and isinstance(source[name], bool):
                return source[name]
    # A published terminal status is also a release witness.  UNKNOWN is deliberately
    # excluded: it is the state this helper must inspect rather than treat as stopped.
    state = _status(payload)
    if state in _TERMINAL_STATUSES:
        return True
    return None


def _bound_model_summary(client: Any) -> dict[str, Any]:
    state = getattr(client, "state", None)
    ref = state.get("ref") if isinstance(state, Mapping) else None
    if not isinstance(ref, Mapping):
        return {"present": False}
    return {
        "present": True,
        "session_id": _safe(ref.get("session_id")),
        "server_instance_id": _safe(ref.get("server_instance_id")),
        "model_tag": _safe(ref.get("model_tag")),
        "generation": _safe(ref.get("generation")),
    }


class _ControlLedger:
    def __init__(self, client: Any, run_dir: Path) -> None:
        self.client = client
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.counter = 0
        self.needs_retained_runtime = False
        self.unknown_evidence: list[dict[str, Any]] = []

    def key(self, label: str) -> str:
        self.counter += 1
        return f"g33-control-{self.counter:03d}-{_slug(label)}"

    def flush(self) -> None:
        summary = {status: sum(1 for row in self.rows if row["status"] == status)
                   for status in sorted(_STATUSES)}
        _write_json(self.run_dir / "g33_control_cases.json", {
            "schema_version": 1,
            "scope": "public_mcp_chain_b_control",
            "run_dir": str(self.run_dir),
            "calls": self.calls,
            "rows": self.rows,
            "summary": summary,
        })

    def add(self, case: str, status: str, *, operation: str | None = None,
            key: str | None = None, response: Mapping[str, Any] | None = None,
            assertions: Mapping[str, Any] | None = None, reason: str | None = None,
            **extra: Any) -> dict[str, Any]:
        if status not in _STATUSES:
            raise ValueError(f"invalid control case status: {status}")
        row: dict[str, Any] = {
            "case": case,
            "status": status,
            "operation": operation,
            "idempotency_key": key,
            "response": _response_summary(response) if isinstance(response, Mapping) else None,
            "assertions": _safe(dict(assertions or {})),
        }
        if reason:
            row["reason"] = reason
        row.update(_safe(extra))
        self.rows.append(row)
        self.flush()
        return row


async def _call(ledger: _ControlLedger, operation: str, body: Mapping[str, Any], label: str,
                *, require_model: bool, rpc_timeout_s: float | None = None,
                _probe_unknown: bool = True,
                unknown_poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
                unknown_poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S) -> tuple[
                    str, Mapping[str, Any] | None, str
                ]:
    key = ledger.key(label)
    request = f"g33-control-{_slug(label)}"
    kwargs: dict[str, Any] = {
        "require_model": require_model,
        "key": key,
        "request": request,
        "reconcile": False,
    }
    if rpc_timeout_s is not None:
        kwargs["rpc_timeout_s"] = float(rpc_timeout_s)
    body_hash = _body_hash(body)
    call_row: dict[str, Any] = {
        "sequence": len(ledger.calls) + 1,
        "operation": operation,
        "idempotency_key": key,
        "request": request,
        "body_sha256": body_hash,
        "reconcile": False,
        "require_model": require_model,
        "rpc_timeout_s": kwargs.get("rpc_timeout_s"),
    }
    ledger.calls.append(call_row)
    ledger.flush()
    started = time.perf_counter()
    try:
        payload = await ledger.client.action(operation, dict(body), **kwargs)
    except Exception as exc:  # A missing route is observed evidence, not a fake response.
        call_row.update({"elapsed_s": time.perf_counter() - started,
                         "mode": "exception", "exception_type": type(exc).__name__,
                         "exception": str(exc)[:300]})
        ledger.flush()
        return key, None, "exception"
    elapsed = time.perf_counter() - started
    if not isinstance(payload, Mapping):
        call_row.update({"elapsed_s": elapsed, "mode": "invalid-response",
                         "response_type": type(payload).__name__})
        ledger.flush()
        return key, None, "invalid-response"
    payload_map = dict(payload)
    call_row.update({"elapsed_s": elapsed, "mode": "response",
                     "response": _response_summary(payload_map)})
    ledger.flush()
    if _probe_unknown and _needs_observation(payload_map):
        evidence = await _query_unknown_job(
            ledger, payload_map, label,
            poll_timeout_s=unknown_poll_timeout_s,
            poll_interval_s=unknown_poll_interval_s,
        )
        call_row["unknown_job_queries"] = _safe(evidence)
        ledger.flush()
    return key, payload_map, "response"


async def _query_unknown_job(ledger: _ControlLedger, payload: Mapping[str, Any] | None,
                             label: str, *, poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
                             poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S) -> dict[str, Any]:
    """Query, then poll, the original UNKNOWN job before any same-key retry is considered.

    The first four reads are mandatory.  When the first reconcile read still says that the
    solver is active, short public status/log/reconcile observations continue until the
    published ``data.metadata.reconciled_quiescent`` flag or a terminal job status proves
    release, or the bounded timeout expires.  A timeout is returned as
    ``needs_retained_runtime`` so the caller can retain the host/worker instead of letting
    cleanup terminate a real solve.
    """
    job_id = _job_id(payload)
    if not job_id:
        result = {"job_id": None, "queries_complete": False, "quiescent": None,
                  "active_observed": False, "queries": [], "poll_rounds": 0,
                  "needs_retained_runtime": False,
                  "reason": "UNKNOWN response had no job_id"}
        ledger.add("unknown-job-query", "FAIL", operation="job_status/job_log/job_result/job_reconcile",
                   assertions=result, reason=result["reason"])
        return result

    queries: list[dict[str, Any]] = []
    complete = True
    active_observed = _status(payload) in _ACTIVE_STATUSES
    reconcile_quiescent: bool | None = None
    terminal_observed = False

    async def observe(operation: str, suffix: str) -> tuple[dict[str, Any], Mapping[str, Any] | None, str]:
        nonlocal terminal_observed
        body: dict[str, Any] = {"job_id": job_id}
        if operation == "job_log":
            body.update({"offset": 0, "limit": _JOB_LOG_LIMIT})
        key, observed, mode = await _call(
            ledger, operation, body, f"{label}-{suffix}", require_model=False,
            _probe_unknown=False,
        )
        call_record = ledger.calls[-1] if ledger.calls else {}
        observed_status = _status(observed)
        active = observed_status in _ACTIVE_STATUSES
        terminal_observed = terminal_observed or observed_status in _TERMINAL_STATUSES
        queries.append({
            "operation": operation,
            "idempotency_key": key,
            "mode": mode,
            "status": observed_status,
            "active": active,
            "elapsed_s": call_record.get("elapsed_s"),
            "response": _response_summary(observed) if isinstance(observed, Mapping) else None,
        })
        if operation == "job_log":
            queries[-1]["event_snapshot"] = _job_log_snapshot(observed)
        return {"active": active, "status": observed_status}, observed, mode

    for operation in ("job_status", "job_log", "job_result", "job_reconcile"):
        observed_info, observed, mode = await observe(operation, operation)
        active_observed = active_observed or bool(observed_info["active"])
        observed_quiescent = _quiescent(observed)
        if operation == "job_reconcile":
            if reconcile_quiescent is not True:
                reconcile_quiescent = observed_quiescent
        elif observed_quiescent is True:
            # A published terminal job status is itself a safe release witness.
            reconcile_quiescent = True
        if mode != "response" or observed is None:
            complete = False

    poll_rounds = 0
    bounded_timeout = max(0.0, float(poll_timeout_s))
    bounded_interval = max(0.0, float(poll_interval_s))
    deadline = asyncio.get_running_loop().time() + bounded_timeout
    while (complete and reconcile_quiescent is not True and not terminal_observed
           and asyncio.get_running_loop().time() < deadline):
        poll_rounds += 1
        for operation in ("job_status", "job_log", "job_reconcile"):
            observed_info, observed, mode = await observe(
                operation, f"poll-{poll_rounds:03d}-{operation}")
            active_observed = active_observed or bool(observed_info["active"])
            observed_quiescent = _quiescent(observed)
            if operation == "job_reconcile":
                reconcile_quiescent = observed_quiescent
            elif observed_quiescent is True:
                reconcile_quiescent = True
            if mode != "response" or observed is None:
                complete = False
            if reconcile_quiescent is True or terminal_observed:
                break
        if reconcile_quiescent is True or terminal_observed or not complete:
            break
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining > 0 and bounded_interval > 0:
            await asyncio.sleep(min(bounded_interval, remaining))

    needs_retained_runtime = (reconcile_quiescent is not True and not terminal_observed
                              and active_observed)
    if not complete:
        reason = ("one or more public UNKNOWN queries did not return a response"
                  + (" while active; retain runtime for follow-up" if needs_retained_runtime else ""))
    elif needs_retained_runtime:
        reason = "job remained active/non-quiescent after bounded polling; retain runtime for follow-up"
    else:
        reason = None
    if needs_retained_runtime:
        ledger.needs_retained_runtime = True

    result = {
        "job_id": job_id,
        "queries_complete": complete,
        "quiescent": reconcile_quiescent,
        "active_observed": active_observed,
        "terminal_observed": terminal_observed,
        "queries": queries,
        "poll_rounds": poll_rounds,
        "poll_timeout_s": bounded_timeout,
        "poll_interval_s": bounded_interval,
        "needs_retained_runtime": needs_retained_runtime,
    }
    ledger.unknown_evidence.append(result)
    ledger.add(
        "unknown-job-query",
        "PASS" if complete else "BLOCKED",
        operation="job_status/job_log/job_result/job_reconcile",
        assertions={"job_id": job_id, "queries_complete": complete,
                    "active_observed": active_observed,
                    "terminal_observed": terminal_observed,
                    "quiescent": reconcile_quiescent,
                    "quiescent_source": "data.metadata.reconciled_quiescent",
                    "poll_rounds": poll_rounds,
                    "poll_timeout_s": bounded_timeout,
                    "query_operations": [row["operation"] for row in queries]},
        query_results=queries,
        reason=reason,
    )
    if needs_retained_runtime:
        ledger.add(
            "unknown-quiescence-gate", "BLOCKED",
            operation="job_reconcile",
            assertions={"quiescent": reconcile_quiescent,
                        "terminal_observed": terminal_observed,
                        "source": "data.metadata.reconciled_quiescent",
                        "poll_rounds": poll_rounds,
                        "poll_timeout_s": bounded_timeout,
                        "needs_retained_runtime": True,
                        "same_key_retry_dispatched": False},
            reason=reason,
        )
    return result


def _finish(ledger: _ControlLedger, *, bound_model: Mapping[str, Any],
            solve_operation: str, solve_body: Mapping[str, Any]) -> dict[str, Any]:
    summary = {status: sum(1 for row in ledger.rows if row["status"] == status)
               for status in sorted(_STATUSES)}
    if summary["FAIL"]:
        overall = "FAIL"
    elif summary["BLOCKED"] or summary["UNVERIFIED"] or not summary["PASS"]:
        overall = "BLOCKED"
    else:
        overall = "PASS"
    result = {
        "schema_version": 1,
        "scope": "public_mcp_chain_b_control",
        "run_dir": str(ledger.run_dir),
        "bound_model": _safe(dict(bound_model)),
        "solve": {"operation": solve_operation, "body_sha256": _body_hash(solve_body),
                   "rpc_timeout_s": ledger.calls[0].get("rpc_timeout_s") if ledger.calls else None},
        "calls": ledger.calls,
        "rows": ledger.rows,
        "summary": summary,
        "overall": overall,
        "needs_retained_runtime": ledger.needs_retained_runtime,
    }
    _write_json(ledger.run_dir / "g33_control_cases.final.json", result)
    return result


async def run_control_cases(
    client: Any,
    run_dir: Path,
    *,
    solve_operation: str = "study.run",
    solve_body: Mapping[str, Any] | None = None,
    rpc_timeout_s: float = 0.05,
    poll_timeout_s: float = _DEFAULT_POLL_TIMEOUT_S,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
) -> dict[str, Any]:
    """Run C15 control checks through the public MCP actions.

    ``client`` must already be bound to Chain B.  The default typed study path matches
    the public ``study.run`` schema; callers may pass a reviewed ``run_study`` body or
    another public solve operation explicitly when their bound model uses that route.
    """
    ledger = _ControlLedger(client, Path(run_dir))
    body = dict(solve_body or {
        "study": {"segments": [{"collection": "study", "tag": "std1"}]},
    })
    bound_model = _bound_model_summary(client)
    if not bound_model.get("present"):
        ledger.add("bound-chain-b-precondition", "BLOCKED", operation=solve_operation,
                   assertions={"bound_model_present": False},
                   reason="helper requires a caller-bound Chain-B model")
        ledger.flush()
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)

    solve_key, solve_reply, solve_mode = await _call(
        ledger, solve_operation, body, "solve", require_model=True,
        rpc_timeout_s=rpc_timeout_s,
        unknown_poll_timeout_s=poll_timeout_s,
        unknown_poll_interval_s=poll_interval_s,
    )
    if solve_mode != "response" or solve_reply is None:
        ledger.add("solve-dispatch", "BLOCKED", operation=solve_operation, key=solve_key,
                   assertions={"response_received": False},
                   reason="public solve call did not return a response")
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)

    unknown = _unknown(solve_reply)
    pending_observation = _needs_observation(solve_reply)
    if not pending_observation:
        # A successful/ordinary failure solve has no observable in-flight window.  Do not
        # claim C15 from a fast solve and do not issue another solve merely to make one.
        if _success(solve_reply):
            ledger.add("solve-active-window", "NOT_RUN", operation=solve_operation, key=solve_key,
                       response=solve_reply,
                       assertions={"unknown_response": False, "active_observed": False},
                       reason="solve completed before a public control observation")
        else:
            ledger.add("solve-dispatch", "FAIL", operation=solve_operation, key=solve_key,
                       response=solve_reply,
                       assertions={"unknown_response": False, "error_code": _error_code(solve_reply)},
                       reason="public solve returned a terminal failure")
        ledger.add("same-key-retry", "NOT_RUN", operation=solve_operation, key=solve_key,
                   assertions={"new_solve_dispatched": False},
                   reason="same-key retry is only evaluated after an UNKNOWN job is queried")
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)

    job_id = _job_id(solve_reply)
    if not job_id:
        ledger.add("solve-unknown", "FAIL", operation=solve_operation, key=solve_key,
                   response=solve_reply,
                   assertions={"unknown_response": True, "job_id_present": False},
                   reason="UNKNOWN solve response had no durable original job id")
        ledger.add("same-key-retry", "BLOCKED", operation=solve_operation, key=solve_key,
                   assertions={"new_solve_dispatched": False},
                   reason="no original job id was available for mandatory queries")
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)

    ledger.add("solve-unknown", "PASS", operation=solve_operation, key=solve_key,
               response=solve_reply,
               assertions={"unknown_response": unknown is not None,
                           "active_success_response": unknown is None and pending_observation,
                           "job_id": job_id,
                           "new_solve_dispatched": False})
    # ``_call`` has already completed the mandatory original-job query and bounded poll
    # before returning the UNKNOWN solve response.  Reusing that evidence avoids a second
    # query cycle (and guarantees no hidden replay path).
    queried = ledger.unknown_evidence[-1] if ledger.unknown_evidence else {
        "job_id": job_id, "queries_complete": False, "quiescent": None,
        "active_observed": False, "queries": [], "needs_retained_runtime": False,
    }
    query_sequence = [row.get("operation") for row in ledger.calls]
    retry_before_queries = any(
        row.get("operation") == solve_operation for row in ledger.calls[1:]
    )
    ordering_ok = (query_sequence[:1] == [solve_operation]
                   and not retry_before_queries
                   and all(operation in query_sequence[1:]
                           for operation in ("job_status", "job_log", "job_result", "job_reconcile")))
    ledger.add(
        "unknown-query-before-retry",
        "PASS" if queried["queries_complete"] and ordering_ok else "BLOCKED",
        operation="job_status/job_log/job_result/job_reconcile",
        key=solve_key,
        assertions={"job_id": job_id, "queries_complete": queried["queries_complete"],
                    "query_before_retry": ordering_ok,
                    "query_operations": query_sequence},
        reason=None if queried["queries_complete"] and ordering_ok
        else "original UNKNOWN job was not fully queried before retry consideration",
    )

    # Record the control-plane observation before the quiescence gate below.  If the
    # solver is still active, this row is useful evidence even though the safe policy
    # correctly refuses to retry until reconciliation proves quiescence.
    active_observed = bool(queried["active_observed"])
    control_calls = [call for call in ledger.calls
                     if call.get("operation") in {"job_status", "job_log"}]
    latency_ok = bool(control_calls) and all(
        isinstance(call.get("elapsed_s"), (int, float))
        and 0 <= call["elapsed_s"] <= _CONTROL_LATENCY_BUDGET_S
        for call in control_calls
    )
    active_row = next((row for row in ledger.rows if row["case"] == "unknown-job-query"), None)
    ledger.add(
        "active-control-response",
        "PASS" if active_observed and latency_ok else "BLOCKED",
        operation="job_status/job_log",
        key=solve_key,
        assertions={"active_observed": active_observed, "latency_recorded": latency_ok,
                    "control_call_count": len(control_calls),
                    "latency_budget_s": _CONTROL_LATENCY_BUDGET_S,
                    "max_latency_s": max((call.get("elapsed_s", 0.0) for call in control_calls),
                                          default=None)},
        query_results=(active_row or {}).get("query_results", []),
        reason=None if active_observed and latency_ok
        else "no public status/log response observed a real active solve",
    )

    # The reconcile read is the release/quiescence gate.  Until it explicitly says that the
    # original job is quiescent, there is no mutation or replay, even though the retry would
    # nominally be idempotent.  This protects an active solver from a guessed second plan.
    if not queried["queries_complete"]:
        ledger.add("same-key-retry", "BLOCKED", operation=solve_operation, key=solve_key,
                   assertions={"new_solve_dispatched": False, "quiescent": queried["quiescent"],
                               "needs_retained_runtime": queried.get("needs_retained_runtime", False)},
                   reason=("mandatory original-job queries did not all return"
                           + (" while active; needs_retained_runtime=true"
                              if queried.get("needs_retained_runtime") else "")))
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)
    if not queried["quiescent"]:
        ledger.add("same-key-retry", "BLOCKED", operation=solve_operation, key=solve_key,
                   assertions={"new_solve_dispatched": False, "quiescent": queried["quiescent"],
                               "active_observed": queried["active_observed"],
                               "needs_retained_runtime": queried.get("needs_retained_runtime", False)},
                   reason=("job remained active/non-quiescent after bounded polling; "
                           "needs_retained_runtime=true; no retry dispatched"
                           if queried.get("needs_retained_runtime") else
                           "job_reconcile did not prove original job quiescence; no retry dispatched"))
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)

    retry = getattr(client, "retry", None)
    if not callable(retry):
        ledger.add("same-key-retry", "BLOCKED", operation=solve_operation, key=solve_key,
                   assertions={"new_solve_dispatched": False, "quiescent": True},
                   reason="bound client exposes no exact-request retry method")
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)
    retry_started = time.perf_counter()
    try:
        retry_reply, retry_record = await retry(solve_key)
    except Exception as exc:  # Preserve the live failure without manufacturing a pass.
        ledger.add("same-key-retry", "FAIL", operation=solve_operation, key=solve_key,
                   assertions={"new_solve_dispatched": False, "quiescent": True},
                   reason=f"exact same-key retry raised {type(exc).__name__}: {exc}",
                   elapsed_s=time.perf_counter() - retry_started)
        return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                       solve_body=body)

    elapsed = time.perf_counter() - retry_started
    record = dict(retry_record) if isinstance(retry_record, Mapping) else {}
    same_key = record.get("key") == solve_key
    same_body = record.get("same_body") is True
    hashes_match = (isinstance(record.get("body_sha256"), str)
                    and record.get("body_sha256") == record.get("retry_body_sha256"))
    same_job = _job_id(retry_reply) == job_id
    same_operation = record.get("operation") == solve_operation

    # Re-read the original public job log after the exact retry.  This is a
    # bounded read with an explicit limit; an exact-limit response is not
    # considered complete, so a hidden appended event cannot pass by accident.
    _post_log_key, post_log_reply, post_log_mode = await _call(
        ledger, "job_log", {"job_id": job_id, "offset": 0, "limit": _JOB_LOG_LIMIT},
        "retry-post-job-log", require_model=False, _probe_unknown=False,
    )
    post_log = _job_log_snapshot(post_log_reply) if post_log_mode == "response" else {
        "present": False, "complete": False, "event_count": None,
        "events_sha256": None, "limit": _JOB_LOG_LIMIT,
    }
    pre_logs = [query.get("event_snapshot") for query in queried.get("queries", [])
                if query.get("operation") == "job_log"
                and isinstance(query.get("event_snapshot"), Mapping)]
    pre_log = dict(pre_logs[-1]) if pre_logs else {
        "present": False, "complete": False, "event_count": None,
        "events_sha256": None, "limit": _JOB_LOG_LIMIT,
    }
    log_complete = pre_log.get("complete") is True and post_log.get("complete") is True
    log_unchanged = (log_complete
                     and pre_log.get("event_count") == post_log.get("event_count")
                     and pre_log.get("events_sha256") == post_log.get("events_sha256"))
    # ``retry`` is the only operation allowed here.  There must be no second solve action
    # in the ActionClient call ledger; the recorded retry must preserve its original key/body.
    solve_calls = [row for row in ledger.calls if row.get("operation") == solve_operation]
    passed = (same_key and same_body and hashes_match and same_job and same_operation
              and log_unchanged and len(solve_calls) == 1)
    ledger.add(
        "same-key-retry",
        "PASS" if passed else "FAIL",
        operation=solve_operation,
        key=solve_key,
        response=retry_reply if isinstance(retry_reply, Mapping) else None,
        assertions={"same_key": same_key, "same_body": same_body,
                    "hashes_match": hashes_match, "quiescent": True,
                    "same_job": same_job, "same_operation": same_operation,
                    "job_id": job_id, "retry_job_id": _job_id(retry_reply),
                    "job_log_complete": log_complete,
                    "job_log_events_unchanged": log_unchanged,
                    "job_log_before": pre_log, "job_log_after": post_log,
                    "solve_dispatch_count": len(solve_calls),
                    "new_solve_dispatched": len(solve_calls) != 1},
        retry_record=record,
        elapsed_s=elapsed,
        reason=None if passed else "retry did not prove exact key/body reuse without a new solve",
    )

    return _finish(ledger, bound_model=bound_model, solve_operation=solve_operation,
                   solve_body=body)


# The explicit alias lets the protocol runner use a descriptive name without changing the
# public helper contract used by offline tests and by the root acceptance driver.
run_g33_control_cases = run_control_cases


__all__ = ["run_control_cases", "run_g33_control_cases"]
