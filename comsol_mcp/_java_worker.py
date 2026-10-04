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


_UNCORRELATED_PROTOCOL_REJECTIONS = frozenset({
    "REQUEST_NOT_FOUND",
    "IDEMPOTENCY_KEY_CONFLICT",
    "UNKNOWN_COMMAND",
})

_D2_COMSOL_VERSION = "6.4.0.293"
_D2_READ_RECEIVERS = {
    "nodeGroup": ("com.comsol.model.Model", {(): "com.comsol.model.NodeGroupList",
                                               ("java.lang.String",): "com.comsol.model.NodeGroup"}),
    "tags": ("com.comsol.model.NodeGroupList", {(): "[Ljava.lang.String;"}),
    "size": ("com.comsol.model.NodeGroup", {(): "int"}),
    "get": ("com.comsol.model.NodeGroup", {("int",): "com.comsol.model.ModelEntity"}),
    "feature": ("com.comsol.model.NodeGroup", {(): "com.comsol.model.NodeGroupList"}),
    "getAfter": ("com.comsol.model.NodeGroup", {(): "com.comsol.model.ModelEntity"}),
    "getContainer": ("com.comsol.model.PrimitiveModelEntity", {(): "com.comsol.model.PrimitiveModelEntity"}),
    "resolveModelPath": ("com.comsol.model.PrimitiveModelEntity", {(): "java.lang.String"}),
}
_D2_SUCCESS_ENVELOPE_FIELDS = frozenset({
    "ok", "request_id", "type", "status", "queued_at_ms", "started_at_ms", "completed_at_ms", "result",
})


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


def _atomic_export_file(destination: str | Path, save_callback: Callable[[Path], Any],
                        project_root: str | Path, *, overwrite: bool) -> dict[str, Any]:
    """Atomically publish a Java/M source export without treating it as MPH."""
    from ._execution_contract import canonical_project_path
    from ._atomic_save import _fsync_file, _fsync_parent

    target = canonical_project_path(project_root, destination)
    parent = target.parent
    canonical_project_path(project_root, parent)
    existed = target.exists()
    if existed and not overwrite:
        raise JavaWorkerError("Source export destination exists and overwrite is disabled")
    if not parent.is_dir():
        raise JavaWorkerError("Source export parent directory does not exist")
    # COMSOL's Java/M save overload may generate a public Java class or an
    # M-file function from the export filename. Keep the final basename while
    # isolating the candidate in a unique child directory on the same volume.
    temporary_directory = parent / f".comsol-export-{uuid.uuid4().hex}.tmp"
    temporary = temporary_directory / target.name
    try:
        canonical_project_path(project_root, temporary_directory)
        temporary_directory.mkdir(mode=0o700, exist_ok=False)
        if temporary_directory.is_symlink() or not temporary_directory.is_dir():
            raise ValueError("source export temporary directory is not a regular staging directory")
        if canonical_project_path(project_root, temporary_directory) != temporary_directory.resolve(strict=True):
            raise ValueError("source export temporary directory changed during creation")
        save_callback(temporary)
        if (temporary_directory.is_symlink() or not temporary_directory.is_dir()
                or temporary.is_symlink() or not temporary.is_file()):
            raise ValueError("COMSOL did not create a regular source export")
        if canonical_project_path(project_root, temporary) != temporary.resolve(strict=False):
            raise ValueError("source export temporary path changed during save")
        if temporary.name != target.name or temporary.parent != temporary_directory:
            raise ValueError("source export candidate basename differs from its destination")
        size = temporary.stat().st_size
        if size <= 0:
            raise ValueError("COMSOL created an empty source export")
        if {entry.name for entry in temporary_directory.iterdir()} != {target.name}:
            raise ValueError("COMSOL created unsupported companion files for the source export")
        # Reuse the MPH publisher's writable-handle fsync helper. Windows
        # FlushFileBuffers requires a writable handle; opening in r+b does not
        # truncate the exported source.
        _fsync_file(temporary)
        temporary_directory_fsync = _fsync_parent(temporary)
        digest = hashlib.sha256()
        with temporary.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        file_sha256 = digest.hexdigest()
        canonical_project_path(project_root, parent)
        if temporary_directory.is_symlink() or not temporary_directory.is_dir():
            raise ValueError("source export temporary directory changed before publication")
        if canonical_project_path(project_root, temporary) != temporary.resolve(strict=False):
            raise ValueError("source export candidate changed before publication")
        if overwrite:
            os.replace(temporary, target)
            publish_mode = "atomic_replace"
        else:
            os.link(temporary, target)
            publish_mode = "atomic_no_clobber"
        parent_fsync = _fsync_parent(target)
        temporary_file_removed = not temporary.exists()
        if temporary.exists():
            try:
                temporary.unlink()
                temporary_file_removed = True
            except OSError:
                temporary_file_removed = False
        temporary_directory_removed = False
        if temporary_file_removed:
            try:
                temporary_directory.rmdir()
            except OSError:
                temporary_directory_removed = False
            else:
                temporary_directory_removed = True
                # Persist both the destination publication and removal of its
                # temporary directory entry where directory fsync is supported.
                parent_fsync = _fsync_parent(target)
        artifact = {"path": str(target), "size": size, "sha256": file_sha256, "verified": True}
        return {**artifact, "artifact": artifact,
                "checkpoint": {**artifact, "publish_mode": publish_mode,
                               "source_basename": target.name,
                               "temporary_basename_matches": temporary.name == target.name,
                               "temporary_directory_fsync": temporary_directory_fsync,
                               "temporary_directory_removed": temporary_directory_removed,
                               "parent_fsync": parent_fsync}}
    except Exception as exc:
        # Keep a partial candidate for inspection, just as the MPH publisher
        # does; never replace the previous destination on a callback failure.
        evidence_path = temporary if temporary.exists() else temporary_directory
        message = f"COMSOL source export was not published: {exc}; temporary evidence retained at {evidence_path}"
        if isinstance(exc, JavaWorkerError):
            raise type(exc)(message, reply=exc.reply) from exc
        raise JavaWorkerError(message) from exc


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
        if self.project_root is not None:
            return Path(self.project_root).resolve()
        env_root = os.environ.get("COMSOL_PROJECT_ROOT")
        if env_root:
            return Path(env_root).resolve()
        fallback = Path(__file__).resolve().parents[1]
        if any(p in fallback.parts for p in ("site-packages", "dist-packages")):
            raise JavaWorkerError(
                "COMSOL_PROJECT_ROOT or explicit project_root is required when running from site-packages; site-packages is not an authorized project root"
            )
        return fallback.resolve()

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
        # F06: Hash actual JAR file contents for cache binding, not just the
        # static manifest text.  Reading full JARs would be expensive (hundreds
        # of MB), so we hash (path, size, first-8K, last-8K) per JAR — enough
        # to detect any replacement including same-size overwrites.
        jar_hasher = hashlib.sha256()
        for jar in jars:
            st = jar.stat()
            jar_hasher.update(jar.name.encode("utf-8"))
            jar_hasher.update(str(st.st_size).encode("utf-8"))
            with jar.open("rb") as f:
                head = f.read(8192)
                jar_hasher.update(head)
                if st.st_size > 16384:
                    f.seek(-8192, 2)
                    jar_hasher.update(f.read(8192))
        jar_content_hash = jar_hasher.hexdigest()
        return self.classpath_separator.join(map(str, jars)), hashlib.sha256(manifest.read_bytes()).hexdigest(), len(jars), jar_content_hash

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


    def jdk_version_info(self) -> dict[str, str]:
        release_file = self.jdk_home / "release"
        if release_file.is_file():
            meta = {}
            for line in release_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    meta[k.strip()] = v.strip().strip('"')
            return {
                "version": meta.get("JAVA_VERSION", "unknown"),
                "vendor": meta.get("IMPLEMENTOR", "unknown"),
                "arch": meta.get("OS_ARCH", "unknown"),
            }
        try:
            res = subprocess.run([str(self.executable("javac")), "-version"],
                                 capture_output=True, text=True, check=False)
            out = (res.stdout + " " + res.stderr).strip()
            return {"version": out, "vendor": "unknown", "arch": "unknown"}
        except Exception:
            return {"version": "unknown", "vendor": "unknown", "arch": "unknown"}

    def comsol_version_info(self) -> dict[str, str]:
        for cand in [
            self.comsol_root / "doc" / "version.txt",
            self.comsol_root / "version.txt",
            self.comsol_root / "build.txt",
        ]:
            if cand.is_file():
                return {"version_text": cand.read_text(encoding="utf-8", errors="replace").strip()[:100]}
        name = self.comsol_root.as_posix()
        detected = "6.4" if "64" in name or "6.4" in name else ("6.3" if "63" in name or "6.3" in name else "unknown")
        return {"version_text": detected, "root_name": self.comsol_root.name}

    def compilation_cache_fingerprint(self, source_bytes: bytes, classpath_hash: str, jar_content_hash: str = "") -> tuple[str, dict[str, Any]]:
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        jdk_info = self.jdk_version_info()
        comsol_info = self.comsol_version_info()
        javac_flags = ["-encoding", "UTF-8"]
        import sys
        platform_id = f"{sys.platform}-{self.is_windows}"

        fingerprint_data = {
            "source_sha256": source_hash,
            "classpath_manifest_sha256": classpath_hash,
            "jar_content_sha256": jar_content_hash,
            "comsol_info": comsol_info,
            "jdk_info": jdk_info,
            "javac_flags": javac_flags,
            "platform": platform_id,
        }
        serialized = json.dumps(fingerprint_data, sort_keys=True)
        cache_key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
        return cache_key, fingerprint_data


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
            classpath, classpath_hash, jar_count, jar_content_hash = self.paths.classpath()
            self.state_dir.mkdir(parents=True, exist_ok=True)
            try: os.chmod(self.state_dir, 0o700)
            except OSError: pass
            source = Path(__file__).with_name("worker_java") / "PersistentComsolWorker.java"
            source_bytes = source.read_bytes()
            cache_key, cache_receipt = self.paths.compilation_cache_fingerprint(source_bytes, classpath_hash, jar_content_hash)
            classes = self.state_dir / "classes" / cache_key
            marker = classes / ".compiled"
            receipt_file = classes / "cache_receipt.json"
            if not marker.is_file() or not receipt_file.is_file():
                classes.mkdir(parents=True, exist_ok=True)
                compile_result = subprocess.run(
                    [str(self.paths.executable("javac")), "-cp", classpath, "-encoding", "UTF-8", "-d", str(classes), str(source)],
                    text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                if compile_result.returncode:
                    raise JavaWorkerError(f"worker compilation failed: {compile_result.stderr[-2000:]}")
                receipt_file.write_text(json.dumps(cache_receipt, indent=2, sort_keys=True), encoding="utf-8")
                marker.write_text(cache_key + "\n", encoding="ascii")
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
        correlated = self._validate_request_reply(data, reply)
        self._sync_generation(reply)
        if request_id is not None and correlated:
            self._known_requests[str(request_id)] = reply
        return reply

    def _remember_uncorrelated_request(self, request_id: Any, reason: str) -> None:
        """Retain uncertainty for the caller's id without replacing known evidence."""
        key = str(request_id)
        if key not in self._known_requests:
            self._known_requests[key] = {
                "request_id": request_id,
                "status": "UNKNOWN",
                "reason": reason,
            }

    def _validate_request_reply(self, data: Mapping[str, Any], reply: Any) -> bool:
        """Validate the response identity before it can update Worker state.

        A boolean return means the response echoed the request id exactly and
        is eligible for the per-request cache. The small set of id-less protocol
        refusals is still returned to callers for its existing diagnostics, but
        it is not treated as a task response or cached as one.
        """
        if not isinstance(reply, Mapping):
            request_id = data.get("request_id")
            if request_id is not None:
                self._remember_uncorrelated_request(request_id, "invalid_worker_reply")
                raise JavaWorkerError(
                    "Worker response is not an object; request state is UNKNOWN",
                    reply={
                        "code": "WORKER_RESPONSE_UNCORRELATED",
                        "request_id": request_id,
                        "status": "UNKNOWN",
                        "execution_state_unknown": True,
                    },
                )
            raise JavaWorkerError("Worker returned a non-object protocol response")

        if "request_id" not in data or data.get("request_id") is None:
            return False

        request_id = data["request_id"]
        if "request_id" in reply:
            observed_id = reply["request_id"]
            if observed_id == request_id:
                return True
            reason = "worker_response_request_id_mismatch"
            failure_code = "WORKER_RESPONSE_ID_MISMATCH"
        else:
            code = reply.get("code")
            if (reply.get("ok") is False and not reply.get("status")
                    and code in _UNCORRELATED_PROTOCOL_REJECTIONS):
                return False
            reason = "worker_response_request_id_missing"
            failure_code = "WORKER_RESPONSE_ID_MISSING"

        self._remember_uncorrelated_request(request_id, reason)
        diagnostic: dict[str, Any] = {
            "code": failure_code,
            "request_id": request_id,
            "status": "UNKNOWN",
            "execution_state_unknown": True,
        }
        if "request_id" in reply:
            observed_id = reply["request_id"]
            diagnostic["observed_request_id_type"] = type(observed_id).__name__
            if isinstance(observed_id, (str, int, float, bool)) or observed_id is None:
                diagnostic["observed_request_id"] = observed_id
        if isinstance(reply.get("code"), str):
            diagnostic["observed_code"] = reply["code"]
        raise JavaWorkerError(
            "Worker response request_id did not match the submitted request; request state is UNKNOWN",
            reply=diagnostic,
        )

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
        if kind not in {"connect", "disconnect", "model", "modelutil", "license_checkout", "model_snapshot", "call", "children", "walk", "public_describe", "lock_selftest", "code_compile", "code_execute", "g2_entity_identity", "g2_nodegroup_read", "g2_nodegroup_ungroup"}:
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

    def checkout_license(self, products: list[str], *, request_id: str,
                         queue_timeout_s: float, rpc_timeout_s: float) -> dict[str, Any]:
        """Issue one explicit seat-consuming request on this existing session.

        The payload contains only the product tokens. Authorization references
        stay in the control plane and are never sent to the Java Worker.
        """
        if not isinstance(products, list) or not products or len(products) > 32:
            raise JavaWorkerError("license checkout requires 1 to 32 products")
        if not isinstance(request_id, str) or not request_id:
            raise JavaWorkerError("license checkout requires a durable request_id")
        return self.submit("license_checkout", {"products": list(products)}, request_id=request_id,
                           queue_timeout_s=queue_timeout_s, rpc_timeout_s=rpc_timeout_s)

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

    def describe_public(self, node: "RemoteJava") -> dict[str, Any]:
        """Actual public receiver hierarchy, generation-fenced and read-only."""
        if not isinstance(node, RemoteJava) or node._worker is not self or node._generation != self.generation:
            raise JavaWorkerError("STALE_WORKER_HANDLE")
        reply = self.submit("public_describe", {"handle": node._handle, "generation": node._generation})
        result = _decode_reply(reply, self)
        if not isinstance(result, Mapping):
            raise JavaWorkerError("invalid public interface descriptor")
        return dict(result)

    def entity_identity(self, left: "RemoteJava", right: "RemoteJava", *,
                        request_id: str | None = None, rpc_timeout_s: float | None = None) -> bool:
        """Compare two current opaque handles by Java reference identity only."""
        left = _require_d2_remote(self, left)
        right = _require_d2_remote(self, right)
        generation = self.generation
        if type(generation) is not int or generation < 1 or left._generation != generation or right._generation != generation:
            raise JavaWorkerError("STALE_WORKER_HANDLE")
        from ._domain_outcome import record_engine_method
        record_engine_method("entity_identity", left._handle, right._handle, generation,
                             command="g2_entity_identity", receiver="private-worker")
        reply = self.submit("g2_entity_identity", {"left_handle": left._handle,
                              "right_handle": right._handle, "generation": generation},
                            request_id=request_id, rpc_timeout_s=rpc_timeout_s)
        result = _d2_success_result(reply, "g2_entity_identity")
        if set(result) != {"same_reference", "generation", "identity_scope"}:
            raise JavaWorkerError("D2 identity reply has missing or unknown fields", reply=reply)
        if (type(result["same_reference"]) is not bool or type(result["generation"]) is not int
                or result["generation"] != generation or result["identity_scope"] != "java_reference_identity"):
            raise JavaWorkerError("D2 identity reply has invalid field types or generation", reply=reply)
        return result["same_reference"]

    def read_nodegroup(self, node: "RemoteJava", method: str, *args: Any,
                       request_id: str | None = None, rpc_timeout_s: float | None = None) -> Any:
        """Use only the exact private COMSOL 6.4 NodeGroup evidence table."""
        from ._domain_outcome import record_engine_method
        receiver_handle = getattr(node, "_handle", None)
        record_engine_method(method, *args, command="g2_nodegroup_read", receiver=receiver_handle)
        node = _require_d2_remote(self, node)
        expected_receiver, parameter_types, return_type = _d2_read_signature(method, args)
        descriptor = self.describe_public(node)
        _require_d2_public_signature(descriptor, expected_receiver, method, parameter_types, return_type)
        reply = self.submit("g2_nodegroup_read", {"handle": node._handle, "generation": node._generation,
                              "method": method, "args": list(args)},
                            request_id=request_id, rpc_timeout_s=rpc_timeout_s)
        result = _d2_success_result(reply, "g2_nodegroup_read")
        expected = {"generation", "receiver_interface", "method", "parameters", "return_type", "runtime_version", "value"}
        if set(result) != expected:
            raise JavaWorkerError("D2 read reply has missing or unknown fields", reply=reply)
        if (type(result["generation"]) is not int or result["generation"] != node._generation
                or type(result["receiver_interface"]) is not str or result["receiver_interface"] != expected_receiver
                or type(result["method"]) is not str or result["method"] != method
                or type(result["parameters"]) is not list or result["parameters"] != list(parameter_types)
                or type(result["return_type"]) is not str or result["return_type"] != return_type
                or type(result["runtime_version"]) is not str or result["runtime_version"] != _D2_COMSOL_VERSION):
            raise JavaWorkerError("D2 read reply signature, version, or generation differs from its exact contract", reply=reply)
        value = _decode_d2_typed_value(result["value"], self, node._generation)
        if method == "tags":
            if type(value) is not list or any(type(item) is not str or not item for item in value):
                raise JavaWorkerError("NodeGroupList.tags returned a malformed String[]", reply=reply)
            if len(set(value)) != len(value):
                raise JavaWorkerError("NodeGroupList.tags returned duplicate tags", reply=reply)
        elif method == "size":
            if type(value) is not int or value < 0:
                raise JavaWorkerError("NodeGroup.size returned a malformed int", reply=reply)
        elif method == "get":
            if not isinstance(value, RemoteJava):
                raise JavaWorkerError("NodeGroup.get returned no typed entity handle", reply=reply)
        elif method == "nodeGroup":
            if not isinstance(value, RemoteJava):
                raise JavaWorkerError("Model.nodeGroup returned no typed receiver handle", reply=reply)
        elif method == "feature":
            if not isinstance(value, RemoteJava):
                raise JavaWorkerError("NodeGroup.feature returned no typed list handle", reply=reply)
        elif method in {"getAfter", "getContainer"}:
            if value is not None and not isinstance(value, RemoteJava):
                raise JavaWorkerError(f"{method} returned neither null nor a typed handle", reply=reply)
        elif method == "resolveModelPath" and value is not None and type(value) is not str:
            raise JavaWorkerError("resolveModelPath returned a non-string value", reply=reply)
        return value

    def ungroup_nodegroup(self, node: "RemoteJava", tag: str, *,
                          request_id: str | None = None, rpc_timeout_s: float | None = None) -> dict[str, Any]:
        from ._domain_outcome import record_engine_method
        handle = getattr(node, "_handle", None)
        record_engine_method("ungroup", tag, command="g2_nodegroup_ungroup", receiver=handle)
        node = _require_d2_remote(self, node)
        if type(tag) is not str or not tag:
            raise JavaWorkerError("ungroup tag must be a nonempty string")
        descriptor = self.describe_public(node)
        _require_d2_public_signature(descriptor, "com.comsol.model.NodeGroupList", "ungroup",
                                     ("java.lang.String",), "void")
        reply = self.submit("g2_nodegroup_ungroup", {"handle": node._handle,
                              "generation": node._generation, "tag": tag},
                            request_id=request_id, rpc_timeout_s=rpc_timeout_s)
        result = _d2_success_result(reply, "g2_nodegroup_ungroup")
        if (set(result) != {"ungroup_dispatched", "generation", "tag", "semantic_operation", "runtime_version"}
                or result.get("ungroup_dispatched") is not True
                or type(result.get("generation")) is not int or result["generation"] != node._generation
                or type(result.get("tag")) is not str or result["tag"] != tag
                or result.get("semantic_operation") != "NodeGroupList.ungroup(String)"
                or result.get("runtime_version") != _D2_COMSOL_VERSION):
            raise JavaWorkerError("D2 ungroup reply has invalid type, signature, version, or generation", reply=reply)
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


def _require_d2_remote(worker: PersistentJavaWorker, node: Any) -> "RemoteJava":
    if not isinstance(node, RemoteJava):
        raise JavaWorkerError("D2 private transport requires an actual RemoteJava handle")
    generation = worker.generation
    if (node._worker is not worker or type(generation) is not int or generation < 1
            or type(node._generation) is not int or node._generation != generation
            or type(node._handle) is not str or not node._handle):
        raise JavaWorkerError("STALE_WORKER_HANDLE")
    return node


def _d2_read_signature(method: Any, args: tuple[Any, ...]) -> tuple[str, tuple[str, ...], str]:
    if type(method) is not str or not method:
        raise JavaWorkerError("D2 read method must be a nonempty exact string")
    rows = _D2_READ_RECEIVERS.get(method)
    if rows is None:
        raise JavaWorkerError("method is not in the private D2 read table")
    receiver, overloads = rows
    if method == "nodeGroup":
        if not args:
            parameters = ()
        elif len(args) == 1 and type(args[0]) is str and args[0]:
            parameters = ("java.lang.String",)
        else:
            raise JavaWorkerError("Model.nodeGroup accepts zero arguments or one nonempty string tag")
    elif method == "get":
        if len(args) != 1 or type(args[0]) is not int or args[0] < 0 or args[0] > 2**31 - 1:
            raise JavaWorkerError("NodeGroup.get requires one exact nonnegative Java int")
        parameters = ("int",)
    else:
        if args:
            raise JavaWorkerError(f"{method} accepts no arguments on the D2 route")
        parameters = ()
    return_type = overloads.get(parameters)
    if return_type is None:
        raise JavaWorkerError("D2 read signature is not admitted")
    return receiver, parameters, return_type


def _require_d2_public_signature(descriptor: Mapping[str, Any], interface: str, method: str,
                                 parameters: tuple[str, ...], returns: str) -> None:
    if (not isinstance(descriptor, Mapping) or type(descriptor.get("runtime_version")) is not str
            or descriptor.get("runtime_version") != _D2_COMSOL_VERSION):
        raise JavaWorkerError("D2 public receiver has no exact supported COMSOL version")
    interfaces = descriptor.get("interfaces")
    methods = descriptor.get("methods")
    if (type(interfaces) is not list or any(type(item) is not str for item in interfaces)
            or interface not in interfaces or type(methods) is not list):
        raise JavaWorkerError("D2 public receiver lacks its exact interface or method metadata")
    wanted_parameters = list(parameters)
    for row in methods:
        if not isinstance(row, Mapping) or set(row) != {"interface", "method", "parameters", "returns"}:
            continue
        if (row.get("interface") == interface and row.get("method") == method
                and type(row.get("parameters")) is list and row["parameters"] == wanted_parameters
                and type(row.get("returns")) is str and row["returns"] == returns):
            return
    raise JavaWorkerError(f"D2 public receiver lacks exact {interface}.{method}{parameters} -> {returns}")


def _d2_success_result(reply: Any, command: str) -> dict[str, Any]:
    if not isinstance(reply, Mapping):
        raise JavaWorkerError("D2 Worker reply is not an object")
    if reply.get("ok") is not True:
        raise JavaWorkerError(json.dumps(dict(reply), sort_keys=True), reply=reply)
    if (set(reply) != _D2_SUCCESS_ENVELOPE_FIELDS or type(reply.get("ok")) is not bool
            or type(reply.get("request_id")) is not str or not reply["request_id"]
            or type(reply.get("type")) is not str or reply["type"] != command
            or type(reply.get("status")) is not str or reply["status"] != "SUCCEEDED"
            or any(type(reply.get(name)) is not str or not reply[name]
                   for name in ("queued_at_ms", "started_at_ms", "completed_at_ms"))
            or not isinstance(reply.get("result"), Mapping)):
        raise JavaWorkerError("D2 Worker success envelope is malformed", reply=reply)
    return dict(reply["result"])


def _decode_d2_typed_value(value: Any, worker: PersistentJavaWorker, generation: int) -> Any:
    if isinstance(value, Mapping) and "$worker_handle" in value:
        if set(value) != {"$worker_handle", "generation", "java_type"}:
            raise JavaWorkerError("D2 typed Java handle has missing or unknown fields")
        handle = value.get("$worker_handle")
        returned_generation = value.get("generation")
        java_type = value.get("java_type")
        if (type(handle) is not str or not handle or type(returned_generation) is not int
                or returned_generation != generation or type(java_type) is not str or not java_type):
            raise JavaWorkerError("D2 typed Java handle has malformed fields or stale generation")
        return RemoteJava(worker, handle, generation, java_type)
    return value


class RemoteJava:
    """Opaque Java object held only by the worker; stale generations fail locally."""
    def __init__(self, worker: PersistentJavaWorker, handle: str, generation: int, java_type: str = "") -> None:
        self._worker, self._handle, self._generation, self.java_type = worker, handle, generation, java_type

    def _call(self, method: str, *args: Any, request_id: str | None = None,
              queue_timeout_s: float | None = None, rpc_timeout_s: float | None = None) -> Any:
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
                                    request_id=request_id, queue_timeout_s=queue_timeout_s,
                                    rpc_timeout_s=rpc_timeout_s)
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

    def _raw_save(self, path: str, *, save_copy: bool = True, **kwargs: Any) -> Any:
        """Engine save used only by the atomic publisher's temporary callback."""
        if not path:
            raise JavaWorkerError("raw save requires the atomic publisher's temporary path")
        return self._call("save", path, save_copy, **kwargs)

    def save(self, path: str = "", copy: bool | str = True, *, overwrite: bool = True,
             **kwargs: Any) -> Any:
        if not isinstance(overwrite, bool):
            raise JavaWorkerError("overwrite must be boolean")
        if isinstance(copy, str):
            if copy not in {"java", "m"}:
                raise JavaWorkerError("source export type must be 'java' or 'm'")
            if not path:
                raise JavaWorkerError("source export requires an explicit project-relative filename")
            from comsol_mcp._execution_contract import canonical_project_path
            target = canonical_project_path(self._worker.paths.resolved_project_root, path)
            return _atomic_export_file(
                target,
                lambda temporary: self._call("save", str(temporary), copy, **kwargs),
                self._worker.paths.resolved_project_root,
                overwrite=overwrite,
            )
        if not isinstance(copy, bool):
            raise JavaWorkerError("saveCopy must be boolean or an explicit source file type")
        saved_path = str(self._call("getFilePath", **kwargs) or "").strip() if not path else str(path)
        if not saved_path:
            raise JavaWorkerError("model has no file path; an explicit project-relative save path is required")
        # Preserve the existing managed-adapter contract: bool ``copy`` is an
        # accepted MPh compatibility argument, while the managed publisher
        # always uses COMSOL saveCopy=true to keep its temporary file from
        # becoming the model's remembered save location. ``overwrite`` is a
        # separate local publication policy and never enters the Java call.
        target = Path(saved_path)
        from comsol_mcp._atomic_save import atomic_save
        return atomic_save(target, lambda temporary: self._raw_save(str(temporary), save_copy=True, **kwargs),
                           project_root=self._worker.paths.resolved_project_root,
                           overwrite=overwrite)


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
