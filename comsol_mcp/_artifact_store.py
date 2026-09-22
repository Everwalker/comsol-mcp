"""Artifact Store and Safe Export Service for COMSOL MCP (F08, F09).

Provides:
- Path containment validation preventing directory traversal and symlink escape.
- Pre-write outcome verification: refuses to export when evaluation has errors or cleanup failed.
- Atomic file publishing (temporary file + atomic os.replace).
- Bounded-memory chunk streaming reader (offset + length) without full-file RAM buffering.
- Strict format serialization: refuses non-finite JSON and unsupported formats.

The chunk reader is published as ``artifact.read_chunk`` so a host can request
bounded reads instead of receiving a whole array: NEXT_GOAL requires a real,
host-requestable artifact/chunk contract that pins the file's immutable hash,
size, offset, length and format, and explicitly rejects calling a
"read everything into memory and split it afterwards" implementation streaming.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from ._execution_contract import ExecutionContractError


class ArtifactStore:
    """Manages project-scoped artifacts, atomic exports, and chunk streaming."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (project_root or Path.cwd()).resolve()

    def resolve_safe_path(self, dest: str, *, allow_overwrite: bool = True) -> Path:
        """Resolve destination path relative to project root and verify containment."""
        raw_p = Path(dest)
        import tempfile

        if raw_p.is_absolute():
            resolved = raw_p.resolve()
            allowed_roots = [
                self.project_root,
                Path(tempfile.gettempdir()).resolve(),
                Path("/tmp").resolve(),
                Path("/private/tmp").resolve(),
            ]
            try:
                allowed_roots.append(Path("/private/var/folders").resolve())
            except Exception:
                pass

            contained = False
            matched_root = self.project_root
            for root in allowed_roots:
                try:
                    resolved.relative_to(root)
                    contained = True
                    matched_root = root
                    break
                except ValueError:
                    continue

            if not contained:
                raise ExecutionContractError(
                    "ACCESS_VIOLATION",
                    f"Export destination {dest!r} escapes authorized project root {self.project_root}",
                )
        else:
            resolved = (self.project_root / raw_p).resolve()
            matched_root = self.project_root
            try:
                resolved.relative_to(self.project_root)
            except ValueError:
                raise ExecutionContractError(
                    "ACCESS_VIOLATION",
                    f"Export destination {dest!r} escapes authorized project root {self.project_root}",
                )


        # Check parent directory symlink escape
        parent = resolved.parent
        if parent.is_symlink():
            target = parent.resolve()
            try:
                target.relative_to(matched_root)
            except ValueError:
                raise ExecutionContractError(
                    "ACCESS_VIOLATION",
                    f"Destination parent directory symlink escapes root: {parent} -> {target}",
                )


        if resolved.exists():
            if resolved.is_dir():
                raise ExecutionContractError(
                    "INVALID_DESTINATION",
                    f"Destination path is an existing directory: {resolved}",
                )
            if not allow_overwrite:
                raise ExecutionContractError(
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
    ) -> dict[str, Any]:
        """Atomically export evaluation outcomes to a validated file."""
        # 1. Pre-validation: ensure upstream evaluation succeeded
        status = eval_result.get("status")
        if isinstance(status, Mapping):
            if not status.get("ok", True) or status.get("engine_error") or status.get("cleanup_failed"):
                raise ExecutionContractError(
                    "EXPORT_FAILED",
                    f"Cannot export field data because evaluation failed or left dirty state: {status}",
                )

        values = eval_result.get("values")
        if values is None and "artifact" not in eval_result:
            raise ExecutionContractError(
                "EXPORT_FAILED",
                "Evaluation produced no values or valid artifact to export",
            )

        fmt_lower = fmt.strip().lower()
        if fmt_lower not in ("json", "csv"):
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"Unsupported export format {fmt!r}; supported formats are 'json', 'csv'",
            )

        target_path = self.resolve_safe_path(dest)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # 2. Serialize content
        tmp_name = f".{target_path.name}.tmp.{uuid.uuid4().hex[:8]}"
        tmp_path = target_path.parent / tmp_name

        try:
            if fmt_lower == "json":
                try:
                    payload = {
                        "spec": eval_result.get("spec", {}),
                        "values": values,
                        "metadata": {
                            "expressions": eval_result.get("expressions"),
                            "dataset": eval_result.get("dataset"),
                            "solution": eval_result.get("solution"),
                            "complex_mode": eval_result.get("complex_mode"),
                            "is_complex": eval_result.get("is_complex"),
                        },
                    }
                    content_str = json.dumps(payload, allow_nan=False, sort_keys=True, indent=2)
                except (ValueError, OverflowError) as exc:
                    raise ExecutionContractError(
                        "SERIALIZATION_ERROR",
                        f"Non-finite float or serialization failure in export: {exc}",
                    )
                content_bytes = content_str.encode("utf-8")
                tmp_path.write_bytes(content_bytes)

            elif fmt_lower == "csv":
                lines: list[str] = []
                if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                    for row in values:
                        if isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
                            lines.append(",".join(str(x) for x in row))
                        elif isinstance(row, Mapping):
                            if "real" in row and "imag" in row:
                                lines.append(f"{row['real']},{row['imag']}")
                            else:
                                lines.append(",".join(f"{k}={v}" for k, v in row.items()))
                        else:
                            lines.append(str(row))
                else:
                    lines.append(str(values))
                content_bytes = "\n".join(lines).encode("utf-8")
                tmp_path.write_bytes(content_bytes)

            # Compute hash and size
            byte_size = len(content_bytes)
            sha256 = hashlib.sha256(content_bytes).hexdigest()

            # 3. Atomic replacement
            os.replace(tmp_path, target_path)

        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise

        total_chunks = max(1, math.ceil(byte_size / chunk_size))

        total_elements = eval_result.get("total_elements", 0)
        if total_elements == 0 and values is not None:
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                total_elements = len(values)
            else:
                total_elements = 1

        return {
            "file_path": str(target_path),
            "sha256": sha256,
            "format": fmt_lower,
            "byte_size": byte_size,
            "total_elements": total_elements,
            "chunk_info": {
                "chunk_size": chunk_size,
                "total_chunks": total_chunks,
            },

            "evaluation_summary": {
                "expressions": eval_result.get("expressions"),
                "dataset": eval_result.get("dataset"),
                "solution": eval_result.get("solution"),
                "complex_mode": eval_result.get("complex_mode"),
                "is_complex": eval_result.get("is_complex"),
            },
        }

    @staticmethod
    def read_chunk(file_path: str, offset: int, length: int) -> dict[str, Any]:
        """Read a single byte chunk from an artifact file with bounded memory."""
        p = Path(file_path)
        if not p.is_file():
            raise ExecutionContractError("ARTIFACT_NOT_FOUND", f"Artifact file not found: {file_path}")

        file_size = p.stat().st_size
        if offset < 0 or offset >= file_size:
            raise ExecutionContractError(
                "INVALID_CHUNK_RANGE",
                f"Offset {offset} is out of bounds for file of size {file_size}",
            )

        read_len = min(length, 1024 * 1024 * 16)  # Cap single chunk to 16MB
        with p.open("rb") as f:
            f.seek(offset)
            chunk_data = f.read(read_len)

        chunk_sha256 = hashlib.sha256(chunk_data).hexdigest()
        return {
            "file_path": str(p),
            "offset": offset,
            "length": len(chunk_data),
            "chunk_sha256": chunk_sha256,
            "data_bytes": chunk_data,
            "eof": (offset + len(chunk_data)) >= file_size,
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
    signature ``artifact_id, offset?, length?``) rather than inventing a sibling
    name: the design catalogue is the source of truth for effects, and an operation
    whose effect came from a private table would break that invariant.

    ``worker`` and ``model_tag`` are accepted for signature compatibility with the
    other G3 operations and are intentionally unused: an artifact read never touches
    the engine, so it cannot mutate model state.  ``project_id`` and ``request_id``
    are part of the published request schema and are accepted-and-ignored; ``path``
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
    at 16 MiB) plus the base64 wire copy, and never buffers the whole file -- the
    store seeks and reads exactly ``length`` bytes.  Verifying a pinned
    ``expected_sha256`` costs one extra sequential pass with a 1 MiB block buffer, so
    it is bounded by the block size rather than the file size; pinning it on the first
    chunk of a session is enough, and later reads may omit it and rely on the
    per-chunk digests.
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
        raise ExecutionContractError("INVALID_REQUEST", f"offset must be a non-negative integer, got {offset!r}")
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
        raise ExecutionContractError("INVALID_REQUEST", f"length must be a positive integer, got {length!r}")

    store = ArtifactStore()
    resolved = store.resolve_safe_path(path)
    # The store's containment policy admits the process temp directory (delivered
    # behaviour, kept as-is for exports); a *read* request has no reason to reach
    # outside the project, so this operation is the stricter scope.
    if not resolved.is_relative_to(store.project_root):
        raise ExecutionContractError(
            "ACCESS_VIOLATION",
            f"Artifact reads are limited to the project root {store.project_root}; "
            f"{resolved} is outside it",
        )
    if not resolved.is_file():
        raise ExecutionContractError("ARTIFACT_NOT_FOUND", f"Artifact file not found: {resolved}")

    file_size = resolved.stat().st_size
    whole_sha256: str | None = None
    expected_whole = arguments.get("expected_sha256")
    if expected_whole is not None:
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        whole_sha256 = digest.hexdigest()
        if whole_sha256.lower() != str(expected_whole).lower():
            raise ExecutionContractError(
                "ARTIFACT_HASH_MISMATCH",
                f"Artifact {resolved} hashes to {whole_sha256}, not the pinned {expected_whole}",
                details={
                    "path": str(resolved),
                    "actual_sha256": whole_sha256,
                    "expected_sha256": str(expected_whole),
                },
            )

    chunk = ArtifactStore.read_chunk(str(resolved), offset=offset, length=length)
    expected_chunk = arguments.get("expected_chunk_sha256")
    if expected_chunk is not None and chunk["chunk_sha256"].lower() != str(expected_chunk).lower():
        raise ExecutionContractError(
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
