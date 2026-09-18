"""Atomic publication for COMSOL MPH checkpoints and artifacts.

The callback writes a complete candidate to a unique temporary path in the
destination directory.  Nothing replaces the previous checkpoint until the
candidate has passed ZIP integrity checks and durable-file synchronization.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import zipfile

from comsol_mcp._execution_contract import canonical_project_path


class AtomicSaveError(RuntimeError):
    """A failed publication whose temporary evidence must remain inspectable."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        destination: Path,
        temporary_path: Path | None,
        original_preserved: bool,
        partial: bool = False,
        cleanup_failure: bool = False,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.destination = destination
        self.temporary_path = temporary_path
        self.original_preserved = original_preserved
        self.partial = partial
        self.cleanup_failure = cleanup_failure

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "destination": str(self.destination),
            "temporary_path": str(self.temporary_path) if self.temporary_path else "",
            "original_preserved": self.original_preserved,
            "partial": self.partial,
            "cleanup_failure": self.cleanup_failure,
            "safe_retry": False,
        }


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_parent(path: Path) -> bool:
    """Request directory durability where this filesystem exposes it."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path.parent, flags)
    except OSError:
        return False
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            # Some supported filesystems do not permit directory fsync.  The
            # file was already synced; publication remains atomic.
            return False
        return True
    finally:
        os.close(descriptor)


def _validate_mph_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.infolist()
            if not members:
                raise ValueError("MPH ZIP has no members")
            total_data = 0
            for member in members:
                if member.is_dir():
                    continue
                total_data += member.file_size
            if total_data <= 0:
                raise ValueError("MPH ZIP has no member data")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f"MPH ZIP CRC check failed for member: {bad_member}")
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise ValueError(f"Candidate MPH verification failed: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_save(
    destination: str | Path,
    save_callback: Callable[[Path], Any],
    project_root: str | Path,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Publish a complete callback-written MPH without destroying a prior one.

    The callback must write to the provided temporary path and return only
    after COMSOL has completed its save operation.  On any callback,
    verification, or publish failure, the old destination is retained and any
    temporary candidate is intentionally left in place for incident evidence.
    """
    destination_path = canonical_project_path(project_root, destination)
    parent = destination_path.parent
    # Resolving the parent separately catches a symlink inserted into an
    # otherwise relative destination path before any candidate is created.
    canonical_project_path(project_root, parent)
    existed = destination_path.exists()
    if existed and not overwrite:
        raise AtomicSaveError(
            "Destination already exists and overwrite is disabled.",
            stage="preflight",
            destination=destination_path,
            temporary_path=None,
            original_preserved=True,
        )
    if not parent.exists() or not parent.is_dir():
        raise AtomicSaveError(
            "Destination directory does not exist.",
            stage="preflight",
            destination=destination_path,
            temporary_path=None,
            original_preserved=existed,
        )

    temporary_path = parent / f".{destination_path.name}.{uuid4().hex}.tmp.mph"
    try:
        save_callback(temporary_path)
    except Exception as exc:
        raise AtomicSaveError(
            f"Candidate save callback failed: {exc}",
            stage="callback",
            destination=destination_path,
            temporary_path=temporary_path,
            original_preserved=existed,
            partial=temporary_path.exists(),
        ) from exc

    try:
        if not temporary_path.is_file():
            raise ValueError("save callback did not create its requested candidate file")
        _validate_mph_zip(temporary_path)
        _fsync_file(temporary_path)
        size = temporary_path.stat().st_size
        digest = _sha256(temporary_path)
    except Exception as exc:
        raise AtomicSaveError(
            f"Candidate save was not publishable: {exc}",
            stage="verification",
            destination=destination_path,
            temporary_path=temporary_path,
            original_preserved=existed,
            partial=True,
        ) from exc

    try:
        if overwrite:
            os.replace(temporary_path, destination_path)
        else:
            # A same-directory hard link is an atomic no-clobber publication.
            # It closes the exists-check race without a nonportable overwrite.
            os.link(temporary_path, destination_path)
            temporary_path.unlink()
        durable_parent = _fsync_parent(destination_path)
    except Exception as exc:
        raise AtomicSaveError(
            f"Atomic MPH publish failed: {exc}",
            stage="publish",
            destination=destination_path,
            temporary_path=temporary_path,
            original_preserved=destination_path.exists(),
            partial=True,
        ) from exc

    artifact = {"path": str(destination_path), "size": size, "sha256": digest, "verified": True}
    return {
        "path": str(destination_path),
        "size": size,
        "sha256": digest,
        "checkpoint": {**artifact, "publish_mode": "atomic_replace" if overwrite else "atomic_no_clobber", "parent_fsync": durable_parent},
        "artifact": artifact,
    }
