#!/usr/bin/env python3
"""Real production-stdio recovery and security driver for T012/T027/T055/T028/T035.

It only signals a verified private control daemon or Java worker.  COMSOL is
never stopped, and all control actions use a fresh MCP ``ClientSession``.
"""
from __future__ import annotations

import argparse, asyncio, hashlib, json, os, signal, sqlite3, subprocess, sys, time, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
JDK11_DEFAULT = "/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"
ACTIVE = {"QUEUED", "STARTING", "RUNNING"}


def _safe(value: Any) -> Any:
    if hasattr(value, "model_dump"): return _safe(value.model_dump(mode="json"))
    if isinstance(value, dict): return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_safe(v) for v in value]
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            sensitive_key = any(x in str(key).lower() for x in ("token", "password", "credential", "authorization", "prefs"))
            # Assertion fields can legitimately include words such as
            # ``credential_path_denied``.  Preserve their boolean/numeric
            # verdicts while never emitting a sensitive string value.
            redacted[str(key)] = "REDACTED" if sensitive_key and isinstance(item, str) else _redact(item)
        return redacted
    if isinstance(value, list): return [_redact(v) for v in value]
    if isinstance(value, str) and any(part in value for part in (".phase1-private", ".phase2-private", "control-private")):
        return "REDACTED_PATH"
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_redact(_safe(value)), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file(): raise RuntimeError(f"unsafe or missing discovery record: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise RuntimeError(f"invalid discovery record: {path}")
    return value


def _control_home(private_home: Path) -> Path:
    home = private_home.resolve() / "control-private"
    if home.is_symlink(): raise RuntimeError("control-private must not be a symlink")
    return home


def _verified_pid(endpoint: dict[str, Any], fragment: str, home: Path, comsol_pid: int) -> dict[str, Any]:
    """Validate PID, user, command and private home immediately before a signal."""
    pid = endpoint.get("pid")
    if type(pid) is not int or pid <= 1 or pid == comsol_pid: raise RuntimeError("invalid or protected signal target")
    row = subprocess.check_output(["ps", "-p", str(pid), "-o", "uid=,pid=,command="], text=True).strip().split(maxsplit=2)
    if len(row) != 3 or int(row[0]) != os.getuid() or int(row[1]) != pid: raise RuntimeError("PID ownership changed")
    if fragment not in row[2] or str(home) not in row[2]: raise RuntimeError("PID command/home does not identify managed component")
    return {"pid": pid, "command": row[2]}


def _wait_dead(pid: int, timeout_s: float = 15.0) -> bool:
    until = time.monotonic() + timeout_s
    while time.monotonic() < until:
        try: os.kill(pid, 0)
        except ProcessLookupError: return True
        time.sleep(.05)
    return False


def _job_row(home: Path, job_id: str) -> dict[str, Any]:
    database = home / "operations.sqlite3"
    if database.is_symlink() or not database.is_file(): raise RuntimeError("durable job ledger unavailable")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT jobs.job_id,jobs.operation_id,jobs.status,jobs.metadata,"
            "jobs.created_at,jobs.started_at,jobs.finished_at,operations.result "
            "FROM jobs JOIN operations ON operations.operation_id=jobs.operation_id "
            "WHERE jobs.job_id=?",
            (job_id,),
        ).fetchone()
        count = db.execute("SELECT count(*) FROM operations WHERE operation_id=(SELECT operation_id FROM jobs WHERE job_id=?)", (job_id,)).fetchone()[0]
    if row is None: raise RuntimeError("job absent from durable ledger")
    return {**dict(row), "operation_count": count}


def _status(payload: dict[str, Any]) -> str | None:
    value = payload.get("data", {})
    return value.get("status") if isinstance(value, dict) and isinstance(value.get("status"), str) else None


def _same_job(payload: dict[str, Any], job_id: str) -> bool:
    return isinstance(payload.get("data"), dict) and payload["data"].get("job_id") == job_id


def _worker_identity(health: dict[str, Any]) -> dict[str, Any] | None:
    session = health.get("data", {}).get("session", {}) if isinstance(health.get("data"), dict) else {}
    worker = session.get("worker") if isinstance(session, dict) else None
    return {k: worker.get(k) for k in ("instance_id", "generation", "pid", "port")} if isinstance(worker, dict) else None


def _model_ref(path: Path) -> dict[str, Any]:
    """Accept either the raw ref or the execution envelope saved by a live run."""
    value = _read_json(path)
    if isinstance(value.get("model_ref"), dict): return value["model_ref"]
    execution = value.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("model_ref"), dict): return execution["model_ref"]
    required = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
    if required <= value.keys(): return value
    raise RuntimeError("model-ref-json contains no model_ref")


def _execution_identity(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    execution = payload.get("execution") if isinstance(payload, dict) else None
    if not isinstance(execution, dict):
        return None, None
    ref, revision = execution.get("model_ref"), execution.get("revision")
    return (ref if isinstance(ref, dict) else None, revision if isinstance(revision, int) and not isinstance(revision, bool) else None)


class Host:
    def __init__(self, params, log, transcript, run_dir, label): self.params, self.log, self.transcript, self.run_dir, self.label = params, log, transcript, run_dir, label
    async def __aenter__(self):
        self.transport = stdio_client(self.params, errlog=self.log); reader, writer = await self.transport.__aenter__()
        self.context = ClientSession(reader, writer, read_timeout_seconds=timedelta(minutes=10)); self.session = await self.context.__aenter__()
        self.transcript.append({"host": self.label, "operation": "initialize", "response": _safe(await self.session.initialize())})
        return self
    async def __aexit__(self, *exc):
        await self.context.__aexit__(*exc); await self.transport.__aexit__(*exc)
    async def call(self, operation: str, arguments: dict | None = None) -> dict:
        started = time.monotonic(); response = await self.session.call_tool(operation, arguments or {}); elapsed = time.monotonic() - started
        try: payload = json.loads(response.content[0].text)
        except Exception: payload = {"success": False, "error": {"code": "NON_JSON_MCP_RESPONSE"}, "raw": _safe(response)}
        # Keep the actual control-plane sample window free of repeated whole-
        # transcript serialization; ``run`` writes this durable evidence once
        # in its finally block after the live operation has been observed.
        self.transcript.append({"host": self.label, "operation": operation, "arguments": arguments or {}, "elapsed_s": elapsed, "outer_isError": bool(response.isError), "payload": payload})
        return {**payload, "_outer_isError": bool(response.isError), "_elapsed_s": elapsed}


async def _query(host: Host, job_id: str) -> dict:
    return {"status": await host.call("job_status", {"job_id": job_id}), "log": await host.call("job_log", {"job_id": job_id, "offset": 0, "limit": 1000}), "result": await host.call("job_result", {"job_id": job_id})}


async def _connect(host: Host, args, prefix: str) -> dict:
    key = prefix + "-" + uuid4().hex
    return await host.call("server_connect", {"host": "127.0.0.1", "port": args.port, "execution": {"idempotency_key": key, "request_id": key}})


async def _terminal(host: Host, job_id: str, wait_s: float) -> dict:
    observed = await _query(host, job_id); deadline = time.monotonic() + wait_s
    while _status(observed["status"]) in ACTIVE and time.monotonic() < deadline:
        await asyncio.sleep(.25)  # actual job polling; never a fake solve delay
        observed = await _query(host, job_id)
    return observed


def _worker_request_submitted(observed: dict) -> bool:
    events = observed.get("log", {}).get("data", {}).get("events", [])
    submitted_run_ids = set()
    observed_ids = set()
    for event in events:
        metadata = event.get("metadata")
        if event.get("event") != "worker_request" or not isinstance(metadata, dict):
            continue
        request_id = metadata.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            continue
        if metadata.get("phase") == "observed":
            observed_ids.add(request_id)
            continue
        payload = metadata.get("metadata")
        if (
            metadata.get("phase") == "submitted"
            and metadata.get("kind") == "call"
            and isinstance(payload, dict)
            and payload.get("method") == "run"
        ):
            submitted_run_ids.add(request_id)
    # A worker `call/run` returns only after COMSOL has answered.  Its submitted
    # event without the matching observed event is therefore the evidence that
    # a solver API call, rather than model preflight, remains pending.
    return bool(submitted_run_ids - observed_ids)


def _result_success(observed: dict) -> bool:
    job = observed.get("result", {}).get("data", {})
    result = job.get("result") if isinstance(job, dict) else None
    return isinstance(result, dict) and result.get("success") is True


def _events(observed: dict, event_name: str | None = None) -> list[dict[str, Any]]:
    value = observed.get("log", {}).get("data", {}).get("events", [])
    if not isinstance(value, list):
        return []
    return [event for event in value if isinstance(event, dict) and (event_name is None or event.get("event") == event_name)]


def _has_worker_submission(observed: dict) -> bool:
    return any(
        event.get("event") == "worker_request"
        and isinstance(event.get("metadata"), dict)
        and event["metadata"].get("phase") == "submitted"
        for event in _events(observed)
    )


def _p95(samples: list[float]) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)]


async def _sample_pending_control_plane(host: Host, job_id: str, *, minimum: int, deadline: float) -> tuple[list[dict[str, float]], dict]:
    """Collect only observations bracketed by a real pending worker `run` call."""
    samples: list[dict[str, float]] = []
    latest = await _query(host, job_id)
    while len(samples) < minimum and time.monotonic() < deadline:
        if _status(latest["status"]) != "RUNNING" or not _worker_request_submitted(latest):
            break
        health = await host.call("session_health")
        status = await host.call("job_status", {"job_id": job_id})
        log = await host.call("job_log", {"job_id": job_id, "offset": 0, "limit": 1000})
        # Do not count a response that was collected after the real solver call
        # completed; this keeps the T012 evidence tied to an actual pending solve.
        latest = {"status": status, "log": log, "result": latest["result"]}
        if _status(status) == "RUNNING" and _worker_request_submitted(latest):
            samples.append({"health_s": health["_elapsed_s"], "status_s": status["_elapsed_s"], "log_s": log["_elapsed_s"]})
            continue
        break
    return samples, latest


async def host_disconnect(args, params, evidence, assertions, transcript, log) -> str:
    control = _control_home(args.private_home)
    ref = _model_ref(args.model_ref_json) if args.model_ref_json is not None else None
    revision = args.revision
    async with Host(params, log, transcript, args.run_dir, "submitting-host") as submitting:
        if args.model_path is not None:
            setup_key = "t012-load-" + args.idempotency_key
            connected = await _connect(submitting, args, "t012-load-connect")
            loaded = await submitting.call("model_load", {"path": str(args.model_path), "execution": {
                "idempotency_key": setup_key, "request_id": setup_key,
            }})
            ref, revision = _execution_identity(loaded)
            if ref is None:
                tag = (loaded.get("data") or {}).get("tag")
                if isinstance(tag, str) and tag:
                    adopted = await submitting.call("model_adopt", {"model_tag": tag})
                    ref, revision = _execution_identity(adopted)
                else:
                    adopted = None
            else:
                adopted = None
            evidence.update(setup_connect=connected, setup_load=loaded, setup_adopt=adopted)
        if ref is None or revision is None:
            assertions["production_fixture_load"] = False
            return "BLOCKED"
        assertions["production_fixture_load"] = True
        execution = {
            "model_ref": ref,
            "session_id": ref["session_id"],
            "expected_revision": revision,
            "idempotency_key": args.idempotency_key,
            "request_id": "t027-" + args.idempotency_key,
            "rpc_timeout_s": 0,
            "queue_timeout_s": None,
            "execution_timeout_s": args.execution_timeout_s,
            "no_progress_warning_s": args.no_progress_warning_s,
        }
        submitted_arguments = {"study_tag": args.study_tag, "execution": execution}
        submitted = await submitting.call("run_study", submitted_arguments)
        job_id = ((submitted.get("data") or {}).get("job_id") or (submitted.get("execution") or {}).get("job_id"))
        if not isinstance(job_id, str) or not job_id:
            evidence["submission"] = submitted
            assertions["solve_job_created"] = False
            return "BLOCKED"
        deadline = time.monotonic() + args.running_window_seconds
        before = await _query(submitting, job_id)
        while time.monotonic() < deadline and not (
            _status(before["status"]) == "RUNNING" and _worker_request_submitted(before)
        ):
            if _status(before["status"]) not in ACTIVE:
                break
            await asyncio.sleep(.1)
            before = await _query(submitting, job_id)
        if _status(before["status"]) == "RUNNING" and _worker_request_submitted(before):
            inspect_key = "t012-inspect-" + args.idempotency_key
            inspect = await submitting.call("model_inspect", {"refresh": False, "execution": {
                "model_ref": ref,
                "session_id": ref["session_id"],
                "idempotency_key": inspect_key,
                "request_id": inspect_key,
                "rpc_timeout_s": 0,
                "queue_timeout_s": None,
                "execution_timeout_s": None,
                "no_progress_warning_s": None,
            }})
            inspect_job_id = ((inspect.get("data") or {}).get("job_id") or (inspect.get("execution") or {}).get("job_id"))
            inspect_before = await _query(submitting, inspect_job_id) if isinstance(inspect_job_id, str) else {}
            samples, before = await _sample_pending_control_plane(
                submitting, job_id, minimum=args.control_plane_samples, deadline=deadline)
        else:
            inspect, inspect_job_id, inspect_before, samples = None, None, {}, []
    # The submitting stdio host has now disconnected during its own real solve.
    assertions.update(
        solve_job_created=True,
        real_solve_running_at_disconnect=_status(before["status"]) == "RUNNING",
        worker_request_submitted_before_disconnect=_worker_request_submitted(before),
        control_plane_sample_count=len(samples) >= args.control_plane_samples,
        control_plane_p95_under_one_second=all(
            (_p95([sample[key] for sample in samples]) or float("inf")) < 1.0
            for key in ("health_s", "status_s", "log_s")),
        inspect_job_created=isinstance(inspect_job_id, str),
        inspect_queued_behind_solver=_status(inspect_before.get("status", {})) == "QUEUED",
        inspect_has_no_java_submission_while_solver_pending=not _has_worker_submission(inspect_before),
    )
    if args.execution_timeout_s is None:
        assertions["null_execution_timeout_recorded"] = execution["execution_timeout_s"] is None
    else:
        assertions.update(
            execution_deadline_warned_while_engine_running=any(
                event.get("event") == "ExecutionDeadlineExceeded" for event in _events(before)),
            execution_deadline_never_cancelled=_status(before["status"]) == "RUNNING",
        )
    if args.no_progress_warning_s is not None:
        assertions["no_progress_warning_recorded_while_engine_running"] = any(
            event.get("event") == "NoProgressWarning" for event in _events(before)) and _status(before["status"]) == "RUNNING"
    durable_before = _job_row(control, job_id)
    if not all(assertions.values()):
        evidence.update(submission=submitted, inspect=inspect, inspect_before=inspect_before, control_plane_samples=samples,
                        before_disconnect=before, durable_before=durable_before,
                        effective_execution=execution,
                        blocked_reason="fixture did not expose the required real pending-solver acceptance window")
        return "BLOCKED"
    async with Host(params, log, transcript, args.run_dir, "fresh-host") as fresh:
        # Exact operation/body/key retry must reuse the existing durable job.
        same_key = await fresh.call("run_study", submitted_arguments)
        after = await _terminal(fresh, job_id, args.wait_seconds)
        inspect_after = await _terminal(fresh, inspect_job_id, args.wait_seconds)
    durable_after = _job_row(control, job_id)
    terminal_status = _status(after["status"])
    assertions.update(
        fresh_host_reuses_original_job=(
            ((same_key.get("data") or {}).get("job_id") or (same_key.get("execution") or {}).get("job_id")) == job_id
        ),
        fresh_host_queries_original_job=_same_job(after["status"], job_id),
        job_operation_id_preserved=durable_before["operation_id"] == durable_after["operation_id"],
        single_durable_operation=durable_before["operation_count"] == durable_after["operation_count"] == 1,
        original_result_success=terminal_status == "SUCCEEDED" and _result_success(after),
        inspect_runs_only_after_solver_finished=(
            _status(inspect_after["status"]) == "SUCCEEDED"
            and _has_worker_submission(inspect_after)
            and terminal_status == "SUCCEEDED"
        ),
    )
    evidence.update(submission=submitted, inspect=inspect, inspect_before=inspect_before, inspect_after=inspect_after,
                    control_plane_samples=samples,
                    control_plane_p95_s={key: _p95([sample[key] for sample in samples]) for key in ("health_s", "status_s", "log_s")},
                    effective_execution=execution, same_key_retry=same_key, before_disconnect=before,
                    after_fresh_host=after, durable_before=durable_before, durable_after=durable_after,
                    job_id=job_id)
    if terminal_status in ACTIVE or terminal_status in {"UNKNOWN", "RECONCILING", None}:
        evidence["blocked_reason"] = "original job has no successful terminal result; no replay was performed"
        return "BLOCKED"
    return "PASS" if all(assertions.values()) else "FAIL"


async def control_restart(args, params, evidence, assertions, transcript, log) -> str:
    control = _control_home(args.private_home)
    async with Host(params, log, transcript, args.run_dir, "control-before") as first:
        health_before, job_before = await first.call("session_health"), await _query(first, args.job_id)
    target = _verified_pid(_read_json(control / "control.json"), "comsol_mcp._control_daemon", control, args.comsol_pid)
    os.kill(target["pid"], signal.SIGTERM)
    if not _wait_dead(target["pid"]): raise RuntimeError("verified control daemon did not terminate")
    async with Host(params, log, transcript, args.run_dir, "control-after") as fresh:
        started = await fresh.call("session_health"); connected = await _connect(fresh, args, "recovery-control-connect"); health_after = await fresh.call("session_health"); job_after = await _query(fresh, args.job_id)
        reconciled = await fresh.call("job_reconcile", {"job_id": args.job_id}) if _status(job_after["status"]) in {"UNKNOWN", "RECONCILING"} else None
    endpoint_after = _read_json(control / "control.json")
    assertions.update(control_pid_replaced=endpoint_after.get("pid") != target["pid"], private_control_restarted=started.get("success") is True, reconnected_existing_server=bool(connected.get("success")), same_job_after_restart=_same_job(job_after["status"], args.job_id), sameworker_restart_identity_preserved=_worker_identity(health_before) is not None and _worker_identity(health_before) == _worker_identity(health_after), reconcile_never_replays=reconciled is None or reconciled.get("data", {}).get("metadata", {}).get("replay_performed") is False)
    evidence.update(control_target=target, control_after_pid=endpoint_after.get("pid"), health_before=health_before, health_after=health_after, job_before=job_before, job_after=job_after, reconciled=reconciled)
    return "PASS" if all(assertions.values()) else "FAIL"


async def worker_replace(args, params, evidence, assertions, transcript, log) -> str:
    control = _control_home(args.private_home)
    async with Host(params, log, transcript, args.run_dir, "worker-before") as first: health_before, job_before = await first.call("session_health"), await _query(first, args.job_id)
    worker_path = control / "worker" / "worker_endpoint.json"; old_worker = _read_json(worker_path)
    worker_target = _verified_pid(old_worker, "comsol_mcp.worker_java.PersistentComsolWorker", control / "worker", args.comsol_pid)
    old_ref = _model_ref(args.model_ref_json)
    os.kill(worker_target["pid"], signal.SIGTERM)
    if not _wait_dead(worker_target["pid"]): raise RuntimeError("verified Java worker did not terminate")
    # Restarting the verified daemon after worker loss causes normal private
    # rendezvous recovery; it is not a COMSOL lifecycle operation.
    control_target = _verified_pid(_read_json(control / "control.json"), "comsol_mcp._control_daemon", control, args.comsol_pid)
    os.kill(control_target["pid"], signal.SIGTERM)
    if not _wait_dead(control_target["pid"]): raise RuntimeError("verified control daemon did not terminate")
    async with Host(params, log, transcript, args.run_dir, "worker-after") as fresh:
        connected = await _connect(fresh, args, "recovery-worker-connect"); health_after = await fresh.call("session_health")
        key = "recovery-stale-ref-" + uuid4().hex
        stale = await fresh.call("model_inspect", {"execution": {"model_ref": old_ref, "idempotency_key": key, "request_id": key}})
        job_after = await _query(fresh, args.job_id); reconciled = await fresh.call("job_reconcile", {"job_id": args.job_id}) if _status(job_after["status"]) in {"UNKNOWN", "RECONCILING"} else None
    new_worker = _read_json(worker_path); error = stale.get("error", {}) if isinstance(stale.get("error"), dict) else {}
    assertions.update(reconnected_existing_server=bool(connected.get("success")), worker_pid_replaced=new_worker.get("pid") != worker_target["pid"], worker_generation_advanced=type(old_worker.get("generation")) is int and type(new_worker.get("generation")) is int and new_worker["generation"] > old_worker["generation"], old_model_ref_rejected=not stale.get("success") and stale.get("_outer_isError") and error.get("code") == "MODEL_IDENTITY_MISMATCH", same_job_after_worker_replacement=_same_job(job_after["status"], args.job_id), unfinished_job_marked_unknown_or_reconciling=_status(job_before["status"]) not in ACTIVE or _status(job_after["status"]) in {"UNKNOWN", "RECONCILING"}, reconcile_never_replays=reconciled is None or reconciled.get("data", {}).get("metadata", {}).get("replay_performed") is False)
    evidence.update(worker_target=worker_target, worker_before=old_worker, worker_after=new_worker, control_target=control_target, health_before=health_before, health_after=health_after, job_before=job_before, job_after=job_after, stale_model_ref=stale, reconciled=reconciled)
    return "PASS" if all(assertions.values()) else "FAIL"


async def path_security(args, params, evidence, assertions, transcript, log) -> str:
    link = args.run_dir / "symlink-escape.mph"
    if link.exists() or link.is_symlink(): raise RuntimeError("refuse pre-existing path-security fixture")
    outside = Path("/private/tmp") / f"phase2-recovery-outside-{uuid4().hex}.mph"; link.symlink_to(outside)
    requests = {"outside_absolute": str(outside), "parent_escape": "../outside.mph", "private_credential_path": str(args.private_home / ".phase1-private" / "credential.mph"), "symlink_escape": str(link)}
    try:
        async with Host(params, log, transcript, args.run_dir, "path-security") as host:
            connected = await _connect(host, args, "t035-connect")
            if not connected.get("success"):
                evidence["connect_before_path_checks"] = connected
                return "BLOCKED"
            replies = {}
            for name, path in requests.items():
                key = "t035-" + name + "-" + uuid4().hex
                replies[name] = await host.call("model_load", {"path": path, "execution": {"idempotency_key": key, "request_id": key}})
        for name, reply in replies.items():
            error = reply.get("error", {}) if isinstance(reply.get("error"), dict) else {}
            assertions[name + "_denied"] = not reply.get("success") and reply.get("_outer_isError") and error.get("code") == "PERMISSION_DENIED"
        assertions["credential_content_not_logged"] = "credential.mph" not in json.dumps(_redact(replies), ensure_ascii=False)
        evidence.update(path_requests={name: "REDACTED" for name in requests}, path_replies=replies)
        return "PASS" if all(assertions.values()) else "FAIL"
    finally:
        if link.is_symlink(): link.unlink()


async def run(args) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"); args.private_home = args.private_home.resolve()
    args.run_dir = Path(args.run_dir).resolve() if args.run_dir else ROOT / "evidence/phase2/recovery" / stamp; args.run_dir.mkdir(parents=True, exist_ok=False)
    cases = {"host-disconnect": "T012/T027/T055", "control-restart": "T028", "worker-replace": "T028", "path-security": "T035"}; transcript, assertions = [], {}; evidence = {"mode": args.mode, "started_at": time.time()}; result = {"case": cases[args.mode], "status": "NOT_RUN"}
    _write(args.run_dir / "request.json", {"mode": args.mode, "job_id": args.job_id, "model_ref_json": str(args.model_ref_json), "model_path": str(args.model_path) if args.model_path else None, "route": "production stdio MCP ClientSession", "fault_policy": "verified private control/worker only", "control_plane_samples": args.control_plane_samples, "execution_timeout_s": args.execution_timeout_s, "no_progress_warning_s": args.no_progress_warning_s})
    _write(args.run_dir / "environment.json", {"os": sys.platform, "python": sys.version, "comsol_root": args.comsol_root, "jdk11": args.jdk11, "server": {"pid": args.comsol_pid, "port": args.port}, "private_home": "REDACTED"})
    env = dict(os.environ, COMSOL_ROOT=args.comsol_root, JAVA_HOME=args.jdk11, COMSOL_PREFS_DIR=args.prefs, COMSOL_SERVER_MCP_HOME=str(args.private_home), PYTHONPATH=str(ROOT)); params = StdioServerParameters(command=args.python, args=["-m", "comsol_mcp.mcp_server"], env=env, cwd=str(ROOT))
    try:
        with (args.run_dir / "engine.log").open("w", encoding="utf-8") as log:
            result["status"] = await {"host-disconnect": host_disconnect, "control-restart": control_restart, "worker-replace": worker_replace, "path-security": path_security}[args.mode](args, params, evidence, assertions, transcript, log)
    except Exception as exc: result.update(status="FAIL", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        evidence["finished_at"] = time.time(); _write(args.run_dir / "transcript.json", transcript); _write(args.run_dir / "assertions.json", assertions); _write(args.run_dir / "result.json", {**result, "evidence": evidence}); _write(args.run_dir / "SHA256SUMS.json", {p.name: _sha256(p) for p in args.run_dir.iterdir() if p.is_file()})
    print(json.dumps({"run_dir": str(args.run_dir), "case": result["case"], "status": result["status"]}, ensure_ascii=False)); return 0 if result["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("host-disconnect", "control-restart", "worker-replace", "path-security"), required=True)
    parser.add_argument("--private-home", type=Path, required=True)
    parser.add_argument("--prefs", required=True)
    parser.add_argument("--job-id", help="Existing job for T028; T027 creates its own solve job.")
    parser.add_argument("--idempotency-key", help="T027 solve key, reused verbatim from the fresh host.")
    parser.add_argument("--model-ref-json", type=Path)
    parser.add_argument("--model-path", type=Path,
                        help="Load this isolated MPH through the submitting production MCP host before T012/T027/T055.")
    parser.add_argument("--revision", type=int, help="Expected revision for the T027 run_study submission.")
    parser.add_argument("--study-tag", default="std1")
    parser.add_argument("--running-window-seconds", type=float, default=30.0)
    parser.add_argument("--control-plane-samples", type=int, default=20)
    parser.add_argument("--execution-timeout-s", type=float, default=None,
                        help="Optional T055 execution deadline; it records a warning and never cancels COMSOL.")
    parser.add_argument("--no-progress-warning-s", type=float, default=None,
                        help="Optional no-progress warning budget; null leaves it disabled.")
    parser.add_argument("--comsol-pid", type=int, default=84749)
    parser.add_argument("--port", type=int, default=56388)
    parser.add_argument("--comsol-root", default="/Applications/COMSOL64/Multiphysics")
    parser.add_argument("--jdk11", default=JDK11_DEFAULT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    parser.add_argument("--run-dir")
    args = parser.parse_args()
    if (args.wait_seconds < 0 or args.running_window_seconds < 0 or args.control_plane_samples < 20
            or any(value is not None and value < 0 for value in (args.execution_timeout_s, args.no_progress_warning_s))):
        parser.error("durations must be non-negative and --control-plane-samples must be at least 20")
    if args.mode == "host-disconnect" and (not args.idempotency_key or (args.model_ref_json is None and args.model_path is None) or (args.model_path is None and args.revision is None)):
        parser.error("host-disconnect requires --idempotency-key and either --model-path or --model-ref-json with --revision")
    if args.mode in {"control-restart", "worker-replace"} and not args.job_id:
        parser.error(f"{args.mode} requires --job-id")
    if args.mode == "worker-replace" and args.model_ref_json is None:
        parser.error("worker-replace requires --model-ref-json")
    return asyncio.run(run(args))


if __name__ == "__main__": raise SystemExit(main())
