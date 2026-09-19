"""Durable job control with one serial engine queue and responsive cached reads."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
import traceback
from typing import Any
from uuid import uuid4

from ._execution_contract import ExecutionContractError, canonical_request_hash
from ._managed_backend import ManagedBackend, ProcessLock, collect_legacy_registry
from ._operation_store import IdempotencyConflict, OperationStore
from ._platform_process import process_identity

TERMINAL = {"SUCCEEDED", "FAILED", "EXPIRED", "LOST", "CANCELLED"}
CONTROL_READS = {"session_health", "server_info", "job_status", "job_log", "job_result", "job_reconcile", "run_study_status", "visible_main_workflow_status",
                 "registry_list", "registry_describe", "registry_search", "registry_manifest", "operation_describe",
                 "docs_search", "docs_get", "docs_examples", "docs_error_search", "checkpoint_list", "checkpoint_inspect", "checkpoint_diff"}


def configure_remote_backend(worker):
    import comsol_mcp._server as server
    server._remote_client_factory = worker.client


class ControlDaemon:
    def __init__(self, home, *, service=None, registry=None, worker=None):
        self.home = Path(home)
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.store = OperationStore(self.home / "operations.sqlite3")
        self.backend = ManagedBackend(self.home, self.store, service=service, registry=registry, worker=worker)
        self.queue = ThreadPoolExecutor(max_workers=1, thread_name_prefix="comsol-engine-queue")
        self.lock = threading.RLock()
        self.running = {}
        self.worker_health = {"status": "NOT_STARTED", "observed_at": None}
        self.store.reconcile_after_restart()
        self.closed = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, name="comsol-cached-control", daemon=True)
        self.monitor.start()

    @property
    def service(self): return self.backend.service

    @property
    def worker(self): return self.backend.worker

    def dispatch(self, request):
        try:
            if not isinstance(request, dict):
                raise ExecutionContractError("INVALID_REQUEST", "request must be an object")
            operation = request.get("operation")
            arguments, execution = request.get("arguments", {}), request.get("execution", {})
            if not isinstance(operation, str) or not isinstance(arguments, dict) or not isinstance(execution, dict):
                raise ExecutionContractError("INVALID_REQUEST", "invalid operation/arguments/execution")
            timeouts = self._timeouts(execution)
            if operation in CONTROL_READS:
                return self._control_read(operation, arguments)
            if operation == "runtime_poc_v64":
                raise ExecutionContractError("UNSUPPORTED_OPERATION", "historical one-shot probe is disabled in the managed backend")
            if operation not in self.backend.registry and operation not in {"model_adopt", "model_inspect"}:
                raise ExecutionContractError("UNSUPPORTED_OPERATION", "operation is not registered")
            if operation == "server_connect" and self.service is None and not (os.environ.get("COMSOL_ROOT") and (os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME"))):
                raise ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "COMSOL_ROOT and COMSOL_JAVA_HOME must be configured")
            request_id = execution.get("request_id") or str(uuid4())
            key = execution.get("idempotency_key") or str(uuid4())
            if not isinstance(request_id, str) or not isinstance(key, str) or not key:
                raise ExecutionContractError("INVALID_REQUEST", "request and idempotency identifiers must be strings")
            execution = {**execution, "request_id": request_id, "idempotency_key": key}
            digest = canonical_request_hash(operation, arguments, execution.get("model_ref"), execution.get("expected_revision"),
                session_id=execution.get("session_id"), queue_timeout_s=timeouts["queue_timeout_s"],
                execution_timeout_s=timeouts["execution_timeout_s"], no_progress_warning_s=timeouts["no_progress_warning_s"])
            record, reused = self.store.begin(request_id=request_id, idempotency_key=key, request_hash=digest,
                operation=operation, metadata={"operation": operation, "arguments": arguments, "execution": execution}, timeouts=timeouts)
            if reused:
                return record["result"] if record["result"] is not None else self._pending(record)
            submitted = time.monotonic()
            # The durable operation/job exists before it can enter the engine queue.
            with self.lock:
                future = self.queue.submit(self._execute, record, operation, arguments, execution, timeouts, submitted)
            try:
                return future.result(timeout=timeouts["rpc_timeout_s"])
            except FutureTimeout:
                return self._pending(record, rpc_wait_expired=True)
        except (ExecutionContractError, IdempotencyConflict) as exc:
            return self._exception(exc)
        except Exception as exc:
            self._log_exception()
            return self._error("EXECUTION_STATE_UNKNOWN", "control could not establish the request state", type=type(exc).__name__)

    def _execute(self, record, operation, arguments, execution, timeouts, submitted):
        job_id, operation_id = record["job_id"], record["operation_id"]
        now = time.monotonic()
        if timeouts["queue_timeout_s"] is not None and now - submitted > timeouts["queue_timeout_s"]:
            return self._finish(record, self._error("QUEUE_TIMEOUT", "request expired before engine dispatch", data={"status": "NOT_EXECUTED"}, safe_retry=True), "EXPIRED")
        unresolved = [job for job in self.store.unresolved_jobs() if job["job_id"] != job_id and job["status"] in {"UNKNOWN", "RECONCILING"} and not job["metadata"].get("reconciled_quiescent")]
        if unresolved and operation not in {"server_connect", "model_inspect"}:
            return self._finish(record, self._error("EXECUTION_STATE_UNKNOWN", "reconcile unfinished engine work before new operations"), "FAILED")
        self.store.update_job(job_id, "RUNNING", {"queue_wait_s": now - submitted, "engine_started_at": time.time()})
        self.store.add_event(job_id, "RUNNING", {"operation_id": operation_id, "at": time.time()})
        with self.lock:
            self.running[job_id] = {"started": now, "last_event": now, "timeouts": timeouts, "execution_warned": False, "progress_warned": False}
        def worker_event(event):
            # Synchronous submission events commit before a Java request is sent.
            self.store.add_event(job_id, "worker_request", event)
            with self.lock:
                if job_id in self.running:
                    self.running[job_id]["last_event"] = time.monotonic()
        try:
            result = self.backend.invoke(operation, arguments, execution, operation_id, worker_event)
            if not isinstance(result, dict) or type(result.get("success")) is not bool:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "backend returned an invalid execution envelope")
            detail = result.get("data") if isinstance(result.get("data"), dict) else {}
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            unknown = bool(
                result.get("execution_state_unknown")
                or result.get("cleanup_failed")
                or detail.get("execution_state_unknown")
                or detail.get("cleanup_failed")
                or error.get("code") in {"EXECUTION_STATE_UNKNOWN", "UNKNOWN"}
            )
            status = "UNKNOWN" if unknown else ("SUCCEEDED" if result["success"] else "FAILED")
            if result.get("execution", {}).get("dirty"):
                # A completed callback with a dirty model is a failed operation;
                # explicit model reconciliation is still required for next writes.
                result.setdefault("data", {})["requires_model_reconciliation"] = True
        except ExecutionContractError as exc:
            result = self._exception(exc)
            status = "UNKNOWN" if exc.code in {"EXECUTION_STATE_UNKNOWN", "ENGINE_UNRESPONSIVE"} else "FAILED"
        except Exception as exc:
            self._log_exception()
            result = self._error("EXECUTION_STATE_UNKNOWN", "backend execution failed; inspect worker evidence", type=type(exc).__name__)
            status = "UNKNOWN"
        finally:
            with self.lock:
                self.running.pop(job_id, None)
        return self._finish(record, result, status)

    def _finish(self, record, result, status):
        execution = result.setdefault("execution", {})
        execution.update({key: record[key] for key in ("request_id", "operation_id", "request_hash", "idempotency_key", "job_id")})
        self.store.finish(record["operation_id"], status=status, result=result)
        self.store.update_job(record["job_id"], status, {"finished_observed_at": time.time()})
        self.store.add_event(record["job_id"], status, {"operation_id": record["operation_id"], "at": time.time()})
        return result

    def _pending(self, record, **extra):
        job_id = record.get("job_id")
        if not job_id:
            job = self.store.operation_job(record["operation_id"])
            job_id = job["job_id"]
        job = self.store.job(job_id)
        if job and job.get("result") is not None:
            return job["result"]
        return {"success": True, "data": {"job_id": job_id, "status": job["status"] if job else "UNKNOWN", **extra},
                "execution": {**{k: record[k] for k in ("request_id", "operation_id", "request_hash", "idempotency_key")}, "job_id": job_id}}

    def _control_read(self, operation, arguments):
        if operation in {"registry_list", "registry_describe", "registry_search", "registry_manifest", "operation_describe"}:
            from . import _g2_registry
            try:
                if operation == "registry_list":
                    data = _g2_registry.registry_list(domain=arguments.get("domain"), cursor=arguments.get("cursor"), limit=arguments.get("limit", 100))
                elif operation == "registry_describe":
                    data = _g2_registry.registry_describe(arguments.get("operation_id", ""))
                elif operation == "operation_describe":
                    data = _g2_registry.operation_describe(arguments.get("operation_id", ""))
                elif operation == "registry_search":
                    data = _g2_registry.registry_search(arguments.get("query", ""), domain=arguments.get("domain"))
                else:
                    data = _g2_registry.registry_manifest(arguments.get("profile"))
                return {"success": True, "data": data}
            except ExecutionContractError as exc:
                return self._exception(exc)
        if operation in {"docs_search", "docs_get", "docs_examples", "docs_error_search"}:
            try:
                index = self.backend.docs_index
                if operation == "docs_search":
                    data = index.search(query=arguments.get("query", ""), version=arguments.get("version", ""), product=arguments.get("product"), limit=arguments.get("limit", 10))
                elif operation == "docs_get":
                    data = index.get(document_ref=arguments.get("document_ref", ""), section=arguments.get("section"), offset=arguments.get("offset", 0), length=arguments.get("length", 6000))
                else:
                    query = arguments.get("query", arguments.get("error", ""))
                    data = index.search(query=query, version=arguments.get("version", ""), product=arguments.get("node_type"), limit=arguments.get("limit", 10))
                return {"success": True, "data": data}
            except ExecutionContractError as exc:
                return self._exception(exc)
            except Exception as exc:
                return self._error("UNAVAILABLE", "offline documentation index is unavailable", type=type(exc).__name__)
        if operation in {"checkpoint_list", "checkpoint_inspect", "checkpoint_diff"}:
            try:
                rows = self.store.list_metadata("checkpoints")
                if operation == "checkpoint_list":
                    return {"success": True, "data": {"checkpoints": rows}}
                checkpoint_id = arguments.get("checkpoint_id") or arguments.get("left")
                if operation == "checkpoint_inspect":
                    value = next((row for row in rows if row.get("checkpoint_id") == checkpoint_id or row.get("sha256") == checkpoint_id), None)
                    return {"success": bool(value), "data": value or {}, "error": None if value else {"code": "NODE_NOT_FOUND", "message": "checkpoint not found", "safe_retry": False}}
                left = arguments.get("left"); right = arguments.get("right")
                lrow = next((row for row in rows if row.get("checkpoint_id") == left or row.get("sha256") == left), None)
                rrow = next((row for row in rows if row.get("checkpoint_id") == right or row.get("sha256") == right), None)
                return {"success": bool(lrow and rrow), "data": {"left": lrow, "right": rrow, "equal": bool(lrow and rrow and lrow.get("sha256") == rrow.get("sha256"))}}
            except Exception as exc:
                return self._error("UNAVAILABLE", "checkpoint metadata is unavailable", type=type(exc).__name__)
        if operation in {"session_health", "server_info"}:
            with self.lock:
                active = list(self.running)
            return {"success": True, "data": {"status": "READY", "control_pid": os.getpid(),
                "worker_connected": self.backend.cached.get("connected", False),
                "worker": dict(self.worker_health), "session": dict(self.backend.cached), "active_jobs": active},
                "execution": {"session_id": self.backend.cached.get("session_id")}}
        job_id = arguments.get("job_id")
        job = self.store.job(job_id) if isinstance(job_id, str) else None
        if not job:
            return self._error("NODE_NOT_FOUND", "job not found")
        if operation == "job_log":
            offset, limit = arguments.get("offset", 0), arguments.get("limit", 100)
            if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 1000:
                return self._error("INVALID_REQUEST", "invalid log offset/limit")
            events = self.store.events(job_id, offset=offset, limit=limit)
            return {"success": True, "data": {"job_id": job_id, "events": events, "next_offset": offset + len(events)}}
        if operation == "job_reconcile" and job["status"] in {"UNKNOWN", "RECONCILING"}:
            return self._reconcile(job)
        success = not (operation == "job_result" and job.get("result") is not None and not job["result"].get("success"))
        return {"success": success, "data": job}

    def _reconcile(self, job):
        # Query existing Java request ids only. Never submit the lost callback.
        if self.worker is None:
            return {"success": False, "data": job, "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "connect to the existing worker before reconciliation", "safe_retry": False}}
        events = []
        while True:
            page = self.store.events(job["job_id"], offset=len(events), limit=1000)
            events.extend(page)
            if len(page) < 1000:
                break
        ids = list(dict.fromkeys(event["metadata"].get("request_id") for event in events if event["event"] == "worker_request" and event["metadata"].get("phase") == "submitted"))
        completed = {event["metadata"]["request_id"]: event["metadata"]["status"] for event in events
                     if event["event"] == "worker_request" and event["metadata"].get("phase") == "observed"
                     and event["metadata"].get("request_id") and event["metadata"].get("status") in {"SUCCEEDED", "FAILED"}}
        observed = []
        for identifier in ids:
            if identifier in completed:
                # A replacement Worker cannot know the old request IDs. The
                # committed completion observation remains valid evidence that
                # this particular API call returned before the old Worker died.
                observed.append({"request_id": identifier, "status": completed[identifier], "source": "durable_worker_observation"})
            else:
                try:
                    observed.append(self.worker.status(identifier))
                except Exception:
                    observed.append({"request_id": identifier, "status": "UNKNOWN", "message": "worker request status unavailable"})
        active = any(item.get("status") in {"QUEUED", "RUNNING"} for item in observed)
        quiescent = bool(ids) and len(observed) == len(ids) and all(item.get("status") in {"SUCCEEDED", "FAILED"} for item in observed)
        status = "RECONCILING" if active else "UNKNOWN"
        self.store.update_job(job["job_id"], status, {"reconciliation": observed, "reconciled_quiescent": quiescent,
            "replay_performed": False, "callback_completion_unknown": True})
        return {"success": True, "data": self.store.job(job["job_id"])}

    def _monitor(self):
        health_at = 0.0
        while not self.closed.wait(0.05):
            now = time.monotonic()
            with self.lock:
                for job_id, state in list(self.running.items()):
                    duration = state["timeouts"]["execution_timeout_s"]
                    warning = state["timeouts"]["no_progress_warning_s"]
                    if duration is not None and not state["execution_warned"] and now - state["started"] >= duration:
                        state["execution_warned"] = True
                        self.store.add_event(job_id, "ExecutionDeadlineExceeded", {"safe_retry": False, "engine_stopped": False})
                        self.store.update_job(job_id, "RUNNING", {"execution_deadline_exceeded": True, "safe_retry": False})
                    if warning is not None and not state["progress_warned"] and now - state["last_event"] >= warning:
                        state["progress_warned"] = True
                        self.store.add_event(job_id, "NoProgressWarning", {"progress_percentage": None, "engine_stopped": False})
            if self.worker is not None and now - health_at >= 0.5:
                health_at = now
                try:
                    self.worker_health = {**self.worker.health(timeout_s=0.2), "observed_at": time.time()}
                except Exception:
                    self.worker_health = {"status": "UNKNOWN", "observed_at": time.time()}

    @staticmethod
    def _timeouts(execution):
        out = {}
        for name in ("rpc_timeout_s", "queue_timeout_s", "execution_timeout_s", "no_progress_warning_s"):
            value = execution.get(name, 30.0 if name == "rpc_timeout_s" else None)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ExecutionContractError("INVALID_REQUEST", f"{name} must be a non-negative finite number or null")
            out[name] = value
        if out["rpc_timeout_s"] is None:
            out["rpc_timeout_s"] = 30.0
        return out

    @staticmethod
    def _error(code, message, *, data=None, **details):
        return {"success": False, "data": data or {}, "error": {"code": code, "message": message, "safe_retry": False, **details}}

    @classmethod
    def _exception(cls, exc):
        if isinstance(exc, ExecutionContractError):
            return {"success": False, "data": {}, "error": exc.as_dict()}
        return cls._error("IDEMPOTENCY_CONFLICT", str(exc))

    def _log_exception(self):
        with (self.home / "control-errors.log").open("a") as stream:
            traceback.print_exc(file=stream)

    def close(self):
        self.closed.set()
        self.queue.shutdown(wait=True)
        self.monitor.join(timeout=2)
        self.store.close()


def serve(home):
    home = Path(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    singleton = ProcessLock(home / "control.lock")
    token = secrets.token_urlsafe(32)
    daemon = ControlDaemon(home)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/rpc" or not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_error(403); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8 * 1024 * 1024:
                    self.send_error(413); return
                result = daemon.dispatch(json.loads(self.rfile.read(length)))
                body = json.dumps(result, allow_nan=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The durable job keeps running when its caller disappears.
            except Exception:
                self.send_error(500, "internal control error")
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    temporary = home / "control.json.tmp"
    endpoint = {"port": server.server_port, "token": token, "pid": os.getpid()}
    start_epoch_ms = process_identity(os.getpid())["start_epoch_ms"]
    if isinstance(start_epoch_ms, int):
        endpoint["process_start_epoch_ms"] = start_epoch_ms
    temporary.write_text(json.dumps(endpoint))
    os.chmod(temporary, 0o600)
    temporary.replace(home / "control.json")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    serve(parser.parse_args().home)
