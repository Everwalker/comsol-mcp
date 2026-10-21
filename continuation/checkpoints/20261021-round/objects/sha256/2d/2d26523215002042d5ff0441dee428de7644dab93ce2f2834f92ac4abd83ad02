#!/usr/bin/env python3
"""Create and verify a non-overwriting Windows source handoff snapshot."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PREFIXES = (
    ".git/",
    ".phase1-private/",
    "evidence/",
    "docs/full_project_execution/evidence/",
    "execution-scratch/",
    "docs/full_project_execution/execution-scratch/",
    "local-handoff/",
    ".local-handoff/",
    ".windows-node-local/",
)
EXCLUDED_DIR_NAMES = frozenset({
    ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "__pycache__", "node_modules", "runtime",
    "execution-scratch", "evidence", "logs", "local-handoff",
    ".local-handoff", ".windows-node-local", ".phase1-private",
    "build", "dist", "target",
})
EXCLUDED_SUFFIXES = (
    ".mph", ".mphbin", ".class", ".recovery", ".status", ".log",
    ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".zip", ".gz",
    ".jar", ".pem", ".key", ".p12", ".pfx", ".p7b", ".p7c",
    ".der", ".crt", ".cer", ".jks", ".keystore",
)
DEFAULT_SOURCE_SUFFIXES = frozenset({
    ".py", ".pyi", ".java", ".js", ".jsx", ".ts", ".tsx", ".mjs",
    ".cjs", ".html", ".htm", ".css", ".scss", ".sql", ".sh", ".bash",
    ".zsh", ".ps1", ".bat", ".cmd", ".m", ".jl", ".rb", ".go", ".rs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".json", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".conf", ".md", ".rst", ".txt",
    ".xml", ".gradle", ".properties", ".cmake", ".mk", ".svg",
})
DEFAULT_SOURCE_NAMES = frozenset({
    "dockerfile", "makefile", "license", "copying", "notice", "readme",
    "requirements.txt", "pyproject.toml", "setup.cfg", "setup.py",
    "go.mod", "go.sum", "cargo.toml", "cargo.lock", "package.json",
    "tsconfig.json", "pom.xml", "build.gradle", "gradlew", "manifest.in",
})
SECRET_NAME_RE = re.compile(
    r"(^|[._-])(secret|secrets|credential|credentials|token|tokens|access[._-]?token|"
    r"api[._-]?key|private[._-]?key|password|passwd)([._-]|$)",
    re.IGNORECASE,
)
METADATA_FILES = frozenset({"SNAPSHOT_MANIFEST.json", "WORKTREE.patch", "NEW_FILES.json"})
METADATA_WINDOWS_KEYS = frozenset(name.casefold() for name in METADATA_FILES)
WINDOWS_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10))})
WINDOWS_FORBIDDEN_COMPONENT_CHARS = frozenset('<>:"\\|?*')
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_HASH_RE = re.compile(r"[0-9a-f]{40,64}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def safe_member_path(name: str) -> PurePosixPath:
    """Return one canonical path safe on both POSIX and Windows filesystems."""
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name or ":" in name:
        raise ValueError(f"unsafe archive member: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or path == PurePosixPath(".") or path.as_posix() != name:
        raise ValueError(f"unsafe archive member: {name!r}")
    for part in path.parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or any(character in WINDOWS_FORBIDDEN_COMPONENT_CHARS or ord(character) < 0x20 for character in part)
        ):
            raise ValueError(f"unsafe archive member: {name!r}")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"unsafe archive member: {name!r}")
    return path


def windows_key(path: PurePosixPath) -> str:
    return "/".join(part.casefold() for part in path.parts)


def git_paths(root: Path, *args: str) -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", *args, "-z"], cwd=root)
    return [Path(os.fsdecode(item)) for item in raw.split(b"\0") if item]


def git_head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def git_head_paths(root: Path) -> set[str]:
    raw = subprocess.check_output(["git", "ls-tree", "-r", "-z", "--name-only", "HEAD"], cwd=root)
    return {os.fsdecode(item) for item in raw.split(b"\0") if item}


def _source_exclusion_reason(name: str, *, default_selection: bool) -> str | None:
    path = safe_member_path(name)
    key = windows_key(path)
    if key in METADATA_WINDOWS_KEYS:
        return "reserved snapshot metadata name"
    folded = name.casefold()
    if any(folded.startswith(prefix.casefold()) for prefix in EXCLUDED_PREFIXES):
        return "excluded artifact directory"
    if any(part.casefold() in EXCLUDED_DIR_NAMES for part in path.parts[:-1]):
        return "runtime, evidence, or cache directory"
    basename = path.name.casefold()
    if (
        basename in {".envrc", ".npmrc", ".pypirc", ".netrc", "netrc"}
        or basename == ".env"
        or basename.startswith(".env.")
        or SECRET_NAME_RE.search(basename)
    ):
        return "secret-like filename"
    if basename.endswith(EXCLUDED_SUFFIXES):
        return "runtime, binary, or key material suffix"
    if default_selection and path.suffix.casefold() not in DEFAULT_SOURCE_SUFFIXES and basename not in DEFAULT_SOURCE_NAMES:
        return "not a recognized source or documentation file"
    return None


def _regular_source_file(root: Path, relative: PurePosixPath) -> Path:
    """Resolve a selected source file without following any symlink component."""
    root = root.resolve(strict=True)
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise ValueError(f"source path is missing or unreadable: {relative.as_posix()!r}") from error
        if stat.S_ISLNK(mode):
            raise ValueError(f"source path contains a symlink: {relative.as_posix()!r}")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            raise ValueError(f"source path has a non-directory parent: {relative.as_posix()!r}")
        if index == len(relative.parts) - 1 and not stat.S_ISREG(mode):
            raise ValueError(f"source path is not a regular file: {relative.as_posix()!r}")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as error:
        raise ValueError(f"source path resolves outside the repository: {relative.as_posix()!r}") from error
    return current


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _read_source_manifest(root: Path, manifest_path: Path) -> tuple[list[tuple[PurePosixPath, str]], str, str]:
    """Load a review manifest: schema_version, source_head, and path/hash files."""
    manifest_path = Path(manifest_path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(f"source manifest is missing, not a regular file, or a symlink: {manifest_path}")
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("source manifest is unreadable or invalid JSON") from error
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "source_head", "files"}:
        raise ValueError("source manifest must contain exactly schema_version, source_head, and files")
    if (
        not isinstance(manifest["schema_version"], int)
        or isinstance(manifest["schema_version"], bool)
        or manifest["schema_version"] != 1
    ):
        raise ValueError("unsupported source manifest schema_version")
    expected_head = manifest["source_head"]
    if not isinstance(expected_head, str) or not GIT_HASH_RE.fullmatch(expected_head):
        raise ValueError("source manifest source_head is not a full Git object ID")
    if expected_head != git_head(root):
        raise ValueError("source manifest source_head does not match the current HEAD")
    entries = manifest["files"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("source manifest files must be a non-empty list")
    selected: list[tuple[PurePosixPath, str]] = []
    exact_names: set[str] = set()
    windows_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("source manifest file entries must contain exactly path and sha256")
        relative = safe_member_path(entry["path"])
        name = relative.as_posix()
        reason = _source_exclusion_reason(name, default_selection=False)
        if reason:
            raise ValueError(f"source manifest selects an excluded path {name!r}: {reason}")
        key = windows_key(relative)
        if name in exact_names or key in windows_names:
            raise ValueError(f"source manifest contains duplicate or duplicate-case path: {name!r}")
        digest = entry["sha256"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"source manifest has an invalid SHA-256 for {name!r}")
        source = _regular_source_file(root, relative)
        actual_digest = sha256(source)
        if actual_digest != digest:
            raise ValueError(f"source manifest hash drift: {name!r}")
        exact_names.add(name)
        windows_names.add(key)
        selected.append((relative, digest))
    selected.sort(key=lambda item: item[0].as_posix())
    return selected, hashlib.sha256(raw).hexdigest(), expected_head


def selected_paths(root: Path, source_manifest: Path | None = None) -> list[Path]:
    """Select safe tracked sources, or the exact files in a hash-bound manifest."""
    if source_manifest is not None:
        return [Path(*path.parts) for path, _digest in _read_source_manifest(root, source_manifest)[0]]
    candidates = git_paths(root, "--cached")
    selected: list[Path] = []
    exact_names: set[str] = set()
    windows_names: set[str] = set()
    for item in candidates:
        name = item.as_posix()
        if _source_exclusion_reason(name, default_selection=True) is not None:
            continue
        relative = safe_member_path(name)
        key = windows_key(relative)
        if name in exact_names or key in windows_names:
            raise ValueError(f"tracked sources contain duplicate or duplicate-case path: {name!r}")
        _regular_source_file(root, relative)
        exact_names.add(name)
        windows_names.add(key)
        selected.append(Path(*relative.parts))
    if not selected:
        raise ValueError("no tracked source files remain after safe source selection")
    return sorted(selected, key=lambda path: path.as_posix())


def _selected_worktree_patch(root: Path, paths: list[Path]) -> bytes:
    if not paths:
        return b""
    return subprocess.check_output(
        [
            "git", "diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames",
            "HEAD", "--", *(f":(literal){path.as_posix()}" for path in paths),
        ],
        cwd=root,
    )


def _refuse_selected_renames(root: Path, paths: list[Path]) -> None:
    selected = {windows_key(safe_member_path(path.as_posix())) for path in paths}
    head_paths = git_head_paths(root)
    selected_new_path = any(path.as_posix() not in head_paths for path in paths)
    raw = subprocess.check_output(["git", "diff", "--name-status", "-z", "--find-renames", "HEAD"], cwd=root)
    records = [item for item in raw.split(b"\0") if item]
    index = 0
    deleted_paths: list[str] = []
    while index < len(records):
        status = os.fsdecode(records[index])
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(records):
                raise ValueError("Git returned a malformed rename record")
            old_name = os.fsdecode(records[index])
            new_name = os.fsdecode(records[index + 1])
            index += 2
            if any(windows_key(safe_member_path(name)) in selected for name in (old_name, new_name)):
                raise ValueError(f"refuse snapshot: selected source participates in a Git rename/copy: {old_name!r} -> {new_name!r}")
        else:
            if index >= len(records):
                raise ValueError("Git returned a malformed diff name record")
            name = os.fsdecode(records[index])
            index += 1
            if status == "D":
                deleted_paths.append(name)
    if selected_new_path and deleted_paths:
        raise ValueError(
            "refuse snapshot: selected source participates in a Git rename or is ambiguous with a tracked deletion "
            f"(possible rename involving {deleted_paths[0]!r})"
        )


def _member_bytes(tar: tarfile.TarFile, members: dict[str, tarfile.TarInfo], name: str) -> bytes:
    stream = tar.extractfile(members[name])
    if stream is None:
        raise ValueError(f"archive member cannot be read: {name}")
    with stream:
        return stream.read()


def _write_archive_temp(path: Path, files: list[Path], root: Path, metadata: dict, patch: bytes, new_files: list[str]) -> None:
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for relative in files:
            source = _regular_source_file(root, safe_member_path(relative.as_posix()))
            tar.add(source, arcname=relative.as_posix(), recursive=False)
        metadata_items = (
            ("SNAPSHOT_MANIFEST.json", json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8") + b"\n"),
            ("WORKTREE.patch", patch),
            ("NEW_FILES.json", json.dumps(new_files, indent=2).encode("utf-8") + b"\n"),
        )
        for name, data in metadata_items:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))


def _verify_created_archive(
    path: Path,
    metadata: dict,
    patch: bytes,
    new_files: list[str],
    root: Path,
    source_manifest: Path | None,
) -> None:
    with tarfile.open(path, "r:gz") as tar:
        manifest, members = validate_archive(tar)
        if manifest != metadata:
            raise ValueError("preflight manifest does not match the selected source set")
        if _member_bytes(tar, members, "WORKTREE.patch") != patch:
            raise ValueError("preflight WORKTREE.patch differs from the selected source diff")
        actual_new_files = json.loads(_member_bytes(tar, members, "NEW_FILES.json"))
        if actual_new_files != new_files:
            raise ValueError("preflight NEW_FILES.json does not match the selected source set")
    if git_head(root) != metadata["source_head"]:
        raise ValueError("Git HEAD changed while creating the snapshot")
    if source_manifest is not None and (
        source_manifest.is_symlink()
        or not source_manifest.is_file()
        or sha256(source_manifest) != metadata["source_manifest_sha256"]
    ):
        raise ValueError("source manifest changed while creating the snapshot")
    if _selected_worktree_patch(root, [Path(entry["path"]) for entry in metadata["files"]]) != patch:
        raise ValueError("selected worktree diff changed while creating the snapshot")
    for entry in metadata["files"]:
        source = _regular_source_file(root, safe_member_path(entry["path"]))
        if sha256(source) != entry["sha256"]:
            raise ValueError(f"source changed while creating the snapshot: {entry['path']!r}")


def _publish_without_overwrite(temp_path: Path, archive: Path) -> None:
    try:
        os.link(temp_path, archive)
        return
    except FileExistsError:
        raise FileExistsError(f"refuse to overwrite archive: {archive}")
    except OSError:
        # Some FAT/network filesystems do not support hard links. Preserve the
        # no-overwrite contract with an exclusive create as a portable fallback.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(archive, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output, temp_path.open("rb") as source:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            try:
                archive.unlink()
            except OSError:
                pass
            raise


def create_snapshot(root: Path, archive: Path, snapshot_id: str, source_manifest: Path | None = None) -> dict:
    root = Path(root).resolve(strict=True)
    archive = Path(os.path.abspath(archive))
    if os.path.lexists(archive):
        raise FileExistsError(f"refuse to overwrite archive: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    source_manifest_sha256 = None
    source_manifest_path = None
    expected_digests: dict[str, str] | None = None
    source_head = git_head(root)
    if source_manifest is None:
        paths = selected_paths(root)
        selection_mode = "tracked_source_default"
    else:
        source_manifest_path = Path(os.path.abspath(source_manifest))
        manifest_entries, source_manifest_sha256, expected_head = _read_source_manifest(root, source_manifest_path)
        if source_head != expected_head:
            raise ValueError("source manifest source_head changed while loading the source selection")
        source_head = expected_head
        paths = [Path(*path.parts) for path, _digest in manifest_entries]
        expected_digests = {path.as_posix(): digest for path, digest in manifest_entries}
        selection_mode = "hash_bound_source_manifest"
    if not paths:
        raise ValueError("source selection is empty")
    _refuse_selected_renames(root, paths)
    if git_head(root) != source_head:
        raise ValueError("Git HEAD changed after source selection")
    head_paths = git_head_paths(root)
    entries = []
    for relative in paths:
        safe = safe_member_path(relative.as_posix())
        source = _regular_source_file(root, safe)
        current_digest = sha256(source)
        expected_digest = expected_digests.get(safe.as_posix()) if expected_digests is not None else None
        if expected_digest is not None and current_digest != expected_digest:
            raise ValueError(f"source manifest hash drift after source selection: {safe.as_posix()!r}")
        entries.append({
            "path": safe.as_posix(),
            "sha256": expected_digest if expected_digest is not None else current_digest,
            "size": source.stat().st_size,
        })
    patch = _selected_worktree_patch(root, paths)
    new_files = [entry["path"] for entry in entries if entry["path"] not in head_paths]
    metadata = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "source_head": source_head,
        "selection_mode": selection_mode,
        "source_manifest_sha256": source_manifest_sha256,
        "files": entries,
        "excluded_prefixes": list(EXCLUDED_PREFIXES),
        "excluded_suffixes": list(EXCLUDED_SUFFIXES),
    }
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{archive.name}.", suffix=".partial", dir=archive.parent, delete=False) as temp:
            temp_name = Path(temp.name)
        _write_archive_temp(temp_name, paths, root, metadata, patch, new_files)
        _verify_created_archive(temp_name, metadata, patch, new_files, root, source_manifest_path)
        if os.path.lexists(archive):
            raise FileExistsError(f"refuse to overwrite archive: {archive}")
        _publish_without_overwrite(temp_name, archive)
    finally:
        if temp_name is not None:
            try:
                temp_name.unlink()
            except FileNotFoundError:
                pass
    return {"archive": str(archive), "archive_sha256": sha256(archive), **metadata}


def validate_archive(tar: tarfile.TarFile) -> tuple[dict, dict[str, tarfile.TarInfo]]:
    """Validate all names and data before creating an extraction destination."""
    members: dict[str, tarfile.TarInfo] = {}
    names_by_windows_key: dict[str, str] = {}
    for member in tar.getmembers():
        path = safe_member_path(member.name)
        if not member.isfile():
            raise ValueError(f"non-regular archive member rejected: {member.name}")
        key = windows_key(path)
        if key in names_by_windows_key:
            raise ValueError(f"duplicate Windows archive member: {member.name!r}")
        names_by_windows_key[key] = member.name
        members[member.name] = member
    if "SNAPSHOT_MANIFEST.json" not in members:
        raise ValueError("snapshot manifest is missing")
    manifest_stream = tar.extractfile(members["SNAPSHOT_MANIFEST.json"])
    if manifest_stream is None:
        raise ValueError("snapshot manifest cannot be read")
    try:
        manifest = json.load(manifest_stream, object_pairs_hook=_reject_duplicate_json_keys)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("snapshot manifest is invalid") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("snapshot_id"), str) or not isinstance(manifest.get("files"), list):
        raise ValueError("snapshot manifest has an invalid schema")
    if "schema_version" in manifest and (
        not isinstance(manifest["schema_version"], int)
        or isinstance(manifest["schema_version"], bool)
        or manifest["schema_version"] != 1
    ):
        raise ValueError("snapshot manifest has an unsupported schema_version")
    declared: dict[str, dict] = {}
    declared_windows_keys: set[str] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("snapshot manifest contains an invalid file entry")
        path = safe_member_path(entry.get("path"))
        if windows_key(path) in METADATA_WINDOWS_KEYS or path.as_posix() in declared or windows_key(path) in declared_windows_keys:
            raise ValueError(f"duplicate or reserved manifest path: {path.as_posix()!r}")
        if not isinstance(entry.get("size"), int) or isinstance(entry["size"], bool) or entry["size"] < 0:
            raise ValueError(f"invalid manifest size: {path.as_posix()!r}")
        if not isinstance(entry.get("sha256"), str) or not SHA256_RE.fullmatch(entry["sha256"]):
            raise ValueError(f"invalid manifest SHA-256: {path.as_posix()!r}")
        declared[path.as_posix()] = entry
        declared_windows_keys.add(windows_key(path))
    expected = set(declared) | METADATA_FILES
    actual = set(members)
    if actual != expected:
        raise ValueError("archive members do not exactly match the snapshot manifest")
    for name, entry in declared.items():
        member = members[name]
        if member.size != entry["size"]:
            raise ValueError(f"manifest size verification failed: {name}")
        stream = tar.extractfile(member)
        if stream is None or sha256_stream(stream) != entry["sha256"]:
            raise ValueError(f"hash verification failed: {name}")
    return manifest, members


def extract_verified(archive: Path, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f"refuse to overwrite destination: {destination}")
    with tarfile.open(archive, "r:gz") as tar:
        manifest, members = validate_archive(tar)
        destination.mkdir(parents=True)
        for name, member in members.items():
            path = destination.joinpath(*safe_member_path(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError(f"archive member cannot be read: {name}")
            with stream, path.open("xb") as output:
                shutil.copyfileobj(stream, output)
    return {"snapshot_id": manifest["snapshot_id"], "verified_files": len(manifest["files"])}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--snapshot-id", required=True)
    create.add_argument(
        "--source-manifest",
        type=Path,
        help=(
            "reviewed JSON with schema_version=1, source_head=<current full Git hash>, and "
            "files=[{path:<repo-relative path>, sha256:<64 lowercase hex>}]"
        ),
    )
    extract = sub.add_parser("extract")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create":
        result = create_snapshot(ROOT, args.archive, args.snapshot_id, args.source_manifest)
    else:
        result = extract_verified(args.archive, args.destination)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
