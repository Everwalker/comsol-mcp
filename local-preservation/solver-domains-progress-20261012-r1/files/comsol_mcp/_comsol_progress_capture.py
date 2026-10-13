"""Bounded, job-bound capture of COMSOL client progress output.

The host creates one private file for one already-admitted operation. The
typed Worker command switches the connected ModelUtil client to that file,
then redirects the sink and disables progress before the host reads it back.
Raw bytes are stored as bounded, digest-checked OperationStore events so the
existing project-authorized ``solver.log_read`` cursor can page them.

The producer is used for the managed ``study.run`` and
``mesh.convergence_study`` routes only. Trusted Java recipes may directly
change ModelUtil's process-global progress sink, so they are deliberately not
reported as captured without a way to read back that sink identity.

This is a software producer contract. A native COMSOL run is still required to
prove that a particular model emits useful progress bytes.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import secrets
import stat
import tempfile
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, PreWriteRefusal


PRODUCER = "ModelUtil.showProgress(String)+showProgress(false)"
API_EVIDENCE = {
    "ModelUtil": {
        "doc": 7768, "chunk": 23171,
        "sha256": "a5e4c080407a6735a5dd78e95b9fb089d92c0f518477ffdcc4f62b3348085c4eb",
    },
    "ProgrammingReference": {
        "doc": 4086, "chunk": 16699,
        "sha256": "efba38a9c78d87e68e7306ced73ccc947c97ca43802bfb5b29045b45c56bb16a",
    },
}
MAX_STORED_BYTES = 1024 * 1024
CHUNK_BYTES = 8192


def _binding_for_job(store: Any, operation_id: str, operation: str,
                     execution: Mapping[str, Any], model_ref: Mapping[str, Any],
                     model_revision: int) -> dict[str, Any]:
    """Bind the capture to the exact host-created job and persisted request."""
    if not isinstance(operation_id, str) or not operation_id:
        raise PreWriteRefusal("OPERATION_IDENTITY_REQUIRED", "progress capture requires a host operation id")
    if operation not in {"study.run", "mesh.convergence_study"}:
        raise PreWriteRefusal("UNSUPPORTED_OPERATION", "progress capture is supported only for managed solver routes")
    if isinstance(model_revision, bool) or not isinstance(model_revision, int) or model_revision < 0:
        raise PreWriteRefusal("REVISION_CONFLICT", "progress capture requires an exact non-negative model revision")
    try:
        job = store.operation_job(operation_id)
    except Exception as exc:
        raise PreWriteRefusal("OPERATION_STORE_UNAVAILABLE", "the operation/job binding could not be read",
                              details={"cause_type": type(exc).__name__}) from exc
    if not isinstance(job, Mapping):
        raise PreWriteRefusal("OPERATION_STORE_UNAVAILABLE", "the admitted operation has no durable job row")
    op = job.get("operation")
    op_metadata = op.get("metadata") if isinstance(op, Mapping) else None
    job_metadata = job.get("metadata")
    op_execution = op_metadata.get("execution") if isinstance(op_metadata, Mapping) else None
    job_execution = job_metadata.get("execution") if isinstance(job_metadata, Mapping) else None
    if (job.get("operation_id") != operation_id or not isinstance(op, Mapping)
            or op.get("operation_id") != operation_id or not isinstance(op_metadata, Mapping)
            or not isinstance(op_execution, Mapping) or op_execution != job_execution):
        raise PreWriteRefusal("OPERATION_IDENTITY_MISMATCH", "job and operation metadata do not share one exact execution binding")

    stored_operation = op_metadata.get("operation")
    stored_arguments = op_metadata.get("arguments")
    if stored_operation != operation:
        if (stored_operation not in {"registry_call", "operation_call"}
                or not isinstance(stored_arguments, Mapping)
                or stored_arguments.get("operation_id") != operation):
            raise PreWriteRefusal("OPERATION_IDENTITY_MISMATCH", "persisted request does not select the solver operation being captured")

    project_id = execution.get("project_id")
    session_id = execution.get("session_id")
    request_ref = execution.get("model_ref")
    request_id = op.get("request_id")
    if (not isinstance(project_id, str) or not project_id
            or not isinstance(session_id, str) or not session_id
            or not isinstance(request_ref, Mapping) or dict(request_ref) != dict(model_ref)
            or op_execution.get("project_id") != project_id
            or op_execution.get("session_id") != session_id
            or op_execution.get("model_ref") != dict(model_ref)
            or op_execution.get("expected_revision") != model_revision
            or (execution.get("request_id") is not None and execution.get("request_id") != request_id)
            or not isinstance(request_id, str) or not request_id
            or not isinstance(op.get("request_hash"), str) or len(op["request_hash"]) != 64
            or any(char not in "0123456789abcdef" for char in op["request_hash"])
            or not isinstance(op.get("idempotency_key"), str) or not op["idempotency_key"]
            or not isinstance(job.get("job_id"), str) or not job.get("job_id")):
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "progress capture is missing an exact project/session/model/revision/request binding")
    return {
        "schema": "comsol-job-progress-capture/1",
        "producer": PRODUCER,
        "capture_id": secrets.token_hex(16),
        "job_id": job["job_id"],
        "operation_id": operation_id,
        "request_id": request_id,
        "request_hash": op["request_hash"],
        "idempotency_key": op["idempotency_key"],
        "project_id": project_id,
        "session_id": session_id,
        "model_ref": dict(model_ref),
        "model_revision": model_revision,
        "operation": operation,
    }


def _identity_key(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size),
            int(info.st_mtime_ns), int(info.st_ctime_ns))


class ComsolProgressCapture:
    """One bounded progress-log lifecycle inside the already-serialized job."""

    def __init__(self, worker: Any, store: Any, project_root: str | Path, *,
                 operation_id: str, operation: str, execution: Mapping[str, Any],
                 model_ref: Mapping[str, Any], model_revision: int) -> None:
        self.worker = worker
        self.store = store
        self.root = Path(project_root).expanduser()
        self.binding = _binding_for_job(store, operation_id, operation, execution, model_ref, model_revision)
        self.scratch: Path | None = None
        self.log_path: Path | None = None
        self.client: Any = None
        self.active = False
        self._scratch_identity: tuple[int, int] | None = None
        self._file_identity: tuple[int, int] | None = None

    def _event(self, name: str, metadata: Mapping[str, Any]) -> None:
        self.store.add_event(self.binding["job_id"], name,
                             {**self.binding, **dict(metadata)})

    def _cleanup_scratch(self) -> bool:
        if self.scratch is None or self.log_path is None:
            return True
        try:
            scratch_info = os.stat(self.scratch, follow_symlinks=False)
            if (self._scratch_identity is None
                    or (int(scratch_info.st_dev), int(scratch_info.st_ino)) != self._scratch_identity
                    or stat.S_ISLNK(scratch_info.st_mode)
                    or not stat.S_ISDIR(scratch_info.st_mode)):
                return False
            try:
                file_info = os.stat(self.log_path, follow_symlinks=False)
            except FileNotFoundError:
                file_info = None
            if file_info is not None:
                if (self._file_identity is None
                        or (int(file_info.st_dev), int(file_info.st_ino)) != self._file_identity
                        or stat.S_ISLNK(file_info.st_mode)
                        or not stat.S_ISREG(file_info.st_mode)):
                    return False
                self.log_path.unlink()
            self.scratch.rmdir()
            return not self.scratch.exists()
        except OSError:
            return False

    def __enter__(self) -> "ComsolProgressCapture":
        try:
            from ._artifact_store import _assert_no_symlink_components

            if not self.root.is_absolute() or self.root.is_symlink() or not self.root.is_dir():
                raise OSError("the active project workspace is not a regular directory")
            _assert_no_symlink_components(self.root, field="progress capture project root")
            self.root = self.root.resolve(strict=True)
            self.scratch = Path(tempfile.mkdtemp(prefix=".comsol-progress-", dir=self.root))
            _assert_no_symlink_components(self.scratch, field="private progress capture directory")
            scratch_info = os.stat(self.scratch, follow_symlinks=False)
            self._scratch_identity = (int(scratch_info.st_dev), int(scratch_info.st_ino))
            self.log_path = self.scratch / "progress.log"
            fd = os.open(self.log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            file_info = os.fstat(fd)
            self._file_identity = (int(file_info.st_dev), int(file_info.st_ino))
            os.close(fd)
            self._event("COMSOLProgressCaptureRequested", {
                "capture_state": "REQUESTED", "scratch_id": self.scratch.name,
                "byte_limit": MAX_STORED_BYTES, "chunk_bytes": CHUNK_BYTES,
                "api_evidence": API_EVIDENCE,
            })
        except PreWriteRefusal:
            raise
        except Exception as exc:
            self._cleanup_scratch()
            raise PreWriteRefusal("COMSOL_PROGRESS_LOG_UNAVAILABLE",
                                  "a private per-job progress file could not be prepared",
                                  details={"cause_type": type(exc).__name__}) from exc

        try:
            self.client = self.worker.client()
            result = self.client.set_progress_log(str(self.log_path))
            if not isinstance(result, Mapping) or result.get("active") is not True:
                raise RuntimeError("Worker did not confirm the progress-file sink")
            self.active = True
            self._event("COMSOLProgressCaptureActivated", {
                "capture_state": "ACTIVE", "sink_path_persisted": False,
            })
            return self
        except Exception as exc:
            # A missing reply may mean ModelUtil accepted the filename before
            # the Worker response was lost. Preserve the private file and keep
            # the operation unresolved instead of allowing another job to
            # inherit an unknown client-side progress sink.
            self.active = True
            try:
                self._event("COMSOLProgressCaptureActivationUnknown", {
                    "capture_state": "UNKNOWN", "cause_type": type(exc).__name__,
                    "scratch_id": self.scratch.name if self.scratch else None,
                })
            except Exception:
                pass
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "COMSOL progress sink activation did not return a verified receipt",
                stage="post_dispatch", details={"job_id": self.binding["job_id"],
                                                 "capture_id": self.binding["capture_id"],
                                                 "cause_type": type(exc).__name__, "safe_retry": False},
            ) from exc

    def _read_chunks(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        from ._artifact_store import _assert_no_symlink_components

        assert self.scratch is not None and self.log_path is not None
        _assert_no_symlink_components(self.scratch, field="private progress capture directory")
        if self.log_path.is_symlink() or not self.log_path.is_file():
            raise OSError("COMSOL progress output is not a regular file")
        if {entry.name for entry in self.scratch.iterdir()} != {self.log_path.name}:
            raise OSError("the private progress directory contains an unexpected entry")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.log_path, flags)
        chunks: list[dict[str, Any]] = []
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise OSError("COMSOL progress output is not a regular file")
            if ((int(before.st_dev), int(before.st_ino)) != self._file_identity):
                raise OSError("COMSOL progress output is not the exact private file created for this job")
            if before.st_size < 0:
                raise OSError("COMSOL progress output has an invalid size")
            limit = min(int(before.st_size), MAX_STORED_BYTES)
            digest = hashlib.sha256()
            captured = 0
            chunk_index = 0
            while captured < limit:
                raw = os.read(fd, min(CHUNK_BYTES, limit - captured))
                if not raw:
                    raise OSError("COMSOL progress output changed before its declared size was read")
                digest.update(raw)
                chunks.append({
                    "event": "COMSOLProgressChunk",
                    "metadata": {
                        **self.binding,
                        "chunk_index": chunk_index,
                        "byte_offset": captured,
                        "byte_length": len(raw),
                        "chunk_sha256": hashlib.sha256(raw).hexdigest(),
                        "content_base64": base64.b64encode(raw).decode("ascii"),
                        "content_encoding": "base64",
                    },
                })
                captured += len(raw)
                chunk_index += 1
            after = os.fstat(fd)
            path_after = os.stat(self.log_path, follow_symlinks=False)
            if (_identity_key(before) != _identity_key(after)
                    or _identity_key(after) != _identity_key(path_after)
                    or stat.S_ISLNK(path_after.st_mode)):
                raise OSError("COMSOL progress output changed during readback")
        finally:
            os.close(fd)
        truncated = int(before.st_size) > MAX_STORED_BYTES
        summary = {
            "capture_state": "CAPTURED_TRUNCATED" if truncated else ("CAPTURED" if captured else "NO_PROGRESS_TEXT"),
            "complete": not truncated,
            "truncated": truncated,
            "file_size_bytes": int(before.st_size),
            "captured_bytes": captured,
            "chunk_count": len(chunks),
            "captured_sha256": digest.hexdigest(),
            "captured_sha256_scope": "prefix" if truncated else "complete_file",
        }
        return summary, chunks

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if not self.active:
            return False
        try:
            stopped = self.client.set_progress_log(None)
            if not isinstance(stopped, Mapping) or stopped.get("active") is not False or stopped.get("disabled") is not True:
                raise RuntimeError("Worker did not confirm progress redirection and disable")
            self.active = False
            summary, chunks = self._read_chunks()
            for chunk in chunks:
                self.store.add_event(self.binding["job_id"], chunk["event"], chunk["metadata"])
            cleaned = self._cleanup_scratch()
            summary.update({"cleanup_verified": cleaned,
                            "scratch_retained": not cleaned,
                            "scratch_id": self.scratch.name if self.scratch else None,
                            "api_evidence": API_EVIDENCE})
            self._event("COMSOLProgressCaptureCompleted", summary)
            if not cleaned:
                raise OSError("private progress scratch cleanup could not be verified")
        except Exception as failure:
            try:
                self._event("COMSOLProgressCaptureFailed", {
                    "capture_state": "UNKNOWN", "cause_type": type(failure).__name__,
                    "scratch_id": self.scratch.name if self.scratch else None,
                    "worker_sink_state": "UNKNOWN" if self.active else "DISABLED",
                    "safe_retry": False,
                })
            except Exception:
                pass
            details: dict[str, Any] = {
                "job_id": self.binding["job_id"], "capture_id": self.binding["capture_id"],
                "cause_type": type(failure).__name__, "safe_retry": False,
            }
            if exc is not None:
                details["prior_error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "COMSOL progress capture did not complete its disable/readback/persistence proof",
                stage="post_dispatch", details=details,
            ) from failure
        return False


__all__ = ["API_EVIDENCE", "CHUNK_BYTES", "MAX_STORED_BYTES", "PRODUCER", "ComsolProgressCapture"]
