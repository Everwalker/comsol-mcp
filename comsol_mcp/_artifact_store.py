"""Artifact Store and Safe Export Service for COMSOL MCP (F08, F09).

Provides:
- Path containment validation preventing directory traversal and symlink escape.
- Pre-write outcome verification: refuses to export when evaluation has errors or cleanup failed.
- Atomic file publishing (temporary file + atomic os.replace).
- Bounded-memory chunk streaming reader (offset + length) without full-file RAM buffering.
- Strict format serialization: refuses non-finite JSON and unsupported formats.

The chunk reader is published as ``artifact.read`` so a host can request
bounded reads instead of receiving a whole array: NEXT_GOAL requires a real,
host-requestable artifact/chunk contract that pins the file's immutable hash,
size, offset, length and format, and explicitly rejects calling a
"read everything into memory and split it afterwards" implementation streaming.
"""
from __future__ import annotations

import base64
import csv
import errno
import hashlib
import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ._execution_contract import ExecutionContractError


MAX_CHUNK_BYTES = 16 * 1024 * 1024
CSV_COLUMNS = (
    "axis", "expr", "outer", "inner", "point", "real", "imag", "unit",
    "coord_0", "coord_1", "coord_2",
)
_UNKNOWN_STATUS = {"UNKNOWN", "EXECUTION_STATE_UNKNOWN", "ENGINE_STATE_UNKNOWN", "STATE_UNKNOWN"}
_FAILED_STATUS = {"FAILED", "FAIL", "ERROR", "REFUSED", "REJECTED"}


def _contract_error(
    code: str,
    message: str,
    *,
    stage: str = "validation",
    details: Mapping[str, Any] | None = None,
) -> ExecutionContractError:
    return ExecutionContractError(code, message, stage=stage, details=details)


def trusted_project_root(worker: Any) -> Path:
    """Return the project root supplied by the trusted Worker configuration.

    A request body never supplies this value.  The persistent Java Worker exposes
    ``paths.resolved_project_root``; small test/adaptor Workers may expose
    ``paths.project_root`` or a backend-owned ``project_root``.  Falling back to
    the process cwd would make a file operation depend on whoever launched the
    daemon, so it is deliberately rejected.
    """
    paths = getattr(worker, "paths", None)
    candidate: Any = None
    if paths is not None:
        resolver = getattr(paths, "resolved_project_root", None)
        if resolver is not None:
            candidate = resolver() if callable(resolver) else resolver
        if candidate is None:
            candidate = getattr(paths, "project_root", None)
    if candidate is None:
        candidate = getattr(worker, "project_root", None)
    if candidate is None:
        raise _contract_error(
            "RUNTIME_CONFIGURATION_REQUIRED",
            "artifact operations require a trusted Worker project root; cwd is not an authorization source",
        )
    try:
        raw = Path(candidate).expanduser()
    except (TypeError, ValueError) as exc:
        raise _contract_error("RUNTIME_CONFIGURATION_REQUIRED", "trusted project root is not a valid path") from exc
    if not raw.is_absolute() or raw.is_symlink() or not raw.is_dir():
        raise _contract_error(
            "RUNTIME_CONFIGURATION_REQUIRED",
            f"trusted project root must be an existing non-symlink directory: {candidate!r}",
        )
    _assert_no_symlink_components(raw, field="trusted project root")
    try:
        root = raw.resolve(strict=True)
    except OSError as exc:
        raise _contract_error("RUNTIME_CONFIGURATION_REQUIRED", "trusted project root could not be resolved") from exc
    if not root.is_dir() or root.is_symlink():
        raise _contract_error("RUNTIME_CONFIGURATION_REQUIRED", "trusted project root is not a directory")
    return root


def _reject_nonfinite(value: Any, *, path: str = "value") -> None:
    """Validate values before a serializer or destination side effect runs."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _contract_error(
                "SERIALIZATION_ERROR",
                f"non-finite numeric value at {path}: {value!r}",
                stage="post_dispatch",
            )
        return
    if isinstance(value, complex):
        raise _contract_error(
            "SERIALIZATION_ERROR",
            f"raw complex value at {path} must be represented by explicit real/imag fields",
            stage="post_dispatch",
        )
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise _contract_error("SERIALIZATION_ERROR", f"mapping key at {path} is not a string", stage="post_dispatch")
            _reject_nonfinite(child, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for index, child in enumerate(value):
            _reject_nonfinite(child, path=f"{path}[{index}]")
        return
    raise _contract_error(
        "SERIALIZATION_ERROR",
        f"unsupported value type at {path}: {type(value).__name__}",
        stage="post_dispatch",
    )


def _empty_values(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, Mapping) or (isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))):
        return len(value) == 0
    return False


def _evaluation_ok(eval_result: Mapping[str, Any]) -> None:
    """Reject failed, partial, unknown, or unverified evaluation outcomes."""
    status = eval_result.get("status")
    if isinstance(status, Mapping):
        if status.get("ok") is False or status.get("execution_state_unknown") is True:
            raise _contract_error("EXPORT_FAILED", f"evaluation status is not publishable: {status}", stage="post_dispatch")
        token = status.get("status")
        if isinstance(token, str) and token.upper() in (_UNKNOWN_STATUS | _FAILED_STATUS):
            raise _contract_error("EXPORT_FAILED", f"evaluation status is not publishable: {status}", stage="post_dispatch")
        if status.get("engine_error") or status.get("cleanup_failed") is True:
            raise _contract_error("EXPORT_FAILED", f"evaluation status contains an error: {status}", stage="post_dispatch")
    elif isinstance(status, str) and status.upper() in (_UNKNOWN_STATUS | _FAILED_STATUS):
        raise _contract_error("EXPORT_FAILED", f"evaluation status is not publishable: {status!r}", stage="post_dispatch")
    for key in ("execution_state_unknown", "engine_state_unknown", "cleanup_failed", "partial_change"):
        if eval_result.get(key) is True:
            raise _contract_error("EXPORT_FAILED", f"evaluation carries {key}=true", stage="post_dispatch")
    cleanup = eval_result.get("cleanup")
    if isinstance(cleanup, Mapping) and cleanup.get("cleanup_failed") is True:
        raise _contract_error("EXPORT_FAILED", f"evaluation cleanup failed: {cleanup}", stage="post_dispatch")
    if eval_result.get("engine_error") or eval_result.get("error"):
        raise _contract_error("EXPORT_FAILED", "evaluation returned an error", stage="post_dispatch")
    if "values" not in eval_result or _empty_values(eval_result.get("values")):
        raise _contract_error("EXPORT_FAILED", "evaluation produced no non-empty values", stage="post_dispatch")
    _reject_nonfinite(eval_result.get("values"), path="values")


def _json_payload(eval_result: Mapping[str, Any]) -> dict[str, Any]:
    metadata = {
        key: value for key, value in eval_result.items()
        if key not in {"values", "artifact", "storage"}
    }
    payload = {
        "spec": eval_result.get("spec", {}),
        "values": eval_result["values"],
        "metadata": metadata,
    }
    _reject_nonfinite(payload, path="payload")
    return payload


def _row_for_leaf(path: tuple[Any, ...], value: Any, eval_result: Mapping[str, Any]) -> list[Any]:
    metadata = value if isinstance(value, Mapping) else {}
    if isinstance(value, Mapping) and ("real" in value or "imag" in value):
        real, imag = value.get("real", ""), value.get("imag", "")
    elif isinstance(value, Mapping) and "value" in value:
        real, imag = value.get("value", ""), ""
    else:
        real, imag = value, ""
    exprs = eval_result.get("expressions")
    expr_index = path[0] if path and isinstance(path[0], int) else None
    expr = metadata.get("expr", exprs[expr_index] if isinstance(expr_index, int) and isinstance(exprs, Sequence) and expr_index < len(exprs) else "")
    units = eval_result.get("expression_units")
    unit = metadata.get("unit", units[expr_index] if isinstance(expr_index, int) and isinstance(units, Sequence) and expr_index < len(units) else "")
    coords = metadata.get("coords", eval_result.get("coordinates", ()))
    if not isinstance(coords, Sequence) or isinstance(coords, (str, bytes)):
        coords = ()
    return [
        metadata.get("axis", "values"), expr, metadata.get("outer", ""),
        metadata.get("inner", path[1] if len(path) > 1 and isinstance(path[1], int) else ""),
        metadata.get("point", path[-1] if path and isinstance(path[-1], int) else ""),
        real, imag, unit,
        coords[0] if len(coords) > 0 else "", coords[1] if len(coords) > 1 else "", coords[2] if len(coords) > 2 else "",
    ]


def _iter_csv_rows(value: Any, eval_result: Mapping[str, Any], path: tuple[Any, ...] = ()) -> Iterator[list[Any]]:
    if isinstance(value, Mapping):
        if "real" in value or "imag" in value or "value" in value:
            yield _row_for_leaf(path, value, eval_result)
            return
        for key, child in value.items():
            yield from _iter_csv_rows(child, eval_result, path + (key,))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            yield from _iter_csv_rows(child, eval_result, path + (index,))
        return
    yield _row_for_leaf(path, value, eval_result)


def _assert_no_symlink_components(path: Path, *, field: str) -> None:
    """Reject symlink components without first resolving them away."""
    lexical = Path(os.path.abspath(os.fspath(path)))
    current = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        current = current / component
        try:
            if current.is_symlink():
                raise _contract_error(
                    "ACCESS_VIOLATION",
                    f"{field} contains a symlink component: {current}",
                )
        except OSError as exc:
            raise _contract_error(
                "ACCESS_VIOLATION",
                f"{field} could not be inspected safely: {current}",
            ) from exc


def _stream_file_hash(path: Path, *, block_size: int = 1024 * 1024) -> tuple[int, str]:
    """Hash a completed file with bounded memory and return (size, digest)."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def _file_identity(st: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return the identity/version fields pinned for one artifact read.

    ``st_dev`` and ``st_ino`` bind the pathname to the opened inode while the
    size and nanosecond mtime/ctime fields detect replacement or in-place
    mutation during the bounded hash-and-read operation.  atime is omitted
    because reading a file may legitimately update it.
    """
    return (
        int(st.st_dev),
        int(st.st_ino),
        int(st.st_size),
        int(st.st_mtime_ns),
        int(st.st_ctime_ns),
    )


def _artifact_changed(path: Path, message: str) -> ExecutionContractError:
    return _contract_error(
        "ARTIFACT_CHANGED",
        f"Artifact {path} changed during its pinned read: {message}",
        stage="validation",
    )


def _open_pinned_artifact(path: Path) -> tuple[Any, os.stat_result]:
    """Open a regular artifact with final-component ``O_NOFOLLOW``.

    The caller must consume and validate the returned handle before closing it.
    The path stat is compared with the first fd stat so a replacement between
    the lexical checks and ``os.open`` cannot be mistaken for the requested
    artifact.
    """
    _assert_no_symlink_components(path, field="artifact")
    try:
        path_before = os.stat(os.fspath(path), follow_symlinks=False)
    except FileNotFoundError as exc:
        raise _contract_error("ARTIFACT_NOT_FOUND", f"Artifact file not found: {path}") from exc
    except OSError as exc:
        raise _contract_error("ACCESS_VIOLATION", f"Artifact could not be inspected safely: {path}") from exc
    if not stat.S_ISREG(path_before.st_mode):
        raise _contract_error("ARTIFACT_NOT_FOUND", f"Artifact is not a regular file: {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except FileNotFoundError as exc:
        raise _contract_error("ARTIFACT_NOT_FOUND", f"Artifact file not found: {path}") from exc
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ELOOP:
            raise _contract_error("ACCESS_VIOLATION", f"Artifact became a symlink: {path}") from exc
        raise _contract_error("ARTIFACT_NOT_FOUND", f"Artifact could not be opened: {path}") from exc

    try:
        handle = os.fdopen(fd, "rb", closefd=True)
        fd = -1
        fd_before = os.fstat(handle.fileno())
        if not stat.S_ISREG(fd_before.st_mode) or _file_identity(fd_before) != _file_identity(path_before):
            raise _artifact_changed(path, "pathname identity changed before reading")
        return handle, fd_before
    except BaseException:
        if fd >= 0:
            os.close(fd)
        else:
            # ``fdopen`` owns the descriptor after it succeeds.
            try:
                handle.close()  # type: ignore[name-defined]
            except (NameError, OSError):
                pass
        raise


def _assert_pinned_artifact_stable(path: Path, before: os.stat_result, after: os.stat_result) -> None:
    """Verify both fd metadata and current pathname identity after reading."""
    if not stat.S_ISREG(after.st_mode) or _file_identity(after) != _file_identity(before):
        raise _artifact_changed(path, "opened-file metadata changed")
    try:
        path_after = os.stat(os.fspath(path), follow_symlinks=False)
    except OSError as exc:
        raise _artifact_changed(path, f"pathname could not be revalidated: {exc}") from exc
    if not stat.S_ISREG(path_after.st_mode) or _file_identity(path_after) != _file_identity(before):
        raise _artifact_changed(path, "pathname no longer names the opened file")


def _read_pinned_chunk(path: Path, offset: int, length: int) -> dict[str, Any]:
    """Hash and read one chunk from the same opened descriptor.

    Keeping the hash and payload read on one fd closes the old race where a
    same-size replacement could pair the old whole-file digest with new chunk
    bytes.  The descriptor and pathname are checked before and after both
    operations, with only bounded hash and chunk buffers allocated.
    """
    handle, before = _open_pinned_artifact(path)
    try:
        file_size = int(before.st_size)
        if offset >= file_size:
            raise _contract_error(
                "INVALID_CHUNK_RANGE",
                f"Offset {offset} is out of bounds for file of size {file_size}",
            )

        digest = hashlib.sha256()
        hashed_size = 0
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            hashed_size += len(block)
        if hashed_size != file_size:
            raise _artifact_changed(path, f"size changed while hashing ({file_size} -> {hashed_size})")

        handle.seek(offset)
        chunk_data = handle.read(length)
        after = os.fstat(handle.fileno())
        _assert_pinned_artifact_stable(path, before, after)

        chunk_sha256 = hashlib.sha256(chunk_data).hexdigest()
        return {
            "file_path": str(path),
            "file_size": file_size,
            "whole_file_sha256": digest.hexdigest(),
            "offset": offset,
            "length": len(chunk_data),
            "chunk_sha256": chunk_sha256,
            "data_bytes": chunk_data,
            "eof": (offset + len(chunk_data)) >= file_size,
        }
    finally:
        handle.close()


def _count_values(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Mapping):
        return sum(_count_values(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_count_values(child) for child in value)
    return 1


class ArtifactStore:
    """Manages project-scoped artifacts, atomic exports, and chunk streaming."""

    def __init__(self, project_root: Path | None = None) -> None:
        if project_root is None:
            raise _contract_error(
                "RUNTIME_CONFIGURATION_REQUIRED",
                "ArtifactStore requires an explicit trusted project root; cwd is not an authorization source",
            )
        try:
            raw_root = Path(project_root).expanduser()
        except (TypeError, ValueError) as exc:
            raise _contract_error("RUNTIME_CONFIGURATION_REQUIRED", "project root is not a valid path") from exc
        if not raw_root.is_absolute():
            raise _contract_error("RUNTIME_CONFIGURATION_REQUIRED", "project root must be an absolute path")
        _assert_no_symlink_components(raw_root, field="project root")
        if not raw_root.is_dir():
            raise _contract_error("RUNTIME_CONFIGURATION_REQUIRED", f"project root is not a directory: {raw_root}")
        self.project_root = raw_root.resolve(strict=True)
        if self.project_root.is_symlink() or not self.project_root.is_dir():
            raise _contract_error("RUNTIME_CONFIGURATION_REQUIRED", f"project root is not a directory: {raw_root}")

    def resolve_safe_path(self, dest: str, *, allow_overwrite: bool = False) -> Path:
        """Resolve a path strictly inside the project root.

        ``allow_overwrite`` is an explicit destination contract.  It only affects
        an existing regular file; it never widens the root or permits a symlink.
        """
        if not isinstance(dest, (str, os.PathLike)) or not str(dest).strip():
            raise _contract_error("INVALID_DESTINATION", "destination must be a non-empty path")
        if not isinstance(allow_overwrite, bool):
            raise _contract_error("INVALID_REQUEST", "allow_overwrite must be boolean")

        raw_p = Path(dest).expanduser()
        candidate = raw_p if raw_p.is_absolute() else self.project_root / raw_p
        # Normalize ``..`` lexically without resolving symlinks; resolving first
        # would hide a symlink that points back inside the root.
        candidate = Path(os.path.abspath(os.fspath(candidate)))
        try:
            candidate.relative_to(self.project_root)
        except ValueError as exc:
            raise _contract_error(
                "ACCESS_VIOLATION",
                f"Export destination {dest!r} escapes authorized project root {self.project_root}",
            ) from exc

        _assert_no_symlink_components(candidate, field="destination")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            # Protect the race where a component appeared as a symlink between the
            # lexical check and the first resolve.
            raise _contract_error(
                "ACCESS_VIOLATION",
                f"Export destination {dest!r} escapes authorized project root {self.project_root}",
            ) from exc
        _assert_no_symlink_components(candidate, field="destination")

        if resolved.exists():
            if resolved.is_symlink() or resolved.is_dir():
                raise _contract_error(
                    "INVALID_DESTINATION",
                    f"Destination path is not an existing regular file: {resolved}",
                )
            if not allow_overwrite:
                raise _contract_error(
                    "DESTINATION_EXISTS",
                    f"Destination file already exists and overwrite is not authorized: {resolved}",
                )

        return resolved

    def export_field_data(
        self,
        dest: str,
        eval_result: Mapping[str, Any],
        fmt: str = "json",
        *,
        chunk_size: int = 1024 * 64,
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """Atomically export evaluation outcomes to a validated file."""
        if not isinstance(eval_result, Mapping):
            raise _contract_error("EXPORT_FAILED", "evaluation result must be a mapping", stage="post_dispatch")
        if not isinstance(fmt, str):
            raise _contract_error("API_UNSUPPORTED", "format must be 'json' or 'csv'")
        fmt_lower = fmt.strip().lower()
        if fmt_lower not in ("json", "csv"):
            raise _contract_error(
                "API_UNSUPPORTED",
                f"Unsupported export format {fmt!r}; supported formats are 'json', 'csv'",
            )
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or not (0 < chunk_size <= MAX_CHUNK_BYTES):
            raise _contract_error(
                "INVALID_REQUEST",
                f"chunk_size must be an integer from 1 to {MAX_CHUNK_BYTES}",
            )
        if not isinstance(allow_overwrite, bool):
            raise _contract_error("INVALID_REQUEST", "allow_overwrite must be boolean")

        # Semantic and destination checks happen before a directory is made or a
        # temporary file is opened.  A failed/unknown outcome can never publish.
        _evaluation_ok(eval_result)
        target_path = self.resolve_safe_path(dest, allow_overwrite=allow_overwrite)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(target_path.parent, field="destination parent")

        values = eval_result["values"]
        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target_path.name}.tmp.", suffix=".partial", dir=str(target_path.parent)
            )
            os.close(fd)
            tmp_path = Path(tmp_name)
            _assert_no_symlink_components(target_path.parent, field="destination parent")
            if fmt_lower == "json":
                payload = _json_payload(eval_result)
                with tmp_path.open("w", encoding="utf-8", newline="") as handle:
                    json.dump(payload, handle, allow_nan=False, sort_keys=True, indent=2, ensure_ascii=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            else:
                with tmp_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(CSV_COLUMNS)
                    for row in _iter_csv_rows(values, eval_result):
                        writer.writerow(row)
                    handle.flush()
                    os.fsync(handle.fileno())

            byte_size, sha256 = _stream_file_hash(tmp_path)

            # Re-authorize immediately before publication.  The first
            # destination check is intentionally early (before evaluation and
            # serialization), while this second check closes the long window in
            # which a parent component or destination could have changed.
            authorized_target = self.resolve_safe_path(dest, allow_overwrite=allow_overwrite)
            if authorized_target != target_path:
                raise _contract_error(
                    "ACCESS_VIOLATION",
                    "destination changed between preflight and publication",
                    stage="post_dispatch",
                )
            _assert_no_symlink_components(authorized_target.parent, field="destination parent")

            if allow_overwrite:
                # Explicit overwrite is the only path that may replace an
                # existing regular file.  The immediate authorization check
                # above prevents a newly appeared symlink or directory from
                # being silently accepted.
                os.replace(tmp_path, authorized_target)
            else:
                # ``os.link`` is an atomic no-clobber publish when the temp file
                # lives in the same directory.  It cannot overwrite a target
                # that appeared after preflight, unlike os.replace.
                try:
                    os.link(tmp_path, authorized_target)
                except FileExistsError as exc:
                    raise _contract_error(
                        "DESTINATION_EXISTS",
                        f"Destination appeared before atomic no-clobber publish: {authorized_target}",
                        stage="post_dispatch",
                    ) from exc
                except OSError as exc:
                    raise _contract_error(
                        "PUBLISH_FAILED",
                        f"Atomic no-clobber publish failed for {authorized_target}: {exc}",
                        stage="post_dispatch",
                    ) from exc
                try:
                    tmp_path.unlink()
                except OSError as exc:
                    raise _contract_error(
                        "PUBLISH_FAILED",
                        f"Published artifact but could not remove temporary link {tmp_path}: {exc}",
                        stage="post_dispatch",
                    ) from exc
            tmp_path = None
        except (TypeError, ValueError, OverflowError) as exc:
            raise _contract_error(
                "SERIALIZATION_ERROR",
                f"serialization failed: {type(exc).__name__}: {exc}",
                stage="post_dispatch",
            ) from exc
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        total_elements = eval_result.get("total_elements", 0)
        if isinstance(total_elements, bool) or not isinstance(total_elements, int) or total_elements < 0:
            total_elements = _count_values(values)
        if total_elements == 0:
            total_elements = _count_values(values)
        return {
            "file_path": str(target_path),
            "sha256": sha256,
            "format": fmt_lower,
            "byte_size": byte_size,
            "total_elements": total_elements,
            "chunk_info": {
                "chunk_size": chunk_size,
                "total_chunks": max(1, math.ceil(byte_size / chunk_size)),
                "hash_algorithm": "sha256",
                "streaming": True,
            },
            "evaluation_summary": {
                key: eval_result.get(key)
                for key in ("expressions", "dataset", "solution", "complex_mode", "is_complex")
            },
        }

    @staticmethod
    def read_chunk(file_path: str, offset: int, length: int) -> dict[str, Any]:
        """Read a single byte chunk from an artifact file with bounded memory."""
        p = Path(file_path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise _contract_error("INVALID_CHUNK_RANGE", "offset must be a non-negative integer")
        if isinstance(length, bool) or not isinstance(length, int) or length <= 0 or length > MAX_CHUNK_BYTES:
            raise _contract_error(
                "INVALID_CHUNK_RANGE",
                f"length must be an integer from 1 to {MAX_CHUNK_BYTES}",
            )
        _assert_no_symlink_components(p, field="artifact")
        pinned = _read_pinned_chunk(p, offset=offset, length=length)
        return {
            "file_path": pinned["file_path"],
            "offset": pinned["offset"],
            "length": pinned["length"],
            "chunk_sha256": pinned["chunk_sha256"],
            "data_bytes": pinned["data_bytes"],
            "eof": pinned["eof"],
        }


def _require_string(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExecutionContractError(
            "INVALID_REQUEST", f"artifact.read requires a non-empty string {key!r}"
        )
    return value


def artifact_read(
    worker: Any, model_tag: str | None, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Read one bounded chunk of a published artifact.

    This publishes the catalogue's ``artifact.read`` (effect READ, scope project,
    signature ``artifact_id, offset?, length?, expected_sha256?, expected_chunk_sha256?``)
    rather than inventing a sibling
    name: the design catalogue is the source of truth for effects, and an operation
    whose effect came from a private table would break that invariant.

    ``worker`` supplies the trusted project root.  It is never replaced by a root
    or project identifier in the request body.  ``model_tag`` is accepted for
    signature compatibility with the other G3 operations; reads do not touch the
    engine.  ``project_id`` and ``request_id`` are accepted-and-ignored; ``path``
    is accepted as an alias for ``artifact_id``.

    Two extra optional keys carry the integrity pins ACCEPTANCE C13 needs
    (``expected_sha256`` for the whole file, ``expected_chunk_sha256`` for the served
    chunk).  A catalogue-only caller that omits them still gets a per-chunk digest
    plus the file's reported digest and size.

    The destination is gated by the project-root containment check *before* any file
    is opened (a request cannot widen the root, because the root comes from the
    process and never from the request body), the pinned whole-file digest is verified
    before the chunk is handed out, and each chunk carries its own digest.  Bytes
    travel base64 encoded because the operation response is JSON.

    Memory bound: one chunk read allocates at most ``length`` payload bytes (capped
    at 16 MiB) plus the base64 wire copy, and never buffers the whole file.  The
    whole-file digest is computed with a 1 MiB block buffer, so its memory remains
    bounded by the block size rather than file size.
    """
    if not isinstance(arguments.get("artifact_id"), str) or not arguments["artifact_id"].strip():
        # ``path`` stays accepted as an alias so the export-side reference can be passed
        # through unchanged.
        path = _require_string(arguments, "path")
    else:
        path = arguments["artifact_id"]
    offset = arguments.get("offset", 0)
    length = arguments.get("length", 1024 * 64)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise _contract_error("INVALID_REQUEST", f"offset must be a non-negative integer, got {offset!r}")
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0 or length > MAX_CHUNK_BYTES:
        raise _contract_error(
            "INVALID_REQUEST",
            f"length must be an integer from 1 to {MAX_CHUNK_BYTES}, got {length!r}",
        )

    root = trusted_project_root(worker)
    store = ArtifactStore(project_root=root)
    # ``allow_overwrite=True`` here means an existing regular file may be read; it
    # does not permit a write and still rejects all symlinks.
    resolved = store.resolve_safe_path(path, allow_overwrite=True)
    if not resolved.is_relative_to(store.project_root):
        raise _contract_error(
            "ACCESS_VIOLATION",
            f"Artifact reads are limited to the project root {store.project_root}; {resolved} is outside it",
        )
    if not resolved.is_file():
        raise _contract_error("ARTIFACT_NOT_FOUND", f"Artifact file not found: {resolved}")

    _assert_no_symlink_components(resolved, field="artifact")
    pinned = _read_pinned_chunk(resolved, offset=offset, length=length)
    file_size = pinned["file_size"]
    whole_sha256 = pinned["whole_file_sha256"]
    expected_whole = arguments.get("expected_sha256")
    if expected_whole is not None:
        if whole_sha256.lower() != str(expected_whole).lower():
            raise _contract_error(
                "ARTIFACT_HASH_MISMATCH",
                f"Artifact {resolved} hashes to {whole_sha256}, not the pinned {expected_whole}",
                details={
                    "path": str(resolved),
                    "actual_sha256": whole_sha256,
                    "expected_sha256": str(expected_whole),
                },
            )

    # ``pinned`` contains the payload read from the same descriptor used for
    # the whole-file hash.  Do not reopen the pathname here: doing so would
    # allow an old digest to be paired with bytes from a same-size replacement.
    chunk = pinned
    expected_chunk = arguments.get("expected_chunk_sha256")
    if expected_chunk is not None and chunk["chunk_sha256"].lower() != str(expected_chunk).lower():
        raise _contract_error(
            "CHUNK_HASH_MISMATCH",
            f"Chunk at offset {chunk['offset']} hashes to {chunk['chunk_sha256']}, "
            f"not the expected {expected_chunk}",
            details={
                "path": str(resolved),
                "offset": chunk["offset"],
                "actual_sha256": chunk["chunk_sha256"],
                "expected_sha256": str(expected_chunk),
            },
        )

    return {
        "file_path": str(resolved),
        "file_size": file_size,
        "format": resolved.suffix.lstrip(".") or None,
        "whole_file_sha256": whole_sha256,
        "whole_file_sha256_pinned": expected_whole is not None,
        "offset": chunk["offset"],
        "length": chunk["length"],
        "eof": chunk["eof"],
        "chunk_sha256": chunk["chunk_sha256"],
        "encoding": "base64",
        "data_base64": base64.b64encode(chunk["data_bytes"]).decode("ascii"),
    }


#: Host-requestable surface.  ``artifact.read`` is the catalogue's own operation for a
#: bounded chunk read (effect READ, scope project, gate G4); publishing it is what
#: ACCEPTANCE C13's chunk contract needs.  Its effect comes from the catalogue, so the
#: operations surface keeps a single source of truth for effects.
OPERATIONS: dict[str, Any] = {
    "artifact.read": artifact_read,
}
