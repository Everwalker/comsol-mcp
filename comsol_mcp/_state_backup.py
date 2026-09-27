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
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
from typing import Any, Iterator, Mapping
from uuid import uuid4

from ._managed_backend import ProcessLock


DATABASE_NAME = "operations.sqlite3"
CONTROL_LOCK_NAME = "control.lock"
ACTIVE_RESTORE_JOURNAL = "operations.restore.pending.json"
MANIFEST_SCHEMA = "comsol-mcp.sqlite-backup/1"
JOURNAL_SCHEMA = "comsol-mcp.sqlite-restore-journal/1"
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


def assert_no_pending_restore(home: str | os.PathLike[str]) -> None:
    """Startup gate: caller must hold ``control.lock`` before calling this."""
    normalized = _existing_directory(home, "control home")
    journal_path = normalized / ACTIVE_RESTORE_JOURNAL
    _reject_symlink_components(journal_path, "restore journal")
    if journal_path.exists():
        raise StateBackupError(
            "RESTORE_RECOVERY_REQUIRED",
            "an interrupted or unverified ledger restore is pending; run comsol-mcp-state recover before starting the daemon",
        )


def _journal_write(home: Path, journal: dict[str, Any]) -> None:
    _replace_json(home / ACTIVE_RESTORE_JOURNAL, journal)


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comsol-mcp-state", description="Back up or restore the private control ledger.")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.home, args.destination)
        elif args.command == "restore":
            result = restore_backup(args.home, args.backup, args.manifest, confirm=args.confirm)
        else:
            result = recover_restore(args.home, confirm=args.confirm)
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
