"""Small, stdlib-only primitives shared by the prospective T038 harness.

This module is part of the frozen harness input set. It intentionally does not
import the candidate package or any MCP/AnyIO modules.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


class NotReady(RuntimeError):
    pass


PROVIDER_IMPORT_ROOTS = frozenset({
    "mph", "jpype", "comsol", "pyvisa", "pywinauto", "pyautogui",
    "Quartz", "AppKit", "Foundation", "Cocoa", "win32com", "pythoncom",
})


def _resolved(path: str | os.PathLike[str], *, strict: bool = True) -> Path:
    try:
        return Path(os.fsdecode(path)).resolve(strict=strict)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise NotReady(f"cannot resolve filesystem path: {path!r}") from exc


def _is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def install_import_origin_guard(
    allowed_roots: list[str | os.PathLike[str]],
    blocked_prefixes: set[str] | frozenset[str] = PROVIDER_IMPORT_ROOTS,
) -> dict[str, Any]:
    """Validate every subsequent module spec against fixed local roots."""
    roots = tuple(_resolved(root, strict=False) for root in allowed_roots)
    zip_roots = tuple(root for root in roots if root.suffix.casefold() == ".zip" and root.is_file())
    denied = frozenset(name.casefold() for name in blocked_prefixes)

    def check_name(fullname: str) -> None:
        top = fullname.partition(".")[0].casefold()
        if top in denied:
            raise NotReady(f"provider/native module import denied: {top}")

    def check_spec(fullname: str, spec: Any) -> None:
        check_name(fullname)
        origin = getattr(spec, "origin", None)
        if origin in {None, "built-in", "frozen"}:
            locations = getattr(spec, "submodule_search_locations", None)
            if origin is None and locations:
                for location in locations:
                    resolved = _resolved(location)
                    if not _is_within(resolved, roots):
                        raise NotReady(f"namespace package origin is outside bound roots: {fullname}")
            elif origin is None:
                raise NotReady(f"module has no fixed origin: {fullname}")
            return
        if isinstance(origin, str) and any(origin.startswith(str(archive) + os.sep) for archive in zip_roots):
            return
        resolved = _resolved(origin)
        if not _is_within(resolved, roots):
            raise NotReady(f"module origin is outside bound roots: {fullname}: {resolved}")

    def check_metadata_context(context: Any) -> tuple[Path, ...]:
        """Allow distribution searches only over exact, already-bound sys.path roots."""
        raw_paths = getattr(context, "path", None)
        if not isinstance(raw_paths, (list, tuple)) or len(raw_paths) > 256:
            raise NotReady("distribution search context has no bounded path list")
        checked: list[Path] = []
        for raw_path in raw_paths:
            if not isinstance(raw_path, (str, os.PathLike)):
                raise NotReady("distribution search context contains a non-path entry")
            try:
                raw_text = os.fsdecode(raw_path)
                resolved = _resolved(raw_text, strict=False)
            except (TypeError, ValueError, NotReady) as exc:
                raise NotReady("distribution search context contains an invalid path") from exc
            if raw_text != str(resolved) or resolved not in roots:
                raise NotReady(f"distribution search path is outside exact bound roots: {raw_text}")
            checked.append(resolved)
        if len(set(checked)) != len(checked):
            raise NotReady("distribution search context contains duplicate roots")
        return tuple(checked)

    def check_metadata_distribution(distribution: Any, search_roots: tuple[Path, ...]) -> None:
        """Require each discovered dist-info/egg-info path directly under a searched root."""
        metadata_path = getattr(distribution, "_path", None)
        try:
            metadata_text = os.fsdecode(metadata_path)
            resolved = _resolved(metadata_text, strict=True)
        except (TypeError, ValueError, NotReady) as exc:
            raise NotReady("distribution metadata has no fixed filesystem origin") from exc
        if resolved.suffix.casefold() not in {".dist-info", ".egg-info"}:
            raise NotReady("distribution metadata origin is not a dist-info or egg-info path")
        if resolved.parent not in search_roots:
            raise NotReady(f"distribution metadata origin is outside the exact search roots: {resolved}")

    class GuardedFinder:
        def __init__(self, finder: Any):
            self._finder = finder

        def find_spec(self, fullname: str, path: Any = None, target: Any = None):
            check_name(fullname)
            method = getattr(self._finder, "find_spec", None)
            if method is None:
                return None
            spec = method(fullname, path, target)
            if spec is not None:
                check_spec(fullname, spec)
            return spec

        def find_distributions(self, context: Any):
            """Delegate PathFinder metadata discovery while bounding both ends."""
            method = getattr(self._finder, "find_distributions", None)
            if method is None:
                return ()
            if self._finder is not importlib.machinery.PathFinder:
                raise NotReady("distribution resolver is not the bound PathFinder")
            search_roots = check_metadata_context(context)
            distributions = method(context)

            def checked_distributions():
                for distribution in distributions:
                    check_metadata_distribution(distribution, search_roots)
                    yield distribution

            return checked_distributions()

        def __repr__(self) -> str:
            return f"GuardedFinder({self._finder!r})"

    original = tuple(sys.meta_path)
    if not original:
        raise NotReady("Python import finder list is empty")
    sys.meta_path[:] = [GuardedFinder(finder) for finder in original]
    return {"allowed_roots": [str(root) for root in roots], "blocked_prefixes": sorted(denied)}


def install_effect_guard(
    read_roots: list[str | os.PathLike[str]],
    write_roots: list[str | os.PathLike[str]],
) -> dict[str, Any]:
    """Fail closed on provider/network/process/signal effects and unowned I/O.

    This is an in-process guard for the prospective software gate, not an OS
    sandbox and not T037 acceptance.
    """
    read_allowed = tuple(_resolved(root, strict=False) for root in read_roots)
    write_allowed = tuple(_resolved(root, strict=False) for root in write_roots)
    forbidden_events = {
        "socket.connect", "socket.connect_ex", "socket.bind", "socket.listen",
        "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
        "socket.sendto", "subprocess.Popen", "os.system", "os.fork",
        "os.posix_spawn", "ctypes.dlopen", "ctypes.dlsym", "os.kill",
        "os.killpg", "signal.signal", "signal.set_wakeup_fd", "os.chdir",
        "os.chmod", "os.chown", "os.utime",
    }

    def path_allowed(value: Any, roots: tuple[Path, ...]) -> bool:
        if isinstance(value, int) or value is None:
            return False
        try:
            candidate = _resolved(os.fsdecode(value), strict=False)
        except (TypeError, ValueError, NotReady):
            return False
        return _is_within(candidate, roots)

    def dirfd_writer_allowed(value: Any) -> bool:
        if not isinstance(value, (str, bytes, os.PathLike)):
            return False
        candidate = Path(os.fsdecode(value))
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            return False
        frame = sys._getframe()
        while frame is not None:
            if frame.f_code is write_new_verified.__code__:
                path = frame.f_locals.get("path")
                if isinstance(path, Path):
                    resolved = _resolved(path, strict=False)
                    return _is_within(resolved, write_allowed)
            frame = frame.f_back
        return False

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event in forbidden_events or event.startswith(("os.spawn", "os.exec")):
            raise NotReady(f"blocked process/provider/network/signal effect: {event}")
        if event == "open" and args:
            path = args[0]
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else 0
            writes = isinstance(mode, str) and any(ch in mode for ch in "wax+")
            writes = writes or (isinstance(flags, int) and bool(flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            )))
            if writes:
                if not path_allowed(path, write_allowed) and not dirfd_writer_allowed(path):
                    raise NotReady(f"write outside task-owned output denied: {path!r}")
            elif not path_allowed(path, read_allowed) and not dirfd_writer_allowed(path):
                raise NotReady(f"read outside fixed candidate/runtime/input roots denied: {path!r}")
        if event in {"os.mkdir", "os.remove", "os.unlink", "os.rmdir", "os.link", "os.symlink"}:
            if not args or not path_allowed(args[0], write_allowed):
                raise NotReady(f"filesystem mutation outside task-owned output denied: {event}")
        if event in {"os.rename", "os.replace"}:
            if len(args) < 2 or not (
                path_allowed(args[0], write_allowed) and path_allowed(args[1], write_allowed)
            ):
                raise NotReady(f"filesystem rename outside task-owned output denied: {event}")

    sys.addaudithook(audit)
    return {
        "read_roots": [str(root) for root in read_allowed],
        "write_roots": [str(root) for root in write_allowed],
        "scope": "cooperative in-process guard only; not T037 OS isolation",
    }


def read_regular(path: str | Path) -> tuple[bytes, dict[str, Any]]:
    """Read a stable regular file through one no-follow descriptor."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise NotReady(f"not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise NotReady(f"file identity changed before read: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        after_fd = os.fstat(fd)
        after_path = path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_mode)
        if identity != (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_mode):
            raise NotReady(f"file changed while reading: {path}")
        if identity != (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns, after_path.st_mode):
            raise NotReady(f"path changed while reading: {path}")
        if len(data) != opened.st_size:
            raise NotReady(f"short read: {path}")
        return data, {
            "path": str(path),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "device": opened.st_dev,
            "inode": opened.st_ino,
            "mtime_ns": opened.st_mtime_ns,
            "mode": opened.st_mode,
        }
    finally:
        os.close(fd)


def read_json_bound(path: str | Path, expected_sha256: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data, ref = read_regular(path)
    if ref["sha256"] != expected_sha256:
        raise NotReady(f"bound JSON digest mismatch: {path}")
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotReady(f"bound JSON is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise NotReady(f"bound JSON root must be an object: {path}")
    return value, ref


def _safe_member(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise NotReady(f"unsafe source member path: {relative!r}")
    current = root
    for part in rel.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise NotReady(f"symlink in source member path: {relative}")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise NotReady(f"source member is not regular: {relative}")
    return current


def source_fingerprint(members: dict[str, dict[str, Any]]) -> str:
    body = {
        name: {"size": ref["size_bytes"], "sha256": ref["sha256"]}
        for name, ref in sorted(members.items())
    }
    packed = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(packed).hexdigest()


def verify_source_binding(binding: dict[str, Any]) -> dict[str, Any]:
    source = binding.get("source")
    if not isinstance(source, dict) or source.get("schema") != "T038_SOURCE_MEMBERS_V1":
        raise NotReady("binding has no T038_SOURCE_MEMBERS_V1 source block")
    root = Path(source["root"])
    root_info = root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise NotReady("candidate source root must be a real directory")
    expected = source.get("members")
    if not isinstance(expected, list) or len(expected) != 207:
        raise NotReady("source binding must contain exactly 207 members")
    expected_rows: dict[str, dict[str, Any]] = {}
    actual_rows: dict[str, dict[str, Any]] = {}
    for row in expected:
        if not isinstance(row, dict) or set(row) != {"relative_path", "size_bytes", "sha256"}:
            raise NotReady("malformed source member row")
        rel = row["relative_path"]
        if rel in expected_rows:
            raise NotReady(f"duplicate source member: {rel}")
        expected_rows[rel] = row
        path = _safe_member(root, rel)
        data, ref = read_regular(path)
        if ref["size_bytes"] != row["size_bytes"] or ref["sha256"] != row["sha256"]:
            raise NotReady(f"source member digest mismatch: {rel}")
        actual_rows[rel] = ref
    digest = source_fingerprint(actual_rows)
    if digest != source.get("fingerprint"):
        raise NotReady("source member fingerprint mismatch")

    actual_paths: set[str] = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        base_path = Path(base)
        for name in dirs:
            if stat.S_ISLNK((base_path / name).lstat().st_mode):
                raise NotReady(f"symlink directory under source root: {base_path / name}")
        for name in files:
            path = base_path / name
            if name.startswith("._") or name in {".DS_Store", "Thumbs.db"}:
                raise NotReady(f"sidecar/noise under source root: {path}")
            if not stat.S_ISREG(path.lstat().st_mode):
                raise NotReady(f"non-regular source entry: {path}")
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != set(expected_rows):
        raise NotReady("source root has missing or extra members")
    return {
        "root": str(root),
        "fingerprint": digest,
        "member_count": len(actual_rows),
        "members": actual_rows,
    }


def verify_runtime_binding(binding: dict[str, Any]) -> dict[str, Any]:
    import importlib.metadata
    import sys

    runtime = binding.get("runtime")
    if not isinstance(runtime, dict):
        raise NotReady("binding has no runtime block")
    expected_executable = str(Path(runtime["executable"]).resolve(strict=True))
    actual_executable = str(Path(sys.executable).resolve(strict=True))
    if actual_executable != expected_executable:
        raise NotReady("interpreter path differs from binding")
    actual_version = ".".join(map(str, sys.version_info[:3]))
    if actual_version != runtime.get("python_version"):
        raise NotReady("Python version differs from binding")
    if str(Path(sys.prefix).resolve(strict=True)) != str(Path(runtime["prefix"]).resolve(strict=True)):
        raise NotReady("interpreter prefix differs from binding")
    if str(Path(sys.base_prefix).resolve(strict=True)) != str(Path(runtime["base_prefix"]).resolve(strict=True)):
        raise NotReady("interpreter base prefix differs from binding")
    expected_site = str(Path(runtime["site_packages"]).resolve(strict=True))
    if expected_site not in runtime.get("sys_path", []):
        raise NotReady("site-packages is absent from the frozen isolated sys.path")
    versions = runtime.get("distributions")
    if not isinstance(versions, dict):
        raise NotReady("runtime distribution versions are missing")

    def normalize_name(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).casefold()

    actual_versions: dict[str, str] = {}
    for name, expected in versions.items():
        actual = importlib.metadata.version(name)
        actual_versions[name] = actual
        if actual != expected:
            raise NotReady(f"distribution version mismatch: {name}")
    actual_distribution_set: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not isinstance(name, str) or not name:
            raise NotReady("installed distribution has no normalized package name")
        normalized = normalize_name(name)
        if normalized in actual_distribution_set:
            raise NotReady(f"duplicate installed distribution metadata: {name}")
        actual_distribution_set[normalized] = distribution.version
    expected_distribution_set = {normalize_name(name): version for name, version in versions.items()}
    if actual_distribution_set != expected_distribution_set:
        raise NotReady("complete installed distribution set differs from the frozen runtime binding")
    return {
        "executable": actual_executable,
        "python_version": actual_version,
        "prefix": str(Path(sys.prefix).resolve(strict=True)),
        "site_packages": expected_site,
        "isolated_sys_path": runtime["sys_path"],
        "distributions": actual_versions,
    }


def verify_bound_files(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actual: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "size_bytes", "sha256"}:
            raise NotReady("malformed bound file row")
        _, ref = read_regular(row["path"])
        if ref["size_bytes"] != row["size_bytes"] or ref["sha256"] != row["sha256"]:
            raise NotReady(f"bound file mismatch: {row['path']}")
        actual.append(ref)
    return actual


def write_new_verified(path: str | Path, data: bytes) -> dict[str, Any]:
    """Create an immutable file relative to a retained directory fd.

    The output digest comes from bytes reread through the same non-inheritable
    descriptor used for the write. Final validation compares both the entry
    under that retained parent fd and the named parent directory.
    """
    path = Path(path)
    parent = path.parent
    parent_info = parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise NotReady(f"output parent must already be a real directory: {path.parent}")
    if parent.resolve(strict=True) != parent.absolute():
        raise NotReady("output parent contains a symlink component")
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_fd = os.open(parent, parent_flags)
    os.set_inheritable(parent_fd, False)
    parent_opened = os.fstat(parent_fd)
    parent_identity = (parent_info.st_dev, parent_info.st_ino, parent_info.st_mode)
    if parent_identity != (parent_opened.st_dev, parent_opened.st_ino, parent_opened.st_mode):
        os.close(parent_fd)
        raise NotReady("output parent identity changed before open")
    file_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    try:
        fd = os.open(path.name, file_flags, 0o600, dir_fd=parent_fd)
        os.set_inheritable(fd, False)
        before = os.fstat(fd)
        view = memoryview(data)
        written = 0
        while written < len(view):
            count = os.write(fd, view[written:])
            if count <= 0:
                raise OSError("short output write")
            written += count
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        terminal_parts: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            terminal_parts.append(chunk)
        terminal = b"".join(terminal_parts)
        after_fd = os.fstat(fd)
        after_entry = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        after_parent_fd = os.fstat(parent_fd)
        after_parent_path = parent.lstat()
        identity = (before.st_dev, before.st_ino, before.st_mode)
        stable_file_fd = (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_mode)
        stable_file_entry = (after_entry.st_dev, after_entry.st_ino, after_entry.st_size, after_entry.st_mtime_ns, after_entry.st_mode)
        if identity != (after_fd.st_dev, after_fd.st_ino, after_fd.st_mode) or stable_file_fd != stable_file_entry:
            raise NotReady(f"output identity changed: {path}")
        if parent_identity != (after_parent_fd.st_dev, after_parent_fd.st_ino, after_parent_fd.st_mode):
            raise NotReady(f"held output parent changed: {parent}")
        if parent_identity != (after_parent_path.st_dev, after_parent_path.st_ino, after_parent_path.st_mode):
            raise NotReady(f"named output parent changed: {parent}")
        if terminal != data or after_fd.st_size != len(data):
            raise NotReady(f"output readback mismatch: {path}")
        os.fsync(parent_fd)
        return {
            "path": str(path),
            "size_bytes": len(terminal),
            "sha256": hashlib.sha256(terminal).hexdigest(),
            "device": after_fd.st_dev,
            "inode": after_fd.st_ino,
            "mtime_ns": after_fd.st_mtime_ns,
            "mode": after_fd.st_mode,
            "parent_ref": {
                "path": str(parent),
                "device": after_parent_fd.st_dev,
                "inode": after_parent_fd.st_ino,
                "mode": after_parent_fd.st_mode,
                "mtime_ns": after_parent_fd.st_mtime_ns,
            },
        }
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def make_dir_new_verified(
    parent_path: str | Path,
    name: str,
    expected_parent: dict[str, Any],
    *,
    mode: int = 0o700,
) -> dict[str, Any]:
    """Create one fresh child directory under a bound held parent descriptor."""
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise NotReady(f"unsafe output directory name: {name!r}")
    parent = Path(parent_path)
    if parent.resolve(strict=True) != parent.absolute():
        raise NotReady("output root contains a symlink component")
    before = parent.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise NotReady("output root is not a real directory")
    if (before.st_dev, before.st_ino, before.st_mode) != (
        expected_parent.get("device"), expected_parent.get("inode"), expected_parent.get("mode")
    ):
        raise NotReady("output root differs from its bound identity")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_fd = os.open(parent, flags)
    os.set_inheritable(parent_fd, False)
    try:
        opened_parent = os.fstat(parent_fd)
        if (opened_parent.st_dev, opened_parent.st_ino, opened_parent.st_mode) != (
            before.st_dev, before.st_ino, before.st_mode
        ):
            raise NotReady("output root identity changed before directory creation")
        os.mkdir(name, mode=mode, dir_fd=parent_fd)
        os.fsync(parent_fd)
        child = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        after_parent_fd = os.fstat(parent_fd)
        after_parent_path = parent.lstat()
        if not stat.S_ISDIR(child.st_mode) or stat.S_ISLNK(child.st_mode):
            raise NotReady("created output entry is not a real directory")
        if (after_parent_fd.st_dev, after_parent_fd.st_ino, after_parent_fd.st_mode) != (
            before.st_dev, before.st_ino, before.st_mode
        ) or (after_parent_path.st_dev, after_parent_path.st_ino, after_parent_path.st_mode) != (
            before.st_dev, before.st_ino, before.st_mode
        ):
            raise NotReady("bound output root identity changed during directory creation")
        return {
            "path": str(parent / name),
            "device": child.st_dev,
            "inode": child.st_ino,
            "mode": child.st_mode,
            "parent_ref": {
                "path": str(parent), "device": after_parent_fd.st_dev,
                "inode": after_parent_fd.st_ino, "mode": after_parent_fd.st_mode,
            },
        }
    finally:
        os.close(parent_fd)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
