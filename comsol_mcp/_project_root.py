"""Pinned project roots and bounded no-follow file reads.

Project paths are names used to select an object.  Once a root is admitted,
source reads are relative to its open directory handle, so replacing the root
pathname does not replace that handle.  No-follow traversal does not establish
the provenance of a real directory moved underneath the root before descent;
it also does not constrain file access by the separate COMSOL engine process.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Sequence

from ._execution_contract import ExecutionContractError


_DIR_FLAGS = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
              | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))


def _failure(code: str, message: str, exc: BaseException | None = None):
    error = ExecutionContractError(code, message)
    if exc is not None:
        error.__cause__ = exc
    return error


def _close_owned(closer, handles):
    """Attempt each owned handle exactly once; a failed close is not safe to
    retry.  Returns ``(failures, interrupts)``: ordinary Exception failures
    plus the non-Exception BaseExceptions (KeyboardInterrupt, SystemExit,
    GeneratorExit and similar) observed from a closer.

    An interrupt is recorded instead of aborting the loop, so the remaining
    handles are still each attempted exactly once while the interrupted
    handle is never retried.  Callers must then propagate the interrupts via
    ``_propagate_cleanup_interrupts`` after recording the release facts: a
    signal may never go missing and business may never continue past one.
    """
    failures = []
    interrupts = []
    for handle in handles:
        try:
            closer(handle)
        except Exception as exc:
            failures.append(exc)
        except BaseException as exc:
            interrupts.append(exc)
    return failures, interrupts


def _release_carrier(exc, limit=8):
    """Find a classified release failure (carrying the true unconfirmed
    handle count) on ``exc``'s __cause__ chain, as attached by
    ``_propagate_cleanup_interrupts`` when an interrupt outlives recorded
    cleanup facts.  Returns None when the chain carries no such carrier.

    Kept as the fallback for propagating interrupts that carry no direct
    binding (``_attached_release_carrier`` below); the bounded walk is
    reachable only for short chains, which is exactly why the direct
    binding exists."""
    cursor = exc.__cause__
    seen = set()
    while cursor is not None and id(cursor) not in seen and len(seen) < limit:
        if isinstance(cursor, ExecutionContractError) and isinstance(
                cursor.details.get("cleanup_failure_count"), int):
            return cursor
        seen.add(id(cursor))
        cursor = cursor.__cause__
    return None


_CARRIER_ATTR = "_cleanup_release_carrier"


def _bind_release_carrier(interrupt: BaseException, classified) -> None:
    """Bind the classified release-facts carrier directly on the
    propagating interrupt instance (attribute ``_CARRIER_ATTR``).

    The chain-tail carrier built by ``_propagate_cleanup_interrupts`` is
    unreachable from the propagated interrupt when a closer already
    occupied the interrupt's __cause__ slot (``raise KI from OSError``)
    or when the interrupt chain is longer than the bounded walk.  A
    one-way reference bound on the interrupt itself survives both
    shapes and can never form a chain cycle (the carrier is never made
    to point back at the interrupt here).  An existing binding, the
    interrupt's own __cause__/__context__/notes and any pre-existing
    attribute are never overwritten.  Setting one attribute on an
    exception instance is supported on every interpreter the package
    targets (>=3.10; static compatibility only, not runtime-tested on
    3.10 here).  An exotic interrupt type that cannot take the attribute
    must never mask the signal: binding failure degrades to an
    add_note (3.11+, guarded) and propagation continues.
    """
    if getattr(interrupt, _CARRIER_ATTR, None) is not None:
        return
    try:
        setattr(interrupt, _CARRIER_ATTR, classified)
    except AttributeError:
        add_note = getattr(interrupt, "add_note", None)
        if callable(add_note):
            add_note("Classified unconfirmed-release facts could not be "
                     "bound directly on this interrupt.")


def _attached_release_carrier(exc: BaseException):
    """Return the classified release-facts carrier bound directly on a
    propagating interrupt by ``_bind_release_carrier``, or None when it
    is absent or not a classified release error."""
    carrier = getattr(exc, _CARRIER_ATTR, None)
    if isinstance(carrier, ExecutionContractError) and isinstance(
            carrier.details.get("cleanup_failure_count"), int):
        return carrier
    return None


def _propagate_cleanup_interrupts(interrupts, classified=None):
    """Raise the first cleanup interrupt (in attempt order) after every
    owned handle was attempted once and the release facts were recorded.

    Priority rule (Python < 3.11 has no BaseExceptionGroup, so exactly one
    interrupt can propagate): the earliest observed interrupt is the one
    that propagates.  The remaining interrupts chain behind it on free
    __cause__ slots in attempt order, and the classified release error
    (when given) rides at the tail of that chain carrying the true
    unconfirmed-handle count and the first ordinary cause.  Already
    occupied chain slots are never overwritten (existing evidence stays),
    and a classified error is never chained into a cycle with the
    interrupts it describes.  Because occupied slots or a deep chain can
    hide the chain-tail carrier from a bounded walk, the classified
    carrier is additionally bound directly on the propagating interrupt
    (``_bind_release_carrier``) so the recorded release facts stay
    queryable from the exception that escapes.  This never returns.
    """
    if classified is not None:
        cursor = classified.__cause__
        seen = set()
        while cursor is not None and id(cursor) not in seen:
            seen.add(id(cursor))
            if any(cursor is exc for exc in interrupts):
                classified.__cause__ = None
                break
            cursor = cursor.__cause__
    payload = classified
    for exc in reversed(interrupts):
        if payload is not None and exc.__cause__ is None:
            exc.__cause__ = payload
        payload = exc
    if classified is not None:
        _bind_release_carrier(interrupts[0], classified)
    raise interrupts[0]


def _record_open_cleanup_and_propagate(error, closer, handles):
    """Cleanup helper for the open-path except handlers: mark the in-flight
    open failure UNCONFIRMED for every attempted-but-unconfirmed handle
    (ordinary failures and interrupted handles alike), then propagate any
    interrupt with the annotated error as its classified carrier."""
    failures, interrupts = _close_owned(closer, handles)
    _mark_cleanup_unconfirmed(error, failures + interrupts)
    if interrupts:
        _propagate_cleanup_interrupts(interrupts, classified=error)


def _effective_failure_count(failures) -> int:
    """True number of underlying handle failures, unwrapping a classified
    release failure (whose own details already carry the real count) instead
    of counting the wrapper as one."""
    total = 0
    for failure in failures:
        nested = None
        if isinstance(failure, ExecutionContractError):
            nested = failure.details.get("cleanup_failure_count")
        total += nested if isinstance(nested, int) else 1
    return total


def _mark_cleanup_unconfirmed(error: BaseException, failures) -> None:
    if not failures:
        return
    count = _effective_failure_count(failures)
    if isinstance(error, ExecutionContractError):
        previous = None
        if error.details.get("cleanup_status") == "UNCONFIRMED":
            # Keep the count truthful across independent release sites:
            # accumulate genuinely new failures, never overwrite prior
            # state and never recount a no-op re-close (close() returns
            # before raising anything once _closed is set).
            previous = error.details.get("cleanup_failure_count")
        error.details["cleanup_status"] = "UNCONFIRMED"
        error.details["cleanup_failure_count"] = (
            (previous if isinstance(previous, int) else 0) + count)
    elif hasattr(error, "add_note"):
        error.add_note("One or more owned handle releases could not be confirmed.")


def _note_interrupted_release(interrupt: BaseException, primary_error: BaseException) -> None:
    add_note = getattr(interrupt, "add_note", None)
    if callable(add_note):
        add_note(
            "An owned project-root release could not be confirmed while "
            f"{type(interrupt).__name__} was raised; the in-flight primary "
            f"({type(primary_error).__name__}) remains attached as __context__.")


def _finish_cleanup(closer, handles, primary_error: BaseException | None) -> None:
    failures, interrupts = _close_owned(closer, handles)
    unconfirmed = failures + interrupts
    if not unconfirmed:
        return
    if primary_error is not None:
        # Ordinary failures and interrupted handles are each attempted-but-
        # unconfirmed; a signal does not erase the primary's release facts.
        _mark_cleanup_unconfirmed(primary_error, unconfirmed)
        if interrupts:
            _propagate_cleanup_interrupts(interrupts)
        return
    error = _failure("ACCESS_VIOLATION", "owned handle release could not be confirmed",
                     failures[0] if failures else None)
    _mark_cleanup_unconfirmed(error, unconfirmed)
    if interrupts:
        _propagate_cleanup_interrupts(interrupts, classified=error)
    raise error from failures[0]


class _ReleaseUnconfirmed(ExecutionContractError, OSError):
    """A classified failure to confirm the release of an owned binding.

    Dual bases keep existing ``except OSError`` call sites working while the
    contract classifiers (``except ExecutionContractError``) see an explicit
    ACCESS_VIOLATION boundary instead of an unclassified crash that would
    downgrade a definite refusal into EXECUTION_STATE_UNKNOWN.
    """

    def __init__(self, message: str, causes: Sequence[BaseException]) -> None:
        ExecutionContractError.__init__(self, "ACCESS_VIOLATION", message)
        cause = causes[0] if causes else None
        OSError.__init__(self, getattr(cause, "errno", None), message)
        if cause is not None:
            self.__cause__ = cause
        self.details["cleanup_status"] = "UNCONFIRMED"
        self.details["cleanup_failure_count"] = len(causes)
        if cause is not None:
            self.details["close_cause"] = f"{type(cause).__name__}: {cause}"


def close_binding_releasing(binding: "ProjectRootBinding",
                            primary_error: BaseException | None) -> None:
    """Release a caller-owned binding without letting the release replace the
    primary error.

    ``primary_error`` must be a locally captured reference, never
    ``sys.exc_info()``: a finally block can run while an unrelated outer
    exception is being handled, and ``sys.exc_info`` would then annotate that
    unrelated error and swallow the failed release (a false success boundary).
    With an ordinary Exception primary in flight the release failure is
    recorded on the primary as an UNCONFIRMED release boundary (true
    underlying failure count included, first cause preserved); with no
    primary the classified release failure is raised (a success must never
    hide an unconfirmed fd).  A non-Exception BaseException raised during the
    release (KeyboardInterrupt, SystemExit and similar interrupts) is never
    swallowed: the facts stay recorded on the primary and on the interrupt
    (``__context__`` plus a note), and the interrupt propagates — no claim
    that the release was confirmed is made in either case.  When the
    interrupt rides a classified release error on its ``__cause__`` chain
    (multi-handle close: the true unconfirmed-handle count and first cause
    live there — supplied operation-locally by the binding's own
    ``close()`` record first, or, for pre-binding shapes, read from the
    carrier bound directly on the interrupt or riding its __cause__
    chain), that carrier supplies the annotation count instead of
    counting the interrupt alone.  A failed release is never retried; the
    binding already marks itself closed before attempting the release, and
    every owned handle has been attempted exactly once before an interrupt
    escapes ``close()``.
    """
    try:
        binding.close()
    except BaseException as close_exc:
        if primary_error is None or close_exc is primary_error:
            raise
        carrier = close_exc
        if not isinstance(close_exc, Exception):
            # Operation-local facts first: ``ProjectRootBinding.close()``
            # recorded this attempt's classified release error on the
            # binding it owns, so a reused exception instance carrying an
            # earlier operation's bound carrier can never misreport this
            # operation.  The exception-bound readers remain fallbacks for
            # interrupt shapes raised outside this binding's close().
            owned = getattr(binding, "_release_error", None)
            if not (isinstance(owned, ExecutionContractError)
                    and isinstance(owned.details.get("cleanup_failure_count"), int)):
                owned = None
            carrier = (owned
                       or _attached_release_carrier(close_exc)
                       or _release_carrier(close_exc) or close_exc)
        _mark_cleanup_unconfirmed(primary_error, [carrier])
        if isinstance(primary_error, ExecutionContractError):
            if carrier is not close_exc and carrier.details.get("close_cause"):
                primary_error.details.setdefault(
                    "close_cause", carrier.details["close_cause"])
            else:
                root_cause = close_exc.__cause__ or close_exc
                primary_error.details.setdefault(
                    "close_cause", f"{type(root_cause).__name__}: {root_cause}")
        if not isinstance(close_exc, Exception):
            if close_exc.__context__ is None:
                close_exc.__context__ = primary_error
            _note_interrupted_release(close_exc, primary_error)
            raise
        return


def _absolute_lexical(path: str | os.PathLike[str]) -> Path:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _failure("ACCESS_VIOLATION", "project root is not a valid absolute path")
    return Path(os.path.abspath(os.path.expanduser(raw)))


def _same_path_or_child(path: Path, root: Path) -> tuple[str, ...]:
    if os.name == "nt":
        import ntpath
        path_text, root_text = os.fspath(path), os.fspath(root)
        try:
            common = ntpath.commonpath((ntpath.normcase(path_text), ntpath.normcase(root_text)))
            if common != ntpath.normcase(root_text):
                raise ValueError("outside root")
            relative = ntpath.relpath(path_text, root_text)
        except ValueError as exc:
            raise _failure("PERMISSION_DENIED", "path escapes the approved project root", exc)
        if relative == ".":
            return ()
        parts = tuple(part for part in relative.split("\\") if part)
        if any(part in {".", ".."} for part in parts):
            raise _failure("PERMISSION_DENIED", "path escapes the approved project root")
        return parts
    try:
        return path.relative_to(root).parts
    except ValueError as exc:
        raise _failure("PERMISSION_DENIED", "path escapes the approved project root", exc)


class ProjectRootBinding:
    """An open project directory object that serves only bounded read requests."""

    def __init__(self, path: Path, identity: tuple[int, ...], *, fd: int | None = None,
                 api=None, handles: Sequence[object] = ()) -> None:
        self.path = path
        self.identity = tuple(identity)
        self._fd = fd
        self._api = api
        self._handles = list(handles)
        self._closed = False
        # Per-operation release facts owned by this binding: close() records
        # the classified release error of its single close attempt here (at
        # most one reference per binding; a no-op re-close never sets or
        # consumes it), so close_binding_releasing can read operation-local
        # facts instead of trusting an exception-bound carrier that a reused
        # interrupt instance may carry from an earlier operation.
        self._release_error: ExecutionContractError | None = None

    @classmethod
    def open(cls, root: str | os.PathLike[str], *, authorized_root: str | os.PathLike[str] | None = None,
             expected_authorized_root_identity: tuple[int, ...] | None = None) -> "ProjectRootBinding":
        path = _absolute_lexical(root)
        if authorized_root is None:
            if os.name == "nt":
                return cls._open_windows(path)
            return cls._open_posix(path)
        parent = _absolute_lexical(authorized_root)
        parts = _same_path_or_child(path, parent)
        if not parts:
            raise _failure("PERMISSION_DENIED", "project root must be below the authorized workspace root")
        if os.name == "nt":
            return cls._open_windows(path, authorized_root=parent,
                                     expected_authorized_root_identity=expected_authorized_root_identity)
        return cls._open_posix(path, authorized_root=parent, relative_parts=parts,
                               expected_authorized_root_identity=expected_authorized_root_identity)

    @classmethod
    def _open_posix(cls, path: Path, *, authorized_root: Path | None = None,
                    relative_parts: Sequence[str] | None = None,
                    expected_authorized_root_identity: tuple[int, ...] | None = None) -> "ProjectRootBinding":
        if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0) or os.open not in os.supports_dir_fd:
            raise _failure("ACCESS_VIOLATION", "platform cannot securely anchor project directory reads")
        descriptors: list[int] = []
        try:
            anchor = authorized_root.anchor if authorized_root is not None else path.anchor
            current = os.open(anchor or "/", _DIR_FLAGS)
            descriptors.append(current)

            def descend(components: Sequence[str]) -> None:
                nonlocal current
                for component in components:
                    child = os.open(component, _DIR_FLAGS, dir_fd=current)
                    descriptors.append(child)
                    info = os.fstat(child)
                    named = os.stat(component, dir_fd=current, follow_symlinks=False)
                    if (not stat.S_ISDIR(info.st_mode) or not stat.S_ISDIR(named.st_mode)
                            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                        raise _failure("ACCESS_VIOLATION", "project directory changed while its root was being bound")
                    current = child

            if authorized_root is None:
                descend(path.parts[1:])
            else:
                descend(authorized_root.parts[1:])
                if expected_authorized_root_identity is not None:
                    parent_info = os.fstat(current)
                    observed_parent = (int(parent_info.st_dev), int(parent_info.st_ino))
                    if observed_parent != tuple(expected_authorized_root_identity):
                        raise _failure("ACCESS_VIOLATION", "authorized workspace root identity changed")
                descend(relative_parts or ())
            if not descriptors:
                raise _failure("ACCESS_VIOLATION", "project root could not be opened")
            root_info = os.fstat(descriptors[-1])
            if not stat.S_ISDIR(root_info.st_mode):
                raise _failure("ACCESS_VIOLATION", "project root must be a directory")
            binding = cls(path, (int(root_info.st_dev), int(root_info.st_ino)), fd=descriptors[-1])
        except ExecutionContractError as exc:
            _record_open_cleanup_and_propagate(exc, os.close, reversed(descriptors))
            raise
        except OSError as exc:
            error = _failure("ACCESS_VIOLATION", "project root contains an unavailable or linked directory", exc)
            _record_open_cleanup_and_propagate(error, os.close, reversed(descriptors))
            raise error from exc
        except BaseException as exc:
            _record_open_cleanup_and_propagate(exc, os.close, reversed(descriptors))
            raise
        failures, interrupts = _close_owned(os.close, reversed(descriptors[:-1]))
        if failures or interrupts:
            # The binding's own descriptor belongs to this same release
            # batch: attempt it exactly once too, so an interrupt in the
            # parent closes can never strand it unattempted.
            try:
                binding.close()
            except Exception as exc:
                failures.append(exc)
            except BaseException as exc:
                interrupts.append(exc)
            unconfirmed = failures + interrupts
            error = _failure("ACCESS_VIOLATION",
                             "project root parent handle could not be released",
                             failures[0] if failures else None)
            _mark_cleanup_unconfirmed(error, unconfirmed)
            if interrupts:
                _propagate_cleanup_interrupts(interrupts, classified=error)
            raise error from failures[0]
        return binding

    @classmethod
    def _open_windows(cls, path: Path, *, authorized_root: Path | None = None,
                      expected_authorized_root_identity: tuple[int, ...] | None = None) -> "ProjectRootBinding":
        import ntpath
        import re
        from ._windows_parameter_publish import _API, _component

        root_text = os.fspath(path).replace("/", "\\")
        drive, tail = ntpath.splitdrive(root_text)
        if (not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith("\\")
                or root_text.startswith(("\\\\", "\\\\?\\")) or len(root_text) >= 240):
            raise _failure("ACCESS_VIOLATION", "Windows source root requires a bounded ordinary drive path")
        path_parts = tuple(part for part in tail[1:].split("\\") if part)
        for part in path_parts:
            try:
                _component(part)
            except Exception as exc:
                raise _failure("ACCESS_VIOLATION", "Windows source root contains an unsupported path component", exc) from exc
        if len(path_parts) > 128:
            raise _failure("ACCESS_VIOLATION", "Windows source root exceeds the supported component bound")
        api = _API()
        handles: list[object] = []
        try:
            current = api.open(drive + "\\", directory=True)
            handles.append(current)
            drive_identity = api.info(current, directory=True)[0]
            if authorized_root is not None:
                auth_text = os.fspath(authorized_root).replace("/", "\\")
                auth_drive, auth_tail = ntpath.splitdrive(auth_text)
                if ntpath.normcase(auth_drive) != ntpath.normcase(drive) or not auth_tail.startswith("\\"):
                    raise _failure("PERMISSION_DENIED", "project root is outside the authorized drive")
                auth_parts = tuple(part for part in auth_tail[1:].split("\\") if part)
                if expected_authorized_root_identity is not None:
                    auth_handle = current
                    for part in auth_parts:
                        child = api.open_child_directory(auth_handle, part)
                        handles.append(child)
                        auth_handle = child
                    if api.info(auth_handle, directory=True)[0] != tuple(expected_authorized_root_identity):
                        raise _failure("ACCESS_VIOLATION", "authorized workspace root identity changed")
                    current = auth_handle
                    relative_parts = _same_path_or_child(path, authorized_root)
                    path_parts = relative_parts
                else:
                    relative_parts = _same_path_or_child(path, authorized_root)
                    for part in auth_parts:
                        child = api.open_child_directory(current, part)
                        handles.append(child)
                        current = child
                    path_parts = relative_parts
            for part in path_parts:
                child = api.open_child_directory(current, part)
                handles.append(child)
                current = child
            identity = api.info(current, directory=True)[0]
            return cls(path, tuple(identity), api=api, handles=handles)
        except ExecutionContractError as exc:
            _record_open_cleanup_and_propagate(exc, api.close, reversed(handles))
            raise
        except Exception as exc:
            error = _failure("ACCESS_VIOLATION", "Windows project root could not be opened without reparse traversal", exc)
            _record_open_cleanup_and_propagate(error, api.close, reversed(handles))
            raise error from exc
        except BaseException as exc:
            _record_open_cleanup_and_propagate(exc, api.close, reversed(handles))
            raise

    def read_file(self, relative_parts: Sequence[str], *, max_bytes: int) -> tuple[bytes, dict[str, int]]:
        if self._closed:
            raise _failure("ACCESS_VIOLATION", "project root binding is closed")
        parts = tuple(relative_parts)
        if (not parts or any(not isinstance(part, str) or not part or part in {".", ".."}
                             or "/" in part or "\\" in part or "\x00" in part for part in parts)):
            raise _failure("ACCESS_VIOLATION", "source path is not a project-relative file")
        if self._api is not None:
            return self._read_windows(parts, max_bytes=max_bytes)
        return self._read_posix(parts, max_bytes=max_bytes)

    def _read_posix(self, parts: Sequence[str], *, max_bytes: int) -> tuple[bytes, dict[str, int]]:
        # A FIFO opened read-only can block before fstat gets a chance to
        # reject it. Nonblocking has no effect on regular-file reads and lets
        # the descriptor's actual type be checked safely after the open.
        if not getattr(os, "O_NONBLOCK", 0):
            raise _failure("ACCESS_VIOLATION", "platform cannot open source files without blocking")
        flags = (os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        directory_flags = _DIR_FLAGS
        opened_dirs: list[int] = []
        file_fd = None
        primary_error: BaseException | None = None
        try:
            current = os.dup(self._fd)
            opened_dirs.append(current)
            for component in parts[:-1]:
                child = os.open(component, directory_flags, dir_fd=current)
                opened_dirs.append(child)
                info = os.fstat(child)
                named = os.stat(component, dir_fd=current, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode) or not stat.S_ISDIR(named.st_mode)
                        or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                    raise _failure("ACCESS_VIOLATION", "source directory changed while opening")
                current = child
            file_fd = os.open(parts[-1], flags, dir_fd=current)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise _failure("ARTIFACT_MISSING", "source artifact must be a regular Java file")
            if before.st_nlink != 1:
                raise _failure("ACCESS_VIOLATION", "source artifact has multiple filesystem links")
            if before.st_size > max_bytes:
                raise _failure("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit")
            chunks = bytearray()
            while len(chunks) <= max_bytes:
                block = os.read(file_fd, min(65536, max_bytes + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
            after = os.fstat(file_fd)
            identity_before = (before.st_dev, before.st_ino, before.st_size,
                               getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)),
                               getattr(before, "st_ctime_ns", int(before.st_ctime * 1_000_000_000)),
                               before.st_nlink)
            identity_after = (after.st_dev, after.st_ino, after.st_size,
                              getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
                              getattr(after, "st_ctime_ns", int(after.st_ctime * 1_000_000_000)),
                              after.st_nlink)
            if len(chunks) > max_bytes:
                raise _failure("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit")
            if len(chunks) != before.st_size or identity_before != identity_after:
                raise _failure("ARTIFACT_CHANGED", "source artifact changed while its bytes were being read")
            return bytes(chunks), {"device": int(before.st_dev), "inode": int(before.st_ino),
                                   "size": int(before.st_size), "mtime_ns": int(identity_before[3])}
        except ExecutionContractError as exc:
            primary_error = exc
            raise
        except FileNotFoundError as exc:
            primary_error = _failure("ARTIFACT_MISSING", "source artifact or a project directory is unavailable", exc)
            raise primary_error from exc
        except OSError as exc:
            primary_error = _failure("ACCESS_VIOLATION", "source path could not be opened without following links", exc)
            raise primary_error from exc
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            owned = ([] if file_fd is None else [file_fd]) + list(reversed(opened_dirs))
            _finish_cleanup(os.close, owned, primary_error)

    def _read_windows(self, parts: Sequence[str], *, max_bytes: int) -> tuple[bytes, dict[str, int]]:
        import ntpath
        from ._windows_parameter_publish import _component

        api = self._api
        if api is None:
            raise _failure("ACCESS_VIOLATION", "Windows project root handle is unavailable")
        handles: list[object] = []
        file_handle = None
        primary_error: BaseException | None = None
        try:
            current = self._handles[-1]
            for component in parts[:-1]:
                _component(component)
                child = api.open_child_directory(current, component)
                handles.append(child)
                current = child
            _component(parts[-1])
            file_handle = api.open_source_for_snapshot(current, parts[-1])
            identity, size, created, written, links = api.source_fingerprint(file_handle)
            if links != 1:
                raise _failure("ACCESS_VIOLATION", "source artifact has multiple filesystem links")
            if size > max_bytes:
                raise _failure("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit")
            output = bytearray()
            while len(output) <= max_bytes:
                block = api.read_chunk(file_handle, min(65536, max_bytes + 1 - len(output)))
                if not block:
                    break
                output.extend(block)
            after = api.source_fingerprint(file_handle)
            if len(output) > max_bytes:
                raise _failure("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit")
            if len(output) != size or (identity, size, created, written, links) != after:
                raise _failure("ARTIFACT_CHANGED", "source artifact changed while its bytes were being read")
            mtime_unix_ns = (int(written) - 116444736000000000) * 100
            return bytes(output), {"device": int(identity[0]), "inode": int(identity[1]),
                                   "size": int(size), "mtime_ns": mtime_unix_ns}
        except ExecutionContractError as exc:
            primary_error = exc
            raise
        except FileNotFoundError as exc:
            primary_error = _failure("ARTIFACT_MISSING", "source artifact or a project directory is unavailable", exc)
            raise primary_error from exc
        except Exception as exc:
            primary_error = _failure("ACCESS_VIOLATION", "Windows source path could not be opened without reparse traversal", exc)
            raise primary_error from exc
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            owned = ([] if file_handle is None else [file_handle]) + list(reversed(handles))
            _finish_cleanup(api.close, owned, primary_error)

    def hash_file(self, relative_parts: Sequence[str]) -> tuple[str, int]:
        """Stream a digest from an ordinary file below this pinned project root."""
        if self._closed:
            raise _failure("ACCESS_VIOLATION", "project root binding is closed")
        parts = tuple(relative_parts)
        if not parts or any(not isinstance(part, str) or not part or part in {".", ".."}
                            or "/" in part or "\\" in part or "\x00" in part for part in parts):
            raise _failure("ACCESS_VIOLATION", "digest path is not a project-relative file")
        if self._api is not None:
            return self._hash_windows(parts)
        return self._hash_posix(parts)

    def _hash_posix(self, parts: Sequence[str]) -> tuple[str, int]:
        if self._fd is None:
            raise _failure("ACCESS_VIOLATION", "project root descriptor is unavailable")
        if not getattr(os, "O_NONBLOCK", 0):
            raise _failure("ACCESS_VIOLATION", "platform cannot open digest files without blocking")
        directories: list[int] = []
        file_fd = None
        primary_error: BaseException | None = None

        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)

        try:
            current = os.dup(self._fd)
            directories.append(current)
            for component in parts[:-1]:
                child = os.open(component, _DIR_FLAGS, dir_fd=current)
                directories.append(child)
                info = os.fstat(child)
                named = os.stat(component, dir_fd=current, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode) or not stat.S_ISDIR(named.st_mode)
                        or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                    raise _failure("ACCESS_VIOLATION", "digest directory changed while opening")
                current = child
            flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            file_fd = os.open(parts[-1], flags, dir_fd=current)
            before = os.fstat(file_fd)
            named = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(named.st_mode):
                raise _failure("ACCESS_VIOLATION", "digest target is not an ordinary file")
            if before.st_nlink != 1 or named.st_nlink != 1:
                raise _failure("ACCESS_VIOLATION", "digest target has multiple filesystem links")
            if identity(before) != identity(named):
                raise _failure("ARTIFACT_CHANGED", "digest target changed before reading")
            digest = hashlib.sha256()
            size = 0
            while size < before.st_size:
                block = os.read(file_fd, min(1024 * 1024, before.st_size - size))
                if not block:
                    break
                digest.update(block)
                size += len(block)
            after = os.fstat(file_fd)
            named = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            if (not stat.S_ISREG(named.st_mode) or size != before.st_size
                    or identity(before) != identity(after) or identity(before) != identity(named)):
                raise _failure("ARTIFACT_CHANGED", "digest target changed while reading")
            for index, component in enumerate(parts[:-1]):
                held = os.fstat(directories[index + 1])
                named = os.stat(component, dir_fd=directories[index], follow_symlinks=False)
                if (not stat.S_ISDIR(named.st_mode)
                        or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)):
                    raise _failure("ARTIFACT_CHANGED", "digest directory changed while reading")
            return digest.hexdigest(), size
        except ExecutionContractError as exc:
            primary_error = exc
            raise
        except FileNotFoundError as exc:
            primary_error = _failure("ARTIFACT_MISSING", "digest target or a project directory is unavailable", exc)
            raise primary_error from exc
        except OSError as exc:
            primary_error = _failure("ACCESS_VIOLATION", "digest path could not be opened without following links", exc)
            raise primary_error from exc
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            owned = ([] if file_fd is None else [file_fd]) + list(reversed(directories))
            _finish_cleanup(os.close, owned, primary_error)

    def _hash_windows(self, parts: Sequence[str]) -> tuple[str, int]:
        from ._windows_parameter_publish import _component

        api = self._api
        if api is None:
            raise _failure("ACCESS_VIOLATION", "Windows project root handle is unavailable")
        handles: list[object] = []
        file_handle = None
        primary_error: BaseException | None = None
        try:
            current = self._handles[-1]
            for component in parts[:-1]:
                _component(component)
                current = api.open_child_directory(current, component)
                handles.append(current)
            _component(parts[-1])
            file_handle = api.open_source_for_snapshot(current, parts[-1])
            before = api.source_fingerprint(file_handle)
            if before[4] != 1:
                raise _failure("ACCESS_VIOLATION", "digest target has multiple filesystem links")
            digest = hashlib.sha256()
            size = 0
            while size < before[1]:
                block = api.read_chunk(file_handle, min(1024 * 1024, before[1] - size))
                if not block:
                    break
                digest.update(block)
                size += len(block)
            if size != before[1] or api.source_fingerprint(file_handle) != before:
                raise _failure("ARTIFACT_CHANGED", "digest target changed while reading")
            return digest.hexdigest(), size
        except ExecutionContractError as exc:
            primary_error = exc
            raise
        except FileNotFoundError as exc:
            primary_error = _failure("ARTIFACT_MISSING", "digest target or a project directory is unavailable", exc)
            raise primary_error from exc
        except Exception as exc:
            primary_error = _failure("ACCESS_VIOLATION", "Windows digest path could not be opened without reparse traversal", exc)
            raise primary_error from exc
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            owned = ([] if file_handle is None else [file_handle]) + list(reversed(handles))
            _finish_cleanup(api.close, owned, primary_error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._api is not None:
            failures, interrupts = _close_owned(self._api.close, reversed(self._handles))
            # Every handle has now been attempted exactly once, so the
            # ownership bookkeeping is complete even when an interrupt must
            # propagate: the interrupted handle is never retried.
            self._handles.clear()
            unconfirmed = failures + interrupts
            if unconfirmed:
                error = _ReleaseUnconfirmed(
                    "Windows project root handle release could not be confirmed",
                    unconfirmed)
                # Keep this attempt's classified release facts on the
                # binding the caller passed to close_binding_releasing;
                # they are per-operation and never live on a possibly
                # reused exception instance.
                self._release_error = error
                if interrupts:
                    _propagate_cleanup_interrupts(interrupts, classified=error)
                raise error from failures[0]
            return
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                os.close(fd)
            except OSError as exc:
                raise _ReleaseUnconfirmed(
                    "project root descriptor release could not be confirmed",
                    [exc]) from exc
            except BaseException as exc:
                # The interrupt propagates raw (never converted into an
                # Exception that an ordinary except would swallow), but it
                # carries the classified unconfirmed-release facts.  The
                # same facts also stay on this binding (its single close
                # attempt), so a reused interrupt instance cannot make a
                # later operation report this operation's stale carrier.
                classified = _ReleaseUnconfirmed(
                    "project root descriptor release could not be confirmed",
                    [exc])
                self._release_error = classified
                _propagate_cleanup_interrupts([exc], classified=classified)


def open_project_root_binding(root: str | os.PathLike[str], *, authorized_root: str | os.PathLike[str] | None = None,
                              expected_authorized_root_identity: tuple[int, ...] | None = None) -> ProjectRootBinding:
    return ProjectRootBinding.open(root, authorized_root=authorized_root,
                                   expected_authorized_root_identity=expected_authorized_root_identity)
