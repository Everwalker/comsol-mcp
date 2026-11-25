"""Atomic publication for COMSOL MPH checkpoints and artifacts.

The callback writes a complete candidate to a unique temporary path in the
destination directory.  Nothing replaces the previous checkpoint until the
candidate has passed ZIP integrity checks and durable-file synchronization.
"""

from __future__ import annotations

from hashlib import sha256
import os
import stat
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import zipfile

from comsol_mcp._execution_contract import canonical_project_path


def _observed_presence(path: Path | None) -> bool | None:
    """Observe whether a filesystem name exists, without guessing on failure.

    ``True``/``False`` are returned only when the ``lstat`` observation
    itself succeeded; a refused or otherwise failed observation (EACCES,
    ELOOP, ESTALE, ...) returns ``None`` so callers can never treat an
    unobservable path as proven absent (or proven present).  ENOENT and
    ENOTDIR are the only errnos that establish definite absence.  This
    helper never raises.
    """
    if path is None:
        return False
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except NotADirectoryError:
        return False
    except (OSError, ValueError):
        return None
    return True


# The durable evidence channel stringifies ``temporary_retained``; encode
# the three epistemic states explicitly so a refused observation is never
# collapsed into absence.
_RETENTION_TOKENS = {True: "observed_retained", False: "observed_absent", None: "observation_denied"}


def _preflight_preservation(existed: bool | None) -> str:
    """Map a three-state preflight observation onto an observation-scoped label."""
    if existed is True:
        return "preserved"
    if existed is False:
        return "no_prior_destination"
    return "unverified"


class AtomicSaveError(RuntimeError):
    """A failed publication whose temporary evidence must remain inspectable.

    ``publication_status`` distinguishes the three honest outcomes:
    ``not_attempted`` (the publish syscall never started), ``unknown`` (the
    publish syscall raised, so its effect on the destination cannot be
    confirmed), and ``published`` (the publish syscall returned before a
    later durability/cleanup step failed).

    ``original_preservation`` is observation-scoped, never lifetime-scoped.
    ``preserved``/``no_prior_destination`` are preflight-relative and state
    that this publisher did not touch the destination; ``unverified`` is
    used whenever existence itself was never established.  For a returned
    publication, ``replace_published_prior_state_unverified`` records that
    an overwrite publication returned but the prior state at the publish
    instant cannot be established from the preflight snapshot or from the
    return value, and ``no_destination_at_publish_instant`` records that a
    no-clobber link success proves the name was absent only at the publish
    instant; neither label makes any claim about the file's full lifetime.
    ``original_preserved`` stays a plain bool for existing consumers: it is
    True exactly when ``original_preservation`` == ``preserved``; False
    means preservation could not be confirmed, never a proven data loss.

    ``temporary_retained`` is a three-state observation made at error
    construction: True (observed present), False (observation succeeded and
    proved absence), or None (the observation itself was refused).  A
    refused observation is never reported as absence.  ``partial`` only
    records that candidate generation occurred or cannot be refuted by the
    post-callback observation; it is not a claim about the candidate's
    current visibility, which ``temporary_retained`` reports separately.
    """

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
        publication_status: str = "not_attempted",
        original_preservation: str = "unverified",
        temporary_retained: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.destination = destination
        self.temporary_path = temporary_path
        self.original_preserved = original_preserved
        self.partial = partial
        self.cleanup_failure = cleanup_failure
        self.publication_status = publication_status
        self.original_preservation = original_preservation
        self.temporary_retained = (temporary_retained if temporary_retained is not None
                                   else _observed_presence(temporary_path))

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "destination": str(self.destination),
            "temporary_path": str(self.temporary_path) if self.temporary_path else "",
            "original_preserved": self.original_preserved,
            "publication_status": self.publication_status,
            "original_preservation": self.original_preservation,
            "temporary_retained": self.temporary_retained,
            "partial": self.partial,
            "cleanup_failure": self.cleanup_failure,
            "safe_retry": False,
        }

    @property
    def details(self) -> dict[str, Any]:
        """Cause/publication record consumed by the control-plane stage evidence.

        Keys follow the cause vocabulary the durable stage record already
        projects (``cause_type``/``cause_stage``/``cause_message``) and add
        the publication state with an explicit three-state retention token,
        so publication outcomes survive into persisted evidence instead of
        stopping at this exception object.
        """
        return {
            "cause_type": type(self).__name__,
            "cause_stage": self.stage,
            "cause_message": str(self)[:500],
            "publication_status": self.publication_status,
            "original_preservation": self.original_preservation,
            "temporary_retention_observed": _RETENTION_TOKENS[self.temporary_retained],
        }


def _fsync_file(path: Path) -> None:
    # Windows FlushFileBuffers requires a writable handle.  ``r+b`` keeps the
    # existing candidate bytes intact while providing the required handle
    # access; ``w+b`` would truncate a candidate before it can be published.
    with path.open("r+b") as handle:
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
    after COMSOL has completed its save operation.  Before the publish
    syscall starts, a callback or verification failure provably — with
    respect to this publisher's own syscalls — leaves any previous
    destination untouched, and the temporary candidate is intentionally
    left in place for incident evidence.  If the publish syscall itself
    raises, its effect on the destination is reported as unknown and never
    claimed preserved or lost without proof.  Once the publish syscall
    returns, the candidate is published and only later durability or
    cleanup steps may fail; a returned no-clobber link additionally proves
    the destination name was absent at that instant, while a returned
    overwrite makes no claim about what (if anything) occupied the name at
    that instant.  Concurrent external writers are outside the proving
    power of every statement above.
    """
    destination_path = canonical_project_path(project_root, destination)
    parent = destination_path.parent
    # Resolving the parent separately catches a symlink inserted into an
    # otherwise relative destination path before any candidate is created.
    canonical_project_path(project_root, parent)
    existed = _observed_presence(destination_path)
    if existed is True and not overwrite:
        raise AtomicSaveError(
            "Destination already exists and overwrite is disabled.",
            stage="preflight",
            destination=destination_path,
            temporary_path=None,
            original_preserved=True,
            publication_status="not_attempted",
            original_preservation="preserved",
            temporary_retained=False,
        )
    # F-1 fix: ONE actual lstat observation drives all three parent
    # outcomes.  Re-observing the parent for the directory-type check
    # could assert a physical fact (absence or not-a-directory) from a
    # second probe that a race may refuse or that may already see a
    # different state — forbidden by this module's three-state standard.
    # ENOENT/ENOTDIR are the only errnos proving definite absence; any
    # other OSError/ValueError is a refused observation and is never
    # collapsed into absence or into a not-directory claim.  "Is not a
    # directory" is claimed only when this observation returned and its
    # mode says so.
    try:
        parent_observation = os.lstat(parent)
    except (FileNotFoundError, NotADirectoryError):
        parent_state = "absent"
    except (OSError, ValueError):
        parent_state = "refused"
    else:
        parent_state = "directory" if stat.S_ISDIR(parent_observation.st_mode) else "not-directory"
    if parent_state == "absent":
        raise AtomicSaveError(
            "Destination directory does not exist.",
            stage="preflight",
            destination=destination_path,
            temporary_path=None,
            original_preserved=existed is True,
            publication_status="not_attempted",
            original_preservation=_preflight_preservation(existed),
            temporary_retained=False,
        )
    if parent_state == "refused":
        # A refused observation is not absence; the refusal stays
        # fail-closed but says only what was observed.
        raise AtomicSaveError(
            "Destination directory could not be observed; refusing without treating it as present or absent.",
            stage="preflight",
            destination=destination_path,
            temporary_path=None,
            original_preserved=existed is True,
            publication_status="not_attempted",
            original_preservation=_preflight_preservation(existed),
            temporary_retained=False,
        )
    if parent_state == "not-directory":
        raise AtomicSaveError(
            "Destination directory is not a directory.",
            stage="preflight",
            destination=destination_path,
            temporary_path=None,
            original_preserved=existed is True,
            publication_status="not_attempted",
            original_preservation=_preflight_preservation(existed),
            temporary_retained=False,
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
            original_preserved=existed is True,
            # Generation occurred unless the post-callback observation
            # proves the candidate name was never created; a refused
            # observation cannot refute it.  Current visibility is
            # reported separately by temporary_retained.
            partial=_observed_presence(temporary_path) is not False,
            publication_status="not_attempted",
            original_preservation=_preflight_preservation(existed),
        ) from exc

    try:
        if _observed_presence(temporary_path) is False:
            raise ValueError("save callback did not create its requested candidate file")
        if not stat.S_ISREG(temporary_path.lstat().st_mode):
            raise ValueError("save callback candidate is not a regular file")
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
            original_preserved=existed is True,
            partial=True,
            publication_status="not_attempted",
            original_preservation=_preflight_preservation(existed),
        ) from exc

    publication_status = "not_attempted"
    try:
        publication_status = "attempted"
        if overwrite:
            os.replace(temporary_path, destination_path)
        else:
            # A same-directory hard link is an atomic no-clobber publication.
            # It closes the exists-check race without a nonportable overwrite.
            os.link(temporary_path, destination_path)
        publication_status = "published"
        if not overwrite:
            temporary_path.unlink()
        durable_parent = _fsync_parent(destination_path)
    except Exception as exc:
        if publication_status == "attempted":
            # The publish syscall raised; whether it took effect before the
            # raise cannot be established, so preservation is neither
            # claimed nor refuted.
            status, preservation = "unknown", "unverified"
            message = f"Atomic MPH publish outcome is unknown: the publish call raised: {exc}"
        elif publication_status == "published":
            # The syscall returned, so the candidate is at the destination;
            # only a later durability/cleanup step failed.
            status = "published"
            if overwrite:
                # os.replace returned; it clobbers whatever occupied the
                # destination at the publish instant, but neither the
                # preflight snapshot nor the return value proves anything
                # existed (or did not exist) at that instant.
                preservation = "replace_published_prior_state_unverified"
            else:
                # os.link success proves the name was absent at the publish
                # instant; it says nothing about the file's full lifetime.
                preservation = "no_destination_at_publish_instant"
            message = f"Atomic MPH publish completed but a post-publication step failed: {exc}"
        else:  # pragma: no cover - defensive; the publish block always attempts first
            status = "not_attempted"
            preservation = _preflight_preservation(existed)
            message = f"Atomic MPH publish failed: {exc}"
        raise AtomicSaveError(
            message,
            stage="publish",
            destination=destination_path,
            temporary_path=temporary_path,
            original_preserved=preservation == "preserved",
            partial=True,
            publication_status=status,
            original_preservation=preservation,
        ) from exc

    artifact = {"path": str(destination_path), "size": size, "sha256": digest, "verified": True}
    return {
        "path": str(destination_path),
        "size": size,
        "sha256": digest,
        "checkpoint": {**artifact, "publish_mode": "atomic_replace" if overwrite else "atomic_no_clobber", "parent_fsync": durable_parent},
        "artifact": artifact,
    }
