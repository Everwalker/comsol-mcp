"""Private persistent-Java Worker client used by the W06 execution backend.

This module deliberately contains no MPh or JPype import.  It starts a Java 11
child that owns its COMSOL connection and speaks a token-authenticated,
loopback-only NDJSON protocol.  It is not an MCP tool and must be called only
through the execution service's per-server queue.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable, Iterator, Mapping

from ._platform_process import process_identity


def _structured_true(value: Mapping[str, Any], name: str) -> bool:
    """Read one explicit boolean from a decoded worker mapping."""
    return isinstance(value, Mapping) and value.get(name) is True


class JavaWorkerError(RuntimeError):
    """Structured failure returned by the private worker protocol."""

    def __init__(self, message: str, *, reply: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        # Keep the decoded protocol object on the exception so a legacy
        # callback can preserve explicit execution-state signals while its
        # human-readable ``str(exc)`` remains backward compatible.
        self.reply: dict[str, Any] = dict(reply) if isinstance(reply, Mapping) else {}
        failure = self.reply.get("failure")
        self.failure: dict[str, Any] = dict(failure) if isinstance(failure, Mapping) else {}
        self.execution_state_unknown = _structured_true(
            self.reply, "execution_state_unknown"
        ) or _structured_true(self.failure, "execution_state_unknown")


class JavaWorkerTimeout(JavaWorkerError):
    """The controller stopped waiting; the worker request may still run."""


@dataclass(frozen=True)
class JavaWorkerPaths:
    comsol_root: Path
    jdk_home: Path
    private_prefs: Path | None = None
    project_root: Path | None = None
    global_lock_root: Path | None = None
    platform_name: str | None = None

    @property
    def is_windows(self) -> bool:
        return (self.platform_name or os.name) == "nt"

    def executable(self, name: str) -> Path:
        """Resolve an external JDK executable for the current host platform."""
        suffix = ".exe" if self.is_windows else ""
        return self.jdk_home / "bin" / f"{name}{suffix}"

    @property
    def classpath_separator(self) -> str:
        return ";" if self.is_windows else os.pathsep

    def validate(self) -> None:
        manifest = self.comsol_root / "bin" / "comsolclientpath.txt"
        if not manifest.is_file():
            raise JavaWorkerError(f"COMSOL client classpath manifest is missing: {manifest}")
        if not self.executable("java").is_file() or not self.executable("javac").is_file():
            raise JavaWorkerError("external JDK with java and javac is required")
        if self.private_prefs is not None:
            resolved = self.private_prefs.resolve()
            if not resolved.is_dir() or resolved.name == "":
                raise JavaWorkerError("private COMSOL preferences directory is required when configured")
        if not self.resolved_project_root.is_dir():
            raise JavaWorkerError("configured project root must be an existing directory")

    @property
    def resolved_project_root(self) -> Path:
        return (self.project_root or Path(__file__).resolve().parents[1]).resolve()

    @property
    def resolved_global_lock_root(self) -> Path:
        uid = str(os.getuid()) if hasattr(os, "getuid") else "user"
        return (self.global_lock_root or (Path(tempfile.gettempdir()) / f"comsol-mcp-{uid}" / "worker-endpoints")).resolve()

    def classpath(self) -> tuple[str, str, int]:
        manifest = self.comsol_root / "bin" / "comsolclientpath.txt"
        names = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
        entries = [self._manifest_entry(name) for name in names]
        explicit = [entry for entry in entries if entry[0] is not None]
        if explicit:
            if len(explicit) != len(entries):
                raise JavaWorkerError("official COMSOL classpath manifest mixes explicit and implicit roots")
            jars = [self.comsol_root / root / relative for root, relative in entries]
            missing = [name for name, jar in zip(names, jars) if not jar.is_file()]
            if missing:
                raise JavaWorkerError(f"official COMSOL explicit classpath entries are unavailable; missing: {', '.join(missing[:3])}")
        else:
            jars = []
            # A classpath is a coherent manifest set, never an opportunistic
            # per-JAR merge. Windows evidence confirms apiplugins contains all
            # 25 manifest entries while plugins is partial; keep this ordering
            # on every platform and use plugins only when it is complete.
            for root in self._classpath_roots():
                candidate = [root / relative for _, relative in entries]
                if all(jar.is_file() for jar in candidate):
                    jars = candidate
                    break
            if not jars:
                roots = ", ".join(str(root) for root in self._classpath_roots())
                missing = [name for name, (_, relative) in zip(names, entries)
                           if not any((root / relative).is_file() for root in self._classpath_roots())]
                detail = f"; missing from every candidate root: {', '.join(missing[:3])}" if missing else ""
                raise JavaWorkerError(f"no complete official COMSOL classpath root contains every manifest entry: {roots}{detail}")
        return self.classpath_separator.join(map(str, jars)), hashlib.sha256(manifest.read_bytes()).hexdigest(), len(jars)

    def _classpath_roots(self) -> tuple[Path, ...]:
        return (self.comsol_root / "apiplugins", self.comsol_root / "plugins")

    def _manifest_entry(self, entry: str) -> tuple[str | None, PurePosixPath]:
        normalized = entry.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
            raise JavaWorkerError("official COMSOL classpath manifest contains an unsupported path entry")
        parts = relative.parts
        if parts and parts[0] in {"plugins", "apiplugins"}:
            return parts[0], PurePosixPath(*parts[1:])
        return None, relative


class PersistentJavaWorker:
    """One persistent worker process.  A timeout never terminates the child."""

    def __init__(self, paths: JavaWorkerPaths, *, state_dir: Path | None = None,
                 on_request_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.paths = paths
        self.state_dir = state_dir or Path(tempfile.mkdtemp(prefix="comsol-mcp-worker-state-"))
        self._token = secrets.token_urlsafe(32)
        self._process: subprocess.Popen[str] | None = None
        self._port: int | None = None
        self._generation: int | None = None
        self._classes_dir: Path | None = None
        self._lock = threading.RLock()
        self._known_requests: dict[str, dict[str, Any]] = {}
        self._next_generation = 1
        self._on_request_event = on_request_event
        self._operation_context = threading.local()

    @property
    def generation(self) -> int:
        if self._generation is None:
            raise JavaWorkerError("worker is not started")
        return self._generation

    @property
    def endpoint(self) -> tuple[str, int]:
        if self._port is None:
            raise JavaWorkerError("worker is not started")
        return "127.0.0.1", self._port

    def start(self, *, startup_timeout_s: float = 20.0) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.health(timeout_s=1.0)
            self.paths.validate()
            classpath, classpath_hash, jar_count = self.paths.classpath()
            self.state_dir.mkdir(parents=True, exist_ok=True)
            try: os.chmod(self.state_dir, 0o700)
            except OSError: pass
            source = Path(__file__).with_name("worker_java") / "PersistentComsolWorker.java"
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:20]
            classes = self.state_dir / "classes" / source_hash
            marker = classes / ".compiled"
            if not marker.is_file():
                classes.mkdir(parents=True, exist_ok=True)
                compile_result = subprocess.run(
                    [str(self.paths.executable("javac")), "-cp", classpath, "-d", str(classes), str(source)],
                    text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                if compile_result.returncode:
                    raise JavaWorkerError(f"worker compilation failed: {compile_result.stderr[-2000:]}")
                marker.write_text(source_hash + "\n", encoding="ascii")
            self._classes_dir = classes
            endpoint = self.state_dir / "worker_endpoint.json"
            existing = self._attach_existing(endpoint)
            if existing is not None:
                return existing
            command = [str(self.paths.executable("java"))]
            if self.paths.private_prefs is not None:
                command.append(f"-Dcs.prefsdir={self.paths.private_prefs}")
            command += ["-cp", str(classes) + self.paths.classpath_separator + classpath,
                        "comsol_mcp.worker_java.PersistentComsolWorker", "--port", "0", "--endpoint-file", str(endpoint),
                        "--server-lock-root", str(self.paths.resolved_global_lock_root)]
            command += ["--generation", str(self._next_generation)]
            environment = dict(os.environ); environment["COMSOL_MCP_WORKER_TOKEN"] = self._token
            log = (self.state_dir / "worker.stderr.log").open("a", encoding="utf-8")
            self._process = subprocess.Popen(command, text=True, stdout=subprocess.DEVNULL, stderr=log,
                                             env=environment, start_new_session=True)
            log.close()
            deadline = time.monotonic() + startup_timeout_s
            ready: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                if endpoint.is_file():
                    try:
                        candidate = json.loads(endpoint.read_text(encoding="utf-8"))
                        if candidate.get("type") == "ready": ready = candidate; break
                    except (OSError, json.JSONDecodeError): pass
                if self._process.poll() is not None:
                    detail = (self.state_dir / "worker.stderr.log").read_text(errors="replace")[-2000:]
                    raise JavaWorkerError(f"worker exited during startup: {detail}")
                time.sleep(0.02)
            if ready is None:
                raise JavaWorkerTimeout("worker did not publish a ready endpoint; do not assume it stopped")
            self._port, self._generation, self._token = int(ready["port"]), int(ready["generation"]), str(ready["token"])
            self._write_endpoint({**ready, "classpath_sha256": classpath_hash, "jar_count": jar_count, "state": "STARTED"})
            return self.health(timeout_s=1.0)

    def _write_endpoint(self, state: Mapping[str, Any]) -> None:
        if self.state_dir is None:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        target = self.state_dir / "worker_endpoint.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(state), sort_keys=True) + "\n", encoding="utf-8")
        try: os.chmod(temporary, 0o600)
        except OSError: pass
        temporary.replace(target)

    def _attach_existing(self, endpoint: Path) -> dict[str, Any] | None:
        if not endpoint.is_file():
            return None
        try:
            saved = json.loads(endpoint.read_text(encoding="utf-8"))
            pid, port, token = int(saved["pid"]), int(saved["port"]), str(saved["token"])
            identity = process_identity(pid)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise JavaWorkerError("existing Worker endpoint is invalid; manual reconciliation is required") from exc
        saved_start = saved.get("process_start_epoch_ms")
        has_saved_start = isinstance(saved_start, int) and saved_start >= 0
        starts_differ = (has_saved_start and isinstance(identity["start_epoch_ms"], int)
                         and saved_start != identity["start_epoch_ms"])
        if not identity["alive"] or starts_differ:
            try: self._next_generation = int(saved.get("generation", 0)) + 1
            except (TypeError, ValueError): self._next_generation = 1
            # A dead child, or a confirmed PID reuse, cannot own this endpoint.
            # Removing only this private rendezvous file prevents a new child
            # from reading its stale ready record before publication.
            endpoint.unlink(missing_ok=True)
            return None
        if self.paths.is_windows and has_saved_start and identity["start_epoch_ms"] is None:
            raise JavaWorkerError("existing Worker identity cannot be verified; do not start a replacement")
        self._port, self._generation, self._token = port, int(saved["generation"]), token
        self._next_generation = self._generation + 1
        try:
            result = self.health(timeout_s=1.0)
        except JavaWorkerError as exc:
            raise JavaWorkerError("existing Worker is alive but unreachable; do not start a replacement or replay requests") from exc
        self._process = None
        return result

    def _request(self, data: dict[str, Any], *, timeout_s: float | None) -> dict[str, Any]:
        if self._port is None:
            raise JavaWorkerError("worker is not started")
        request_id = data.get("request_id")
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=5.0 if timeout_s is None else timeout_s) as conn:
                conn.settimeout(timeout_s)
                stream = conn.makefile("rwb")
                stream.write((json.dumps({"token": self._token}) + "\n").encode()); stream.flush()
                auth = json.loads(stream.readline())
                if not auth.get("ok"):
                    raise JavaWorkerError(str(auth))
                stream.write((json.dumps(data, separators=(",", ":")) + "\n").encode()); stream.flush()
                reply = json.loads(stream.readline())
        except (TimeoutError, socket.timeout) as exc:
            if request_id:
                self._known_requests[str(request_id)] = {"request_id": request_id, "status": "UNKNOWN", "reason": "rpc_timeout"}
            raise JavaWorkerTimeout("RPC timeout; worker request may still be executing; query the original request_id") from exc
        except OSError as exc:
            raise JavaWorkerError("ENGINE_UNRESPONSIVE: Worker endpoint could not be reached") from exc
        self._sync_generation(reply)
        if request_id:
            self._known_requests[str(request_id)] = reply
        return reply

    def _sync_generation(self, reply: Mapping[str, Any]) -> None:
        value = reply.get("generation")
        if value is None and isinstance(reply.get("result"), Mapping):
            value = reply["result"].get("generation")
        if value is None: return
        try: generation = int(value)
        except (TypeError, ValueError): return
        if generation < 1: raise JavaWorkerError("worker returned invalid generation")
        self._generation, self._next_generation = generation, generation + 1
        endpoint = self.state_dir / "worker_endpoint.json"
        if endpoint.is_file():
            try:
                state = json.loads(endpoint.read_text(encoding="utf-8"))
                if int(state.get("generation", 0)) != generation:
                    state["generation"] = generation
                    self._write_endpoint(state)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # A damaged rendezvous record is an explicit recovery concern;
                # it must not alter the live worker's generation in memory.
                pass

    @contextmanager
    def operation_context(self, operation_id: str, *, on_request_event: Callable[[dict[str, Any]], None] | None = None) -> Iterator[None]:
        """Associate every submitted Java request with a durable operation id.

        The callback runs before send and after terminal/observed reply.  It is
        intentionally synchronous so a daemon can write its job event before
        the worker sees the request.  Callers must redact before durable logs;
        this module also redacts credential fields defensively.
        """
        previous = getattr(self._operation_context, "value", None)
        self._operation_context.value = (operation_id, on_request_event)
        try: yield
        finally: self._operation_context.value = previous

    def _emit_request_event(self, phase: str, *, request_id: str, kind: str, payload: Mapping[str, Any], **extra: Any) -> None:
        context = getattr(self._operation_context, "value", None)
        callback = context[1] if context and context[1] is not None else self._on_request_event
        if callback is None: return
        event = {"phase": phase, "request_id": request_id, "kind": kind,
                 "operation_id": context[0] if context else "", "request_hash": _request_hash(payload),
                 "metadata": _redact(payload), **extra}
        callback(event)

    def submit(self, kind: str, payload: Mapping[str, Any], *, request_id: str | None = None,
               queue_timeout_s: float | None = None, rpc_timeout_s: float | None = None) -> dict[str, Any]:
        if kind not in {"connect", "disconnect", "model", "modelutil", "model_snapshot", "call", "children", "walk", "lock_selftest", "code_compile", "code_execute"}:
            raise JavaWorkerError("unknown private worker command")
        body = dict(payload); body["type"] = kind; body["request_id"] = request_id or f"wrk-{uuid.uuid4()}"
        if queue_timeout_s is not None:
            body["queue_timeout_ms"] = max(0, int(queue_timeout_s * 1000))
        identifier = str(body["request_id"])
        self._emit_request_event("submitted", request_id=identifier, kind=kind, payload=body)
        try:
            reply = self._request(body, timeout_s=rpc_timeout_s)
        except JavaWorkerTimeout as exc:
            self._emit_request_event("unknown", request_id=identifier, kind=kind, payload=body, status="UNKNOWN", error=str(exc))
            raise
        except JavaWorkerError as exc:
            self._emit_request_event("unresponsive", request_id=identifier, kind=kind, payload=body, status="UNKNOWN", error=str(exc))
            raise
        self._emit_request_event("observed", request_id=identifier, kind=kind, payload=body, status=reply.get("status", ""), reply=_redact(reply))
        return reply

    def health(self, *, timeout_s: float = 1.0) -> dict[str, Any]:
        return self._request({"type": "health"}, timeout_s=timeout_s)

    def runtime_metadata(self, *, timeout_s: float = 1.0) -> dict[str, Any]:
        """Non-secret Worker identity for daemon/session binding."""
        health = self.health(timeout_s=timeout_s)
        endpoint = self.state_dir / "worker_endpoint.json"
        endpoint_data: dict[str, Any] = {}
        if endpoint.is_file():
            try: endpoint_data = json.loads(endpoint.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): pass
        return {"host": endpoint_data.get("host", "127.0.0.1"), "port": endpoint_data.get("port", self._port),
                "pid": endpoint_data.get("pid"), "generation": health.get("generation"),
                "instance_id": health.get("instance_id", endpoint_data.get("instance_id", "")),
                "connected": health.get("connected", False), "server": health.get("server", "")}

    def status(self, request_id: str, *, timeout_s: float = 1.0) -> dict[str, Any]:
        reply = self._request({"type": "status", "request_id": request_id}, timeout_s=timeout_s)
        self._emit_request_event("status_observed", request_id=request_id, kind="status", payload={}, status=reply.get("status", ""), reply=_redact(reply))
        return reply

    def connect(self, host: str, port: int, *, encrypted: bool = False, user: str = "", password: str = "",
                request_id: str | None = None, rpc_timeout_s: float | None = None) -> dict[str, Any]:
        return self.submit("connect", {"host": host, "port": port, "encrypted": encrypted, "user": user, "password": password},
                           request_id=request_id, rpc_timeout_s=rpc_timeout_s)

    def model_snapshot(self, model_tag: str, *, request_id: str | None = None, rpc_timeout_s: float | None = None) -> dict[str, Any]:
        return self.submit("model_snapshot", {"tag": model_tag}, request_id=request_id, rpc_timeout_s=rpc_timeout_s)

    def compile_java(self, source_artifact: str, entrypoint: str, *, request_id: str | None = None,
                     rpc_timeout_s: float | None = None) -> dict[str, Any]:
        """Compile a source file inside the same Worker classpath.

        Compilation does not receive a Model and therefore cannot change the
        COMSOL state.  The Worker still owns the compiler invocation so the
        production classpath and diagnostics are the ones actually used by the
        bound runtime.
        """
        from ._domain_outcome import record_engine_method
        record_engine_method("compile", source_artifact, entrypoint, command="code_compile", receiver="trusted_java")
        return self.submit("code_compile", {"source_artifact": source_artifact, "entrypoint": entrypoint},
                           request_id=request_id, rpc_timeout_s=rpc_timeout_s)

    def execute_java(self, model_tag: str, source_artifact: str, entrypoint: str, arguments: Mapping[str, Any], *,
                     request_id: str | None = None, rpc_timeout_s: float | None = None) -> dict[str, Any]:
        """Execute a previously described source against the named Worker model."""
        from ._domain_outcome import record_engine_method
        record_engine_method(entrypoint, model_tag, source_artifact, command="code_execute", receiver="trusted_java")
        return self.submit("code_execute", {"tag": model_tag, "source_artifact": source_artifact,
                           "entrypoint": entrypoint, "arguments": dict(arguments or {})},
                           request_id=request_id, rpc_timeout_s=rpc_timeout_s)

    def backend_snapshot(self, model_tag: str, *, request_id: str | None = None,
                         rpc_timeout_s: float | None = None) -> dict[str, Any]:
        """W05 adapter contract: a direct, validated model identity snapshot."""
        result = _decode_reply(self.model_snapshot(model_tag, request_id=request_id, rpc_timeout_s=rpc_timeout_s), self)
        if not isinstance(result, dict):
            raise JavaWorkerError("worker returned an invalid model snapshot")
        return result

    def client(self) -> "RemoteClient": return RemoteClient(self)

    def probe_children(self, node: "RemoteJava", candidates: list[Mapping[str, str]], *,
                       request_id: str | None = None, rpc_timeout_s: float | None = None) -> dict[str, Any]:
        """R02: list one node's children in a single engine-side batch.

        The worker probes every candidate collection inside its serial engine
        task.  Collections the node does not expose are omitted; every other
        failure is returned in ``errors`` - it is never silently dropped.
        """
        if not isinstance(node, RemoteJava):
            raise JavaWorkerError("probe_children requires a worker node handle")
        reply = self.submit("children", {"handle": node._handle, "generation": node._generation,
                                         "candidates": [dict(item) for item in candidates]},
                            request_id=request_id, rpc_timeout_s=rpc_timeout_s)
        result = _decode_reply(reply, self)
        if not isinstance(result, Mapping):
            raise JavaWorkerError("worker children probe returned an invalid result")
        return dict(result)

    def walk_nodes(self, node: "RemoteJava", *, query: Mapping[str, Any], candidates: list[Mapping[str, str]],
                   max_nodes: int, max_seconds: float, skip_visited: int, limit: int,
                   request_id: str | None = None, rpc_timeout_s: float | None = None) -> dict[str, Any]:
        """R02: deterministic, budgeted node-tree walk inside one engine task.

        ``skip_visited`` replays the deterministic walk prefix for a resumed
        cursor; budgets only charge nodes beyond that prefix.  The reply is
        validated by the engine before it reaches the wire.
        """
        if not isinstance(node, RemoteJava):
            raise JavaWorkerError("walk_nodes requires a worker node handle")
        reply = self.submit("walk", {"handle": node._handle, "generation": node._generation,
                                     "query": dict(query), "candidates": [dict(item) for item in candidates],
                                     "max_nodes": int(max_nodes), "max_seconds": float(max_seconds),
                                     "skip_visited": int(skip_visited), "limit": int(limit)},
                            request_id=request_id, rpc_timeout_s=rpc_timeout_s)
        result = _decode_reply(reply, self)
        if not isinstance(result, Mapping):
            raise JavaWorkerError("worker walk returned an invalid result")
        return dict(result)

    def close(self) -> None:
        # Only the child started by this instance is eligible for termination. No COMSOL server is touched.
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try: self._process.wait(timeout=3)
                except subprocess.TimeoutExpired: self._process.kill(); self._process.wait(timeout=3)
            self._process = None; self._port = None; self._generation = None
            self._classes_dir = None


class RemoteJava:
    """Opaque Java object held only by the worker; stale generations fail locally."""
    def __init__(self, worker: PersistentJavaWorker, handle: str, generation: int, java_type: str = "") -> None:
        self._worker, self._handle, self._generation, self.java_type = worker, handle, generation, java_type

    def _call(self, method: str, *args: Any, request_id: str | None = None, rpc_timeout_s: float | None = None) -> Any:
        if self._generation != self._worker.generation:
            raise JavaWorkerError("STALE_WORKER_HANDLE")
        # C01 dispatch witness: record the method actually dispatched to the
        # worker so the managed backend can prove whether a mutation-class call
        # was issued, instead of trusting an exception class name.  The import is
        # local because ``_domain_outcome`` is a plain-python contract module
        # with no worker dependency.
        from ._domain_outcome import record_engine_method
        record_engine_method(method, *args, command="call", receiver=self._handle)
        reply = self._worker.submit("call", {"handle": self._handle, "generation": self._generation, "method": method, "args": list(args)},
                                    request_id=request_id, rpc_timeout_s=rpc_timeout_s)
        return _decode_reply(reply, self._worker)

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda *args, **kwargs: self._call(method, *args, **kwargs)


class RemoteModel(RemoteJava):
    @property
    def java(self) -> "RemoteModel": return self

    def name(self) -> str:
        return str(self._call("label"))

    def solve(self, study: str = "", **kwargs: Any) -> Any:
        studies = self._call("study")
        if not study:
            tags = studies._call("tags", **kwargs)
            if len(tags) != 1:
                raise JavaWorkerError("study tag is required when the model has zero or multiple studies")
            return self._call("study", str(tags[0]), **kwargs)._call("run", **kwargs)
        tags = [str(tag) for tag in studies._call("tags", **kwargs)]
        if study in tags:
            return self._call("study", study, **kwargs)._call("run", **kwargs)
        matched = [tag for tag in tags if str(self._call("study", tag, **kwargs)._call("label", **kwargs)) == study]
        if len(matched) != 1:
            raise JavaWorkerError("study label did not resolve to exactly one study tag")
        return self._call("study", matched[0], **kwargs)._call("run", **kwargs)

    def _raw_save(self, path: str, **kwargs: Any) -> Any:
        """Engine save used only by the atomic publisher's temporary callback."""
        if not path:
            raise JavaWorkerError("raw save requires the atomic publisher's temporary path")
        return self._call("save", path, True, **kwargs)

    def save(self, path: str = "", copy: bool = True, **kwargs: Any) -> Any:
        # Both explicit and implicit (current-file) saves publish only after a
        # complete candidate has been verified. `copy` is accepted for MPh
        # compatibility but never opens an in-place overwrite bypass.
        saved_path = str(self._call("getFilePath", **kwargs) or "").strip() if not path else str(path)
        if not saved_path:
            raise JavaWorkerError("model has no file path; an explicit project-relative save path is required")
        target = Path(saved_path)
        from comsol_mcp._atomic_save import atomic_save
        return atomic_save(target, lambda temporary: self._raw_save(str(temporary), **kwargs),
                           project_root=self._worker.paths.resolved_project_root)


class RemoteClient:
    """MPh-shaped compatibility facade; all engine work remains in Java."""
    def __init__(self, worker: PersistentJavaWorker) -> None: self._worker = worker
    @property
    def java(self) -> "RemoteClient": return self
    def connect(self, port: int, host: str = "localhost", **kwargs: Any) -> Any:
        """Match MPh's connect(port, host) argument order for legacy wrappers."""
        return _decode_reply(self._worker.connect(host, int(port), **kwargs), self._worker)
    def disconnect(self, **kwargs: Any) -> Any: return _decode_reply(self._worker.submit("disconnect", {}, **kwargs), self._worker)
    def model(self, tag: str, **kwargs: Any) -> RemoteModel:
        return _as_model(_decode_reply(self._worker.submit("model", {"tag": tag}, **kwargs), self._worker))
    def models(self, **kwargs: Any) -> list[RemoteModel]:
        tags = _decode_reply(self._worker.submit("modelutil", {"method": "tags", "args": []}, **kwargs), self._worker)
        return [self.model(str(tag), **kwargs) for tag in tags]
    def tags(self, **kwargs: Any) -> Any:
        return _decode_reply(self._worker.submit("modelutil", {"method": "tags", "args": []}, **kwargs), self._worker)
    def uniquetag(self, prefix: str, **kwargs: Any) -> Any:
        return _decode_reply(self._worker.submit("modelutil", {"method": "uniquetag", "args": [prefix]}, **kwargs), self._worker)
    def getComsolVersion(self, **kwargs: Any) -> Any:
        return _decode_reply(self._worker.submit("modelutil", {"method": "getComsolVersion", "args": []}, **kwargs), self._worker)
    def create(self, name: str, **kwargs: Any) -> RemoteModel:
        """Create with a generated tag; the caller's value is a display label."""
        from ._domain_outcome import record_engine_method
        tag = str(self.uniquetag("mcp", **kwargs))
        record_engine_method("create", tag, command="modelutil", receiver="modelutil")
        model = _as_model(_decode_reply(self._worker.submit("modelutil", {"method": "create", "args": [tag]}, **kwargs), self._worker))
        model.label(name, **kwargs)
        return model
    def load(self, path: str, tag: str | None = None, **kwargs: Any) -> RemoteModel:
        from ._domain_outcome import record_engine_method
        tag = tag or f"mcp_{uuid.uuid4().hex[:12]}"
        record_engine_method("load", tag, str(path), command="modelutil", receiver="modelutil")
        return _as_model(_decode_reply(self._worker.submit("modelutil", {"method": "load", "args": [tag, str(path)]}, **kwargs), self._worker))
    def remove(self, tag: str | RemoteJava, **kwargs: Any) -> Any:
        from ._domain_outcome import record_engine_method
        if isinstance(tag, RemoteJava):
            tag = str(tag.tag(**kwargs))
        record_engine_method("remove", str(tag), command="modelutil", receiver="modelutil")
        return _decode_reply(self._worker.submit("modelutil", {"method": "remove", "args": [tag]}, **kwargs), self._worker)


def _decode_reply(reply: Mapping[str, Any], worker: PersistentJavaWorker) -> Any:
    if reply.get("status") in {"QUEUED", "RUNNING"}:
        return dict(reply)
    if not reply.get("ok", False):
        # Preserve the structured failure for the execution-state guard.  The
        # serialized message is intentionally unchanged for callers that only
        # consume ``str(exc)``.
        raise JavaWorkerError(json.dumps(dict(reply), sort_keys=True), reply=reply)
    result = reply.get("result")
    if isinstance(result, Mapping) and "$worker_handle" in result:
        return RemoteJava(worker, str(result["$worker_handle"]), int(result["generation"]), str(result.get("java_type", "")))
    return result


def _as_model(value: Any) -> RemoteModel:
    if not isinstance(value, RemoteJava):
        raise JavaWorkerError("worker did not return a model handle")
    return RemoteModel(value._worker, value._handle, value._generation, value.java_type)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): "<redacted>" if str(key).lower() in {"token", "password", "secret", "authorization"} else _redact(item)
                for key, item in value.items()}
    if isinstance(value, list): return [_redact(item) for item in value]
    return value


def _request_hash(payload: Mapping[str, Any]) -> str:
    """Hash semantic worker content without persisting sensitive values."""
    semantic = {str(key): value for key, value in payload.items() if key not in {"request_id", "queue_timeout_ms"}}
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
