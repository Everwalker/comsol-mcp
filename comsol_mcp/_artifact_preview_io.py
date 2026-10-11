"""Pinned descriptor admission for host-only artifact previews."""
from __future__ import annotations

import hashlib
import errno
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from ._artifact_store import ArtifactStore, _assert_pinned_artifact_stable, _open_pinned_artifact
from ._execution_contract import ExecutionContractError
from ._g2_artifact_preview import MAX_SOURCE_BYTES, build_preview_data


def _safe_filesystem_error(exc: ExecutionContractError) -> ExecutionContractError:
    if exc.code == "ACCESS_VIOLATION":
        return ExecutionContractError("ACCESS_VIOLATION", "registered artifact failed a safe access check", stage=exc.stage)
    if exc.code == "ARTIFACT_NOT_FOUND":
        return ExecutionContractError("ARTIFACT_NOT_FOUND", "registered artifact content is unavailable", stage=exc.stage)
    if exc.code == "ARTIFACT_CHANGED":
        return ExecutionContractError("ARTIFACT_CHANGED", "registered artifact changed during its pinned preview", stage=exc.stage)
    if exc.code == "ARTIFACT_READ_FAILED":
        return ExecutionContractError("ARTIFACT_READ_FAILED", "registered artifact content could not be read safely", stage=exc.stage)
    if exc.code == "INVALID_DESTINATION":
        return ExecutionContractError("ARTIFACT_NOT_FOUND", "registered artifact content is unavailable", stage=exc.stage)
    if exc.code == "RUNTIME_CONFIGURATION_REQUIRED":
        return ExecutionContractError("RUNTIME_CONFIGURATION_REQUIRED", "authorized project artifact root is unavailable", stage=exc.stage)
    return ExecutionContractError(exc.code, "registered artifact could not be opened safely", stage=exc.stage)


def _pinned_io_failure(exc: OSError) -> ExecutionContractError:
    """Convert a descriptor/filesystem failure to a constant public refusal."""
    error_number = getattr(exc, "errno", None)
    if isinstance(exc, PermissionError) or error_number in {errno.EACCES, errno.EPERM}:
        return ExecutionContractError("ACCESS_VIOLATION", "registered artifact failed a safe access check", stage="preview")
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)) or error_number in {errno.ENOENT, errno.ENOTDIR}:
        return ExecutionContractError("ARTIFACT_NOT_FOUND", "registered artifact content is unavailable", stage="preview")
    return ExecutionContractError("ARTIFACT_READ_FAILED", "registered artifact content could not be read safely", stage="preview")


def _sha256_pinned(handle: Any, expected_max_bytes: int) -> tuple[str, int]:
    try:
        handle.seek(0)
    except OSError as exc:
        raise _pinned_io_failure(exc) from None
    digest = hashlib.sha256()
    total = 0
    while total <= expected_max_bytes:
        try:
            block = handle.read(min(1024 * 1024, expected_max_bytes + 1 - total))
        except OSError as exc:
            raise _pinned_io_failure(exc) from None
        if not block:
            break
        total += len(block)
        digest.update(block)
    return digest.hexdigest(), total


def _record_identity_matches(record: Mapping[str, Any], observed: os.stat_result) -> bool:
    expected = record.get("file_identity")
    if not isinstance(expected, Mapping):
        return False
    actual = {
        "device": int(observed.st_dev),
        "inode": int(observed.st_ino),
        "size": int(observed.st_size),
        "mtime_ns": int(getattr(observed, "st_mtime_ns", int(observed.st_mtime * 1_000_000_000))),
    }
    return (record.get("size") == actual["size"]
            and all(expected.get(name) == value for name, value in actual.items()))


def preview_registered_artifact(
    project_root: str | Path,
    record: Mapping[str, Any],
    *,
    artifact_id: str,
    project_id: str,
    options: Mapping[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    """Hash, parse, and re-hash the same project-scoped file descriptor."""
    relative = record.get("path")
    if not isinstance(relative, str):
        raise ExecutionContractError("ARTIFACT_STATE_UNKNOWN", "project artifact path metadata is malformed")
    try:
        path = ArtifactStore(Path(project_root)).resolve_safe_path(relative, allow_overwrite=True)
        handle, before = _open_pinned_artifact(path)
    except OSError as exc:
        raise _pinned_io_failure(exc) from None
    except ExecutionContractError as exc:
        raise _safe_filesystem_error(exc) from exc

    try:
        if not _record_identity_matches(record, before) or int(getattr(before, "st_nlink", 1)) != 1:
            raise ExecutionContractError("ARTIFACT_IDENTITY_MISMATCH", "registered artifact pinned file identity is stale")
        if before.st_size > MAX_SOURCE_BYTES:
            raise ExecutionContractError("INCOMPLETE", "artifact exceeds the 16 MiB source preview limit", stage="preview")

        try:
            initial_hash, initial_size = _sha256_pinned(handle, MAX_SOURCE_BYTES)
            if initial_size > MAX_SOURCE_BYTES:
                raise ExecutionContractError("ARTIFACT_CHANGED", "registered artifact grew beyond the pinned source limit")
            if initial_size != before.st_size or initial_hash != artifact_id:
                raise ExecutionContractError("ARTIFACT_HASH_MISMATCH", "registered artifact bytes do not match artifact_id")
            try:
                before_parse = os.fstat(handle.fileno())
            except OSError as exc:
                raise _pinned_io_failure(exc) from None
            _assert_pinned_artifact_stable(path, before, before_parse)
            try:
                handle.seek(0)
                source = handle.read(before.st_size + 1)
            except OSError as exc:
                raise _pinned_io_failure(exc) from None
            if len(source) != before.st_size or hashlib.sha256(source).hexdigest() != artifact_id:
                parse_error: BaseException | None = ExecutionContractError(
                    "ARTIFACT_CHANGED", "registered artifact changed while pinned preview bytes were read",
                )
                data = None
            else:
                try:
                    data = build_preview_data(
                        source,
                        artifact_id=artifact_id,
                        project_id=project_id,
                        record=record,
                        options=options,
                        request_id=request_id,
                    )
                    parse_error = None
                except BaseException as exc:
                    parse_error = exc
                    data = None
        except ExecutionContractError as exc:
            parse_error = exc
            data = None

        try:
            final_hash, final_size = _sha256_pinned(handle, MAX_SOURCE_BYTES)
            try:
                after = os.fstat(handle.fileno())
            except OSError as exc:
                raise _pinned_io_failure(exc) from None
            _assert_pinned_artifact_stable(path, before, after)
        except ExecutionContractError as exc:
            raise _safe_filesystem_error(exc) from exc
        if (int(getattr(after, "st_nlink", 1)) != 1 or final_size > MAX_SOURCE_BYTES
                or final_size != before.st_size or final_hash != artifact_id):
            raise ExecutionContractError("ARTIFACT_CHANGED", "registered artifact changed during its pinned preview")
        if parse_error is not None:
            if isinstance(parse_error, ExecutionContractError):
                raise _safe_filesystem_error(parse_error) if parse_error.code in {
                    "ACCESS_VIOLATION", "ARTIFACT_NOT_FOUND", "ARTIFACT_CHANGED",
                    "ARTIFACT_READ_FAILED", "INVALID_DESTINATION", "RUNTIME_CONFIGURATION_REQUIRED",
                } else parse_error
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "artifact preview could not establish a bounded result", stage="preview") from parse_error
        assert data is not None
        return data
    finally:
        pending_failure = sys.exc_info()[0] is not None
        try:
            handle.close()
        except OSError as exc:
            if not pending_failure:
                raise _pinned_io_failure(exc) from None
