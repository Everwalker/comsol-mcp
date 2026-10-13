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
_PROGRESS_LEAF = "progress.log"
PATH_CAPABILITY = "POSIX_PINNED_DIRECTORY_CHAIN"
CLEANUP_CAPABILITY = "EXCLUSIVE_QUARANTINE_AND_POSTMOVE_IDENTITY_CHECK"
CLEANUP_SAME_UID_LIMITATION = (
    "POSIX has no atomic unlink-by-inode primitive here; the private 0700 directory, random exclusive "
    "quarantine names, post-move identity checks, and deterministic replacement controls reduce the race, "
    "but do not claim protection from an arbitrary concurrent same-UID mutator after the final check."
)


def _supports_pinned_progress_cleanup() -> bool:
    return os.name != "nt"


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


def _nofollow_file_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _renameat_noreplace(source_parent_fd: int, source_leaf: str,
                        destination_parent_fd: int, destination_leaf: str) -> None:
    """Use ArtifactStore's reviewed pinned-directory exclusive rename primitive."""
    if source_parent_fd != destination_parent_fd:
        raise OSError("progress cleanup quarantine must stay in one pinned parent")
    from ._artifact_store import _rename_no_clobber_at
    _rename_no_clobber_at(source_parent_fd, source_leaf, destination_leaf)


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
        self._root_fds: list[int] = []
        self._root_links: list[tuple[int, str, dict[str, int]]] = []
        self._root_identity: dict[str, int] | None = None
        self._scratch_fd: int | None = None
        self._scratch_leaf: str | None = None
        self._file_leaf = _PROGRESS_LEAF
        self._windows_pin: Any = None

    def _event(self, name: str, metadata: Mapping[str, Any]) -> None:
        self.store.add_event(self.binding["job_id"], name,
                             {**self.binding, **dict(metadata)})

    def _cleanup_scratch(self) -> bool:
        if self.scratch is None or self.log_path is None or self._scratch_identity is None:
            self._close_pinned_handles()
            return True
        try:
            if not _supports_pinned_progress_cleanup():
                # The current publisher API can pin/create the directory tree,
                # but it has no reviewed open-for-delete/verified-remove seam
                # for this file lifecycle. Retain it and report UNKNOWN rather
                # than falling back to a checked absolute-path unlink.
                return False
            from ._state_backup import _posix_directory_flags, _verify_posix_directory_links

            if (not self._root_fds or self._scratch_fd is None or self._scratch_leaf is None
                    or self._root_identity is None or self._scratch_identity is None):
                return False
            root_fd = self._root_fds[-1]
            _verify_posix_directory_links(self._root_links)
            self._check_pinned_scratch_binding()

            # Quarantine the leaf using an exclusive relative rename. If the
            # name was swapped after the preceding check, inspect the actual
            # moved object and put a foreign object back without replacement.
            try:
                named_file = os.stat(self._file_leaf, dir_fd=self._scratch_fd, follow_symlinks=False)
            except FileNotFoundError:
                named_file = None
            if named_file is not None:
                if (self._file_identity is None
                        or (int(named_file.st_dev), int(named_file.st_ino)) != self._file_identity
                        or not stat.S_ISREG(named_file.st_mode)):
                    return False
                file_quarantine = ".cleanup-file-" + secrets.token_hex(16)
                _renameat_noreplace(self._scratch_fd, self._file_leaf,
                                    self._scratch_fd, file_quarantine)
                moved_file = os.open(file_quarantine, _nofollow_file_flags(), dir_fd=self._scratch_fd)
                try:
                    moved_info = os.fstat(moved_file)
                    moved_identity = (int(moved_info.st_dev), int(moved_info.st_ino))
                finally:
                    os.close(moved_file)
                if moved_identity != self._file_identity:
                    try:
                        _renameat_noreplace(self._scratch_fd, file_quarantine,
                                            self._scratch_fd, self._file_leaf)
                    except OSError:
                        return False
                    return False
                # This name is unpredictable and lives in the private pinned
                # directory. Recheck its exact inode immediately before unlink;
                # any deterministic replacement is retained as UNKNOWN.
                current_quarantine = os.stat(file_quarantine, dir_fd=self._scratch_fd,
                                             follow_symlinks=False)
                if (int(current_quarantine.st_dev), int(current_quarantine.st_ino)) != self._file_identity:
                    try:
                        _renameat_noreplace(self._scratch_fd, file_quarantine,
                                            self._scratch_fd, self._file_leaf)
                    except OSError:
                        pass
                    return False
                os.unlink(file_quarantine, dir_fd=self._scratch_fd)

            if os.listdir(self._scratch_fd):
                return False
            _verify_posix_directory_links(self._root_links)
            self._check_pinned_scratch_binding()
            dir_quarantine = ".cleanup-dir-" + secrets.token_hex(16)
            _renameat_noreplace(root_fd, self._scratch_leaf, root_fd, dir_quarantine)
            moved_dir = os.open(dir_quarantine, _posix_directory_flags(), dir_fd=root_fd)
            try:
                moved_info = os.fstat(moved_dir)
                moved_identity = (int(moved_info.st_dev), int(moved_info.st_ino))
                if moved_identity != self._scratch_identity:
                    try:
                        os.close(moved_dir)
                    finally:
                        moved_dir = -1
                    _renameat_noreplace(root_fd, dir_quarantine, root_fd, self._scratch_leaf)
                    return False
                if os.listdir(moved_dir):
                    return False
            finally:
                if moved_dir >= 0:
                    os.close(moved_dir)
            try:
                _verify_posix_directory_links(self._root_links)
            except Exception:
                try:
                    _renameat_noreplace(root_fd, dir_quarantine, root_fd, self._scratch_leaf)
                except OSError:
                    pass
                return False
            quarantined_info = os.stat(dir_quarantine, dir_fd=root_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(quarantined_info.st_mode)
                    or (int(quarantined_info.st_dev), int(quarantined_info.st_ino)) != self._scratch_identity):
                try:
                    _renameat_noreplace(root_fd, dir_quarantine, root_fd, self._scratch_leaf)
                except OSError:
                    pass
                return False
            os.rmdir(dir_quarantine, dir_fd=root_fd)
            try:
                _verify_posix_directory_links(self._root_links)
            except Exception:
                return False
            try:
                os.stat(self._scratch_leaf, dir_fd=root_fd, follow_symlinks=False)
                return False
            except FileNotFoundError:
                return True
        except Exception:
            return False
        finally:
            self._close_pinned_handles()

    def _close_pinned_handles(self) -> None:
        if self._scratch_fd is not None:
            try:
                os.close(self._scratch_fd)
            except OSError:
                pass
            self._scratch_fd = None
        for descriptor in reversed(self._root_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._root_fds = []
        if self._windows_pin is not None:
            try:
                self._windows_pin.__exit__(None, None, None)
            except Exception:
                pass
            self._windows_pin = None

    def _check_pinned_scratch_binding(self) -> None:
        assert self._root_fds and self._scratch_fd is not None and self._scratch_leaf is not None
        root_fd = self._root_fds[-1]
        scratch_info = os.stat(self._scratch_leaf, dir_fd=root_fd, follow_symlinks=False)
        scratch_fd_info = os.fstat(self._scratch_fd)
        identity = (int(scratch_info.st_dev), int(scratch_info.st_ino))
        if (not stat.S_ISDIR(scratch_info.st_mode) or identity != self._scratch_identity
                or identity != (int(scratch_fd_info.st_dev), int(scratch_fd_info.st_ino))):
            raise OSError("private progress directory name no longer names its pinned inode")

    def __enter__(self) -> "ComsolProgressCapture":
        try:
            if not _supports_pinned_progress_cleanup():
                raise PreWriteRefusal(
                    "COMSOL_PROGRESS_PATH_ADAPTER_UNAVAILABLE",
                    "Windows progress-file capture is disabled until a verified handle-relative file cleanup adapter exists",
                    details={"native_operation_dispatched": False, "cleanup_verified": True,
                             "residual_status": "ABSENT", "capability_state": "NOT_CONFIGURED"},
                )
            from ._artifact_store import _assert_no_symlink_components
            from ._state_backup import (_create_directory_tree, _directory_identity,
                                       _open_posix_directory_chain, _posix_directory_flags,
                                       _verify_posix_directory_links)

            if not self.root.is_absolute() or self.root.is_symlink() or not self.root.is_dir():
                raise OSError("the active project workspace is not a regular directory")
            _assert_no_symlink_components(self.root, field="progress capture project root")
            self.root = self.root.resolve(strict=True)
            self._root_identity = _directory_identity(self.root)
            self._root_fds, self._root_links, _ = _open_posix_directory_chain(
                self.root, expected_identity=self._root_identity)
            root_fd = self._root_fds[-1]
            _verify_posix_directory_links(self._root_links)
            self._scratch_leaf = ".comsol-progress-" + secrets.token_hex(16)
            self.scratch = self.root / self._scratch_leaf
            self._scratch_identity = tuple(_create_directory_tree(
                self.root, self._scratch_leaf, expected_root_identity=self._root_identity).values())
            # The helper returns device/inode as an ordered identity mapping.
            if len(self._scratch_identity) != 2:
                raise OSError("private progress directory identity is malformed")
            _verify_posix_directory_links(self._root_links)
            self._scratch_fd = os.open(self._scratch_leaf, _posix_directory_flags(), dir_fd=root_fd)
            scratch_info = os.fstat(self._scratch_fd)
            scratch_name_info = os.stat(self._scratch_leaf, dir_fd=root_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(scratch_name_info.st_mode)
                    or self._scratch_identity != (int(scratch_info.st_dev), int(scratch_info.st_ino))
                    or self._scratch_identity != (int(scratch_name_info.st_dev), int(scratch_name_info.st_ino))):
                raise OSError("private progress directory changed during handle-relative open")
            self.log_path = self.scratch / self._file_leaf
            fd = os.open(self._file_leaf, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                         | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=self._scratch_fd)
            try:
                file_info = os.fstat(fd)
                if not stat.S_ISREG(file_info.st_mode):
                    raise OSError("private progress output is not a regular file")
                self._file_identity = (int(file_info.st_dev), int(file_info.st_ino))
            finally:
                os.close(fd)
            self._event("COMSOLProgressCaptureRequested", {
                "capture_state": "REQUESTED", "scratch_id": self.scratch.name,
                "byte_limit": MAX_STORED_BYTES, "chunk_bytes": CHUNK_BYTES,
                "path_capability": PATH_CAPABILITY,
                "cleanup_capability": CLEANUP_CAPABILITY,
                "same_uid_mutation_limitation": CLEANUP_SAME_UID_LIMITATION,
                "api_evidence": API_EVIDENCE,
            })
        except PreWriteRefusal:
            raise
        except Exception as exc:
            cleaned = self._cleanup_scratch()
            if not cleaned:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    "progress capture setup failed and its handle-pinned scratch cleanup is unverified",
                    stage="pre_dispatch",
                    details={"partial_change": self._scratch_identity is not None,
                             "publication_committed": False, "native_operation_dispatched": False,
                             "cleanup_verified": False, "residual_status": "UNKNOWN",
                             "safe_retry": False, "cause_type": type(exc).__name__},
                ) from exc
            raise PreWriteRefusal("COMSOL_PROGRESS_LOG_UNAVAILABLE",
                                  "a private per-job progress file could not be prepared",
                                  details={"cause_type": type(exc).__name__,
                                           "partial_change": False, "cleanup_verified": True,
                                           "residual_status": "ABSENT"}) from exc

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
            self._close_pinned_handles()
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "COMSOL progress sink activation did not return a verified receipt",
                stage="post_dispatch", details={"job_id": self.binding["job_id"],
                                                 "capture_id": self.binding["capture_id"],
                                                 "cause_type": type(exc).__name__, "partial_change": True,
                                                 "native_operation_dispatched": True,
                                                 "cleanup_verified": False, "residual_status": "UNKNOWN",
                                                 "safe_retry": False},
            ) from exc

    def _read_chunks(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        from ._state_backup import _verify_posix_directory_links

        assert self.scratch is not None and self.log_path is not None
        if not _supports_pinned_progress_cleanup() or self._scratch_fd is None or not self._root_fds:
            raise OSError("handle-relative progress readback is unavailable")
        _verify_posix_directory_links(self._root_links)
        self._check_pinned_scratch_binding()
        if set(os.listdir(self._scratch_fd)) != {self._file_leaf}:
            raise OSError("the private progress directory contains an unexpected entry")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self._file_leaf, flags, dir_fd=self._scratch_fd)
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
            path_after = os.stat(self._file_leaf, dir_fd=self._scratch_fd, follow_symlinks=False)
            if (_identity_key(before) != _identity_key(after)
                    or _identity_key(after) != _identity_key(path_after)
                    or stat.S_ISLNK(path_after.st_mode)):
                raise OSError("COMSOL progress output changed during readback")
            _verify_posix_directory_links(self._root_links)
            self._check_pinned_scratch_binding()
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
        cleanup_attempted = False
        cleanup_verified = False
        try:
            stopped = self.client.set_progress_log(None)
            if not isinstance(stopped, Mapping) or stopped.get("active") is not False or stopped.get("disabled") is not True:
                raise RuntimeError("Worker did not confirm progress redirection and disable")
            self.active = False
            summary, chunks = self._read_chunks()
            for chunk in chunks:
                self.store.add_event(self.binding["job_id"], chunk["event"], chunk["metadata"])
            cleanup_attempted = True
            cleaned = self._cleanup_scratch()
            cleanup_verified = cleaned
            summary.update({"cleanup_verified": cleaned,
                            "scratch_retained": not cleaned,
                            "scratch_id": self.scratch.name if self.scratch else None,
                            "path_capability": PATH_CAPABILITY,
                            "cleanup_capability": CLEANUP_CAPABILITY,
                            "same_uid_mutation_limitation": CLEANUP_SAME_UID_LIMITATION,
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
                    "cleanup_verified": cleanup_verified,
                    "residual_status": "ABSENT" if cleanup_verified else "UNKNOWN",
                    "native_operation_dispatched": True,
                    "path_capability": PATH_CAPABILITY,
                    "cleanup_capability": CLEANUP_CAPABILITY,
                    "same_uid_mutation_limitation": CLEANUP_SAME_UID_LIMITATION,
                    "safe_retry": False,
                })
            except Exception:
                pass
            self._close_pinned_handles()
            details: dict[str, Any] = {
                "job_id": self.binding["job_id"], "capture_id": self.binding["capture_id"],
                "cause_type": type(failure).__name__, "partial_change": True,
                "native_operation_dispatched": True, "cleanup_attempted": cleanup_attempted,
                "cleanup_verified": cleanup_verified,
                "residual_status": "ABSENT" if cleanup_verified else "UNKNOWN",
                "safe_retry": False,
            }
            if exc is not None:
                details["prior_error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "COMSOL progress capture did not complete its disable/readback/persistence proof",
                stage="post_dispatch", details=details,
            ) from failure
        return False


__all__ = ["API_EVIDENCE", "CHUNK_BYTES", "CLEANUP_CAPABILITY", "CLEANUP_SAME_UID_LIMITATION",
           "MAX_STORED_BYTES", "PATH_CAPABILITY", "PRODUCER", "ComsolProgressCapture"]
