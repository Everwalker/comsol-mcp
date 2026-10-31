"""Pinned project roots and bounded no-follow file reads.

Project paths are names used to select an object.  Once a root is admitted,
source reads are relative to its open directory handle so a later path swap
cannot redirect the read to a different tree.
"""
from __future__ import annotations

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
                    info = os.fstat(child)
                    named = os.stat(component, dir_fd=current, follow_symlinks=False)
                    if (not stat.S_ISDIR(info.st_mode) or not stat.S_ISDIR(named.st_mode)
                            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                        os.close(child)
                        raise _failure("ACCESS_VIOLATION", "project directory changed while its root was being bound")
                    descriptors.append(child)
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
        except ExecutionContractError:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise
        except OSError as exc:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise _failure("ACCESS_VIOLATION", "project root contains an unavailable or linked directory", exc) from exc
        try:
            for descriptor in reversed(descriptors[:-1]):
                os.close(descriptor)
        except OSError as exc:
            try:
                binding.close()
            except OSError:
                pass
            raise _failure("ACCESS_VIOLATION", "project root parent handle could not be released") from exc
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
        except ExecutionContractError:
            for handle in reversed(handles):
                try:
                    api.close(handle)
                except Exception:
                    pass
            raise
        except Exception as exc:
            for handle in reversed(handles):
                try:
                    api.close(handle)
                except Exception:
                    pass
            raise _failure("ACCESS_VIOLATION", "Windows project root could not be opened without reparse traversal", exc) from exc

    def read_file(self, relative_parts: Sequence[str], *, max_bytes: int) -> tuple[bytes, dict[str, int]]:
        if self._closed:
            raise _failure("ACCESS_VIOLATION", "project root binding is closed")
        parts = tuple(relative_parts)
        if not parts or any(not isinstance(part, str) or not part or part in {".", ".."} for part in parts):
            raise _failure("ACCESS_VIOLATION", "source path is not a project-relative file")
        if self._api is not None:
            return self._read_windows(parts, max_bytes=max_bytes)
        return self._read_posix(parts, max_bytes=max_bytes)

    def _read_posix(self, parts: Sequence[str], *, max_bytes: int) -> tuple[bytes, dict[str, int]]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        directory_flags = _DIR_FLAGS
        opened_dirs: list[int] = []
        file_fd = None
        try:
            current = os.dup(self._fd)
            opened_dirs.append(current)
            for component in parts[:-1]:
                child = os.open(component, directory_flags, dir_fd=current)
                info = os.fstat(child)
                named = os.stat(component, dir_fd=current, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode) or not stat.S_ISDIR(named.st_mode)
                        or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                    os.close(child)
                    raise _failure("ACCESS_VIOLATION", "source directory changed while opening")
                opened_dirs.append(child)
                current = child
            file_fd = os.open(parts[-1], flags, dir_fd=current)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise _failure("ARTIFACT_MISSING", "source artifact must be a regular Java file")
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
                               getattr(before, "st_ctime_ns", int(before.st_ctime * 1_000_000_000)))
            identity_after = (after.st_dev, after.st_ino, after.st_size,
                              getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
                              getattr(after, "st_ctime_ns", int(after.st_ctime * 1_000_000_000)))
            if len(chunks) > max_bytes:
                raise _failure("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit")
            if len(chunks) != before.st_size or identity_before != identity_after:
                raise _failure("ARTIFACT_CHANGED", "source artifact changed while its bytes were being read")
            return bytes(chunks), {"device": int(before.st_dev), "inode": int(before.st_ino),
                                   "size": int(before.st_size), "mtime_ns": int(identity_before[3])}
        except ExecutionContractError:
            raise
        except FileNotFoundError as exc:
            raise _failure("ARTIFACT_MISSING", "source artifact or a project directory is unavailable", exc) from exc
        except OSError as exc:
            raise _failure("ACCESS_VIOLATION", "source path could not be opened without following links", exc) from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for descriptor in reversed(opened_dirs):
                os.close(descriptor)

    def _read_windows(self, parts: Sequence[str], *, max_bytes: int) -> tuple[bytes, dict[str, int]]:
        import ntpath
        from ._windows_parameter_publish import _component

        api = self._api
        handles: list[object] = []
        file_handle = None
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
        except ExecutionContractError:
            raise
        except FileNotFoundError as exc:
            raise _failure("ARTIFACT_MISSING", "source artifact or a project directory is unavailable", exc) from exc
        except Exception as exc:
            raise _failure("ACCESS_VIOLATION", "Windows source path could not be opened without reparse traversal", exc) from exc
        finally:
            if file_handle is not None:
                api.close(file_handle)
            for handle in reversed(handles):
                api.close(handle)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._api is not None:
            for handle in reversed(self._handles):
                self._api.close(handle)
            self._handles.clear()
            return
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)


def open_project_root_binding(root: str | os.PathLike[str], *, authorized_root: str | os.PathLike[str] | None = None,
                              expected_authorized_root_identity: tuple[int, ...] | None = None) -> ProjectRootBinding:
    return ProjectRootBinding.open(root, authorized_root=authorized_root,
                                   expected_authorized_root_identity=expected_authorized_root_identity)
