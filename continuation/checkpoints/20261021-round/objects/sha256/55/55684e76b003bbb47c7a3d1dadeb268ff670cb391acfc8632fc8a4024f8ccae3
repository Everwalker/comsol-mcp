#!/usr/bin/env python3
"""Real Phase-2 MCP acceptance entrypoint (never starts or stops COMSOL).

This driver intentionally uses the production ``python -m comsol_mcp.mcp_server``
stdio transport.  It is an executable evidence entrypoint, not a mock test: an
unavailable managed worker or an unsupported live operation is recorded as
NOT_RUN/BLOCKED in the run directory instead of being simulated.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import traceback
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "evidence/w02/runs/20260918T110411819403Z/model.mph"
JDK11_DEFAULT = "/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home"
_RUN_IDEMPOTENCY_PREFIX = ""


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "REDACTED" if any(word in key.lower() for word in ("token", "password", "credential", "prefs", "authorization")) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_redact(_json_safe(value)), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _server_identity(pid: int, port: int) -> dict[str, str]:
    ps = subprocess.check_output(["ps", "-p", str(pid), "-o", "pid=,lstart=,command="], text=True).strip()
    listeners = subprocess.check_output(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"], text=True).strip()
    if "comsol" not in ps.lower() or f":{port} " not in listeners:
        raise RuntimeError("Registered COMSOL server PID/port identity mismatch; attach refused.")
    return {"ps": ps, "listeners": listeners}


def _execution(ref: dict[str, Any] | None = None, revision: int | None = None, *, key: str, request: str, **timeouts: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"idempotency_key": _RUN_IDEMPOTENCY_PREFIX + key, "request_id": request, **timeouts}
    if ref is not None:
        data["model_ref"] = ref
        data["session_id"] = ref["session_id"]
        data["expected_revision"] = revision
    return data


def _extract_execution(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    execution = payload.get("execution", {}) if isinstance(payload, dict) else {}
    return execution.get("model_ref"), execution.get("revision")


def _metric_ok(payload: dict[str, Any]) -> bool:
    data = payload.get("data", {})
    rows = data.get("results", []) if isinstance(data, dict) else []
    value = rows[0].get("value") if len(rows) == 1 else None
    while isinstance(value, list) and value:
        value = value[-1]
    return bool(payload.get("success")) and len(rows) == 1 and rows[0].get("ok") and abs(float(value)) < 1e-8


async def run(args: argparse.Namespace) -> int:
    global _RUN_IDEMPOTENCY_PREFIX
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    # The control store is deliberately durable across MCP hosts.  Scope each
    # acceptance run's keys to its evidence stamp, while retries inside that
    # run keep the same key and remain idempotent.
    _RUN_IDEMPOTENCY_PREFIX = stamp + "-"
    run_dir = Path(args.run_dir).resolve() if args.run_dir else ROOT / "evidence/phase2/runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    transcript: list[dict[str, Any]] = []
    assertions: dict[str, Any] = {}
    result: dict[str, Any] = {"status": "NOT_RUN", "cases": {}}
    saved_output: Path | None = None
    request_plan = {
        "model": str(MODEL), "server": {"pid": args.pid, "host": "127.0.0.1", "port": args.port},
        "required": ["connect/load", "revision", "idempotency", "metrics", "atomic save", "fresh MCP reopen"],
        "not_run_without_real_long_solve": ["T012 p95 during long solve", "T026 timeout queueing", "T027 host disconnect reconciliation"],
    }
    _write(run_dir / "request.json", request_plan)
    try:
        identity = _server_identity(args.pid, args.port)
        if not MODEL.is_file():
            raise FileNotFoundError(f"Accepted W02 fixture not found: {MODEL}")
        private_home = Path(args.private_home).resolve() if args.private_home else ROOT / ".phase1-private" / f"phase2-live-{stamp}"
        env = dict(os.environ)
        env.update({
            "COMSOL_ROOT": args.comsol_root,
            "JAVA_HOME": args.jdk11,
            "COMSOL_PREFS_DIR": args.prefs,
            "COMSOL_SERVER_MCP_HOME": str(private_home),
            "PYTHONPATH": str(ROOT),
        })
        _write(run_dir / "environment.json", {
            "os": sys.platform, "python": sys.version, "fixture_sha256": _sha256(MODEL),
            "server_identity": identity, "comsol_root": args.comsol_root,
            "jdk11": args.jdk11, "mcp_transport": "python -m comsol_mcp.mcp_server",
            "private_home_configured": True,
        })
        params = StdioServerParameters(command=args.python, args=["-m", "comsol_mcp.mcp_server"], env=env, cwd=str(ROOT))
        with (run_dir / "engine.log").open("w", encoding="utf-8") as stderr:
            async with stdio_client(params, errlog=stderr) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=timedelta(minutes=10)) as session:
                    initialized = await session.initialize()
                    transcript.append({"operation": "initialize", "response": _json_safe(initialized)})

                    async def call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
                        started = time.monotonic()
                        response = await session.call_tool(name, arguments or {})
                        elapsed = time.monotonic() - started
                        dumped = _json_safe(response)
                        try:
                            payload = json.loads(response.content[0].text)
                        except Exception:
                            payload = {"success": False, "error": "non-JSON MCP response", "raw": dumped}
                        transcript.append({"operation": name, "arguments": arguments or {}, "elapsed_s": elapsed, "outer_isError": response.isError, "payload": payload})
                        _write(run_dir / "transcript.json", transcript)
                        return {**payload, "_outer_isError": bool(response.isError), "_elapsed_s": elapsed}

                    if args.health_only:
                        health = await call("session_health")
                        assertions["fresh_stdio_session_health"] = bool(health.get("success") and not health.get("_outer_isError"))
                        result.update({
                            "status": "PASS" if assertions["fresh_stdio_session_health"] else "FAIL",
                            "scope": "Fresh production MCP stdio host to existing control daemon; health only, no COMSOL connection or model action.",
                        })
                        return 0 if result["status"] == "PASS" else 1

                    configured = await call("configure_single_main_workflow", {
                        "current_main_model_path": str(run_dir / "current-main.mph"),
                        "snapshot_dir": str(run_dir / "snapshots"),
                        "snapshot_prefix": "phase2",
                        "model_dimension": 2,
                        "notes": "Phase 2 live 2D fixture workflow; snapshots are retained with public run evidence.",
                    })
                    assertions["workflow_configured_2d"] = bool(
                        configured.get("success")
                        and (configured.get("data") or {}).get("workflow", {}).get("model_dimension") == 2
                        and (configured.get("data") or {}).get("workflow", {}).get("snapshot_dir") == str((run_dir / "snapshots").resolve())
                    )
                    connect_arguments = {"host": "127.0.0.1", "port": args.port, "execution": _execution(key="phase2-connect", request="phase2-connect")}
                    if args.adopt_tag:
                        connect_arguments["model_name"] = args.adopt_tag
                    connected = await call("server_connect", connect_arguments)
                    loaded = connected if args.adopt_tag else await call("model_load", {"path": str(MODEL), "execution": _execution(key="phase2-load", request="phase2-load")})
                    ref, revision = _extract_execution(loaded)
                    # A managed backend may require explicit adoption after legacy load.
                    if ref is None:
                        tag = (loaded.get("data") or {}).get("tag")
                        if tag:
                            adopted = await call("model_adopt", {"model_tag": tag})
                            ref, revision = _extract_execution(adopted)
                    assertions["connect_load"] = bool(connected.get("success") and loaded.get("success") and ref and isinstance(revision, int))
                    if not assertions["connect_load"]:
                        result["status"] = "BLOCKED"
                        result["reason"] = "Managed worker did not return a bound model_ref/revision; remaining live cases were not attempted."
                    else:
                        tree_before = await call("model_tree", {"execution": _execution(ref, revision, key="phase2-feature-tree-before", request="phase2-feature-tree-before")})
                        same_feature = await call("create_feature", {
                            "component": "comp1", "geometry": "geom1", "tag": "r1", "feature_type": "Rectangle",
                            "properties_json": "[]", "run_geometry": False,
                            "execution": _execution(ref, revision, key="phase2-feature-same", request="phase2-feature-same"),
                        })
                        same_ref, same_revision = _extract_execution(same_feature)
                        ref, revision = same_ref or ref, same_revision if isinstance(same_revision, int) else revision
                        tree_after_same = await call("model_tree", {"execution": _execution(ref, revision, key="phase2-feature-tree-after-same", request="phase2-feature-tree-after-same")})
                        type_conflict = await call("create_feature", {
                            "component": "comp1", "geometry": "geom1", "tag": "r1", "feature_type": "Circle",
                            "properties_json": "[]", "run_geometry": False,
                            "execution": _execution(ref, revision, key="phase2-feature-conflict", request="phase2-feature-conflict"),
                        })
                        assertions["feature_same_tag_type_idempotent"] = bool(
                            same_feature.get("success") and (same_feature.get("data") or {}).get("created") is False
                            and tree_before.get("data") == tree_after_same.get("data")
                        )
                        assertions["feature_same_tag_type_conflict"] = bool(
                            not type_conflict.get("success") and type_conflict.get("_outer_isError")
                            and "type conflict" in json.dumps(type_conflict).lower()
                        )
                        writes = {"parameters_json": '[{"name":"phase2_guard","expression":"1"}]'}
                        first = await call("set_parameters", {**writes, "execution": _execution(ref, revision, key="phase2-idem", request="phase2-idem")})
                        second = await call("set_parameters", {**writes, "execution": _execution(ref, revision, key="phase2-idem", request="phase2-idem-retry")})
                        changed_ref, changed_revision = _extract_execution(first)
                        assertions["idempotency_same_body"] = bool(first.get("success") and second.get("success") and first.get("execution", {}).get("operation_id") == second.get("execution", {}).get("operation_id"))
                        conflict = await call("set_parameters", {"parameters_json": '[{"name":"phase2_guard","expression":"2"}]', "execution": _execution(ref, revision, key="phase2-idem", request="phase2-idem-conflict")})
                        assertions["idempotency_different_body"] = bool(not conflict.get("success") and conflict.get("_outer_isError") and "IDEMPOTENCY_CONFLICT" in json.dumps(conflict))
                        stale = await call("set_parameters", {**writes, "execution": _execution(ref, revision, key="phase2-stale", request="phase2-stale")})
                        assertions["stale_revision"] = bool(not stale.get("success") and stale.get("_outer_isError") and "REVISION_CONFLICT" in json.dumps(stale))
                        ref, revision = changed_ref or ref, changed_revision if isinstance(changed_revision, int) else revision
                        metric_bad = await call("get_core_metrics", {"metrics_json": '[{"name":"bad","expression":"phase2_missing_symbol","aggregate":"max","domains":[1]}]', "execution": _execution(ref, revision, key="phase2-bad-metric", request="phase2-bad-metric")})
                        metric_bad_text = json.dumps(metric_bad).lower()
                        assertions["required_metric_outer_error"] = bool(
                            not metric_bad.get("success") and metric_bad.get("_outer_isError")
                            and "phase2_missing_symbol" in metric_bad_text
                            and ("undefined" in metric_bad_text or "unknown" in metric_bad_text)
                        )
                        # Load a copy, not the original path: same-path loads may
                        # correctly adopt the first model and cannot establish a
                        # second server model for the per-server queue case.
                        second_path = run_dir / "second.mph"
                        shutil.copy2(MODEL, second_path)
                        second_loaded = await call("model_load", {"path": str(second_path), "execution": _execution(key="phase2-second-load", request="phase2-second-load")})
                        second_ref, second_revision = _extract_execution(second_loaded)
                        assertions["second_model_bound"] = bool(second_loaded.get("success") and second_ref and isinstance(second_revision, int))
                        if not assertions["second_model_bound"]:
                            result["status"] = "BLOCKED"
                            result["reason"] = "Managed model_load did not return a second root execution.model_ref/revision."
                            raise RuntimeError(result["reason"])
                        # Re-select the original bound model before submitting its
                        # solve.  Legacy callbacks are still being routed through
                        # a selected model during the transition, so this prevents
                        # the second-model setup from silently changing the target.
                        reselected = await call("model_adopt", {"model_tag": ref["model_tag"], "execution": _execution(key="phase2-reselect-first", request="phase2-reselect-first")})
                        selected_ref, selected_revision = _extract_execution(reselected)
                        if selected_ref is None or not isinstance(selected_revision, int):
                            result["status"] = "BLOCKED"
                            result["reason"] = "Managed re-selection did not return the first model_ref/revision."
                            raise RuntimeError(result["reason"])
                        ref, revision = selected_ref, selected_revision
                        # A zero RPC wait is deliberately not cancellation.  If the
                        # solve exposes a RUNNING/QUEUED window, sample cached job
                        # state while it continues; if it completes too quickly the
                        # evidence remains explicitly insufficient for T012/T026.
                        solved = await call("run_study", {"study_tag": "std1", "execution": _execution(ref, revision, key="phase2-solve", request="phase2-solve", rpc_timeout_s=0, queue_timeout_s=None, execution_timeout_s=None)})
                        job_id = ((solved.get("data") or {}).get("job_id") or (solved.get("execution") or {}).get("job_id") or (solved.get("execution") or {}).get("operation_id"))
                        observations = []
                        queued_expired: dict[str, Any] | None = None
                        if isinstance(job_id, str) and job_id:
                            for _ in range(10):
                                health = await call("session_health")
                                status = await call("job_status", {"job_id": job_id})
                                log_page = await call("job_log", {"job_id": job_id, "offset": 0, "limit": 20})
                                observations.append({"health_s": health["_elapsed_s"], "status": status.get("data", {}).get("status"), "log_s": log_page["_elapsed_s"]})
                                if observations[-1]["status"] not in {"QUEUED", "STARTING", "RUNNING"}:
                                    break
                                if observations[-1]["status"] == "RUNNING" and queued_expired is None:
                                    queued_expired = await call("set_parameters", {
                                        "parameters_json": '[{"name":"phase2_second_expired","expression":"1"}]',
                                        "execution": _execution(second_ref, second_revision, key="phase2-second-expired", request="phase2-second-expired", queue_timeout_s=0),
                                    })
                                await asyncio.sleep(0.05)
                        assertions["live_job_observations"] = observations
                        if observations and observations[-1]["status"] in {"QUEUED", "STARTING", "RUNNING"} and isinstance(job_id, str):
                            deadline = time.monotonic() + 60.0
                            while time.monotonic() < deadline:
                                terminal = await call("job_status", {"job_id": job_id})
                                state = (terminal.get("data") or {}).get("status")
                                if state not in {"QUEUED", "STARTING", "RUNNING"}:
                                    observations.append({"health_s": None, "status": state, "log_s": None})
                                    break
                                await asyncio.sleep(0.2)
                            else:
                                result["status"] = "BLOCKED"
                                result["reason"] = "Real solve remained active beyond the driver's bounded observation; no conflicting write was submitted."
                        if isinstance(job_id, str):
                            completed = await call("job_status", {"job_id": job_id})
                            original = (completed.get("data") or {}).get("result") or {}
                            done_ref, done_revision = _extract_execution(original)
                            if done_ref is not None:
                                ref, revision = done_ref, done_revision
                        solve_ref, solve_revision = _extract_execution(solved)
                        if solve_ref is not None:
                            ref, revision = solve_ref, solve_revision
                        if result.get("status") == "BLOCKED":
                            raise RuntimeError(result["reason"])
                        if queued_expired is not None:
                            expired_job = ((queued_expired.get("data") or {}).get("job_id") or (queued_expired.get("execution") or {}).get("job_id") or (queued_expired.get("execution") or {}).get("operation_id"))
                            expired_status = await call("job_status", {"job_id": expired_job}) if isinstance(expired_job, str) else {"success": False, "data": {}}
                            assertions["queue_timeout_zero"] = {
                                "initial": queued_expired,
                                "after_solve_status": expired_status,
                                "pass": bool((queued_expired.get("data") or {}).get("status") in {"QUEUED", "EXPIRED", "NOT_EXECUTED"} and (expired_status.get("data") or {}).get("status") in {"EXPIRED", "CANCELLED", "NOT_EXECUTED"}),
                            }
                            queued_after = await call("set_parameters", {
                                "parameters_json": '[{"name":"phase2_second_after","expression":"1"}]',
                                "execution": _execution(second_ref, second_revision, key="phase2-second-after", request="phase2-second-after", queue_timeout_s=None),
                            })
                            assertions["queue_after_solve"] = bool(queued_after.get("success"))
                        metrics = await call("get_core_metrics", {"metrics_json": '[{"name":"max_abs_error","expression":"abs(u-2)","aggregate":"max","domains":[1]}]', "execution": _execution(ref, revision, key="phase2-metric", request="phase2-metric")})
                        assertions["real_metric"] = _metric_ok(metrics)
                        metric_ref, metric_revision = _extract_execution(metrics)
                        if metric_ref is not None:
                            ref, revision = metric_ref, metric_revision
                        saved = await call("save_model", {"path": str(run_dir / "after.mph"), "execution": _execution(ref, revision, key="phase2-save", request="phase2-save")})
                        assertions["saved"] = bool(saved.get("success") and (run_dir / "after.mph").is_file())
                        if assertions["saved"]:
                            saved_output = run_dir / "after.mph"
                        # Include any solve-window health samples in p95.  A short
                        # fixture is never promoted to the long-solve acceptance.
                        health_latencies = [item["health_s"] for item in observations if isinstance(item.get("health_s"), (int, float))] or [(await call("session_health"))["_elapsed_s"] for _ in range(5)]
                        assertions["health_p95_s"] = statistics.quantiles(health_latencies, n=20)[18] if len(health_latencies) >= 2 else max(health_latencies)
                        result["cases"].update({"T012": "NOT_RUN", "T026": "PASS" if assertions.get("queue_timeout_zero", {}).get("pass") and assertions.get("queue_after_solve") else "NOT_RUN", "T027": "NOT_RUN"})
                        required = [v for k, v in assertions.items() if k not in {"health_p95_s", "live_job_observations", "queue_timeout_zero"}]
                        result["status"] = "PASS" if all(v is True for v in required) else "FAIL"
                        if result["status"] == "PASS":
                            result["scope"] = "Phase 2 live connect/load/write/solve/metric/save/fresh-MCP-reopen; T012 and T027 are NOT_RUN."
        # The first stdio host is now closed.  Create a genuinely new MCP host,
        # reattach to the same control daemon, load the saved MPH, and evaluate
        # it.  This is intentionally not labelled a fresh Java Worker.
        if saved_output is not None and result.get("status") == "PASS":
            with (run_dir / "reopen_engine.log").open("w", encoding="utf-8") as reopen_stderr:
                async with stdio_client(params, errlog=reopen_stderr) as (reader, writer):
                    async with ClientSession(reader, writer, read_timeout_seconds=timedelta(minutes=10)) as fresh:
                        await fresh.initialize()
                        async def fresh_call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
                            started = time.monotonic()
                            response = await fresh.call_tool(name, arguments or {})
                            try:
                                payload = json.loads(response.content[0].text)
                            except Exception:
                                payload = {"success": False, "error": "non-JSON MCP response", "raw": _json_safe(response)}
                            transcript.append({"host": "fresh", "operation": name, "arguments": arguments or {}, "elapsed_s": time.monotonic() - started, "outer_isError": response.isError, "payload": payload})
                            _write(run_dir / "transcript.json", transcript)
                            return {**payload, "_outer_isError": bool(response.isError)}
                        reopened_connect = await fresh_call("server_connect", {"host": "127.0.0.1", "port": args.port, "execution": _execution(key="phase2-reopen-connect", request="phase2-reopen-connect")})
                        reopened = await fresh_call("model_load", {"path": str(saved_output), "execution": _execution(key="phase2-reopen-load", request="phase2-reopen-load")})
                        reopen_ref, reopen_revision = _extract_execution(reopened)
                        if reopen_ref is None:
                            tag = (reopened.get("data") or {}).get("tag")
                            if tag:
                                adopted = await fresh_call("model_adopt", {"model_tag": tag})
                                reopen_ref, reopen_revision = _extract_execution(adopted)
                        reopened_metric = await fresh_call("get_core_metrics", {"metrics_json": '[{"name":"reopen_max_abs_error","expression":"abs(u-2)","aggregate":"max","domains":[1]}]', "execution": _execution(reopen_ref, reopen_revision, key="phase2-reopen-metric", request="phase2-reopen-metric")}) if reopen_ref is not None else {"success": False}
                        assertions["fresh_mcp_host_reopen"] = bool(reopened_connect.get("success") and reopened.get("success") and reopen_ref and _metric_ok(reopened_metric))
                        if not assertions["fresh_mcp_host_reopen"]:
                            result["status"] = "FAIL"
                        result["fresh_host"] = {"mcp_process": "new", "java_worker": "not asserted fresh", "saved_path": str(saved_output)}
    except Exception as exc:
        if result.get("status") == "BLOCKED":
            result.update({"blocked_detail": str(exc)})
        else:
            result.update({"status": "FAIL", "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        _write(run_dir / "assertions.json", assertions)
        _write(run_dir / "result.json", result)
        hashes = {path.name: _sha256(path) for path in run_dir.iterdir() if path.is_file()}
        _write(run_dir / "SHA256SUMS.json", hashes)
    print(json.dumps({"run_dir": str(run_dir), "status": result["status"]}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=84749)
    parser.add_argument("--port", type=int, default=56388)
    parser.add_argument("--prefs", required=True, help="Private COMSOL preference directory; never copied to evidence.")
    parser.add_argument("--private-home", help="Existing private Phase 2 home to reattach to; never copied to evidence.")
    parser.add_argument("--health-only", action="store_true", help="Verify a fresh MCP stdio host reaches the existing control daemon without COMSOL actions.")
    parser.add_argument("--adopt-tag", help="Recover an already observed server model by tag instead of submitting another model_load.")
    parser.add_argument("--run-dir", help="Explicit public evidence directory; default is evidence/phase2/runs/<UTC>.")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter used for each production MCP stdio host.")
    parser.add_argument("--comsol-root", default="/Applications/COMSOL64/Multiphysics")
    parser.add_argument("--jdk11", default=JDK11_DEFAULT)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
