"""Locked, hash-verified backup and staged restore for the control ledger.

This module never starts a COMSOL engine or control daemon.  Every operation
uses the daemon's exact ``control.lock`` and refuses symlinked paths.  Restore
keeps byte-for-byte copies of the prior SQLite main/WAL/SHM set before making
any live-path change; a durable journal blocks daemon startup until the new
ledger is verified or the old file set is restored.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
from typing import Any, Iterator, Mapping
from uuid import uuid4

from ._managed_backend import ProcessLock


DATABASE_NAME = "operations.sqlite3"
CONTROL_LOCK_NAME = "control.lock"
ACTIVE_RESTORE_JOURNAL = "operations.restore.pending.json"
ACTIVE_FULL_RESTORE_JOURNAL = "full-state.restore.pending.json"
MODEL_PUBLICATION_KEY_PREFIX = "model-publication:"
MODEL_PUBLICATION_TERMINAL = frozenset({"COMMITTED", "RESTORED", "ABORTED"})
MANIFEST_SCHEMA = "comsol-mcp.sqlite-backup/1"
JOURNAL_SCHEMA = "comsol-mcp.sqlite-restore-journal/1"
FULL_BACKUP_SCHEMA = "comsol-mcp.full-state-backup/1"
FULL_BACKUP_MANIFEST_SCHEMA = "comsol-mcp.full-state-backup-manifest/1"
FULL_RESTORE_JOURNAL_SCHEMA = "comsol-mcp.full-state-restore-journal/1"
MAX_FULL_BACKUP_FILES = 20_000
# USTAR's octal size field is bounded to eleven octal digits. Keep the
# admitted file limit below that format ceiling so a configured larger limit
# cannot create an archive that the corresponding restore path cannot parse.
MAX_FULL_BACKUP_FILE_BYTES = 8 * 1024 * 1024 * 1024 - 1
MAX_FULL_BACKUP_TOTAL_BYTES = 64 * 1024 * 1024 * 1024
MAX_FULL_BACKUP_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_FULL_BACKUP_ARCHIVE_BYTES = MAX_FULL_BACKUP_TOTAL_BYTES + 512 * 1024 * 1024
_CORE_COLUMNS = {
    "schema_meta": {"version"},
    "operations": {
        "operation_id", "request_id", "idempotency_key", "request_hash", "operation",
        "status", "result", "metadata", "created_at", "started_at", "finished_at",
        "effective_timeouts",
    },
    "jobs": {
        "job_id", "operation_id", "status", "metadata", "created_at", "started_at",
        "finished_at", "effective_timeouts",
    },
    "job_events": {"id", "job_id", "event", "metadata", "created_at"},
    "sessions": {"session_id", "metadata"},
    "runtimes": {"runtime_id", "metadata"},
    "revisions": {"model_key", "metadata", "revision"},
    "artifacts": {"artifact_id", "metadata"},
    "checkpoints": {"checkpoint_id", "metadata"},
}
_RESUME_COLUMNS = {
    "source_job_id", "idempotency_key", "request_hash", "child_operation_id",
    "child_job_id", "created_at",
}
_SIDECAR_SUFFIXES = ("-wal", "-shm")
_BACKUP_SIDECAR_SUFFIXES = (*_SIDECAR_SUFFIXES, "-journal")


class StateBackupError(RuntimeError):
    """A fail-closed backup/restore error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, recovery_path: str | None = None):
        super().__init__(message)
        self.code = code
        self.recovery_path = recovery_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _absolute_path(value: str | os.PathLike[str], field: str) -> Path:
    raw = Path(value).expanduser()
    if ".." in raw.parts:
        raise StateBackupError("PATH_TRAVERSAL", f"{field} must not contain parent-directory components")
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    return Path(os.path.abspath(raw))


def _reject_symlink_components(path: Path, field: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        if component in {"", "."}:
            continue
        current = current / component
        try:
            if current.is_symlink():
                raise StateBackupError("SYMLINK_PATH", f"{field} contains a symlink component")
        except OSError as exc:
            raise StateBackupError("PATH_UNVERIFIED", f"{field} path could not be inspected") from exc


def _existing_directory(value: str | os.PathLike[str], field: str) -> Path:
    path = _absolute_path(value, field)
    _reject_symlink_components(path, field)
    if not path.exists() or not path.is_dir():
        raise StateBackupError("DIRECTORY_REQUIRED", f"{field} must be an existing directory")
    try:
        if path.resolve(strict=True) != path:
            raise StateBackupError("NONCANONICAL_PATH", f"{field} must use its canonical path")
    except OSError as exc:
        raise StateBackupError("PATH_UNVERIFIED", f"{field} path could not be resolved") from exc
    return path


def _existing_file(value: str | os.PathLike[str], field: str) -> Path:
    path = _absolute_path(value, field)
    _reject_symlink_components(path, field)
    try:
        info = path.lstat()
    except OSError as exc:
        raise StateBackupError("FILE_REQUIRED", f"{field} must be an existing regular file") from exc
    if not stat.S_ISREG(info.st_mode):
        raise StateBackupError("FILE_REQUIRED", f"{field} must be an existing regular file")
    return path


def _new_file_path(value: str | os.PathLike[str], field: str, *, outside: Path) -> Path:
    path = _absolute_path(value, field)
    _reject_symlink_components(path, field)
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise StateBackupError("DIRECTORY_REQUIRED", f"{field} parent must be an existing directory")
    if path.exists() or path.is_symlink():
        raise StateBackupError("DESTINATION_EXISTS", f"{field} must be a new path")
    try:
        if parent.resolve(strict=True) != parent:
            raise StateBackupError("NONCANONICAL_PATH", f"{field} parent must use its canonical path")
    except OSError as exc:
        raise StateBackupError("PATH_UNVERIFIED", f"{field} parent could not be resolved") from exc
    if path == outside or path.is_relative_to(outside):
        raise StateBackupError("DESTINATION_IN_CONTROL_HOME", f"{field} must be outside the control home")
    return path


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def _database_paths(home: Path, *, required: bool, allow_partial: bool = False) -> dict[str, Path]:
    result = {"database": home / DATABASE_NAME}
    result.update({suffix[1:]: home / f"{DATABASE_NAME}{suffix}" for suffix in _SIDECAR_SUFFIXES})
    for label, path in result.items():
        if path.is_symlink():
            raise StateBackupError("SYMLINK_PATH", f"control database {label} path is a symlink")
        if path.exists():
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise StateBackupError("FILE_REQUIRED", f"control database {label} path is not a regular file")
            except OSError as exc:
                raise StateBackupError("PATH_UNVERIFIED", f"control database {label} path could not be inspected") from exc
    if required and not result["database"].is_file():
        raise StateBackupError("DATABASE_REQUIRED", "control ledger database does not exist")
    if (not allow_partial and not result["database"].exists()
            and (result["wal"].exists() or result["shm"].exists())):
        raise StateBackupError("INCOMPLETE_DATABASE_SET", "SQLite sidecars exist without the control database")
    return result


@contextmanager
def _locked_home(value: str | os.PathLike[str]) -> Iterator[Path]:
    home = _existing_directory(value, "control home")
    try:
        lock = ProcessLock(home / CONTROL_LOCK_NAME)
    except Exception as exc:
        code = getattr(exc, "code", "CONTROL_HOME_BUSY")
        raise StateBackupError(code, "could not acquire the daemon's control lock") from exc
    try:
        yield home
    finally:
        lock.close()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0, isolation_level=None)
    except sqlite3.Error as exc:
        raise StateBackupError("DATABASE_OPEN_FAILED", "SQLite database could not be opened read-only") from exc


def _integrity_check(connection: sqlite3.Connection) -> None:
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        raise StateBackupError("INTEGRITY_CHECK_FAILED", "SQLite integrity check could not run") from exc
    if rows != [("ok",)]:
        raise StateBackupError("INTEGRITY_CHECK_FAILED", "SQLite integrity check did not return exactly 'ok'")


def _inspect_schema(connection: sqlite3.Connection, *, allow_legacy_v1: bool = True) -> int:
    from ._operation_store import SCHEMA_VERSION

    try:
        object_types = {
            row[1]: row[0]
            for row in connection.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if object_types.get("schema_meta") != "table":
            raise StateBackupError("SCHEMA_UNSUPPORTED", "control ledger has no schema metadata table")
        versions = connection.execute("SELECT version FROM schema_meta").fetchall()
        if len(versions) != 1 or type(versions[0][0]) is not int or versions[0][0] != SCHEMA_VERSION:
            raise StateBackupError("SCHEMA_UNSUPPORTED", "control ledger schema version is unsupported or ambiguous")
        for table, required_columns in _CORE_COLUMNS.items():
            if object_types.get(table) != "table":
                raise StateBackupError("SCHEMA_UNSUPPORTED", f"control ledger is missing required table {table}")
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
            if not required_columns <= columns:
                raise StateBackupError("SCHEMA_UNSUPPORTED", f"control ledger table {table} has an unsupported layout")
        if "resume_claims" in object_types:
            if object_types["resume_claims"] != "table":
                raise StateBackupError("SCHEMA_UNSUPPORTED", "resume_claims database object is not a table")
            columns = {row[1] for row in connection.execute('PRAGMA table_info("resume_claims")').fetchall()}
            if columns != _RESUME_COLUMNS:
                raise StateBackupError("SCHEMA_UNSUPPORTED", "resume_claims has an unsupported layout")
        elif not allow_legacy_v1:
            raise StateBackupError("SCHEMA_UNSUPPORTED", "control ledger has not been migrated to the current layout")
        return versions[0][0]
    except sqlite3.Error as exc:
        raise StateBackupError("SCHEMA_UNSUPPORTED", "control ledger schema could not be inspected") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise StateBackupError("FILE_READ_FAILED", "a database file could not be read for verification") from exc
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    except OSError as exc:
        raise StateBackupError("DURABILITY_FAILED", "a database or receipt file could not be flushed") from exc


def _directory_fsync_policy(platform_name: str = os.name) -> str:
    if platform_name == "nt":
        return "skipped_windows_directory_fsync_unsupported_by_implementation"
    return "required_posix_directory_fsync_after_publish"


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise StateBackupError("DURABILITY_FAILED", "a directory update could not be flushed") from exc


def _write_new_json(path: Path, document: Mapping[str, Any]) -> None:
    data = (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise StateBackupError("DESTINATION_EXISTS", "receipt destination already exists") from exc
    except OSError as exc:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise StateBackupError("DURABILITY_FAILED", "receipt could not be written completely") from exc
    try:
        _fsync_directory(path.parent)
    except Exception:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


class _PublishedManifestError(StateBackupError):
    """Manifest link was created, but its final durability step was uncertain."""

    published = True


def _publish_new_manifest_atomic(path: Path, document: Mapping[str, Any]) -> str:
    """Write and fsync a same-directory temporary file, then publish no-clobber."""
    data = (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary_created = False
    try:
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise StateBackupError("TEMPORARY_COLLISION", "manifest temporary file already exists") from exc
        temporary_created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        # link(2) atomically publishes the complete inode and cannot replace a
        # destination created by a racing writer. Unsupported filesystems fail
        # closed; there is no replace/copy fallback.
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise StateBackupError("DESTINATION_EXISTS", "backup manifest destination already exists") from exc
        except OSError as exc:
            raise StateBackupError(
                "ATOMIC_PUBLISH_FAILED",
                "backup manifest could not be atomically published without replacing an existing destination",
            ) from exc

        try:
            temporary.unlink()
            temporary_created = False
            _fsync_directory(path.parent)
        except Exception as exc:
            raise _PublishedManifestError(
                "DURABILITY_FAILED",
                "backup manifest was published but post-publication durability could not be confirmed",
            ) from exc
        return _directory_fsync_policy()
    except Exception:
        if temporary_created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _replace_json(path: Path, document: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    _write_new_json(temporary, document)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise StateBackupError("JOURNAL_WRITE_FAILED", "restore journal could not be updated") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path, field: str) -> dict[str, Any]:
    try:
        if path.stat().st_size > 64 * 1024:
            raise StateBackupError("INVALID_RECEIPT", f"{field} is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except StateBackupError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateBackupError("INVALID_RECEIPT", f"{field} could not be read as JSON") from exc
    if not isinstance(value, dict):
        raise StateBackupError("INVALID_RECEIPT", f"{field} must contain a JSON object")
    return value


def _package_version() -> str:
    try:
        return importlib.metadata.version("comsol-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _component_fingerprints() -> dict[str, Any]:
    package_dir = Path(__file__).resolve().parent
    return {
        "scope": "component_fingerprints_not_full_wheel_identity",
        "installation_version": _package_version(),
        "components": {
            "comsol_mcp._state_backup.py": {"sha256": _sha256(Path(__file__).resolve())},
            "comsol_mcp._operation_store.py": {"sha256": _sha256(package_dir / "_operation_store.py")},
        },
    }


def _manifest_publication_policy() -> dict[str, str]:
    return {
        "method": "same_directory_hard_link_no_clobber",
        "file_fsync": "required_before_publish",
        "directory_fsync_policy": _directory_fsync_policy(),
    }


def _backup_manifest_path(database_path: Path) -> Path:
    return database_path.with_name(database_path.name + ".manifest.json")


def create_backup(
    home: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> dict[str, Any]:
    """Create a consistent SQLite online backup without opening OperationStore."""
    normalized_home = _existing_directory(home, "control home")
    target = _new_file_path(destination, "backup destination", outside=normalized_home)
    manifest_path = _backup_manifest_path(target)
    _reject_symlink_components(manifest_path, "backup manifest")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise StateBackupError("DESTINATION_EXISTS", "backup manifest destination must be new")
    if _is_within(manifest_path, normalized_home):
        raise StateBackupError("DESTINATION_IN_CONTROL_HOME", "backup manifest must be outside the control home")

    target_created = False
    manifest_created = False
    source: sqlite3.Connection | None = None
    target_db: sqlite3.Connection | None = None
    try:
        with _locked_home(normalized_home) as locked:
            assert_no_pending_restore(locked)
            source_paths = _database_paths(locked, required=True)
            source_path = source_paths["database"]
            source = _readonly_connection(source_path)
            source_schema = _inspect_schema(source)
            _integrity_check(source)
            # Exclusive creation closes the TOCTOU gap between path checking and
            # SQLite's normal O_CREAT open behavior.
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            target_created = True
            target_db = sqlite3.connect(target, timeout=5.0, isolation_level=None)
            source.backup(target_db)
            mode = target_db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise StateBackupError("BACKUP_JOURNAL_MODE_FAILED", "backup database did not enter DELETE journal mode")
            _integrity_check(target_db)
            target_schema = _inspect_schema(target_db)
            if target_schema != source_schema:
                raise StateBackupError("BACKUP_SCHEMA_MISMATCH", "online backup schema differs from its source")
            target_db.close()
            target_db = None
            source.close()
            source = None
            _fsync_file(target)
            target_sidecars = [Path(str(target) + suffix) for suffix in _SIDECAR_SUFFIXES]
            if any(path.exists() or path.is_symlink() for path in target_sidecars):
                raise StateBackupError("BACKUP_SIDECAR_REMAINS", "backup did not produce a standalone database file")
            payload = {
                "schema": MANIFEST_SCHEMA,
                "created_utc": _utc_now(),
                "source_database_name": DATABASE_NAME,
                "database_name": target.name,
                "database_sha256": _sha256(target),
                "database_size_bytes": target.stat().st_size,
                "schema_version": target_schema,
                "integrity_check": "ok",
                "package_version": _package_version(),
                "build_identity": _component_fingerprints(),
                "manifest_publication": _manifest_publication_policy(),
                "backup_scope": "control SQLite ledger only; referenced model/artifact files are not included",
            }
            _publish_new_manifest_atomic(manifest_path, payload)
            manifest_created = True
            return {"success": True, "operation": "backup", "manifest": payload,
                    "backup_path": str(target), "manifest_path": str(manifest_path)}
    except Exception as exc:
        if target_db is not None:
            try:
                target_db.close()
            except sqlite3.Error:
                pass
        if source is not None:
            try:
                source.close()
            except sqlite3.Error:
                pass
        publication_uncertain = isinstance(exc, _PublishedManifestError)
        if manifest_created:
            manifest_path.unlink(missing_ok=True)
        if target_created and not publication_uncertain:
            for path in (target, *(Path(str(target) + suffix) for suffix in _SIDECAR_SUFFIXES)):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise


def _table_names(connection: sqlite3.Connection) -> list[str]:
    try:
        return [str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
    except sqlite3.Error as exc:
        raise StateBackupError("SCHEMA_UNSUPPORTED", "control ledger tables could not be enumerated") from exc


def _history_fingerprint(connection: sqlite3.Connection) -> str:
    """Hash all durable rows except the project/file bindings intentionally rebound on restore."""
    digest = hashlib.sha256()
    for table in _table_names(connection):
        if table in {"projects", "artifacts"}:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise StateBackupError("SCHEMA_UNSUPPORTED", "ledger contains an unsupported table name")
        try:
            columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
            rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
        except sqlite3.Error as exc:
            raise StateBackupError("DATABASE_READ_FAILED", "durable ledger history could not be read") from exc
        encoded_rows = []
        for row in rows:
            values = []
            for value in tuple(row):
                if isinstance(value, bytes):
                    values.append({"bytes_hex": value.hex()})
                elif value is None or isinstance(value, (str, int, float, bool)):
                    values.append(value)
                else:
                    raise StateBackupError("DATABASE_READ_FAILED", "ledger history contains an unsupported value")
            encoded_rows.append(json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False))
        # Sorting canonical rows avoids dependence on SQLite row visitation order.
        if encoded_rows:
            digest.update(table.encode("utf-8") + b"\0")
            digest.update(json.dumps(columns, separators=(",", ":")).encode("utf-8") + b"\0")
            for encoded in sorted(encoded_rows):
                digest.update(encoded.encode("utf-8") + b"\n")
    return digest.hexdigest()


def _project_rows_for_backup(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if "projects" not in _table_names(connection):
        raise StateBackupError("FULL_BACKUP_SCHEMA_UNSUPPORTED", "ledger has no ProjectAuthority project table")
    try:
        rows = connection.execute(
            "SELECT project_id,workspace,schema_version,revision,record_json FROM projects ORDER BY project_id"
        ).fetchall()
    except sqlite3.Error as exc:
        raise StateBackupError("FULL_BACKUP_SCHEMA_UNSUPPORTED", "ProjectAuthority rows could not be read") from exc
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        project_id, workspace, version, revision, record_json = tuple(row)
        if (not isinstance(project_id, str) or not project_id or len(project_id) > 128
                or type(version) is not int or version != 1 or type(revision) is not int or revision < 1):
            raise StateBackupError("PROJECT_STATE_UNKNOWN", "a persisted project row has malformed identity")
        try:
            record = json.loads(record_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise StateBackupError("PROJECT_STATE_UNKNOWN", "a persisted project record is malformed") from exc
        if (not isinstance(record, dict) or record.get("schema_version") != version
                or record.get("project_id") != project_id or record.get("workspace") != workspace
                or record.get("revision") != revision):
            raise StateBackupError("PROJECT_STATE_UNKNOWN", "project row and record identity do not match")
        root = _existing_directory(workspace, "registered project workspace")
        if root == Path(root.anchor):
            raise StateBackupError("PROJECT_WORKSPACE_INVALID", "project workspace cannot be a filesystem root")
        from ._artifact_store import project_root_identity
        output[project_id] = {
            "project_id": project_id,
            "workspace": root,
            "workspace_path_sha256": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
            "project_root_identity": project_root_identity(root),
            "revision": revision,
            "record": record,
        }
    return output


def _canonical_project_relative(raw: str, *, field: str) -> str:
    from pathlib import PurePosixPath

    if (not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw
            or raw.startswith("/") or PurePosixPath(raw).as_posix() != raw
            or any(part in {"", ".", ".."} for part in raw.split("/"))):
        raise StateBackupError("ARTIFACT_IDENTITY_UNKNOWN", f"{field} is not a canonical project-relative path")
    return raw


def _full_artifact_inventory(
    connection: sqlite3.Connection,
    projects: Mapping[str, Mapping[str, Any]],
    *, max_files: int, max_file_bytes: int, max_total_bytes: int,
) -> list[dict[str, Any]]:
    from ._artifact_store import ArtifactStore, _hash_regular_artifact, _assert_no_symlink_components

    path_rows: list[dict[str, Any]] = []
    try:
        rows = connection.execute("SELECT artifact_id,metadata FROM artifacts ORDER BY artifact_id").fetchall()
    except sqlite3.Error as exc:
        raise StateBackupError("FULL_BACKUP_SCHEMA_UNSUPPORTED", "artifact registrations could not be read") from exc
    by_location: dict[tuple[str, str], dict[str, Any]] = {}
    total = 0
    for artifact_key, raw_metadata in rows:
        if not isinstance(artifact_key, str) or not artifact_key:
            raise StateBackupError("ARTIFACT_IDENTITY_UNKNOWN", "artifact table contains a malformed key")
        try:
            record = json.loads(raw_metadata)
        except (TypeError, json.JSONDecodeError) as exc:
            raise StateBackupError("ARTIFACT_IDENTITY_UNKNOWN", "artifact table contains malformed metadata") from exc
        if not isinstance(record, Mapping):
            raise StateBackupError("ARTIFACT_IDENTITY_UNKNOWN", "artifact registration metadata is not an object")
        schema = record.get("schema_version")
        raw_path = record.get("path")
        if schema == 2 and not isinstance(raw_path, str):
            raise StateBackupError("ARTIFACT_IDENTITY_UNKNOWN", "schema-v2 registration has no file path")
        if not isinstance(raw_path, str):
            continue
        digest = record.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise StateBackupError("ARTIFACT_HASH_UNKNOWN", "path-bearing artifact registration has no SHA-256")
        project_id = record.get("project_id")
        if isinstance(project_id, str) and project_id not in projects:
            raise StateBackupError("PROJECT_NOT_FOUND", "artifact registration points to a missing project")
        raw_file = Path(raw_path).expanduser()
        if raw_file.is_absolute():
            _reject_symlink_components(raw_file, "registered artifact path")
            try:
                resolved_raw = raw_file.resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise StateBackupError("ARTIFACT_NOT_FOUND", "a registered artifact path is missing") from exc
            candidates = [key for key, project in projects.items()
                          if _is_within(resolved_raw, project["workspace"]) and resolved_raw != project["workspace"]]
            if project_id is None:
                if len(candidates) != 1:
                    raise StateBackupError("ARTIFACT_SCOPE_UNKNOWN", "absolute artifact path does not identify exactly one project")
                project_id = candidates[0]
            elif project_id not in candidates:
                raise StateBackupError("ARTIFACT_SCOPE_MISMATCH", "absolute artifact path is outside its registered project")
            relative = resolved_raw.relative_to(projects[project_id]["workspace"]).as_posix()
        else:
            if not isinstance(project_id, str) or project_id not in projects:
                raise StateBackupError("ARTIFACT_SCOPE_UNKNOWN", "relative artifact path has no project identity")
            relative = _canonical_project_relative(raw_path, field="artifact path")
            resolved_raw = ArtifactStore(projects[project_id]["workspace"]).resolve_safe_path(relative, allow_overwrite=True)
        relative = _canonical_project_relative(relative, field="artifact path")
        root = projects[project_id]["workspace"]
        source = ArtifactStore(root).resolve_safe_path(relative, allow_overwrite=True)
        _assert_no_symlink_components(source, field="registered artifact")
        try:
            actual_sha, identity = _hash_regular_artifact(source)
        except Exception as exc:
            raise StateBackupError("ARTIFACT_UNVERIFIED", "a registered project file could not be safely pinned") from exc
        if actual_sha != digest:
            raise StateBackupError("ARTIFACT_HASH_MISMATCH", "registered project file differs from its recorded SHA-256")
        recorded_size = record.get("size")
        if recorded_size is not None and (type(recorded_size) is not int or recorded_size != identity["size"]):
            raise StateBackupError("ARTIFACT_IDENTITY_MISMATCH", "registered project file size differs from its record")
        recorded_identity = record.get("file_identity")
        if schema == 2 and (not isinstance(recorded_identity, Mapping)
                or any(recorded_identity.get(key) != value for key, value in identity.items())):
            raise StateBackupError("ARTIFACT_IDENTITY_MISMATCH", "schema-v2 artifact file identity differs from its record")
        if schema == 2:
            from pathlib import PurePosixPath
            if (record.get("project_root_identity") != projects[project_id]["project_root_identity"]
                    or not isinstance(record.get("host_identity"), str) or not record.get("host_identity")
                    or not isinstance(record.get("engine_host_identity"), str) or not record.get("engine_host_identity")
                    or PurePosixPath(relative).parts[:2] != ("g2_artifacts", "registered")
                    or len(PurePosixPath(relative).parts) != 3
                    or not PurePosixPath(relative).name.startswith(digest)):
                raise StateBackupError("ARTIFACT_IDENTITY_MISMATCH", "schema-v2 artifact registration is not bound to its project content address")
        key = (project_id, relative)
        prior = by_location.get(key)
        if prior is not None:
            if prior["sha256"] != digest or prior["size_bytes"] != identity["size"]:
                raise StateBackupError("ARTIFACT_REGISTRATION_CONFLICT", "two artifact records disagree about one project file")
            prior["registrations"].append({"artifact_key": artifact_key, "schema_version": schema,
                                            "role": record.get("role"), "classification": record.get("classification"),
                                            "old_project_root_identity": record.get("project_root_identity"),
                                            "old_host_identity": record.get("host_identity"),
                                            "old_engine_host_identity": record.get("engine_host_identity")})
            continue
        total += identity["size"]
        if identity["size"] > max_file_bytes or total > max_total_bytes:
            raise StateBackupError("FULL_BACKUP_SIZE_LIMIT", "registered project files exceed the bounded backup size")
        if len(by_location) >= max_files:
            raise StateBackupError("FULL_BACKUP_FILE_LIMIT", "registered project file count exceeds the bounded backup limit")
        entry = {
            "project_id": project_id, "relative_path": relative, "source_path": source,
            "sha256": digest, "size_bytes": identity["size"],
            "registrations": [{"artifact_key": artifact_key, "schema_version": schema,
                               "role": record.get("role"), "classification": record.get("classification"),
                               "old_project_root_identity": record.get("project_root_identity"),
                               "old_host_identity": record.get("host_identity"),
                               "old_engine_host_identity": record.get("engine_host_identity")}],
        }
        by_location[key] = entry
    return list(by_location.values())


class _HashingReader:
    def __init__(self, handle: Any):
        self.handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, amount: int = -1) -> bytes:
        data = self.handle.read(amount)
        if data:
            self.digest.update(data)
            self.size += len(data)
        return data


def _tar_add_pinned_file(archive: tarfile.TarFile, path: Path, member_name: str,
                         *, expected_sha256: str, expected_size: int) -> None:
    from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact

    handle, before = _open_pinned_artifact(path)
    reader = _HashingReader(handle)
    try:
        if before.st_size != expected_size:
            raise StateBackupError("ARTIFACT_CHANGED", "registered file size changed before archive streaming")
        info = tarfile.TarInfo(member_name)
        info.size = expected_size
        info.mode = 0o600
        info.mtime = 0
        archive.addfile(info, reader)
        _assert_pinned_artifact_stable(path, before, os.fstat(handle.fileno()))
        if reader.size != expected_size or reader.digest.hexdigest() != expected_sha256:
            raise StateBackupError("ARTIFACT_CHANGED", "registered file bytes changed while archiving")
    finally:
        handle.close()


def _online_snapshot_database(source_path: Path, destination: Path) -> dict[str, Any]:
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = _readonly_connection(source_path)
        source_version = _inspect_schema(source)
        _integrity_check(source)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        target = sqlite3.connect(destination, timeout=5.0, isolation_level=None)
        source.backup(target)
        mode = target.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise StateBackupError("BACKUP_JOURNAL_MODE_FAILED", "full backup database did not enter DELETE journal mode")
        _integrity_check(target)
        if _inspect_schema(target) != source_version:
            raise StateBackupError("BACKUP_SCHEMA_MISMATCH", "full backup database schema differs from its source")
        history_sha256 = _history_fingerprint(target)
        target.close()
        target = None
        source.close()
        source = None
        _fsync_file(destination)
        return {"sha256": _sha256(destination), "size_bytes": destination.stat().st_size,
                "schema_version": source_version, "integrity_check": "ok",
                "history_sha256": history_sha256}
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


def _publish_archive_no_clobber(staged: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise StateBackupError("DESTINATION_EXISTS", "full backup archive destination must be new")
    try:
        os.link(staged, destination)
    except FileExistsError as exc:
        raise StateBackupError("DESTINATION_EXISTS", "full backup archive destination already exists") from exc
    except OSError as exc:
        raise StateBackupError("ATOMIC_PUBLISH_FAILED", "full backup archive could not be published without replacing an existing file") from exc
    try:
        _fsync_directory(destination.parent)
    except Exception as exc:
        raise StateBackupError("DURABILITY_FAILED", "full backup archive was published but directory durability is unknown",
                               recovery_path=str(destination)) from exc


def create_full_backup(
    home: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *, max_files: int = MAX_FULL_BACKUP_FILES,
    max_file_bytes: int = MAX_FULL_BACKUP_FILE_BYTES,
    max_total_bytes: int = MAX_FULL_BACKUP_TOTAL_BYTES,
) -> dict[str, Any]:
    """Back up the locked ledger and every path-bearing artifact registration.

    This is a separate format from the backwards-compatible ledger-only
    ``backup`` command. Files are streamed into a tar archive and both its
    embedded inventory and sidecar manifest bind every member by SHA-256.
    """
    normalized_home = _existing_directory(home, "control home")
    target = _new_file_path(destination, "full backup destination", outside=normalized_home)
    manifest_path = target.with_name(target.name + ".manifest.json")
    _reject_symlink_components(manifest_path, "full backup manifest")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise StateBackupError("DESTINATION_EXISTS", "full backup manifest destination must be new")
    if _is_within(manifest_path, normalized_home):
        raise StateBackupError("DESTINATION_IN_CONTROL_HOME", "full backup manifest must be outside the control home")
    if any(type(value) is not int or value <= 0 for value in (max_files, max_file_bytes, max_total_bytes)):
        raise StateBackupError("INVALID_REQUEST", "full backup limits must be positive integers")
    scratch = Path(tempfile.mkdtemp(prefix=".comsol-full-backup-", dir=target.parent))
    snapshot = scratch / "operations.sqlite3"
    staged_archive = scratch / "full-state.tar"
    published = False
    try:
        with _locked_home(normalized_home) as locked:
            assert_no_pending_restore(locked)
            database = _database_paths(locked, required=True)["database"]
            database_entry = _online_snapshot_database(database, snapshot)
            connection = _readonly_connection(snapshot)
            try:
                projects = _project_rows_for_backup(connection)
                files = _full_artifact_inventory(connection, projects, max_files=max_files,
                                                 max_file_bytes=max_file_bytes,
                                                 max_total_bytes=max_total_bytes)
                source_host_identity = ""
                if files:
                    # These are provenance values only; restore always audits
                    # and rebinds to the host performing the restore.
                    source_host_identity = next((item["old_host_identity"] for file in files
                                                 for item in file["registrations"]
                                                 if isinstance(item.get("old_host_identity"), str)), "")
                project_entries = []
                project_ordinals: dict[str, str] = {}
                for index, (project_id, project) in enumerate(sorted(projects.items()), start=1):
                    relative_target = f"projects/project-{index:08d}"
                    project_ordinals[project_id] = relative_target
                    project_entries.append({
                        "project_id": project_id,
                        "source_workspace_path_sha256": project["workspace_path_sha256"],
                        "source_project_root_identity": project["project_root_identity"],
                        "source_revision": project["revision"],
                        "target_workspace_relative": relative_target,
                    })
                object_records = [{"member": "objects/database", **database_entry, "kind": "sqlite_database"}]
                for index, file in enumerate(files, start=1):
                    file["member"] = f"objects/file-{index:08d}"
                    object_records.append({"member": file["member"], "sha256": file["sha256"],
                                           "size_bytes": file["size_bytes"], "kind": "project_file"})
                core_manifest = {
                    "schema": FULL_BACKUP_SCHEMA,
                    "created_utc": _utc_now(),
                    "source_control_home_path_sha256": hashlib.sha256(str(locked).encode("utf-8")).hexdigest(),
                    "source_host_identity": source_host_identity or "UNRECORDED",
                    "database": {"member": "objects/database", **database_entry},
                    "projects": project_entries,
                    "files": [{key: value for key, value in file.items() if key != "source_path"}
                              for file in files],
                    "members": object_records,
                    "durable_history_sha256": database_entry["history_sha256"],
                    "scope": "complete durable ledger plus every resolvable path-bearing artifact registration; non-file metadata remains in SQLite",
                    "unknown_jobs_reclassified": False,
                    "worker_or_model_bindings_revived": False,
                }
                encoded_manifest = (json.dumps(core_manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
                if len(encoded_manifest) > MAX_FULL_BACKUP_MANIFEST_BYTES:
                    raise StateBackupError("FULL_BACKUP_MANIFEST_LIMIT", "full backup inventory exceeds the manifest size limit")
                with tarfile.open(staged_archive, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    _tar_add_pinned_file(archive, snapshot, "objects/database",
                                         expected_sha256=database_entry["sha256"],
                                         expected_size=database_entry["size_bytes"])
                    for file in files:
                        _tar_add_pinned_file(archive, Path(file["source_path"]), file["member"],
                                             expected_sha256=file["sha256"], expected_size=file["size_bytes"])
                    info = tarfile.TarInfo("manifest.json")
                    info.size = len(encoded_manifest)
                    info.mode = 0o600
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(encoded_manifest))
            finally:
                connection.close()
            _fsync_file(staged_archive)
            archive_sha256 = _sha256(staged_archive)
            archive_size = staged_archive.stat().st_size
            if archive_size > MAX_FULL_BACKUP_ARCHIVE_BYTES:
                raise StateBackupError("FULL_BACKUP_SIZE_LIMIT", "full backup archive exceeds the archive size limit")
            _publish_archive_no_clobber(staged_archive, target)
            published = True
            sidecar = {**core_manifest, "schema": FULL_BACKUP_MANIFEST_SCHEMA,
                       "archive_name": target.name, "archive_sha256": archive_sha256,
                       "archive_size_bytes": archive_size}
            _publish_new_manifest_atomic(manifest_path, sidecar)
            return {"success": True, "operation": "full_backup", "archive_path": str(target),
                    "manifest_path": str(manifest_path), "archive_sha256": archive_sha256,
                    "archive_size_bytes": archive_size, "file_count": len(files),
                    "project_count": len(project_entries), "durable_history_sha256": database_entry["history_sha256"],
                    "manifest": sidecar}
    except Exception as exc:
        receipt = {"schema": "comsol-mcp.full-state-backup-failure/1", "created_utc": _utc_now(),
                   "target": target.name, "manifest_target": manifest_path.name, "published_archive": published,
                   "cause_type": type(exc).__name__, "cause_code": getattr(exc, "code", None),
                   "cause_message": str(exc)[:500], "safe_retry": False if published else True}
        recovery = scratch / "failure_receipt.json"
        try:
            _write_new_json(recovery, receipt)
        except Exception:
            pass
        if isinstance(exc, StateBackupError):
            if exc.recovery_path is None:
                exc.recovery_path = str(scratch)
            raise
        raise StateBackupError("FULL_BACKUP_FAILED", "full-state backup failed; inspect its preserved receipt",
                               recovery_path=str(scratch)) from exc
    else:
        shutil.rmtree(scratch)


def _verify_backup(
    backup_value: str | os.PathLike[str],
    manifest_value: str | os.PathLike[str],
    *,
    staging_directory: Path,
) -> tuple[Path, dict[str, Any], int]:
    backup = _existing_file(backup_value, "backup database")
    manifest_path = _existing_file(manifest_value, "backup manifest")
    manifest = _read_json(manifest_path, "backup manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("database_name") != backup.name:
        raise StateBackupError("INVALID_MANIFEST", "backup manifest does not describe this database file")
    for suffix in _BACKUP_SIDECAR_SUFFIXES:
        sidecar = Path(str(backup) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise StateBackupError("BACKUP_SIDECAR_PRESENT", "backup database has an unmanifested SQLite sidecar")

    # SQLite must never read the caller-controlled path after this point. Copy
    # it into the private control-home staging area first, then bind all checks
    # and the later migration to the exact bytes that will be restored.
    verified_copy = staging_directory / f".restore-verified-{uuid4().hex}.sqlite3"
    _copy_file_exclusive(backup, verified_copy)
    try:
        if (type(manifest.get("database_size_bytes")) is not int
                or manifest["database_size_bytes"] != verified_copy.stat().st_size
                or manifest.get("database_sha256") != _sha256(verified_copy)):
            raise StateBackupError("BACKUP_HASH_MISMATCH", "backup database size or SHA-256 differs from its manifest")
        # Recheck source sidecars to catch a concurrent appearance during the
        # copy. The verified private copy remains the only restore input.
        for suffix in _BACKUP_SIDECAR_SUFFIXES:
            sidecar = Path(str(backup) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise StateBackupError("BACKUP_SIDECAR_PRESENT", "backup database has an unmanifested SQLite sidecar")
        connection = _readonly_connection(verified_copy)
        try:
            _integrity_check(connection)
            version = _inspect_schema(connection)
        finally:
            connection.close()
        if manifest.get("integrity_check") != "ok" or manifest.get("schema_version") != version:
            raise StateBackupError("BACKUP_SCHEMA_MISMATCH", "backup database schema differs from its manifest")
        return verified_copy, manifest, version
    except Exception:
        try:
            verified_copy.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _exclusive_empty_file(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    except FileExistsError as exc:
        raise StateBackupError("DESTINATION_EXISTS", "staging database path already exists") from exc
    except OSError as exc:
        raise StateBackupError("STAGING_CREATE_FAILED", "staging database could not be created") from exc


def _finalize_database(path: Path, *, migrate: bool) -> None:
    if migrate:
        from ._operation_store import OperationStore

        store = OperationStore(path)
        store.close()
    connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise StateBackupError("STAGE_FINALIZE_FAILED", "staged database did not enter DELETE journal mode")
        _integrity_check(connection)
        _inspect_schema(connection, allow_legacy_v1=False)
    finally:
        connection.close()
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise StateBackupError("STAGE_SIDECAR_REMAINS", "staged database is not a standalone file")
    _fsync_file(path)
    _fsync_directory(path.parent)


def _copy_file_exclusive(source: Path, target: Path) -> None:
    _reject_symlink_components(source, "recovery source")
    digest_before = _sha256(source)
    _exclusive_empty_file(target)
    try:
        with source.open("rb") as incoming, target.open("wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.chmod(target, 0o600)
        digest_after = _sha256(source)
        digest_copy = _sha256(target)
        if digest_before != digest_after or digest_copy != digest_before:
            raise StateBackupError("RECOVERY_COPY_RACE", "a protected SQLite file changed while it was being copied")
    except StateBackupError:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise StateBackupError("RECOVERY_COPY_FAILED", "a protected database copy could not be written") from exc


def _capture_original_files(home: Path, recovery: Path, paths: Mapping[str, Path]) -> dict[str, dict[str, Any]]:
    old_dir = recovery / "original"
    old_dir.mkdir(mode=0o700)
    state: dict[str, dict[str, Any]] = {}
    for role in ("database", "wal", "shm"):
        source = paths[role]
        present = source.exists()
        item: dict[str, Any] = {"name": source.name, "present": present}
        if present:
            stored = old_dir / source.name
            _copy_file_exclusive(source, stored)
            item["sha256"] = _sha256(stored)
            item["size_bytes"] = stored.stat().st_size
        state[role] = item
    _fsync_directory(old_dir)
    _fsync_directory(recovery)
    return state


def _read_restore_journal(home: Path) -> dict[str, Any]:
    journal_path = home / ACTIVE_RESTORE_JOURNAL
    _reject_symlink_components(journal_path, "restore journal")
    journal = _read_json(journal_path, "restore journal")
    if journal.get("schema") != JOURNAL_SCHEMA:
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending restore journal has an unsupported schema")
    recovery_name = journal.get("recovery_directory")
    if not isinstance(recovery_name, str) or Path(recovery_name).name != recovery_name or not recovery_name.startswith("state-recovery-"):
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending restore journal has an invalid recovery location")
    return journal


def _model_publication_journals(home: Path) -> list[tuple[str, Any]]:
    """Read publication state from a private SQLite copy, never the live WAL set."""
    database = home / DATABASE_NAME
    if not database.exists():
        return []
    _reject_symlink_components(database, "operation database")
    scratch = Path(tempfile.mkdtemp(prefix=".model-publication-check-", dir=home))
    scratch_identity = _directory_identity(scratch)
    try:
        snapshot = scratch / DATABASE_NAME
        _copy_file_exclusive(database, snapshot)
        wal = Path(str(database) + "-wal")
        if wal.exists() or wal.is_symlink():
            _reject_symlink_components(wal, "operation database WAL")
            _copy_file_exclusive(wal, Path(str(snapshot) + "-wal"))
        # SHM is reconstructible from DB/WAL and can change merely because a
        # reader opens. Keep it out of the private copy and preserve live bytes.
        connection = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='revisions'"
            ).fetchone()
            if not exists:
                return []
            return connection.execute(
                "SELECT model_key,metadata FROM revisions WHERE model_key LIKE ?",
                (MODEL_PUBLICATION_KEY_PREFIX + "%",),
            ).fetchall()
        finally:
            connection.close()
    finally:
        if _directory_identity(scratch) != scratch_identity:
            raise StateBackupError("MODEL_PUBLICATION_CHECK_UNKNOWN",
                                   "temporary model-publication check directory changed identity")
        shutil.rmtree(scratch)


def assert_no_pending_restore(home: str | os.PathLike[str]) -> None:
    """Startup gate: caller must hold ``control.lock`` before calling this."""
    normalized = _existing_directory(home, "control home")
    journal_paths = (normalized / ACTIVE_RESTORE_JOURNAL, normalized / ACTIVE_FULL_RESTORE_JOURNAL)
    for journal_path in journal_paths:
        _reject_symlink_components(journal_path, "restore journal")
    if any(path.exists() for path in journal_paths):
        raise StateBackupError(
            "RESTORE_RECOVERY_REQUIRED",
            "an interrupted or unverified ledger/full-state restore is pending; run the matching comsol-mcp-state recovery command before starting the daemon",
        )
    try:
        rows = _model_publication_journals(normalized)
    except StateBackupError:
        raise
    except Exception as exc:
        raise StateBackupError(
            "MODEL_PUBLICATION_RECOVERY_REQUIRED",
            "the operation database could not be checked for interrupted model publication journals",
        ) from exc
    pending = []
    for key, encoded in rows:
        try:
            value = json.loads(encoded)
        except (TypeError, json.JSONDecodeError):
            pending.append(str(key))
            continue
        if (not isinstance(value, dict)
                or value.get("schema") != "comsol-mcp.model-publication-journal/1"
                or value.get("status") not in MODEL_PUBLICATION_TERMINAL):
            pending.append(str(key))
    if pending:
        raise StateBackupError(
            "MODEL_PUBLICATION_RECOVERY_REQUIRED",
            "an interrupted model publication is fenced; run explicit model-publication recovery before starting the daemon",
        )


def _journal_write(home: Path, journal: dict[str, Any]) -> None:
    _replace_json(home / ACTIVE_RESTORE_JOURNAL, journal)


def _full_journal_write(home: Path, journal: dict[str, Any]) -> None:
    _replace_json(home / ACTIVE_FULL_RESTORE_JOURNAL, journal)


def _copy_snapshot_to_home(home: Path, recovery: Path, journal: dict[str, Any]) -> None:
    old = journal.get("original_files")
    if not isinstance(old, dict) or set(old) != {"database", "wal", "shm"}:
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "restore journal lacks an exact prior SQLite file inventory")
    old_dir = recovery / "original"
    _existing_directory(old_dir, "recovery snapshot")

    # Validate every preserved byte before removing any current live file.
    for role, item in old.items():
        name = DATABASE_NAME if role == "database" else f"{DATABASE_NAME}-{role}"
        if not isinstance(item, dict) or item.get("name") != name or type(item.get("present")) is not bool:
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "restore journal prior-file entry is malformed")
        if item["present"]:
            saved = old_dir / name
            if not saved.is_file() or saved.is_symlink() or _sha256(saved) != item.get("sha256"):
                raise StateBackupError("RECOVERY_HASH_MISMATCH", "preserved pre-restore database bytes do not match the journal")

    # A prior failed rollback can leave a partially restored main/WAL/SHM set;
    # recovery must inspect and repair that state rather than rejecting it.
    paths = _database_paths(home, required=False, allow_partial=True)
    displaced = recovery / "interrupted"
    if not displaced.exists():
        displaced.mkdir(mode=0o700)
    elif displaced.is_symlink() or not displaced.is_dir():
        raise StateBackupError("RECOVERY_PATH_INVALID", "interrupted restore area is not a private directory")
    journal["phase"] = "ROLLBACK_STARTED"
    _journal_write(home, journal)

    # Each rename is individually atomic. The journal remains until all three
    # original file paths have been restored and hash-checked.
    for role in ("wal", "shm", "database"):
        live = paths[role]
        if live.exists():
            if live.is_symlink() or not stat.S_ISREG(live.lstat().st_mode):
                raise StateBackupError("RESTORE_PATH_CHANGED", "a live SQLite file changed to an unsafe path during restore")
            destination = displaced / f"{role}-{uuid4().hex}"
            os.replace(live, destination)
    _fsync_directory(home)
    for role in ("wal", "shm", "database"):
        item = old[role]
        if not item["present"]:
            continue
        name = item["name"]
        saved = old_dir / name
        temporary = displaced / f"rollback-{name}-{uuid4().hex}"
        _copy_file_exclusive(saved, temporary)
        os.replace(temporary, paths[role])
    _fsync_directory(home)

    for role, item in old.items():
        live = paths[role]
        if item["present"]:
            if not live.is_file() or live.is_symlink() or _sha256(live) != item.get("sha256"):
                raise StateBackupError("ROLLBACK_VERIFY_FAILED", "restored prior SQLite bytes do not match the recovery copy")
        elif live.exists() or live.is_symlink():
            raise StateBackupError("ROLLBACK_VERIFY_FAILED", "a SQLite file is present where the prior state was absent")


def _archive_journal(home: Path, recovery: Path, journal: dict[str, Any], status: str) -> None:
    journal["phase"] = status
    journal["finished_utc"] = _utc_now()
    archived = recovery / "restore-journal.json"
    if archived.exists():
        if archived.is_symlink() or not archived.is_file():
            raise StateBackupError("RECOVERY_PATH_INVALID", "restore journal archive is not a regular file")
        existing = _read_json(archived, "archived restore journal")
        if existing.get("operation_id") != journal.get("operation_id"):
            raise StateBackupError("RECOVERY_PATH_INVALID", "restore journal archive belongs to another operation")
        _replace_json(archived, journal)
    else:
        _write_new_json(archived, journal)
    (home / ACTIVE_RESTORE_JOURNAL).unlink()
    _fsync_directory(home)


def _rollback_restore(home: Path, journal: dict[str, Any]) -> Path:
    recovery = home / journal["recovery_directory"]
    _existing_directory(recovery, "restore recovery directory")
    try:
        _copy_snapshot_to_home(home, recovery, journal)
        _archive_journal(home, recovery, journal, "ROLLED_BACK_TO_PRE_RESTORE_FILE_SET")
    except Exception as exc:
        journal["phase"] = "ROLLBACK_FAILED_OR_INCOMPLETE"
        journal["last_error_type"] = type(exc).__name__
        try:
            _journal_write(home, journal)
        except Exception:
            pass
        raise StateBackupError(
            "RESTORE_ROLLBACK_INCOMPLETE",
            "restore rollback was not confirmed; the recovery directory is preserved. Keep the daemon stopped and use `comsol-mcp-state recover` before restarting",
            recovery_path=str(recovery),
        ) from exc
    return recovery


def _stage_backup(home: Path, backup: Path) -> Path:
    stage = home / f".{DATABASE_NAME}.stage-{uuid4().hex}"
    _exclusive_empty_file(stage)
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = _readonly_connection(backup)
        target = sqlite3.connect(stage, timeout=5.0, isolation_level=None)
        source.backup(target)
        target.close()
        target = None
        source.close()
        source = None
        _finalize_database(stage, migrate=True)
        return stage
    except Exception:
        if target is not None:
            try:
                target.close()
            except sqlite3.Error:
                pass
        if source is not None:
            try:
                source.close()
            except sqlite3.Error:
                pass
        for candidate in (stage, *(Path(str(stage) + suffix) for suffix in _SIDECAR_SUFFIXES)):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def restore_backup(
    home: str | os.PathLike[str],
    backup_value: str | os.PathLike[str],
    manifest_value: str | os.PathLike[str],
    *,
    confirm: bool,
) -> dict[str, Any]:
    """Verify, migrate in isolation, and restore one ledger under control.lock."""
    if confirm is not True:
        raise StateBackupError("CONFIRM_REQUIRED", "restore requires the explicit --confirm flag")
    normalized_home = _existing_directory(home, "control home")
    backup = _existing_file(backup_value, "backup database")
    manifest_path = _existing_file(manifest_value, "backup manifest")
    if _is_within(backup, normalized_home) or _is_within(manifest_path, normalized_home):
        raise StateBackupError("BACKUP_IN_CONTROL_HOME", "restore backup and manifest must be outside the control home")

    with _locked_home(normalized_home) as locked:
        stage: Path | None = None
        verified_backup: Path | None = None
        journal_written = False
        recovery: Path | None = None
        try:
            assert_no_pending_restore(locked)
            verified_backup, manifest, _ = _verify_backup(
                backup, manifest_path, staging_directory=locked
            )
            current = _database_paths(locked, required=False)
            stage = _stage_backup(locked, verified_backup)
            verified_backup.unlink()
            verified_backup = None

            recovery = locked / f"state-recovery-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
            recovery.mkdir(mode=0o700)
            original_files = _capture_original_files(locked, recovery, current)
            journal = {
                "schema": JOURNAL_SCHEMA,
                "operation_id": str(uuid4()),
                "started_utc": _utc_now(),
                "phase": "PREPARED",
                "stage_name": stage.name,
                "recovery_directory": recovery.name,
                "incoming_database_sha256": manifest["database_sha256"],
                "original_files": original_files,
                "daemon_started_by_command": False,
            }
            _journal_write(locked, journal)
            journal_written = True

            displaced = recovery / "displaced"
            displaced.mkdir(mode=0o700)
            journal["phase"] = "SWAP_STARTED"
            _journal_write(locked, journal)
            for role in ("wal", "shm", "database"):
                live = current[role]
                if live.exists():
                    os.replace(live, displaced / live.name)
                    _fsync_directory(locked)
            os.replace(stage, current["database"])
            stage = None
            _fsync_directory(locked)
            journal["phase"] = "CANDIDATE_INSTALLED"
            _journal_write(locked, journal)

            live = _readonly_connection(current["database"])
            try:
                _integrity_check(live)
                version = _inspect_schema(live, allow_legacy_v1=False)
            finally:
                live.close()
            if version != manifest["schema_version"]:
                # Staging may add a compatible table without changing the
                # schema version; require the stated version still to match.
                raise StateBackupError("RESTORE_SCHEMA_MISMATCH", "restored ledger schema version changed unexpectedly")
            for suffix in _SIDECAR_SUFFIXES:
                sidecar = Path(str(current["database"]) + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    raise StateBackupError("RESTORE_SIDECAR_REMAINS", "restored ledger has unexpected SQLite sidecars")
            journal["phase"] = "CANDIDATE_VERIFIED"
            _journal_write(locked, journal)
            _archive_journal(locked, recovery, journal, "RESTORED_AND_VERIFIED")
            journal_written = False
            return {
                "success": True,
                "operation": "restore",
                "database": DATABASE_NAME,
                "database_sha256": _sha256(current["database"]),
                "integrity_check": "ok",
                "schema_version": version,
                "recovery_directory": str(recovery),
                "daemon_started": False,
                "replay_performed": False,
            }
        except Exception as exc:
            # Rollback and journal completion are part of the same locked
            # critical section as the replacement. Releasing control.lock
            # before this point would let another maintenance process observe
            # or mutate a half-restored database set.
            if journal_written and recovery is not None:
                try:
                    journal = _read_restore_journal(locked)
                    recovery = _rollback_restore(locked, journal)
                except Exception as rollback_exc:
                    if isinstance(rollback_exc, StateBackupError) and rollback_exc.code == "RESTORE_ROLLBACK_INCOMPLETE":
                        raise rollback_exc from exc
                    raise StateBackupError(
                        "RESTORE_ROLLBACK_INCOMPLETE",
                        "restore failed and rollback could not be confirmed; inspect the restore journal and preserved recovery directory, keep the daemon stopped, and resolve with `comsol-mcp-state recover` before restarting",
                        recovery_path=str(recovery),
                    ) from exc
            if isinstance(exc, StateBackupError):
                raise
            raise StateBackupError("RESTORE_FAILED", f"restore failed ({type(exc).__name__}); original database state was rolled back") from exc
        finally:
            for temporary in (stage, verified_backup):
                if temporary is None:
                    continue
                for candidate in (temporary, *(Path(str(temporary) + suffix) for suffix in _SIDECAR_SUFFIXES)):
                    try:
                        candidate.unlink(missing_ok=True)
                    except OSError:
                        pass


def recover_restore(home: str | os.PathLike[str], *, confirm: bool) -> dict[str, Any]:
    """Restore the exact pre-restore DB/WAL/SHM byte set from its journal."""
    if confirm is not True:
        raise StateBackupError("CONFIRM_REQUIRED", "recovery requires the explicit --confirm flag")
    with _locked_home(home) as locked:
        journal_path = locked / ACTIVE_RESTORE_JOURNAL
        _reject_symlink_components(journal_path, "restore journal")
        if not journal_path.exists():
            raise StateBackupError("NO_PENDING_RESTORE", "no interrupted restore journal exists")
        journal = _read_restore_journal(locked)
        recovery = _rollback_restore(locked, journal)
        return {
            "success": True,
            "operation": "recover",
            "restored_file_set": "pre-restore SQLite main/WAL/SHM bytes",
            "recovery_directory": str(recovery),
            "daemon_started": False,
        }


def _read_full_document(path: Path, field: str, *, maximum_bytes: int = MAX_FULL_BACKUP_MANIFEST_BYTES) -> dict[str, Any]:
    from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact

    handle, before = _open_pinned_artifact(path)
    try:
        if before.st_size > maximum_bytes:
            raise StateBackupError("INVALID_RECEIPT", f"{field} exceeds its size limit")
        data = handle.read(maximum_bytes + 1)
        if len(data) != before.st_size or len(data) > maximum_bytes:
            raise StateBackupError("INVALID_RECEIPT", f"{field} changed while it was read")
        _assert_pinned_artifact_stable(path, before, os.fstat(handle.fileno()))
        value = json.loads(data)
    except StateBackupError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateBackupError("INVALID_RECEIPT", f"{field} could not be read as JSON") from exc
    finally:
        handle.close()
    if not isinstance(value, dict):
        raise StateBackupError("INVALID_RECEIPT", f"{field} must contain a JSON object")
    return value


def _validate_full_manifest_shape(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, tarfile.TarInfo]]:
    if manifest.get("schema") != FULL_BACKUP_MANIFEST_SCHEMA:
        raise StateBackupError("FULL_BACKUP_FORMAT_UNSUPPORTED", "full backup manifest schema is unsupported")
    archive_name = manifest.get("archive_name")
    archive_sha = manifest.get("archive_sha256")
    archive_size = manifest.get("archive_size_bytes")
    if (not isinstance(archive_name, str) or Path(archive_name).name != archive_name
            or not isinstance(archive_sha, str) or re.fullmatch(r"[0-9a-f]{64}", archive_sha) is None
            or type(archive_size) is not int or archive_size <= 0 or archive_size > MAX_FULL_BACKUP_ARCHIVE_BYTES):
        raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "full backup outer archive identity is malformed")
    core = dict(manifest)
    for key in ("archive_name", "archive_sha256", "archive_size_bytes"):
        core.pop(key, None)
    core["schema"] = FULL_BACKUP_SCHEMA
    if (not isinstance(core.get("projects"), list) or not isinstance(core.get("files"), list)
            or not isinstance(core.get("members"), list)
            or len(core["files"]) > MAX_FULL_BACKUP_FILES
            or len(core["members"]) != len(core["files"]) + 1
            or core.get("unknown_jobs_reclassified") is not False
            or core.get("worker_or_model_bindings_revived") is not False):
        raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "full backup inventory is malformed")
    db = core.get("database")
    if (not isinstance(db, Mapping) or db.get("member") != "objects/database"
            or not isinstance(db.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", db["sha256"]) is None
            or type(db.get("size_bytes")) is not int or db["size_bytes"] <= 0
            or db.get("integrity_check") != "ok"
            or db.get("history_sha256") != core.get("durable_history_sha256")):
        raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "full backup database inventory is malformed")
    projects: dict[str, dict[str, Any]] = {}
    target_names: set[str] = set()
    for index, item in enumerate(core["projects"], start=1):
        if (not isinstance(item, Mapping) or not isinstance(item.get("project_id"), str)
                or not item["project_id"] or len(item["project_id"]) > 128
                or item["project_id"] in projects
                or type(item.get("source_revision")) is not int or item["source_revision"] < 1
                or not isinstance(item.get("source_workspace_path_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", item["source_workspace_path_sha256"]) is None
                or not isinstance(item.get("source_project_root_identity"), str)
                or re.fullmatch(r"[0-9a-f]{64}", item["source_project_root_identity"]) is None
                or item.get("target_workspace_relative") != f"projects/project-{index:08d}"):
            raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "full backup project inventory is malformed")
        projects[item["project_id"]] = dict(item)
        target_names.add(item["target_workspace_relative"])
    members: dict[str, dict[str, Any]] = {}
    for item in core["members"]:
        if (not isinstance(item, Mapping) or not isinstance(item.get("member"), str)
                or item["member"] in members or item["member"] == "manifest.json"
                or re.fullmatch(r"objects/(?:database|file-[0-9]{8})", item["member"]) is None
                or not isinstance(item.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
                or type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0
                or item["size_bytes"] > MAX_FULL_BACKUP_FILE_BYTES):
            raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "full backup member inventory is malformed")
        members[item["member"]] = dict(item)
    if members.get("objects/database", {}).get("sha256") != db.get("sha256"):
        raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "database member differs from its inventory")
    expected_paths: set[tuple[str, str]] = set()
    for item in core["files"]:
        if (not isinstance(item, Mapping) or item.get("project_id") not in projects
                or item.get("member") not in members or item["member"] == "objects/database"
                or item.get("sha256") != members[item["member"]]["sha256"]
                or item.get("size_bytes") != members[item["member"]]["size_bytes"]
                or not isinstance(item.get("registrations"), list) or not item["registrations"]):
            raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "full backup project-file inventory is malformed")
        relative = _canonical_project_relative(item.get("relative_path"), field="full backup file path")
        key = (item["project_id"], relative)
        if key in expected_paths:
            raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "full backup repeats a project file")
        expected_paths.add(key)
        for registration in item["registrations"]:
            if (not isinstance(registration, Mapping) or not isinstance(registration.get("artifact_key"), str)
                    or type(registration.get("schema_version")) is not int
                    or registration["schema_version"] not in (1, 2)):
                raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "artifact registration entry is malformed")
    return core, members


def _open_and_verify_full_archive(archive_path: Path, manifest_path: Path) -> tuple[Any, Any, dict[str, Any], dict[str, tarfile.TarInfo], os.stat_result]:
    from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact

    manifest = _read_full_document(manifest_path, "full backup manifest")
    core, expected_members = _validate_full_manifest_shape(manifest)
    if manifest.get("archive_name") != archive_path.name:
        raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "manifest names a different archive")
    handle, before = _open_pinned_artifact(archive_path)
    digest = hashlib.sha256()
    total = 0
    while True:
        block = handle.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > MAX_FULL_BACKUP_ARCHIVE_BYTES:
            handle.close()
            raise StateBackupError("FULL_BACKUP_SIZE_LIMIT", "full backup archive exceeds the archive size limit")
        digest.update(block)
    if total != manifest["archive_size_bytes"] or digest.hexdigest() != manifest["archive_sha256"]:
        handle.close()
        raise StateBackupError("FULL_BACKUP_HASH_MISMATCH", "archive bytes differ from the external manifest")
    _assert_pinned_artifact_stable(archive_path, before, os.fstat(handle.fileno()))
    handle.seek(0)
    archive: tarfile.TarFile | None = None
    try:
        archive = tarfile.open(fileobj=handle, mode="r:")
        infos = archive.getmembers()
        if len(infos) != len(expected_members) + 1 or len(infos) > MAX_FULL_BACKUP_FILES + 2:
            raise StateBackupError("FULL_BACKUP_MEMBER_MISMATCH", "archive has missing or extra members")
        actual: dict[str, tarfile.TarInfo] = {}
        for info in infos:
            if (not info.isreg() or info.name in actual or info.pax_headers
                    or (info.name != "manifest.json" and info.name not in expected_members)):
                raise StateBackupError("FULL_BACKUP_MEMBER_UNSAFE", "archive contains a duplicate, link, special, or unlisted member")
            if info.size < 0 or info.size > MAX_FULL_BACKUP_FILE_BYTES:
                raise StateBackupError("FULL_BACKUP_SIZE_LIMIT", "archive member exceeds its size limit")
            actual[info.name] = info
        if set(actual) != set(expected_members) | {"manifest.json"}:
            raise StateBackupError("FULL_BACKUP_MEMBER_MISMATCH", "archive member names differ from the manifest")
        manifest_info = actual["manifest.json"]
        if manifest_info.size > MAX_FULL_BACKUP_MANIFEST_BYTES:
            raise StateBackupError("FULL_BACKUP_MANIFEST_LIMIT", "embedded archive manifest exceeds its size limit")
        embedded_stream = archive.extractfile(manifest_info)
        if embedded_stream is None:
            raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "embedded manifest cannot be read")
        embedded_bytes = embedded_stream.read(MAX_FULL_BACKUP_MANIFEST_BYTES + 1)
        if len(embedded_bytes) != manifest_info.size or len(embedded_bytes) > MAX_FULL_BACKUP_MANIFEST_BYTES:
            raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "embedded manifest size is inconsistent")
        try:
            embedded = json.loads(embedded_bytes)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise StateBackupError("FULL_BACKUP_MANIFEST_INVALID", "embedded manifest is not valid JSON") from exc
        if embedded != core:
            raise StateBackupError("FULL_BACKUP_MANIFEST_MISMATCH", "embedded and external manifests differ")
        for name, expected in expected_members.items():
            if actual[name].size != expected["size_bytes"]:
                raise StateBackupError("FULL_BACKUP_MEMBER_MISMATCH", "tar member size differs from the manifest")
        return handle, archive, core, actual, before
    except Exception:
        if archive is not None:
            archive.close()
        handle.close()
        raise


def _posix_directory_flags() -> int:
    return (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0))


def _fd_directory_identity(descriptor: int) -> dict[str, int]:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise StateBackupError("RESTORE_STAGE_UNSAFE", "restore path component is not a directory")
    return {"device": int(info.st_dev), "inode": int(info.st_ino)}


def _open_posix_directory_chain(path: Path, *, expected_identity: Mapping[str, Any] | None = None):
    """Open every absolute directory component with O_NOFOLLOW and retain the chain."""
    absolute = Path(os.path.abspath(path))
    descriptors: list[int] = []
    links: list[tuple[int, str, dict[str, int]]] = []
    try:
        current = os.open(absolute.anchor or "/", _posix_directory_flags())
        descriptors.append(current)
        for component in absolute.parts[1:]:
            child = os.open(component, _posix_directory_flags(), dir_fd=current)
            identity = _fd_directory_identity(child)
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or identity != {"device": int(named.st_dev), "inode": int(named.st_ino)}):
                os.close(child)
                raise StateBackupError("RESTORE_STAGE_UNSAFE", "restore root path changed during handle-relative open")
            links.append((current, component, identity))
            descriptors.append(child)
            current = child
        identity = _fd_directory_identity(descriptors[-1])
        if expected_identity is not None and identity != {
                "device": expected_identity.get("device"), "inode": expected_identity.get("inode")}:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "pinned restore directory identity differs from its journal")
        return descriptors, links, identity
    except Exception:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _verify_posix_directory_links(links: list[tuple[int, str, dict[str, int]]]) -> None:
    for parent, name, expected in links:
        try:
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "pinned restore directory name disappeared") from exc
        if (not stat.S_ISDIR(named.st_mode)
                or {"device": int(named.st_dev), "inode": int(named.st_ino)} != expected):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "pinned restore directory name changed")


def _directory_identity_beneath(root: Path, relative: str, *,
                                expected_root_identity: Mapping[str, Any] | None = None) -> dict[str, int]:
    parts = _safe_relative_parts(relative)
    if os.name == "nt":
        from ._windows_parameter_publish import _API, pinned_parent
        api = _API()
        with pinned_parent(root, relative, add_directory=True, _api=api) as (
                api, parent, _, binding):
            expected = (_directory_identity(root) if expected_root_identity is None
                        else (expected_root_identity.get("device"), expected_root_identity.get("inode")))
            if binding[0][1] != expected:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows directory root differs from its journal")
            handle = api.open_child_directory(parent, parts[-1])
            try:
                identity = api.info(handle, directory=True)[0]
                api.verify_named_binding(binding)
                if api.named_identity(str(root.joinpath(*parts)), directory=True) != identity:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows directory name differs from its pinned handle")
                return {"device": int(identity[0]), "inode": int(identity[1])}
            finally:
                api.close(handle)
    descriptors: list[int] = []
    links: list[tuple[int, str, dict[str, int]]] = []
    try:
        descriptors, links, _ = _open_posix_directory_chain(root,
            expected_identity=expected_root_identity)
        current = descriptors[-1]
        for component in parts:
            child = os.open(component, _posix_directory_flags(), dir_fd=current)
            identity = _fd_directory_identity(child)
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or identity != {"device": int(named.st_dev), "inode": int(named.st_ino)}):
                os.close(child)
                raise StateBackupError("RESTORE_TARGET_CHANGED", "directory path changed during identity verification")
            links.append((current, component, identity))
            descriptors.append(child)
            current = child
        _verify_posix_directory_links(links)
        return _fd_directory_identity(current)
    except StateBackupError:
        raise
    except OSError as exc:
        raise StateBackupError("RESTORE_TARGET_CHANGED", "restore directory is missing or unsafe") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _safe_relative_parts(value: str) -> tuple[str, ...]:
    if (not isinstance(value, str) or not value or value.startswith("/") or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise StateBackupError("RESTORE_STAGE_UNSAFE", "restore relative path is not canonical")
    return tuple(value.split("/"))


def _renameat_directory_noreplace(source_parent_fd: int, source_leaf: str,
                                  destination_parent_fd: int, destination_leaf: str) -> None:
    """Use the platform's atomic exclusive directory rename primitive."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        flag = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        flag = 0x00000001  # RENAME_NOREPLACE
    else:
        function = None
        flag = 0
    if function is None:
        raise StateBackupError("RESTORE_NOREPLACE_UNAVAILABLE",
                               "this platform lacks a verified handle-relative no-replace directory rename")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = function(source_parent_fd, os.fsencode(source_leaf), destination_parent_fd,
                      os.fsencode(destination_leaf), flag)
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {getattr(__import__("errno"), "EEXIST", 17),
                            getattr(__import__("errno"), "ENOTEMPTY", 39)}:
            raise StateBackupError("RESTORE_TARGET_CHANGED",
                                   "project destination appeared during exclusive directory publication")
        if error_number in {getattr(__import__("errno"), "ENOSYS", 38),
                            getattr(__import__("errno"), "EINVAL", 22),
                            getattr(__import__("errno"), "ENOTSUP", 95)}:
            raise StateBackupError("RESTORE_NOREPLACE_UNAVAILABLE",
                                   "filesystem does not support exclusive directory publication")
        raise OSError(error_number, os.strerror(error_number))


def _create_directory_tree(root: Path, relative: str, *,
                           expected_root_identity: Mapping[str, Any] | None = None,
                           existing_components: int = 0) -> dict[str, int]:
    """Create every directory component exclusively below an exact pinned root."""
    parts = _safe_relative_parts(relative)
    if type(existing_components) is not int or not 0 <= existing_components < len(parts):
        if existing_components != 0 or type(existing_components) is not int:
            raise StateBackupError("RESTORE_STAGE_UNSAFE", "existing directory prefix count is invalid")
    if os.name == "nt":
        from ._windows_parameter_publish import _API
        api = _API()
        handles = []
        try:
            handle = api.open(str(root), directory=True, add_directory=True)
            handles.append(handle)
            root_id = api.info(handle, directory=True)[0]
            expected = (_directory_identity(root) if expected_root_identity is None
                        else (expected_root_identity.get("device"), expected_root_identity.get("inode")))
            if root_id != expected:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows restore root identity changed before directory creation")
            current = handle
            cursor = root
            for index, part in enumerate(parts):
                child = (api.open_child_directory(current, part, add_directory=True)
                         if index < existing_components else api.create_child_directory(current, part))
                handles.append(child)
                identity = api.info(child, directory=True)[0]
                cursor = cursor / part
                if api.named_identity(str(cursor), directory=True) != identity:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows created directory name differs from its handle")
                current = child
            final = api.info(handles[-1], directory=True)[0]
            return {"device": int(final[0]), "inode": int(final[1])}
        except FileExistsError as exc:
            raise StateBackupError("RESTORE_STAGE_COLLISION", "restore staging directory already exists") from exc
        except StateBackupError:
            raise
        except Exception as exc:
            raise StateBackupError("RESTORE_STAGE_UNSAFE", "Windows handle-relative directory creation failed") from exc
        finally:
            for handle in reversed(handles):
                try:
                    api.close(handle)
                except OSError:
                    pass

    descriptors: list[int] = []
    links: list[tuple[int, str, dict[str, int]]] = []
    try:
        descriptors, links, root_id = _open_posix_directory_chain(root,
            expected_identity=expected_root_identity)
        current = descriptors[-1]
        for index, part in enumerate(parts):
            if index >= existing_components:
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError as exc:
                    raise StateBackupError("RESTORE_STAGE_COLLISION", "restore staging directory already exists") from exc
            child = os.open(part, _posix_directory_flags(), dir_fd=current)
            identity = _fd_directory_identity(child)
            named = os.stat(part, dir_fd=current, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or identity != {"device": int(named.st_dev), "inode": int(named.st_ino)}):
                os.close(child)
                raise StateBackupError("RESTORE_TARGET_CHANGED", "created restore directory name changed")
            links.append((current, part, identity))
            descriptors.append(child)
            current = child
        _verify_posix_directory_links(links)
        result = _fd_directory_identity(descriptors[-1])
        if not result:
            raise StateBackupError("RESTORE_STAGE_UNSAFE", "created restore directory has no identity")
        return result
    except StateBackupError:
        raise
    except OSError as exc:
        raise StateBackupError("RESTORE_STAGE_UNSAFE", "handle-relative restore directory creation failed") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _rename_directory_noreplace(source_root: Path, source_relative: str,
                                destination_root: Path, destination_relative: str, *,
                                source_identity: Mapping[str, Any],
                                source_root_identity: Mapping[str, Any],
                                destination_root_identity: Mapping[str, Any]) -> None:
    """Atomically move a verified directory name without replacing any target."""
    source_parts = _safe_relative_parts(source_relative)
    destination_parts = _safe_relative_parts(destination_relative)
    if os.name == "nt":
        from ._windows_parameter_publish import _API, pinned_parent
        api = _API()
        with pinned_parent(source_root, source_relative, add_directory=True, _api=api) as (
                api, source_parent, _, source_binding):
            with pinned_parent(destination_root, destination_relative, add_directory=True, _api=api) as (
                    api, destination_parent, _, destination_binding):
                expected_source = (source_identity.get("device"), source_identity.get("inode"))
                actual_source = api.open_child_directory(source_parent, source_parts[-1])
                try:
                    if api.info(actual_source, directory=True)[0] != expected_source:
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows source directory identity changed before install")
                    if source_binding[0][1] != (
                            source_root_identity.get("device"), source_root_identity.get("inode")):
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows source root identity changed")
                    if destination_binding[0][1] != (
                            destination_root_identity.get("device"), destination_root_identity.get("inode")):
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows destination root identity changed")
                    api.rename(actual_source, destination_parent, destination_parts[-1])
                    installed = api.open_child_directory(destination_parent, destination_parts[-1])
                    try:
                        if api.info(installed, directory=True)[0] != expected_source:
                            raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows installed directory differs from its exact source handle")
                    finally:
                        api.close(installed)
                    api.verify_named_binding(source_binding)
                    api.verify_named_binding(destination_binding)
                finally:
                    api.close(actual_source)
        return

    source_fds: list[int] = []
    destination_fds: list[int] = []
    source_links: list[tuple[int, str, dict[str, int]]] = []
    destination_links: list[tuple[int, str, dict[str, int]]] = []
    try:
        source_fds, source_links, _ = _open_posix_directory_chain(
            source_root, expected_identity=source_root_identity)
        destination_fds, destination_links, _ = _open_posix_directory_chain(
            destination_root, expected_identity=destination_root_identity)
        source_parent_fd = source_fds[-1]
        destination_parent_fd = destination_fds[-1]
        for component in source_parts[:-1]:
            child = os.open(component, _posix_directory_flags(), dir_fd=source_parent_fd)
            identity = _fd_directory_identity(child)
            named = os.stat(component, dir_fd=source_parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or identity != {"device": int(named.st_dev), "inode": int(named.st_ino)}):
                os.close(child)
                raise StateBackupError("RESTORE_TARGET_CHANGED", "source parent directory changed before install")
            source_links.append((source_parent_fd, component, identity))
            source_fds.append(child)
            source_parent_fd = child
        for component in destination_parts[:-1]:
            child = os.open(component, _posix_directory_flags(), dir_fd=destination_parent_fd)
            identity = _fd_directory_identity(child)
            named = os.stat(component, dir_fd=destination_parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or identity != {"device": int(named.st_dev), "inode": int(named.st_ino)}):
                os.close(child)
                raise StateBackupError("RESTORE_TARGET_CHANGED", "destination parent directory changed before install")
            destination_links.append((destination_parent_fd, component, identity))
            destination_fds.append(child)
            destination_parent_fd = child
        source_info = os.stat(source_parts[-1], dir_fd=source_parent_fd, follow_symlinks=False)
        actual_source = {"device": int(source_info.st_dev), "inode": int(source_info.st_ino)}
        if not stat.S_ISDIR(source_info.st_mode) or actual_source != {
                "device": source_identity.get("device"), "inode": source_identity.get("inode")}:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "source directory differs from its journaled identity")
        try:
            os.stat(destination_parts[-1], dir_fd=destination_parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "project directory destination appeared before no-replace publication")

        _renameat_directory_noreplace(source_parent_fd, source_parts[-1],
                                      destination_parent_fd, destination_parts[-1])
        installed = os.stat(destination_parts[-1], dir_fd=destination_parent_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(installed.st_mode)
                or {"device": int(installed.st_dev), "inode": int(installed.st_ino)} != actual_source):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "published project directory failed exact identity readback")
        _verify_posix_directory_links(source_links)
        _verify_posix_directory_links(destination_links)
    except StateBackupError:
        raise
    except OSError as exc:
        raise StateBackupError("RESTORE_STAGE_UNSAFE", "handle-relative no-replace directory publication failed") from exc
    finally:
        for descriptor in reversed(destination_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in reversed(source_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_tar_member_exclusive(archive: tarfile.TarFile, info: tarfile.TarInfo,
                                target: Path, *, trusted_root: Path,
                                expected_root_identity: Mapping[str, Any] | None,
                                expected_sha256: str, expected_size: int) -> None:
    """Stream one verified tar member through pinned root/parent handles."""
    try:
        relative = target.relative_to(trusted_root).as_posix()
    except ValueError as exc:
        raise StateBackupError("RESTORE_STAGE_UNSAFE", "restore member is outside its pinned staging root") from exc
    parts = _safe_relative_parts(relative)
    source = archive.extractfile(info)
    if source is None:
        raise StateBackupError("FULL_BACKUP_MEMBER_INVALID", "archive member cannot be read")
    if os.name == "nt":
        from ._windows_parameter_publish import _API, pinned_parent
        api = _API()
        try:
            with pinned_parent(trusted_root, relative, add_file=True, _api=api) as (api, parent, _, binding):
                root_identity = binding[0][1]
                expected = (_directory_identity(trusted_root) if expected_root_identity is None
                            else (expected_root_identity.get("device"), expected_root_identity.get("inode")))
                if root_identity != expected:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows pinned extraction root differs from its journal")
                handle = None
                digest = hashlib.sha256()
                total = 0
                try:
                    handle = api.create_owned(parent, parts[-1])
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > expected_size or total > MAX_FULL_BACKUP_FILE_BYTES:
                            raise StateBackupError("FULL_BACKUP_MEMBER_MISMATCH", "archive member expanded past its declared size")
                        digest.update(block)
                        api.write(handle, block)
                    api.flush(handle)
                    if total != expected_size or digest.hexdigest() != expected_sha256:
                        raise StateBackupError("FULL_BACKUP_MEMBER_HASH_MISMATCH", "archive member bytes differ from the manifest")
                    read_hash, read_size = api.readback(handle)
                    identity = api.source_fingerprint(handle)
                    if read_hash != expected_sha256 or read_size != expected_size or identity[1] != expected_size:
                        raise StateBackupError("FULL_BACKUP_MEMBER_HASH_MISMATCH", "Windows staged member failed same-handle readback")
                    api.verify_named_binding(binding)
                    if api.child_identity(parent, parts[-1]) != identity[0]:
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "Windows staged member name differs from its pinned handle")
                except Exception as exc:
                    if handle is not None:
                        try:
                            api.delete_owned(handle)
                        except Exception as cleanup_exc:
                            raise StateBackupError("RESTORE_STAGE_CLEANUP_UNKNOWN", "owned Windows staged member could not be safely removed") from cleanup_exc
                    raise
                finally:
                    if handle is not None:
                        api.close(handle)
        except StateBackupError:
            raise
        except Exception as exc:
            raise StateBackupError("RESTORE_STAGE_UNSAFE", "Windows handle-relative member extraction failed") from exc
        finally:
            source.close()
        return

    descriptors: list[int] = []
    links: list[tuple[int, str, dict[str, int]]] = []
    descriptor: int | None = None
    created_identity: dict[str, int] | None = None
    current = -1
    try:
        descriptors, links, root_id = _open_posix_directory_chain(
            trusted_root, expected_identity=expected_root_identity)
        current = descriptors[-1]
        for component in parts[:-1]:
            child = os.open(component, _posix_directory_flags(), dir_fd=current)
            identity = _fd_directory_identity(child)
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or identity != {"device": int(named.st_dev), "inode": int(named.st_ino)}):
                os.close(child)
                raise StateBackupError("RESTORE_STAGE_UNSAFE", "restore parent changed during handle-relative open")
            links.append((current, component, identity))
            descriptors.append(child)
            current = child
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        try:
            descriptor = os.open(parts[-1], flags, 0o600, dir_fd=current)
        except FileExistsError as exc:
            raise StateBackupError("RESTORE_STAGE_COLLISION", "restore staging file already exists") from exc
        opened = os.fstat(descriptor)
        created_identity = {"device": int(opened.st_dev), "inode": int(opened.st_ino)}
        digest = hashlib.sha256()
        total = 0
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > expected_size or total > MAX_FULL_BACKUP_FILE_BYTES:
                raise StateBackupError("FULL_BACKUP_MEMBER_MISMATCH", "archive member expanded past its declared size")
            digest.update(block)
            view = memoryview(block)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("zero-byte restore staging write")
                view = view[written:]
        os.fsync(descriptor)
        final_info = os.fstat(descriptor)
        if created_identity != {"device": int(final_info.st_dev), "inode": int(final_info.st_ino)}:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "opened restore file identity changed while writing")
        if total != expected_size or digest.hexdigest() != expected_sha256 or int(final_info.st_size) != expected_size:
            raise StateBackupError("FULL_BACKUP_MEMBER_HASH_MISMATCH", "archive member bytes differ from the manifest")
        named = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        if (not stat.S_ISREG(named.st_mode)
                or {"device": int(named.st_dev), "inode": int(named.st_ino)} != created_identity):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "restore output name no longer identifies its opened file")
        _verify_posix_directory_links(links)
        os.fsync(current)
    except StateBackupError:
        if descriptor is not None and current >= 0 and created_identity is not None:
            try:
                named = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
                if {"device": int(named.st_dev), "inode": int(named.st_ino)} == created_identity:
                    os.unlink(parts[-1], dir_fd=current)
            except OSError:
                pass
        raise
    except OSError as exc:
        if descriptor is not None and current >= 0 and created_identity is not None:
            try:
                named = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
                if {"device": int(named.st_dev), "inode": int(named.st_ino)} == created_identity:
                    os.unlink(parts[-1], dir_fd=current)
            except OSError:
                pass
        raise StateBackupError("RESTORE_STAGE_UNSAFE", "handle-relative restore member write failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        source.close()


def _tree_file_inventory(root: Path) -> dict[str, tuple[str, int]]:
    from ._artifact_store import _hash_regular_artifact

    if root.is_symlink() or not root.is_dir():
        raise StateBackupError("RECOVERY_PATH_INVALID", "full restore directory is missing or unsafe")
    files: dict[str, tuple[str, int]] = {}
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            item = current_path / name
            if item.is_symlink() or not item.is_dir():
                raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree contains a symlink or non-directory")
        for name in filenames:
            item = current_path / name
            if item.is_symlink() or not item.is_file():
                raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree contains a symlink or special file")
            digest, identity = _hash_regular_artifact(item)
            files[item.relative_to(root).as_posix()] = (digest, identity["size"])
    return files


def _expected_project_files(manifest: Mapping[str, Any], project_id: str) -> dict[str, tuple[str, int]]:
    return {str(item["relative_path"]): (str(item["sha256"]), int(item["size_bytes"]))
            for item in manifest["files"] if item["project_id"] == project_id}


def _expected_stage_files(manifest: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    targets = {item["project_id"]: item["target_workspace_relative"] for item in manifest["projects"]}
    return {f"{targets[item['project_id']]}/{item['relative_path']}":
            (item["sha256"], item["size_bytes"]) for item in manifest["files"]}


def _expected_projects_files(manifest: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    stage = _expected_stage_files(manifest)
    return {name.removeprefix("projects/"): value for name, value in stage.items()}


def _verify_exact_tree(root: Path, expected: Mapping[str, tuple[str, int]]) -> None:
    actual = _tree_file_inventory(root)
    if actual != dict(expected):
        raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree no longer matches the exact verified file inventory")


def _verify_tree_subset(root: Path, expected: Mapping[str, tuple[str, int]]) -> None:
    actual = _tree_file_inventory(root)
    if any(name not in expected or expected[name] != identity for name, identity in actual.items()):
        raise StateBackupError("RESTORE_TARGET_CHANGED", "partial restore tree contains an unlisted or changed file")


def _tree_inventory_fd(directory_fd: int, prefix: str = "") -> dict[str, tuple[str, int]]:
    """Inventory a tree through already-pinned directory handles only."""
    result: dict[str, tuple[str, int]] = {}
    for name in sorted(os.listdir(directory_fd)):
        if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree contains a non-canonical entry")
        relative = f"{prefix}/{name}" if prefix else name
        try:
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree entry changed during handle-relative inventory") from exc
        identity = {"device": int(named.st_dev), "inode": int(named.st_ino)}
        if stat.S_ISDIR(named.st_mode):
            try:
                child_fd = os.open(name, _posix_directory_flags(), dir_fd=directory_fd)
            except OSError as exc:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree directory could not be opened without following links") from exc
            try:
                if _fd_directory_identity(child_fd) != identity:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree directory changed during handle-relative inventory")
                result.update(_tree_inventory_fd(child_fd, relative))
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(named.st_mode):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree contains a symlink or special file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            file_fd = os.open(name, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree file could not be opened without following links") from exc
        try:
            before = os.fstat(file_fd)
            if (not stat.S_ISREG(before.st_mode)
                    or {"device": int(before.st_dev), "inode": int(before.st_ino)} != identity):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree file changed during handle-relative inventory")
            digest = hashlib.sha256()
            total = 0
            while True:
                block = os.read(file_fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                total += len(block)
            after = os.fstat(file_fd)
            if ({"device": int(after.st_dev), "inode": int(after.st_ino)} != identity
                    or int(after.st_size) != total):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree file changed while being hashed")
            result[relative] = (digest.hexdigest(), total)
        finally:
            os.close(file_fd)
    return result


def _before_restore_quarantine_delete(root: Path, relative: str, quarantine_leaf: str) -> None:
    """Narrow fault-injection seam for deterministic restore-cleanup race tests."""


def _before_restore_quarantine_rmdir(parent_fd: int, quarantine_leaf: str) -> None:
    """Narrow fault-injection seam for deterministic restore-cleanup race tests."""


def _after_restore_quarantine_move(root: Path, relative: str, quarantine_leaf: str) -> None:
    """Fault seam after the durable PREPARED rename but before phase advance."""


def _after_restore_quarantine_child_removed(directory_fd: int, relative: str) -> None:
    """Fault seam for restartable partial removal of a quarantined tree."""


def _after_restore_quarantine_rmdir(parent_fd: int, quarantine_leaf: str) -> None:
    """Fault seam after the root quarantine is gone and before DONE is journaled."""


def _remove_quarantined_tree_contents(directory_fd: int, *, prefix: str,
                                      expected_files: Mapping[str, tuple[str, int]] | None) -> None:
    for name in sorted(os.listdir(directory_fd)):
        if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined restore tree contains a non-canonical entry")
        relative = f"{prefix}/{name}" if prefix else name
        try:
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined restore entry changed before removal") from exc
        identity = {"device": int(named.st_dev), "inode": int(named.st_ino)}
        if stat.S_ISDIR(named.st_mode):
            try:
                child_fd = os.open(name, _posix_directory_flags(), dir_fd=directory_fd)
            except OSError as exc:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined directory could not be opened without following links") from exc
            try:
                if _fd_directory_identity(child_fd) != identity:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined directory changed before removal")
                _remove_quarantined_tree_contents(child_fd, prefix=relative, expected_files=expected_files)
            finally:
                os.close(child_fd)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(current.st_mode)
                    or {"device": int(current.st_dev), "inode": int(current.st_ino)} != identity):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined directory name changed before removal")
            try:
                # Descriptor-relative rmdir avoids re-resolving the workspace
                # root. POSIX still has no inode-CAS rmdir for a hostile
                # same-UID swap between this check and the syscall.
                os.rmdir(name, dir_fd=directory_fd)
            except OSError as exc:
                raise StateBackupError("RESTORE_ROLLBACK_INCOMPLETE", "pinned nested restore directory could not be removed") from exc
            _after_restore_quarantine_child_removed(directory_fd, relative)
            continue
        if not stat.S_ISREG(named.st_mode):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined restore tree contains a symlink or special file")
        if expected_files is not None and relative not in expected_files:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined restore tree contains an unlisted file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            file_fd = os.open(name, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined file could not be opened without following links") from exc
        try:
            opened = os.fstat(file_fd)
            if (not stat.S_ISREG(opened.st_mode)
                    or {"device": int(opened.st_dev), "inode": int(opened.st_ino)} != identity):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined file differs from its pinned handle")
            digest = hashlib.sha256()
            total = 0
            while True:
                block = os.read(file_fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                total += len(block)
            after = os.fstat(file_fd)
            if ({"device": int(after.st_dev), "inode": int(after.st_ino)} != identity
                    or int(after.st_size) != total):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined file changed while being verified")
        finally:
            os.close(file_fd)
        if expected_files is not None and expected_files[relative] != (digest.hexdigest(), total):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined file bytes differ from the durable manifest")
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode)
                or {"device": int(current.st_dev), "inode": int(current.st_ino)} != identity):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined file name was substituted before unlink")
        # This is relative to the pinned parent handle. POSIX does not provide
        # unlink-by-inode, so a hostile same-UID swap after this final stat is
        # an OS-level race we cannot claim to eliminate.
        os.unlink(name, dir_fd=directory_fd)
        _after_restore_quarantine_child_removed(directory_fd, relative)


def _remove_exact_directory(root: Path, relative: str, *, expected_identity: Mapping[str, Any],
                            expected_root_identity: Mapping[str, Any],
                            expected_files: Mapping[str, tuple[str, int]] | None = None,
                            allow_subset: bool = False, journal_home: Path,
                            journal: dict[str, Any], journal_root_relative: str = "") -> None:
    if os.name == "nt":
        # The current Windows publisher can pin and move a directory handle,
        # but the installed helper does not expose safe recursive deletion by
        # that exact handle. Keep the durable recovery fence instead of
        # resolving a pathname for deletion.
        raise StateBackupError("RESTORE_CLEANUP_UNAVAILABLE",
                               "Windows exact-handle recursive directory cleanup is unavailable; recovery remains fenced")
    expected = {"device": expected_identity.get("device"), "inode": expected_identity.get("inode")}
    root_expected = {"device": expected_root_identity.get("device"),
                     "inode": expected_root_identity.get("inode")}
    parts = _safe_relative_parts(relative)
    cleanup_items = journal.setdefault("directory_cleanup", [])
    if not isinstance(cleanup_items, list):
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "directory cleanup journal field is malformed")
    item = next((entry for entry in cleanup_items
                 if isinstance(entry, dict) and entry.get("root_relative") == journal_root_relative
                 and entry.get("relative") == relative and entry.get("identity") == expected), None)
    completed = item is not None and item.get("phase") == "DONE"
    if item is not None and item.get("phase") not in {"PREPARED", "QUARANTINED", "REMOVE_INTENT", "DONE"}:
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "restore directory cleanup has an unknown phase")
    if item is not None:
        quarantine_value = item.get("quarantine_leaf")
        if (item.get("root_identity") != root_expected or item.get("leaf") != parts[-1]
                or item.get("parent_relative") != "/".join(parts[:-1])
                or not isinstance(quarantine_value, str)
                or not re.fullmatch(r"\.comsol-restore-cleanup-[0-9a-f]{32}", quarantine_value)
                or type(item.get("allow_subset")) is not bool
                or item.get("allow_subset") != bool(allow_subset)
                or _journal_cleanup_files(item.get("expected_files")) != expected_files):
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "directory cleanup identity or manifest changed")
    resuming_quarantine = item is not None and item.get("phase") in {"QUARANTINED", "REMOVE_INTENT"}

    descriptors: list[int] = []
    links: list[tuple[int, str, dict[str, int]]] = []
    parent_fd = -1
    try:
        descriptors, links, actual_root = _open_posix_directory_chain(
            root, expected_identity=root_expected)
        if actual_root != root_expected:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "restore cleanup root differs from its journaled identity")
        parent_fd = descriptors[-1]
        for component in parts[:-1]:
            child_fd = os.open(component, _posix_directory_flags(), dir_fd=parent_fd)
            child_identity = _fd_directory_identity(child_fd)
            named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or child_identity != {"device": int(named.st_dev), "inode": int(named.st_ino)}):
                os.close(child_fd)
                raise StateBackupError("RESTORE_TARGET_CHANGED", "restore cleanup parent changed during handle-relative open")
            links.append((parent_fd, component, child_identity))
            descriptors.append(child_fd)
            parent_fd = child_fd
        if completed:
            _verify_posix_directory_links(links)
            for leaf in (parts[-1], item["quarantine_leaf"]):
                try:
                    os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise StateBackupError("RESTORE_TARGET_CHANGED", "a new leaf appeared after cleanup was journaled DONE")
            return
        if item is None:
            # Capture a durable cleanup intent before moving any directory.
            named = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            actual = {"device": int(named.st_dev), "inode": int(named.st_ino)}
            if not stat.S_ISDIR(named.st_mode) or actual != expected:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "restore directory identity differs from its durable journal")
            target_fd = os.open(parts[-1], _posix_directory_flags(), dir_fd=parent_fd)
            try:
                if _fd_directory_identity(target_fd) != expected:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "restore directory changed before pinned inventory")
                current_files = _tree_inventory_fd(target_fd)
                if expected_files is not None:
                    if allow_subset:
                        if any(name not in expected_files or expected_files[name] != value
                               for name, value in current_files.items()):
                            raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree differs from the allowed manifest subset")
                    elif current_files != dict(expected_files):
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "restore tree differs from the exact verified manifest")
            finally:
                os.close(target_fd)
            quarantine = f".comsol-restore-cleanup-{uuid4().hex}"
            item = {"root_relative": journal_root_relative, "root_identity": root_expected,
                    "relative": relative, "leaf": parts[-1], "parent_relative": "/".join(parts[:-1]),
                    "quarantine_leaf": quarantine, "identity": expected, "phase": "PREPARED",
                    "expected_files": ({name: [value[0], value[1]] for name, value in expected_files.items()}
                                       if expected_files is not None else None),
                    "allow_subset": bool(allow_subset)}
            cleanup_items.append(item)
            _full_journal_write(journal_home, journal)
        quarantine = item["quarantine_leaf"]
        if item.get("phase") == "PREPARED":
            moved_this_call = False
            try:
                quarantined = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                try:
                    source = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "prepared cleanup source and quarantine are both missing") from exc
                if (not stat.S_ISDIR(source.st_mode)
                        or {"device": int(source.st_dev), "inode": int(source.st_ino)} != expected):
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "prepared restore cleanup source no longer has its journaled identity")
                _renameat_directory_noreplace(parent_fd, parts[-1], parent_fd, quarantine)
                moved_this_call = True
                _after_restore_quarantine_move(root, relative, quarantine)
                quarantined = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            else:
                try:
                    os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "prepared restore cleanup has both source and quarantine entries")
            if (not stat.S_ISDIR(quarantined.st_mode)
                    or {"device": int(quarantined.st_dev), "inode": int(quarantined.st_ino)} != expected):
                # A foreign substitution was moved; restore it only by an
                # exclusive rename and never overwrite an arriving leaf.
                if moved_this_call:
                    try:
                        _renameat_directory_noreplace(parent_fd, quarantine, parent_fd, parts[-1])
                    except Exception:
                        pass
                raise StateBackupError("RESTORE_TARGET_CHANGED", "moved cleanup directory failed exact identity readback")
            item["phase"] = "QUARANTINED"
            _full_journal_write(journal_home, journal)

        if item.get("phase") in {"QUARANTINED", "REMOVE_INTENT"}:
            try:
                moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                moved = None
            try:
                source = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                source = None
            if source is not None:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "a leaf appeared at the original cleanup path")
            if moved is None:
                if item.get("phase") != "REMOVE_INTENT":
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined cleanup disappeared before remove intent")
                _verify_posix_directory_links(links)
                item["phase"] = "DONE"
                _full_journal_write(journal_home, journal)
                return
            if (not stat.S_ISDIR(moved.st_mode)
                    or {"device": int(moved.st_dev), "inode": int(moved.st_ino)} != expected):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantine leaf no longer has its journaled identity")
        else:
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "restore directory cleanup has an unknown phase")

        quarantine_fd = os.open(quarantine, _posix_directory_flags(), dir_fd=parent_fd)
        try:
            if _fd_directory_identity(quarantine_fd) != expected:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined directory no longer has its journaled identity")
            raw_expected = item.get("expected_files")
            if raw_expected is None:
                files_for_delete = None
            elif isinstance(raw_expected, dict):
                files_for_delete = {name: (value[0], value[1]) for name, value in raw_expected.items()
                                    if isinstance(name, str) and isinstance(value, list) and len(value) == 2}
                if len(files_for_delete) != len(raw_expected):
                    raise StateBackupError("RESTORE_JOURNAL_INVALID", "cleanup file inventory is malformed")
            else:
                raise StateBackupError("RESTORE_JOURNAL_INVALID", "cleanup file inventory is malformed")
            current_files = _tree_inventory_fd(quarantine_fd)
            if files_for_delete is not None:
                if item.get("allow_subset") or resuming_quarantine:
                    if any(name not in files_for_delete or files_for_delete[name] != value
                           for name, value in current_files.items()):
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined tree contains an unlisted or changed file")
                elif current_files != files_for_delete:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined tree differs from the exact manifest")
            _before_restore_quarantine_delete(root, relative, quarantine)
            _verify_posix_directory_links(links)
            moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(moved.st_mode)
                    or {"device": int(moved.st_dev), "inode": int(moved.st_ino)} != expected):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined directory leaf was substituted before cleanup")
            # Re-check bytes and names after the deterministic race seam.
            current_files = _tree_inventory_fd(quarantine_fd)
            if files_for_delete is not None:
                if item.get("allow_subset") or resuming_quarantine:
                    changed = any(name not in files_for_delete or files_for_delete[name] != value
                                  for name, value in current_files.items())
                else:
                    changed = current_files != files_for_delete
                if changed:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "quarantined tree changed before pinned cleanup")
            _remove_quarantined_tree_contents(quarantine_fd, prefix="", expected_files=files_for_delete)
        finally:
            os.close(quarantine_fd)
        _before_restore_quarantine_rmdir(parent_fd, quarantine)
        _verify_posix_directory_links(links)
        moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(moved.st_mode)
                or {"device": int(moved.st_dev), "inode": int(moved.st_ino)} != expected):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "empty cleanup quarantine was substituted before directory removal")
        if item.get("phase") != "REMOVE_INTENT":
            item["phase"] = "REMOVE_INTENT"
            _full_journal_write(journal_home, journal)
        try:
            # POSIX has no unlink-by-directory-handle operation. The pinned
            # parent plus no-replace quarantine prevents re-resolving the
            # workspace path; a hostile same-UID process can still race the
            # final name check and rmdir, so this is not an inode-CAS claim.
            os.rmdir(quarantine, dir_fd=parent_fd)
        except OSError as exc:
            raise StateBackupError("RESTORE_ROLLBACK_INCOMPLETE", "pinned restore cleanup quarantine could not be removed") from exc
        _after_restore_quarantine_rmdir(parent_fd, quarantine)
        _verify_posix_directory_links(links)
        item["phase"] = "DONE"
        _full_journal_write(journal_home, journal)
    except StateBackupError:
        raise
    except OSError as exc:
        raise StateBackupError("RESTORE_ROLLBACK_INCOMPLETE", "handle-relative restore cleanup failed; keep the recovery journal fenced") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _directory_identity(path: Path) -> dict[str, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StateBackupError("PATH_UNVERIFIED", "workspace root identity could not be read") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StateBackupError("DIRECTORY_REQUIRED", "workspace root must be a real directory")
    return {"device": int(info.st_dev), "inode": int(info.st_ino)}


def _assert_directory_identity(path: Path, expected: Mapping[str, Any]) -> None:
    actual = _directory_identity(path)
    if actual != {"device": expected.get("device"), "inode": expected.get("inode")}:
        raise StateBackupError("RESTORE_TARGET_CHANGED", "workspace root identity changed during restore")


def _restore_empty_home(home: Path) -> None:
    allowed = {CONTROL_LOCK_NAME}
    for item in home.iterdir():
        if item.name not in allowed or item.is_symlink() or not item.is_file():
            raise StateBackupError("RESTORE_TARGET_NOT_EMPTY", "full restore requires a new empty control home")


def _restore_empty_workspace(root: Path) -> None:
    if any(root.iterdir()):
        raise StateBackupError("RESTORE_TARGET_NOT_EMPTY", "full restore requires a new empty authorized workspace root")


def _rebind_full_database(database: Path, manifest: Mapping[str, Any], workspace_root: Path,
                          *, target_host_identity: str) -> dict[str, Any]:
    from ._artifact_store import _hash_regular_artifact, project_root_identity

    projects = {item["project_id"]: item for item in manifest["projects"]}
    records_by_key = {registration["artifact_key"]: (file, registration)
                      for file in manifest["files"] for registration in file["registrations"]}
    audit_projects = []
    connection = sqlite3.connect(database, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        for project_id, item in projects.items():
            workspace = workspace_root / item["target_workspace_relative"]
            workspace_identity = project_root_identity(workspace)
            row = connection.execute(
                "SELECT workspace,schema_version,revision,record_json FROM projects WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if row is None or row["workspace"] == str(workspace):
                raise StateBackupError("PROJECT_IDENTITY_MISMATCH", "restored project does not match the source database")
            try:
                record = json.loads(row["record_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StateBackupError("PROJECT_STATE_UNKNOWN", "restored project record is malformed") from exc
            old_revision = row["revision"]
            if (record.get("project_id") != project_id or record.get("workspace") != row["workspace"]
                    or record.get("revision") != old_revision or record.get("schema_version") != row["schema_version"]
                    or old_revision != item["source_revision"]):
                raise StateBackupError("PROJECT_STATE_UNKNOWN", "restored ProjectAuthority row differs from its archive")
            new_revision = old_revision + 1
            record["workspace"] = str(workspace)
            record["revision"] = new_revision
            record["updated_at"] = _utc_now()
            connection.execute(
                "UPDATE projects SET workspace=?,revision=?,record_json=?,updated_at=? WHERE project_id=?",
                (str(workspace), new_revision, json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False),
                 record["updated_at"], project_id),
            )
            audit_projects.append({"project_id": project_id,
                                   "source_workspace_path_sha256": item["source_workspace_path_sha256"],
                                   "source_project_root_identity": item["source_project_root_identity"],
                                   "source_revision": old_revision, "restored_revision": new_revision,
                                   "restored_workspace_path_sha256": hashlib.sha256(str(workspace).encode("utf-8")).hexdigest(),
                                   "restored_project_root_identity": workspace_identity})
        for artifact_key, (file, registration) in records_by_key.items():
            row = connection.execute("SELECT metadata FROM artifacts WHERE artifact_id=?", (artifact_key,)).fetchone()
            if row is None:
                raise StateBackupError("ARTIFACT_NOT_FOUND", "archive references a missing artifact registration")
            try:
                record = json.loads(row["metadata"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StateBackupError("ARTIFACT_IDENTITY_UNKNOWN", "artifact registration is malformed in the restored ledger") from exc
            if (not isinstance(record, dict) or record.get("sha256") != file["sha256"]
                    or record.get("path") is None or record.get("schema_version") != registration["schema_version"]):
                raise StateBackupError("ARTIFACT_IDENTITY_MISMATCH", "artifact registration differs from its archive inventory")
            project_id = file["project_id"]
            relative = file["relative_path"]
            restored_path = workspace_root / projects[project_id]["target_workspace_relative"] / relative
            digest, file_identity = _hash_regular_artifact(restored_path)
            if digest != file["sha256"] or file_identity["size"] != file["size_bytes"]:
                raise StateBackupError("ARTIFACT_HASH_MISMATCH", "restored registered file failed SHA/size readback")
            record["path"] = relative
            if record.get("schema_version") == 2:
                record["project_root_identity"] = project_root_identity(workspace_root / projects[project_id]["target_workspace_relative"])
                record["host_identity"] = target_host_identity
                record["engine_host_identity"] = target_host_identity
            if "file_identity" in record:
                record["file_identity"] = file_identity
            if "size" in record:
                record["size"] = file_identity["size"]
            connection.execute("UPDATE artifacts SET metadata=? WHERE artifact_id=?",
                               (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False), artifact_key))
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return {"schema": "comsol-mcp.full-state-rebind-audit/1", "performed_utc": _utc_now(),
            "target_workspace_root_path_sha256": hashlib.sha256(str(workspace_root).encode("utf-8")).hexdigest(),
            "target_host_identity": target_host_identity,
            "engine_host_binding": "local-host identity; runtime attachment and model reload remain required",
            "projects": audit_projects,
            "legacy_model_refs": "preserved as historical identity; Worker/session binding was not revived"}


def _validate_rebound_database(database: Path, manifest: Mapping[str, Any], workspace_root: Path,
                               target_host_identity: str) -> None:
    from ._artifact_store import resolve_registered_artifact
    from ._operation_store import OperationStore
    from ._project_authority import ProjectAuthority

    store = OperationStore(database)
    try:
        authority = ProjectAuthority(store, workspace_root=workspace_root,
                                     permission_provider=lambda: set(), grant_ceiling=set())
        for project in manifest["projects"]:
            record = authority.get_project(project["project_id"])
            if record.get("revision") != project["source_revision"] + 1:
                raise StateBackupError("PROJECT_REVISION_CONFLICT", "restored project rebind revision failed readback")
        for file in manifest["files"]:
            project_id = file["project_id"]
            for registration in file["registrations"]:
                if registration["schema_version"] != 2:
                    continue
                result = resolve_registered_artifact(
                    workspace_root / next(item["target_workspace_relative"] for item in manifest["projects"]
                                          if item["project_id"] == project_id),
                    store, registration["artifact_key"], project_id=project_id,
                    current_host_identity=target_host_identity,
                    current_engine_host_identity=target_host_identity,
                    current_server_instance_id="",
                )
                if result["sha256"] != file["sha256"] or result["size"] != file["size_bytes"]:
                    raise StateBackupError("ARTIFACT_HASH_MISMATCH", "ArtifactStore did not resolve the restored file identity")
    finally:
        store.close()


def _read_full_restore_journal(home: Path) -> dict[str, Any]:
    path = home / ACTIVE_FULL_RESTORE_JOURNAL
    _reject_symlink_components(path, "full restore journal")
    journal = _read_full_document(path, "full restore journal", maximum_bytes=1024 * 1024)
    if journal.get("schema") != FULL_RESTORE_JOURNAL_SCHEMA:
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "full restore journal has an unsupported schema")
    recovery_name = journal.get("recovery_directory")
    if (not isinstance(recovery_name, str) or Path(recovery_name).name != recovery_name
            or not recovery_name.startswith("state-full-recovery-")):
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "full restore journal recovery location is invalid")
    return journal


def _archive_full_restore_journal(home: Path, recovery: Path, journal: dict[str, Any], status: str) -> None:
    journal["phase"] = status
    journal["finished_utc"] = _utc_now()
    archived = recovery / "full-restore-journal.json"
    _write_new_json(archived, journal)
    (home / ACTIVE_FULL_RESTORE_JOURNAL).unlink()
    _fsync_directory(home)


def _journal_identity(value: Any, field: str) -> dict[str, int]:
    if (not isinstance(value, Mapping)
            or type(value.get("device")) is not int or value["device"] < 0
            or type(value.get("inode")) is not int or value["inode"] <= 0):
        raise StateBackupError("RESTORE_JOURNAL_INVALID", f"{field} identity is malformed")
    return {"device": value["device"], "inode": value["inode"]}


def _journal_cleanup_files(value: Any) -> dict[str, tuple[str, int]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "cleanup file inventory is malformed")
    result: dict[str, tuple[str, int]] = {}
    for relative, item in value.items():
        parts = _safe_relative_parts(relative) if isinstance(relative, str) else ()
        if (not parts or not isinstance(item, list) or len(item) != 2
                or not isinstance(item[0], str) or not re.fullmatch(r"[0-9a-f]{64}", item[0])
                or type(item[1]) is not int or item[1] < 0):
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "cleanup file inventory entry is malformed")
        result[relative] = (item[0], item[1])
    return result


def _resume_pending_directory_cleanup(home: Path, journal: dict[str, Any],
                                      workspace_root: Path) -> None:
    entries = journal.get("directory_cleanup", [])
    if not isinstance(entries, list):
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "directory cleanup journal field is malformed")
    workspace_identity = _journal_identity(journal.get("workspace_root_identity"), "workspace root")
    stage_leaf = journal.get("workspace_stage_leaf")
    stage_identity_value = journal.get("workspace_stage_identity")
    stage_identity = (_journal_identity(stage_identity_value, "staging root")
                      if stage_identity_value is not None else None)
    if workspace_identity != _directory_identity(workspace_root):
        raise StateBackupError("RESTORE_TARGET_CHANGED", "workspace root differs from its journaled identity")

    for item in entries:
        if not isinstance(item, dict):
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "directory cleanup journal entry is malformed")
        phase = item.get("phase")
        if phase not in {"PREPARED", "QUARANTINED", "REMOVE_INTENT", "DONE"}:
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "directory cleanup phase is invalid")
        if phase == "DONE":
            continue
        root_relative = item.get("root_relative")
        if root_relative == "":
            cleanup_root = workspace_root
            root_identity = workspace_identity
        elif isinstance(stage_leaf, str) and root_relative == stage_leaf:
            if stage_identity is None:
                raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending cleanup lacks its staging-root identity")
            cleanup_root = workspace_root / stage_leaf
            if _directory_identity_beneath(workspace_root, stage_leaf,
                                           expected_root_identity=workspace_identity) != stage_identity:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "pending cleanup staging root changed identity")
            root_identity = stage_identity
        else:
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending cleanup has an unrecognized root")
        item_root_identity = _journal_identity(item.get("root_identity"), "cleanup root")
        if item_root_identity != root_identity:
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending cleanup root identity conflicts with restore journal")
        relative = item.get("relative")
        parts = _safe_relative_parts(relative) if isinstance(relative, str) else ()
        if (not parts or item.get("leaf") != parts[-1]
                or item.get("parent_relative") != "/".join(parts[:-1])):
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending cleanup path is malformed")
        quarantine = item.get("quarantine_leaf")
        if (not isinstance(quarantine, str)
                or not re.fullmatch(r"\.comsol-restore-cleanup-[0-9a-f]{32}", quarantine)):
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending cleanup quarantine name is malformed")
        identity = _journal_identity(item.get("identity"), "cleanup target")
        expected_files = _journal_cleanup_files(item.get("expected_files"))
        if type(item.get("allow_subset")) is not bool:
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "pending cleanup subset policy is malformed")
        _remove_exact_directory(
            cleanup_root, relative, expected_identity=identity,
            expected_root_identity=root_identity, expected_files=expected_files,
            allow_subset=item["allow_subset"], journal_home=home, journal=journal,
            journal_root_relative=root_relative,
        )


def _rollback_full_restore(home: Path, journal: dict[str, Any]) -> Path:
    recovery = home / journal["recovery_directory"]
    _existing_directory(recovery, "full restore recovery directory")
    workspace_root = _existing_directory(journal["workspace_root"], "restore workspace root")
    _assert_directory_identity(workspace_root, journal["workspace_root_identity"])
    # Resume durable cleanup intents before checking whether the original leaf
    # exists. A successful quarantine rename makes that leaf disappear, but
    # the journal must still finish the exact owned cleanup after a restart.
    _resume_pending_directory_cleanup(home, journal, workspace_root)
    state_path = recovery / "restore-state.json"
    if not state_path.exists():
        # Before state publication, the journal guarantees that no live path
        # was touched. Only clear it when both new targets are still empty.
        _restore_empty_workspace(workspace_root)
        db_path = home / DATABASE_NAME
        if db_path.exists() or db_path.is_symlink():
            raise StateBackupError("RESTORE_TARGET_CHANGED", "control database appeared before restore state was journaled")
        _archive_full_restore_journal(home, recovery, journal, "ROLLED_BACK_BEFORE_MUTATION")
        return recovery
    state = _read_full_document(state_path, "full restore state")
    manifest = state.get("manifest")
    if not isinstance(manifest, Mapping) or manifest.get("schema") != FULL_BACKUP_SCHEMA:
        raise StateBackupError("RESTORE_JOURNAL_INVALID", "full restore state has no verified manifest")
    workspace_root = _existing_directory(journal["workspace_root"], "restore workspace root")
    _assert_directory_identity(workspace_root, journal["workspace_root_identity"])
    stage = workspace_root / journal["workspace_stage_leaf"]
    if stage.is_symlink() or (stage.exists() and not stage.is_dir()):
        raise StateBackupError("RESTORE_TARGET_CHANGED", "restore staging root changed to an unsafe type")
    workspace_root_identity = journal["workspace_root_identity"]
    stage_expected = journal.get("workspace_stage_identity")
    staged_projects_expected = journal.get("staged_projects_parent_identity")
    installed_projects_expected = journal.get("installed_projects_parent_identity")
    project_identities = journal.get("staged_project_directory_identities")
    installed_project_identities = journal.get("installed_project_directory_identities")
    if stage.exists():
        if not isinstance(stage_expected, Mapping):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "staging directory exists without a durable identity")
        if _directory_identity_beneath(workspace_root, journal["workspace_stage_leaf"],
                                       expected_root_identity=workspace_root_identity) != {
                "device": stage_expected.get("device"), "inode": stage_expected.get("inode")}:
            raise StateBackupError("RESTORE_TARGET_CHANGED", "staging directory was replaced after restore began")

    projects_path = workspace_root / "projects"
    if projects_path.exists() or projects_path.is_symlink():
        expected_files = _expected_projects_files(manifest)
        if not isinstance(installed_projects_expected, Mapping):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "installed project directory has no durable identity")
        expected_children = installed_project_identities
        if not isinstance(expected_children, Mapping) or len(expected_children) != len(manifest["projects"]):
            raise StateBackupError("RESTORE_JOURNAL_INVALID", "installed project directory identities are incomplete")
        for project in manifest["projects"]:
            relative = str(project["target_workspace_relative"])
            child_expected = expected_children.get(relative)
            if not isinstance(child_expected, Mapping):
                raise StateBackupError("RESTORE_JOURNAL_INVALID", "installed project directory identity is missing")
            if _directory_identity_beneath(workspace_root, relative,
                                           expected_root_identity=workspace_root_identity) != {
                    "device": child_expected.get("device"), "inode": child_expected.get("inode")}:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "installed project directory was replaced")
        _remove_exact_directory(workspace_root, "projects", expected_identity=installed_projects_expected,
                                expected_root_identity=workspace_root_identity, expected_files=expected_files,
                                journal_home=home, journal=journal, journal_root_relative="")

    if stage.exists():
        staged_projects = stage / "projects"
        if staged_projects.exists() or staged_projects.is_symlink():
            if not isinstance(staged_projects_expected, Mapping):
                raise StateBackupError("RESTORE_TARGET_CHANGED", "staged project tree has no durable identity")
            if not isinstance(project_identities, Mapping):
                raise StateBackupError("RESTORE_JOURNAL_INVALID", "staged project identities are malformed")
            for project in manifest["projects"]:
                relative = str(project["target_workspace_relative"])
                project_path = stage / relative
                if project_path.exists() or project_path.is_symlink():
                    child_expected = project_identities.get(relative)
                    if not isinstance(child_expected, Mapping):
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "staged project directory lacks a durable identity")
                    if _directory_identity_beneath(stage, relative,
                            expected_root_identity=stage_expected) != {
                            "device": child_expected.get("device"), "inode": child_expected.get("inode")}:
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "staged project directory was replaced")
            _remove_exact_directory(stage, "projects", expected_identity=staged_projects_expected,
                                    expected_root_identity=stage_expected,
                                    expected_files=_expected_projects_files(manifest), allow_subset=True,
                                    journal_home=home, journal=journal,
                                    journal_root_relative=journal["workspace_stage_leaf"])
        _remove_exact_directory(workspace_root, journal["workspace_stage_leaf"],
                                expected_identity=stage_expected,
                                expected_root_identity=workspace_root_identity, expected_files={},
                                journal_home=home, journal=journal, journal_root_relative="")

    db_path = home / DATABASE_NAME
    expected_db = journal.get("installed_database_sha256")
    if db_path.exists() or db_path.is_symlink():
        if (not isinstance(expected_db, str) or db_path.is_symlink() or not db_path.is_file()
                or _sha256(db_path) != expected_db):
            raise StateBackupError("RESTORE_TARGET_CHANGED", "installed control database changed before rollback")
        db_path.unlink()
    for suffix in _SIDECAR_SUFFIXES:
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise StateBackupError("RESTORE_TARGET_CHANGED", "unexpected SQLite sidecar blocks full restore rollback")
    _restore_empty_workspace(workspace_root)
    staged_db = recovery / "operations.sqlite3"
    if staged_db.exists():
        expected_stage = journal.get("staged_database_sha256")
        if staged_db.is_symlink() or not staged_db.is_file() or not isinstance(expected_stage, str) or _sha256(staged_db) != expected_stage:
            raise StateBackupError("RECOVERY_HASH_MISMATCH", "staged control database changed before recovery")
        staged_db.unlink()
    _archive_full_restore_journal(home, recovery, journal, "ROLLED_BACK_TO_EMPTY_TARGET")
    return recovery


def restore_full_backup(
    home: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    archive_value: str | os.PathLike[str],
    manifest_value: str | os.PathLike[str],
    *, confirm: bool,
) -> dict[str, Any]:
    """Restore a full state archive into a new empty control/workspace target.

    Registered project paths and host identity are rebound explicitly. The
    durable operations/jobs/events remain unchanged; runtime ModelRefs are not
    attached or replayed by this offline restore command.
    """
    if confirm is not True:
        raise StateBackupError("CONFIRM_REQUIRED", "full restore requires the explicit --confirm flag")
    normalized_home = _existing_directory(home, "control home")
    target_workspace = _existing_directory(workspace_root, "target workspace root")
    if target_workspace == Path(target_workspace.anchor):
        raise StateBackupError("RESTORE_TARGET_INVALID", "target workspace root cannot be a filesystem root")
    archive_path = _existing_file(archive_value, "full backup archive")
    manifest_path = _existing_file(manifest_value, "full backup manifest")
    if (_is_within(archive_path, normalized_home) or _is_within(manifest_path, normalized_home)
            or _is_within(archive_path, target_workspace) or _is_within(manifest_path, target_workspace)):
        raise StateBackupError("BACKUP_IN_RESTORE_TARGET", "full backup inputs must be outside restore targets")
    try:
        if _is_within(normalized_home, target_workspace) or _is_within(target_workspace, normalized_home):
            raise StateBackupError("RESTORE_TARGET_ALIAS", "control home and workspace root must be disjoint")
    except ValueError:
        pass
    from ._artifact_store import _assert_pinned_artifact_stable, _open_pinned_artifact, local_artifact_host_identity

    with _locked_home(normalized_home) as locked:
        assert_no_pending_restore(locked)
        _restore_empty_home(locked)
        _restore_empty_workspace(target_workspace)
        workspace_identity = _directory_identity(target_workspace)
        recovery = locked / f"state-full-recovery-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
        recovery_identity = _create_directory_tree(locked, recovery.name,
                                                   expected_root_identity=_directory_identity(locked))
        operation_id = str(uuid4())
        stage_leaf = f".comsol-full-restore-stage-{operation_id.replace('-', '')}"
        journal = {"schema": FULL_RESTORE_JOURNAL_SCHEMA, "operation_id": operation_id,
                   "started_utc": _utc_now(), "phase": "PREPARING", "recovery_directory": recovery.name,
                   "recovery_directory_identity": recovery_identity,
                   "workspace_root": str(target_workspace), "workspace_root_identity": workspace_identity,
                   "workspace_root_path_sha256": hashlib.sha256(str(target_workspace).encode("utf-8")).hexdigest(),
                   "workspace_stage_leaf": stage_leaf, "workspace_stage_identity": None,
                   "staged_projects_parent_identity": None, "installed_projects_parent_identity": None,
                   "staged_project_directory_identities": {}, "installed_project_directory_identities": {},
                   "installed_database_sha256": None, "staged_database_sha256": None}
        _full_journal_write(locked, journal)
        try:
            handle, archive, manifest, infos, archive_before = _open_and_verify_full_archive(archive_path, manifest_path)
            try:
                _assert_pinned_artifact_stable(archive_path, archive_before, os.fstat(handle.fileno()))
                state_path = recovery / "restore-state.json"
                _write_new_json(state_path, {"schema": "comsol-mcp.full-state-restore-state/1", "manifest": manifest})
                stage = target_workspace / stage_leaf
                journal["workspace_stage_identity"] = _create_directory_tree(
                    target_workspace, stage_leaf, expected_root_identity=workspace_identity)
                journal["phase"] = "STAGING"
                _full_journal_write(locked, journal)
                by_member = {info.name: info for info in infos.values()}
                db_info = by_member[manifest["database"]["member"]]
                staged_db = recovery / DATABASE_NAME
                _write_tar_member_exclusive(archive, db_info, staged_db,
                                            trusted_root=recovery,
                                            expected_root_identity=recovery_identity,
                                            expected_sha256=manifest["database"]["sha256"],
                                            expected_size=manifest["database"]["size_bytes"])
                journal["staged_database_sha256"] = _sha256(staged_db)
                if manifest["projects"]:
                    journal["staged_projects_parent_identity"] = _create_directory_tree(
                        stage, "projects", expected_root_identity=journal["workspace_stage_identity"])
                    _full_journal_write(locked, journal)
                for project in manifest["projects"]:
                    relative = str(project["target_workspace_relative"])
                    identity = _create_directory_tree(stage, relative,
                        expected_root_identity=journal["workspace_stage_identity"], existing_components=1)
                    journal["staged_project_directory_identities"][relative] = identity
                    _full_journal_write(locked, journal)
                for file in manifest["files"]:
                    info = by_member[file["member"]]
                    project = next(item for item in manifest["projects"] if item["project_id"] == file["project_id"])
                    project_relative = str(project["target_workspace_relative"])
                    file_parent = str(Path(str(file["relative_path"])).parent).replace("\\", "/")
                    parent_parts = () if file_parent in {"", "."} else tuple(file_parent.split("/"))
                    project_parts = tuple(project_relative.split("/"))
                    if parent_parts:
                        _create_directory_tree(stage,
                            "/".join((*project_parts, *parent_parts)),
                            expected_root_identity=journal["workspace_stage_identity"],
                            existing_components=len(project_parts))
                    target = stage / project["target_workspace_relative"] / file["relative_path"]
                    _write_tar_member_exclusive(archive, info, target,
                                                trusted_root=stage,
                                                expected_root_identity=journal["workspace_stage_identity"],
                                                expected_sha256=file["sha256"], expected_size=file["size_bytes"])
                if manifest["projects"]:
                    journal["staged_projects_parent_identity"] = _directory_identity_beneath(
                        stage, "projects", expected_root_identity=journal["workspace_stage_identity"])
                    for project in manifest["projects"]:
                        relative = str(project["target_workspace_relative"])
                        journal["staged_project_directory_identities"][relative] = _directory_identity_beneath(
                            stage, relative, expected_root_identity=journal["workspace_stage_identity"])
                    _full_journal_write(locked, journal)
                _verify_exact_tree(stage, _expected_stage_files(manifest))
                _integrity_connection = _readonly_connection(staged_db)
                try:
                    _integrity_check(_integrity_connection)
                    schema_version = _inspect_schema(_integrity_connection)
                    if schema_version != manifest["database"]["schema_version"]:
                        raise StateBackupError("RESTORE_SCHEMA_MISMATCH", "full backup database schema differs from its manifest")
                    if _history_fingerprint(_integrity_connection) != manifest["durable_history_sha256"]:
                        raise StateBackupError("HISTORY_HASH_MISMATCH", "durable operation/job history differs from its backup receipt")
                finally:
                    _integrity_connection.close()
                journal["phase"] = "VERIFIED_STAGING"
                _full_journal_write(locked, journal)
            finally:
                archive.close()
                _assert_pinned_artifact_stable(archive_path, os.fstat(handle.fileno()), os.fstat(handle.fileno()))
                handle.close()

            journal["phase"] = "PROJECTS_INSTALLING"
            if manifest["projects"]:
                journal["staged_projects_parent_identity"] = _directory_identity_beneath(
                    stage, "projects", expected_root_identity=journal["workspace_stage_identity"])
                journal["installed_projects_parent_identity"] = dict(journal["staged_projects_parent_identity"])
                journal["installed_project_directory_identities"] = dict(
                    journal["staged_project_directory_identities"])
            _full_journal_write(locked, journal)
            if manifest["projects"]:
                _rename_directory_noreplace(
                    target_workspace, f"{stage_leaf}/projects", target_workspace, "projects",
                    source_identity=journal["staged_projects_parent_identity"],
                    source_root_identity=workspace_identity,
                    destination_root_identity=workspace_identity)
                if _directory_identity_beneath(target_workspace, "projects",
                        expected_root_identity=workspace_identity) != journal["installed_projects_parent_identity"]:
                    raise StateBackupError("RESTORE_TARGET_CHANGED", "installed project root failed identity readback")
                for project in manifest["projects"]:
                    relative = str(project["target_workspace_relative"])
                    if _directory_identity_beneath(target_workspace, relative,
                            expected_root_identity=workspace_identity) != journal["installed_project_directory_identities"][relative]:
                        raise StateBackupError("RESTORE_TARGET_CHANGED", "installed project directory failed identity readback")
                _verify_exact_tree(target_workspace / "projects", _expected_projects_files(manifest))
            journal["phase"] = "PROJECTS_INSTALLED"
            _full_journal_write(locked, journal)

            target_host = local_artifact_host_identity()
            audit = _rebind_full_database(staged_db, manifest, target_workspace, target_host_identity=target_host)
            _write_new_json(recovery / "rebind-audit.json", audit)
            journal["staged_database_sha256"] = _sha256(staged_db)
            journal["phase"] = "DATABASE_REBOUND"
            _full_journal_write(locked, journal)
            _finalize_database(staged_db, migrate=True)
            journal["staged_database_sha256"] = _sha256(staged_db)
            journal["phase"] = "DATABASE_FINALIZED"
            _full_journal_write(locked, journal)
            rebound_connection = _readonly_connection(staged_db)
            try:
                if _history_fingerprint(rebound_connection) != manifest["durable_history_sha256"]:
                    raise StateBackupError("HISTORY_HASH_MISMATCH", "ledger history changed during project rebind")
            finally:
                rebound_connection.close()
            _validate_rebound_database(staged_db, manifest, target_workspace, target_host)
            journal["installed_database_sha256"] = _sha256(staged_db)
            journal["staged_database_sha256"] = journal["installed_database_sha256"]
            journal["phase"] = "DATABASE_INSTALL_PENDING"
            _full_journal_write(locked, journal)
            live_db = locked / DATABASE_NAME
            try:
                os.link(staged_db, live_db)
            except FileExistsError as exc:
                raise StateBackupError("RESTORE_TARGET_CHANGED", "control database destination appeared during restore") from exc
            except OSError as exc:
                raise StateBackupError("RESTORE_INSTALL_FAILED", "restored database could not be installed atomically") from exc
            _fsync_directory(locked)
            journal["phase"] = "DATABASE_INSTALLED"
            _full_journal_write(locked, journal)
            live = _readonly_connection(live_db)
            try:
                _integrity_check(live)
                if _inspect_schema(live, allow_legacy_v1=False) != manifest["database"]["schema_version"]:
                    raise StateBackupError("RESTORE_SCHEMA_MISMATCH", "installed full restore database failed schema readback")
                if _history_fingerprint(live) != manifest["durable_history_sha256"]:
                    raise StateBackupError("HISTORY_HASH_MISMATCH", "installed full restore changed durable history")
            finally:
                live.close()
            _remove_exact_directory(target_workspace, stage_leaf,
                                    expected_identity=journal["workspace_stage_identity"],
                                    expected_root_identity=workspace_identity, expected_files={},
                                    journal_home=locked, journal=journal, journal_root_relative="")
            _archive_full_restore_journal(locked, recovery, journal, "RESTORED_AND_REBOUND")
            return {"success": True, "operation": "full_restore", "database": DATABASE_NAME,
                    "database_sha256": _sha256(live_db), "integrity_check": "ok",
                    "durable_history_sha256": manifest["durable_history_sha256"],
                    "project_count": len(manifest["projects"]), "file_count": len(manifest["files"]),
                    "rebind_audit_path": str(recovery / "rebind-audit.json"),
                    "recovery_directory": str(recovery), "daemon_started": False,
                    "worker_or_model_bindings_revived": False, "operation_replay_performed": False,
                    "next_step": "configure the restored workspace root, attach a verified runtime, then explicitly load registered models"}
        except Exception as exc:
            try:
                current = _read_full_restore_journal(locked)
                recovery = _rollback_full_restore(locked, current)
            except Exception as rollback_exc:
                if isinstance(rollback_exc, StateBackupError) and rollback_exc.code == "RESTORE_ROLLBACK_INCOMPLETE":
                    raise rollback_exc from exc
                try:
                    current = _read_full_restore_journal(locked)
                    current["phase"] = "ROLLBACK_FAILED_OR_INCOMPLETE"
                    current["last_error_type"] = type(rollback_exc).__name__
                    current["last_error_code"] = getattr(rollback_exc, "code", None)
                    current["last_error_message"] = str(rollback_exc)[:500]
                    _full_journal_write(locked, current)
                except Exception:
                    pass
                raise StateBackupError("RESTORE_ROLLBACK_INCOMPLETE",
                                       "full restore failed and exact rollback could not be confirmed; keep the daemon stopped and preserve the recovery directory",
                                       recovery_path=str(recovery)) from exc
            if isinstance(exc, StateBackupError):
                raise
            raise StateBackupError("FULL_RESTORE_FAILED", f"full restore failed ({type(exc).__name__}); target was returned to empty state",
                                   recovery_path=str(recovery)) from exc


def recover_full_restore(home: str | os.PathLike[str], *, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise StateBackupError("CONFIRM_REQUIRED", "full restore recovery requires the explicit --confirm flag")
    with _locked_home(home) as locked:
        path = locked / ACTIVE_FULL_RESTORE_JOURNAL
        _reject_symlink_components(path, "full restore journal")
        if not path.exists():
            raise StateBackupError("NO_PENDING_RESTORE", "no pending full restore journal exists")
        journal = _read_full_restore_journal(locked)
        recovery = _rollback_full_restore(locked, journal)
        return {"success": True, "operation": "full_recover", "recovery_directory": str(recovery),
                "daemon_started": False, "worker_or_model_bindings_revived": False}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comsol-mcp-state", description="Back up or restore the private control state.")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create a new online SQLite backup and hash manifest")
    backup.add_argument("--home", type=Path, required=True, help="exact private control home")
    backup.add_argument("--destination", type=Path, required=True, help="new database file outside the control home")
    restore = commands.add_parser("restore", help="stage and restore a verified backup under the control lock")
    restore.add_argument("--home", type=Path, required=True)
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--confirm", action="store_true", help="confirm replacement of the stopped control ledger")
    recover = commands.add_parser("recover", help="restore the exact prior file set after an interrupted restore")
    recover.add_argument("--home", type=Path, required=True)
    recover.add_argument("--confirm", action="store_true", help="confirm recovery from the pending restore journal")
    full_backup = commands.add_parser("full-backup", help="stream a complete ledger plus registered project files into an archive")
    full_backup.add_argument("--home", type=Path, required=True, help="exact private control home")
    full_backup.add_argument("--destination", type=Path, required=True, help="new archive path outside the control home")
    full_restore = commands.add_parser("full-restore", help="restore full state into a new empty control home and workspace")
    full_restore.add_argument("--home", type=Path, required=True)
    full_restore.add_argument("--workspace-root", type=Path, required=True)
    full_restore.add_argument("--archive", type=Path, required=True)
    full_restore.add_argument("--manifest", type=Path, required=True)
    full_restore.add_argument("--confirm", action="store_true", help="confirm restore into the new empty target")
    full_recover = commands.add_parser("full-recover", help="return an interrupted full restore to its empty target")
    full_recover.add_argument("--home", type=Path, required=True)
    full_recover.add_argument("--confirm", action="store_true", help="confirm rollback of the pending full restore")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.home, args.destination)
        elif args.command == "restore":
            result = restore_backup(args.home, args.backup, args.manifest, confirm=args.confirm)
        elif args.command == "recover":
            result = recover_restore(args.home, confirm=args.confirm)
        elif args.command == "full-backup":
            result = create_full_backup(args.home, args.destination)
        elif args.command == "full-restore":
            result = restore_full_backup(args.home, args.workspace_root, args.archive, args.manifest,
                                         confirm=args.confirm)
        else:
            result = recover_full_restore(args.home, confirm=args.confirm)
    except Exception as exc:
        payload = {"success": False, "error": getattr(exc, "code", "STATE_BACKUP_FAILED"), "message": str(exc)}
        recovery_path = getattr(exc, "recovery_path", None)
        if recovery_path:
            payload["recovery_path"] = recovery_path
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
