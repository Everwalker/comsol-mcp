"""Artifact Store and Safe Export Service for COMSOL MCP (F08, F09).

Provides:
- Path containment validation preventing directory traversal and symlink escape.
- Pre-write outcome verification: refuses to export when evaluation has errors or cleanup failed.
- Atomic file publishing (temporary file + atomic os.replace).
- Bounded-memory chunk streaming reader (offset + length) without full-file RAM buffering.
- Strict format serialization: refuses non-finite JSON and unsupported formats.
"""
from __future__ import annotations

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
