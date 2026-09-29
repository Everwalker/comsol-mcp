"""Private, fixed-schema diagnostics for owned-process startup.

These records explain what the launcher observed. They are not process
identity credentials and must never be used to adopt, stop, or restart a child.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


_SCHEMA = "COMSOL_OWNED_SERVER_STARTUP_DIAGNOSTIC_V1"
_FIELDS = (
    "schema", "event", "observed_at_utc", "project_id", "session_id",
    "runtime_id", "pid", "birth", "exit_code", "exception_type", "errno",
    "winerror", "error_category",
)
_EVENTS = frozenset({
    "PROCESS_CREATED", "BIRTH_OBSERVED", "READY", "EXIT_OBSERVED",
    "STARTUP_UNKNOWN", "PROCESS_CREATE_FAILED",
})
_ERROR_CATEGORIES = frozenset({
    "PROCESS_CREATE_FAILED", "BIRTH_OBSERVATION_FAILED", "STARTUP_TIMEOUT",
    "PROCESS_EXITED_BEFORE_READY", "LISTENER_PROOF_FAILED",
    "PROCESS_IDENTITY_CHANGED", "STARTUP_UNKNOWN",
})
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_BIRTH_RE = re.compile(r"^start_epoch_ms:[1-9][0-9]{0,18}$")
_SAFE_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SAFE_JSON_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,126}\.json$")


class ProcessDiagnosticError(ValueError):
    """A diagnostic payload or destination violates the fixed safe schema."""


def sanitize_startup_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy one exact, non-secret launcher observation.

    Extra fields are rejected instead of silently filtered, so callers cannot
    accidentally begin persisting command lines, paths, endpoints, or secrets.
    Every accepted record has the same fixed set of keys.
    """
    if not isinstance(payload, Mapping):
        raise ProcessDiagnosticError("startup observation must be a mapping")
    if set(payload) != set(_FIELDS):
        raise ProcessDiagnosticError("startup observation fields do not match the fixed schema")
    row = {key: payload[key] for key in _FIELDS}
    if row["schema"] != _SCHEMA:
        raise ProcessDiagnosticError("startup observation schema is unsupported")
    if not isinstance(row["event"], str) or row["event"] not in _EVENTS:
        raise ProcessDiagnosticError("startup observation event is unsupported")
    if not isinstance(row["observed_at_utc"], str) or not _UTC_RE.fullmatch(row["observed_at_utc"]):
        raise ProcessDiagnosticError("startup observation timestamp is malformed")
    for key in ("project_id", "session_id", "runtime_id"):
        value = row[key]
        if (not isinstance(value, str) or not value or len(value) > 512
                or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)):
            raise ProcessDiagnosticError(f"startup observation {key} is malformed")

    pid = row["pid"]
    if pid is not None and (type(pid) is not int or not 1 <= pid <= 2**63 - 1):
        raise ProcessDiagnosticError("startup observation pid is malformed")
    birth = row["birth"]
    if birth is not None and (not isinstance(birth, str) or not _BIRTH_RE.fullmatch(birth)):
        raise ProcessDiagnosticError("startup observation birth is malformed")
    exit_code = row["exit_code"]
    if exit_code is not None and type(exit_code) is not int:
        raise ProcessDiagnosticError("startup observation exit code is malformed")
    for key in ("errno", "winerror"):
        value = row[key]
        if value is not None and (type(value) is not int or not -(2**31) <= value < 2**32):
            raise ProcessDiagnosticError(f"startup observation {key} is malformed")
    exception_type = row["exception_type"]
    if exception_type is not None and (
        not isinstance(exception_type, str) or not _SAFE_TYPE_RE.fullmatch(exception_type)
    ):
        raise ProcessDiagnosticError("startup observation exception type is malformed")
    category = row["error_category"]
    if category is not None and (
        not isinstance(category, str) or category not in _ERROR_CATEGORIES
    ):
        raise ProcessDiagnosticError("startup observation error category is unsupported")

    event = row["event"]
    if event == "PROCESS_CREATED":
        valid = pid is not None and birth is None and exit_code is None and category is None
    elif event in {"BIRTH_OBSERVED", "READY"}:
        valid = pid is not None and birth is not None and exit_code is None and category is None
    elif event == "EXIT_OBSERVED":
        valid = pid is not None and exit_code is not None and category == "PROCESS_EXITED_BEFORE_READY"
    elif event == "STARTUP_UNKNOWN":
        valid = pid is not None and exit_code is None and category in _ERROR_CATEGORIES
    else:  # PROCESS_CREATE_FAILED
        valid = (pid is None and birth is None and exit_code is None
                 and category == "PROCESS_CREATE_FAILED" and exception_type is not None)
    if not valid:
        raise ProcessDiagnosticError("startup observation event fields are inconsistent")
    return row


def write_startup_diagnostic(directory: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically replace the owned-server startup snapshot and return its row.

    The temporary file is created in the destination directory, written with
    mode 0600, flushed, and atomically renamed. A failed write/replace leaves an
    existing complete snapshot in place.
    """
    row = sanitize_startup_observation(payload)
    root = Path(directory)
    if root.is_symlink():
        raise ProcessDiagnosticError("startup diagnostic directory cannot be a symlink")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ProcessDiagnosticError("startup diagnostic directory is unavailable") from exc
    if not resolved_root.is_dir():
        raise ProcessDiagnosticError("startup diagnostic directory is not a directory")
    target = resolved_root / "startup-diagnostic.json"
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ProcessDiagnosticError("startup diagnostic target is not a regular file")

    return write_atomic_json_snapshot(target, row)


def write_atomic_json_snapshot(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically write an already validated, non-secret JSON snapshot.

    Callers must validate their own fixed schema before calling this generic
    primitive. The destination must be a JSON file directly inside an existing
    non-symlink directory; the returned mapping is the exact serialized value.
    """
    if not isinstance(payload, Mapping):
        raise ProcessDiagnosticError("JSON snapshot payload must be a mapping")
    target_path = Path(path)
    if not _SAFE_JSON_FILENAME_RE.fullmatch(target_path.name):
        raise ProcessDiagnosticError("JSON snapshot filename is not a safe basename")
    parent = target_path.parent
    if parent.is_symlink():
        raise ProcessDiagnosticError("JSON snapshot directory cannot be a symlink")
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise ProcessDiagnosticError("JSON snapshot directory is unavailable") from exc
    if not resolved_parent.is_dir():
        raise ProcessDiagnosticError("JSON snapshot directory is not a directory")
    target = resolved_parent / target_path.name
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ProcessDiagnosticError("JSON snapshot target is not a regular file")
    row = dict(payload)

    descriptor: int | None = None
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".atomic-json-snapshot-", suffix=".tmp", dir=resolved_parent,
        )
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - exercised on Windows Python builds
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            json.dump(row, stream, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    except Exception as exc:
        raise ProcessDiagnosticError("atomic JSON snapshot could not be persisted") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return row
