"""Journalled, no-clobber publication for an owned Windows model target.

This is intentionally a two-step filesystem transaction.  The exact existing
target handle is moved to an exclusive recovery name, then ArtifactStore's
normal streaming publisher atomically publishes the new file without replacing
any name that appeared in the visibility gap.  The transaction never claims a
continuous atomic path replacement or target compare-and-swap.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from ._execution_contract import ExecutionContractError

MODEL_PUBLICATION_JOURNAL_SCHEMA = "comsol-mcp.model-publication-journal/1"
MODEL_PUBLICATION_KEY_PREFIX = "model-publication:"
_ACTIVE = frozenset({"PREPARED", "OLD_RETAINED", "NEW_PUBLISHED", "ROLLBACK_INTENT"})
_TERMINAL = frozenset({"RESTORED", "COMMITTED", "ABORTED"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _contract(code: str, message: str, *, details: Mapping[str, Any] | None = None,
              stage: str = "pre_dispatch"):
    from ._execution_contract import ExecutionContractError
    return ExecutionContractError(code, message, stage=stage, details=dict(details or {}))


def _store_put(store: Any, journal: dict[str, Any]) -> None:
    """Persist each phase in the existing SQLite ledger before the next rename."""
    key = MODEL_PUBLICATION_KEY_PREFIX + journal["journal_id"]
    row = {**journal, "revision": 0}
    try:
        lock = getattr(store, "lock", None)
        db = getattr(store, "db", None)
        if lock is None or db is None or not hasattr(store, "put_metadata"):
            raise TypeError("model publication requires the existing OperationStore")
        with lock:
            # WAL/FULL makes the rename intent durable before touching the MPH.
            db.execute("PRAGMA synchronous=FULL")
            store.put_metadata("revisions", key, row)
    except Exception as exc:
        raise _contract("MODEL_PUBLICATION_JOURNAL_FAILED", "model publication phase could not be durably recorded",
                        details={"journal_id": journal.get("journal_id"),
                                 "phase": journal.get("status"), "cause_type": type(exc).__name__}) from exc


def _store_get(store: Any, journal_id: str) -> dict[str, Any]:
    try:
        row = store.get_metadata("revisions", MODEL_PUBLICATION_KEY_PREFIX + journal_id)
    except Exception as exc:
        raise _contract("MODEL_PUBLICATION_JOURNAL_INVALID", "model publication journal could not be read",
                        details={"journal_id": journal_id, "cause_type": type(exc).__name__}) from exc
    if (not isinstance(row, dict) or row.get("schema") != MODEL_PUBLICATION_JOURNAL_SCHEMA
            or row.get("journal_id") != journal_id or row.get("status") not in _ACTIVE | _TERMINAL):
        raise _contract("MODEL_PUBLICATION_JOURNAL_INVALID", "model publication journal is malformed",
                        details={"journal_id": journal_id})
    return dict(row)


def _set_status(store: Any, journal: dict[str, Any], status: str, **updates: Any) -> None:
    journal.update(updates)
    journal["status"] = status
    journal["updated_ns"] = __import__("time").time_ns()
    _store_put(store, journal)


def _canonical_relative(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _contract("INVALID_DESTINATION", "model overwrite destination must be nonempty project-relative syntax")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise _contract("INVALID_DESTINATION", "model overwrite destination must be canonical project-relative syntax")
    return value


def _relative_under_root(root: Path, value: Any, field: str) -> str:
    if not isinstance(value, (str, os.PathLike)):
        raise _contract("MODEL_PUBLICATION_JOURNAL_INVALID", f"journal {field} is missing")
    candidate = Path(value)
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise _contract("ACCESS_VIOLATION", f"journal {field} is outside the exact project root") from exc
    if not relative or any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts):
        raise _contract("ACCESS_VIOLATION", f"journal {field} is not a canonical project-local path")
    return relative


def _open_existing_delete_handle(api: Any, parent: Any, leaf: str) -> Any:
    """Open an ordinary child relative to a pinned parent with DELETE rights."""
    injected = getattr(api, "open_existing_for_delete", None)
    if callable(injected):
        return injected(parent, leaf)
    from . import _windows_parameter_publish as wp

    encoded = leaf.encode("utf-16-le")
    name_buffer = wp.C.create_unicode_buffer(leaf)
    name = wp._UnicodeString(len(encoded), len(encoded) + 2,
                             wp.C.cast(name_buffer, wp.C.POINTER(wp.C.c_wchar)))
    attributes = wp._ObjectAttributes(wp.C.sizeof(wp._ObjectAttributes), parent,
                                      wp.C.pointer(name), 0x40 | 0x1000, None, None)
    output = wp.HANDLE()
    io_status = wp._IoStatusBlock()
    access = 0x00110081  # SYNCHRONIZE | DELETE | READ_ATTRIBUTES | READ_DATA
    share = 0x1  # Permit existing readers only; deny new write/delete opens.
    options = 0x40 | 0x20 | 0x00200000  # NON_DIRECTORY | SYNCHRONOUS | OPEN_REPARSE_POINT
    status = int(api.nt.NtCreateFile(wp.C.byref(output), access, wp.C.byref(attributes),
                                     wp.C.byref(io_status), None, 0x80, share, 1,
                                     options, None, 0))
    if status < 0:
        unsigned = status & 0xFFFFFFFF
        if unsigned == 0xC000050B:
            raise _contract("ACCESS_VIOLATION", "model target encountered a reparse point")
        if unsigned in {0xC0000034, 0xC000003A}:
            raise FileNotFoundError(2, "relative model target does not exist", leaf)
        dos_error = int(api.nt.RtlNtStatusToDosError(status))
        raise wp.C.WinError(dos_error)
    if int(io_status.Information) != 1:
        api.close(output)
        raise _contract("ACCESS_VIOLATION", "model target open did not bind one existing file")
    api.info(output)
    return output


def _hash_handle(api: Any, handle: Any) -> tuple[str, int, tuple[Any, ...]]:
    before = api.source_fingerprint(handle)
    digest, size = api.readback(handle)
    after = api.source_fingerprint(handle)
    if before != after or size != before[1]:
        raise _contract("ARTIFACT_CHANGED", "model file changed during same-handle verification")
    return digest, size, before


def _identity_matches(actual: tuple[Any, ...], expected: Mapping[str, Any]) -> bool:
    # Win32 volume serial/file index maps to Python's st_dev/st_ino on Windows.
    return (len(actual) >= 2 and isinstance(actual[0], tuple) and len(actual[0]) == 2
            and actual[0][0] == expected.get("device")
            and actual[0][1] == expected.get("inode")
            and ("size" not in expected or actual[1] == expected.get("size")))


def _verify_named(api: Any, parent: Any, leaf: str, handle: Any,
                  digest: str, size: int, expected_identity: Mapping[str, Any] | None = None) -> tuple[Any, ...]:
    actual = api.source_fingerprint(handle)
    named = api.child_identity(parent, leaf)
    if named != actual[0]:
        raise _contract("ARTIFACT_CHANGED", "model path no longer names the exact opened file")
    got_hash, got_size, after = _hash_handle(api, handle)
    if after[0] != actual[0] or got_hash != digest or got_size != size:
        raise _contract("ARTIFACT_HASH_MISMATCH", "model file failed exact identity/SHA/size readback")
    if expected_identity is not None and not _identity_matches(after, expected_identity):
        raise _contract("ARTIFACT_IDENTITY_MISMATCH", "model file identity differs from its registered preflight")
    return after


class WindowsModelPublication:
    """Live transaction handle bundle; the parent and old/new handles stay pinned."""

    def __init__(self, *, root: Path, store: Any, journal: dict[str, Any], api: Any,
                 parent_context: Any, parent: Any, parts: tuple[str, ...], binding: Any,
                 old_handle: Any, new_handle: Any, receipt: dict[str, Any]):
        self.root, self.store, self.journal, self.api = root, store, journal, api
        self.parent_context, self.parent, self.parts, self.binding = parent_context, parent, parts, binding
        self.old_handle, self.new_handle, self.receipt = old_handle, new_handle, receipt
        self.closed = False

    def _assert_binding(self) -> None:
        self.api.verify_named_binding(self.binding)

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        pending = __import__("sys").exc_info()[0] is not None
        for handle in (self.new_handle, self.old_handle):
            if handle is not None:
                try:
                    self.api.close(handle)
                except OSError:
                    if not pending:
                        raise
        try:
            self.parent_context.__exit__(None, None, None)
        except OSError:
            if not pending:
                raise

    def rollback(self) -> dict[str, Any]:
        """Retain the new MPH separately, then no-clobber restore the old handle."""
        destination = self.parts[-1]
        recovery_new = self.journal.get("new_recovery_name") or (
            ".model-overwrite-new-" + self.journal["journal_id"] + ".mph")
        self.journal["new_recovery_name"] = recovery_new
        _set_status(self.store, self.journal, "ROLLBACK_INTENT")
        try:
            self._assert_binding()
            if self.new_handle is not None:
                _verify_named(self.api, self.parent, destination, self.new_handle,
                              self.journal["new_sha256"], self.journal["new_size_bytes"],
                              self.journal.get("new_published_identity"))
                if self.api.exists_child(self.parent, recovery_new):
                    raise FileExistsError(183, "new recovery path already exists", recovery_new)
                self.api.rename(self.new_handle, self.parent, recovery_new)
                _verify_named(self.api, self.parent, recovery_new, self.new_handle,
                              self.journal["new_sha256"], self.journal["new_size_bytes"],
                              self.journal.get("new_published_identity"))
            elif self.api.exists_child(self.parent, destination):
                raise _contract("MODEL_PUBLICATION_RECOVERY_BLOCKED",
                                "destination contains a file whose identity was not durably recorded")
            if self.api.exists_child(self.parent, destination):
                raise FileExistsError(183, "a foreign target appeared; preserving it", destination)
            backup = self.journal["backup_name"]
            _verify_named(self.api, self.parent, backup, self.old_handle,
                          self.journal["old_sha256"], self.journal["old_size_bytes"],
                          self.journal["old_identity"])
            self.api.rename(self.old_handle, self.parent, destination)
            _verify_named(self.api, self.parent, destination, self.old_handle,
                          self.journal["old_sha256"], self.journal["old_size_bytes"],
                          self.journal["old_identity"])
            _set_status(self.store, self.journal, "RESTORED",
                        recovery_new_relative_path=(
                            str(PurePosixPath(*self.parts[:-1], recovery_new))
                            if self.new_handle is not None else None))
            self.receipt.update({"status": "RESTORED", "recovery_new_path": self.journal.get("recovery_new_relative_path")})
            self._close()
            return dict(self.receipt)
        except Exception as exc:
            details = {"safe_retry": False, "journal_id": self.journal["journal_id"],
                       "destination": self.journal["destination"],
                       "recovery_path": self.journal["backup_relative_path"],
                       "cause_type": type(exc).__name__}
            try:
                if self.new_handle is not None:
                    details["recovery_new_path"] = str(PurePosixPath(*self.parts[:-1], recovery_new))
                _set_status(self.store, self.journal, "ROLLBACK_INTENT", last_error=type(exc).__name__)
            except Exception:
                pass
            raise _contract("OVERWRITE_STATE_UNKNOWN", "old model could not be safely restored; preserving journal and recovery MPH",
                            details=details, stage="post_dispatch") from exc

    def commit(self) -> dict[str, Any]:
        """Record completion before best-effort removal of the old recovery name."""
        destination = self.parts[-1]
        backup = self.journal["backup_name"]
        try:
            self._assert_binding()
            _verify_named(self.api, self.parent, destination, self.new_handle,
                          self.journal["new_sha256"], self.journal["new_size_bytes"],
                          self.journal.get("new_published_identity"))
            _verify_named(self.api, self.parent, backup, self.old_handle,
                          self.journal["old_sha256"], self.journal["old_size_bytes"],
                          self.journal["old_identity"])
            _set_status(self.store, self.journal, "COMMITTED")
        except Exception as exc:
            raise _contract("OVERWRITE_STATE_UNKNOWN", "model publication completion could not be durably confirmed",
                            details={"safe_retry": False, "journal_id": self.journal["journal_id"],
                                     "destination": self.journal["destination"],
                                     "recovery_path": self.journal["backup_relative_path"],
                                     "cause_type": type(exc).__name__}, stage="post_dispatch") from exc
        backup_removed = False
        try:
            self.api.delete_owned(self.old_handle)
            backup_removed = not self.api.exists_child(self.parent, backup)
        except Exception:
            backup_removed = False
        self.receipt.update({"status": "COMMITTED", "backup_removed": backup_removed,
                             "backup_retained_path": None if backup_removed else self.journal["backup_relative_path"]})
        self._close()
        return dict(self.receipt)


def begin_registered_overwrite(
    project_root: str | os.PathLike[str], destination: str, source: str | os.PathLike[str], *,
    operation_store: Any, project_id: str, operation_id: str, request_id: str,
    model_ref: Mapping[str, Any], source_revision: int,
    old_sha256: str, old_identity: Mapping[str, Any], old_artifact_relative_path: str,
    new_sha256: str, new_size_bytes: int, source_identity: Mapping[str, Any],
    _api: Any = None, _pin_parent: Callable[..., Any] | None = None,
    _publisher: Callable[..., Mapping[str, Any]] | None = None,
) -> WindowsModelPublication:
    """Move the validated old handle aside and publish new bytes with no-clobber."""
    from ._windows_parameter_publish import _API, pinned_parent
    if os.name != "nt" and _api is None:
        raise _contract("OVERWRITE_PLATFORM_UNSUPPORTED", "Windows model publication requires Win32 handles")
    if (not isinstance(old_sha256, str) or not _SHA256.fullmatch(old_sha256)
            or not isinstance(new_sha256, str) or not _SHA256.fullmatch(new_sha256)
            or type(new_size_bytes) is not int or new_size_bytes <= 0):
        raise _contract("INVALID_REQUEST", "model overwrite requires exact SHA-256 and positive sizes")
    destination = _canonical_relative(destination)
    old_artifact_relative_path = _canonical_relative(old_artifact_relative_path)
    root = Path(project_root).resolve(strict=True)
    source_path = Path(source).resolve(strict=True)
    try:
        source_relative = source_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise _contract("ACCESS_VIOLATION", "new saved MPH source must be project-local") from exc
    if source_relative == destination or not source_path.is_file() or source_path.is_symlink():
        raise _contract("ACCESS_VIOLATION", "new saved MPH source is not a distinct project-local regular file")
    from ._artifact_store import project_root_identity
    selected_api = _api if _api is not None else _API()
    parent_factory = _pin_parent or pinned_parent
    parent_context = parent_factory(root, destination, add_file=True, _api=selected_api)
    parent = None
    entered = False
    old_handle = None
    new_handle = None
    moved_old = False
    journal: dict[str, Any] | None = None
    try:
        selected_api, parent, parts, binding = parent_context.__enter__()
        entered = True
        destination_leaf = parts[-1]
        backup_name = ".model-overwrite-old-" + secrets.token_hex(16) + ".mph"
        if selected_api.exists_child(parent, backup_name):
            raise _contract("OVERWRITE_UNSAFE", "exclusive recovery name unexpectedly exists")
        old_handle = _open_existing_delete_handle(selected_api, parent, destination_leaf)
        old_actual = selected_api.source_fingerprint(old_handle)
        old_hash, old_size, old_actual = _hash_handle(selected_api, old_handle)
        if (not _identity_matches(old_actual, old_identity) or old_hash != old_sha256
                or old_size != old_identity.get("size") or old_actual[4] != 1):
            raise _contract("ARTIFACT_CHANGED", "registered destination changed before exact-handle overwrite")
        source_relative = source_path.relative_to(root).as_posix()
        backup_relative = str(PurePosixPath(*parts[:-1], backup_name))
        journal_id = secrets.token_hex(16)
        journal = {
            "schema": MODEL_PUBLICATION_JOURNAL_SCHEMA,
            "revision": 0,
            "journal_id": journal_id,
            "status": "PREPARED",
            "project_id": project_id,
            "project_root": str(root),
            "project_root_identity": project_root_identity(root),
            "operation_id": operation_id,
            "request_id": request_id,
            "model_ref": dict(model_ref),
            "source_revision": source_revision,
            "destination": destination,
            "backup_name": backup_name,
            "backup_relative_path": backup_relative,
            "old_artifact_relative_path": old_artifact_relative_path,
            "old_sha256": old_sha256,
            "old_size_bytes": int(old_size),
            "old_identity": {"device": int(old_actual[0][0]), "inode": int(old_actual[0][1]),
                             "size": int(old_size)},
            "new_sha256": new_sha256,
            "new_size_bytes": new_size_bytes,
            "new_source_relative_path": source_relative,
            "new_source_identity": dict(source_identity),
            "new_published_identity": None,
            "atomic_new_file_publication": True,
            "atomic_path_replacement": False,
            "visibility_gap_possible": True,
            "created_ns": __import__("time").time_ns(),
            "updated_ns": __import__("time").time_ns(),
        }
        _store_put(operation_store, journal)
        selected_api.rename(old_handle, parent, backup_name)
        moved_old = True
        if selected_api.exists_child(parent, destination_leaf):
            raise FileExistsError(183, "destination appeared during the documented visibility gap", destination_leaf)
        _verify_named(selected_api, parent, backup_name, old_handle, old_sha256, old_size,
                      journal["old_identity"])
        selected_api.verify_named_binding(binding)
        _set_status(operation_store, journal, "OLD_RETAINED")
        if _publisher is None:
            from ._artifact_store import ArtifactStore
            from ._artifact_store import publish_project_file_no_clobber
            publisher = publish_project_file_no_clobber
        else:
            publisher = _publisher
        published = publisher(root, destination, source_path,
                              expected_sha256=new_sha256, expected_size=new_size_bytes)
        published_identity = published.get("file_identity") if isinstance(published, Mapping) else None
        if not isinstance(published_identity, Mapping):
            raise _contract("ARTIFACT_IDENTITY_MISMATCH", "no-clobber publisher did not return target identity")
        new_handle = _open_existing_delete_handle(selected_api, parent, destination_leaf)
        new_actual = _verify_named(selected_api, parent, destination_leaf, new_handle,
                                   new_sha256, new_size_bytes, published_identity)
        journal["new_published_identity"] = {"device": int(new_actual[0][0]), "inode": int(new_actual[0][1]),
                                             "size": int(new_size_bytes)}
        _set_status(operation_store, journal, "NEW_PUBLISHED")
        receipt = {"destination": destination, "recovery_path": backup_relative,
                   "journal_id": journal_id, "old_sha256": old_sha256,
                   "new_sha256": new_sha256, "new_size_bytes": new_size_bytes,
                   "atomic_new_file_publication": True,
                   "atomic_path_replacement": False,
                   "visibility_gap_possible": True}
        return WindowsModelPublication(root=root, store=operation_store, journal=journal,
                                       api=selected_api, parent_context=parent_context, parent=parent,
                                       parts=tuple(parts), binding=binding, old_handle=old_handle,
                                       new_handle=new_handle, receipt=receipt)
    except Exception as exc:
        if moved_old and journal is not None:
            tx = WindowsModelPublication(root=root, store=operation_store, journal=journal,
                                         api=selected_api, parent_context=parent_context, parent=parent,
                                         parts=tuple(parts), binding=binding, old_handle=old_handle,
                                         new_handle=new_handle,
                                         receipt={"destination": destination,
                                                  "recovery_path": journal["backup_relative_path"],
                                                  "journal_id": journal["journal_id"],
                                                  "atomic_new_file_publication": True,
                                                  "atomic_path_replacement": False,
                                                  "visibility_gap_possible": True})
            try:
                tx.rollback()
            except Exception as rollback_exc:
                try:
                    tx._close()
                except Exception:
                    pass
                raise _contract("OVERWRITE_STATE_UNKNOWN", "Windows overwrite failed and exact rollback is blocked",
                                details={"safe_retry": False, "journal_id": journal["journal_id"],
                                         "destination": destination,
                                         "recovery_path": journal["backup_relative_path"],
                                         "cause_type": type(exc).__name__,
                                         "rollback_cause_type": type(rollback_exc).__name__},
                                stage="post_dispatch") from exc
            raise _contract("OVERWRITE_ROLLED_BACK", "Windows overwrite failed after restoring the original MPH",
                            details={"safe_retry": False, "journal_id": journal["journal_id"],
                                     "destination": destination,
                                     "cause_code": getattr(exc, "code", None)},
                            stage="post_dispatch") from exc
        if journal is not None:
            try:
                _set_status(operation_store, journal, "ABORTED", last_error=type(exc).__name__)
            except Exception:
                pass
        if new_handle is not None:
            try:
                selected_api.close(new_handle)
            except Exception:
                pass
        if old_handle is not None:
            try:
                selected_api.close(old_handle)
            except Exception:
                pass
        if entered:
            try:
                parent_context.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
        raise


def recover_model_publication(operation_store: Any, journal_id: str, *,
                              _api: Any = None, _pin_parent: Callable[..., Any] | None = None,
                              _publisher: Callable[..., Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Explicitly restore an interrupted transaction; never replace a leaf."""
    if not isinstance(journal_id, str) or not re.fullmatch(r"[0-9a-f]{32}", journal_id):
        raise _contract("INVALID_REQUEST", "journal_id must be the exact 32-character lowercase id")
    journal = _store_get(operation_store, journal_id)
    if journal["status"] in _TERMINAL:
        return {"status": journal["status"], "journal_id": journal_id, "already_terminal": True}
    from ._windows_parameter_publish import _API, pinned_parent
    if os.name != "nt" and _api is None:
        raise _contract("OVERWRITE_PLATFORM_UNSUPPORTED", "explicit Windows model recovery requires Win32 handles")
    root = Path(journal.get("project_root", "")).resolve(strict=True)
    from ._artifact_store import project_root_identity
    if project_root_identity(root) != journal.get("project_root_identity"):
        raise _contract("PROJECT_IDENTITY_MISMATCH", "journal project root identity changed")
    destination = _canonical_relative(journal.get("destination"))
    backup_name = journal.get("backup_name")
    if not isinstance(backup_name, str) or Path(backup_name).name != backup_name:
        raise _contract("MODEL_PUBLICATION_JOURNAL_INVALID", "journal backup name is malformed")
    selected_api = _api if _api is not None else _API()
    parent_context = (_pin_parent or pinned_parent)(root, destination, add_file=True, _api=selected_api)
    with parent_context as (api, parent, parts, binding):
        dest_leaf = parts[-1]
        backup_exists = api.exists_child(parent, backup_name)
        dest_exists = api.exists_child(parent, dest_leaf)
        if dest_exists:
            current = _open_existing_delete_handle(api, parent, dest_leaf)
            try:
                current_id = api.source_fingerprint(current)
                current_hash, current_size, current_id = _hash_handle(api, current)
                old_match = (current_hash == journal["old_sha256"]
                             and current_size == journal["old_size_bytes"]
                             and _identity_matches(current_id, journal["old_identity"]))
                new_expected = journal.get("new_published_identity")
                new_match = (current_hash == journal["new_sha256"]
                             and current_size == journal["new_size_bytes"]
                             and isinstance(new_expected, Mapping)
                             and _identity_matches(current_id, new_expected))
                if old_match and not backup_exists:
                    _set_status(operation_store, journal, "RESTORED", recovered_at_ns=__import__("time").time_ns())
                    return {"status": "RESTORED", "journal_id": journal_id,
                            "destination": destination, "restored": True}
                if not new_match:
                    raise _contract("MODEL_PUBLICATION_RECOVERY_BLOCKED",
                                    "destination is foreign or its identity is not journal-bound; preserving it")
                recovery_new = journal.get("new_recovery_name") or (
                    ".model-overwrite-new-" + journal_id + ".mph")
                if api.exists_child(parent, recovery_new):
                    raise _contract("MODEL_PUBLICATION_RECOVERY_BLOCKED", "new recovery path already exists")
                _set_status(operation_store, journal, "ROLLBACK_INTENT", new_recovery_name=recovery_new)
                api.rename(current, parent, recovery_new)
                _verify_named(api, parent, recovery_new, current, journal["new_sha256"],
                              journal["new_size_bytes"], new_expected)
            finally:
                api.close(current)
        else:
            recovery_new = journal.get("new_recovery_name")
        if backup_exists:
            old_handle = _open_existing_delete_handle(api, parent, backup_name)
            try:
                _verify_named(api, parent, backup_name, old_handle, journal["old_sha256"],
                              journal["old_size_bytes"], journal["old_identity"])
                if api.exists_child(parent, dest_leaf):
                    raise _contract("MODEL_PUBLICATION_RECOVERY_BLOCKED", "foreign target appeared; no-clobber restore refused")
                api.rename(old_handle, parent, dest_leaf)
                _verify_named(api, parent, dest_leaf, old_handle, journal["old_sha256"],
                              journal["old_size_bytes"], journal["old_identity"])
            finally:
                api.close(old_handle)
        elif not api.exists_child(parent, dest_leaf):
            # If the old recovery name was lost after an interruption, restore
            # the immutable registered original through the same no-clobber
            # ArtifactStore publisher after preserving the expected new bytes.
            old_source = root / journal["old_artifact_relative_path"]
            if not old_source.is_file() or old_source.is_symlink():
                raise _contract("MODEL_PUBLICATION_RECOVERY_BLOCKED", "both recovery MPH and registered original are unavailable")
            if _publisher is None:
                from ._artifact_store import publish_project_file_no_clobber
                publisher = publish_project_file_no_clobber
            else:
                publisher = _publisher
            publisher(root, destination, old_source, expected_sha256=journal["old_sha256"],
                      expected_size=journal["old_size_bytes"])
            if _api is not None and api.exists_child(parent, dest_leaf):
                restored = _open_existing_delete_handle(api, parent, dest_leaf)
                try:
                    _verify_named(api, parent, dest_leaf, restored, journal["old_sha256"],
                                  journal["old_size_bytes"])
                finally:
                    api.close(restored)
        api.verify_named_binding(binding)
        _set_status(operation_store, journal, "RESTORED", recovered_at_ns=__import__("time").time_ns(),
                    recovery_new_relative_path=(
                        str(PurePosixPath(*parts[:-1], recovery_new)) if recovery_new else None))
        return {"status": "RESTORED", "journal_id": journal_id,
                "destination": destination, "restored": True,
                "recovery_new_path": (str(PurePosixPath(*parts[:-1], recovery_new)) if recovery_new else None)}
