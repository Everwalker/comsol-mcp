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
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PREFIXES = (".git/", ".phase1-private/", "evidence/", "local-handoff/", ".local-handoff/", ".windows-node-local/")
EXCLUDED_SUFFIXES = (".mph", ".mphbin", ".class", ".recovery", ".status")
METADATA_FILES = frozenset({"SNAPSHOT_MANIFEST.json", "WORKTREE.patch", "NEW_FILES.json"})
WINDOWS_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10))})
SHA256_RE = re.compile(r"[0-9a-f]{64}")


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
        if part in {"", ".", ".."} or part.endswith((".", " ")):
            raise ValueError(f"unsafe archive member: {name!r}")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"unsafe archive member: {name!r}")
    return path


def windows_key(path: PurePosixPath) -> str:
    return "/".join(part.casefold() for part in path.parts)


def git_paths(root: Path, *args: str) -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", *args, "-z"], cwd=root)
    return [Path(os.fsdecode(item)) for item in raw.split(b"\0") if item]


def selected_paths(root: Path) -> list[Path]:
    tracked = git_paths(root, "-co", "--exclude-standard")
    selected = []
    for item in tracked:
        relative = Path(item)
        name = relative.as_posix()
        if name.startswith(EXCLUDED_PREFIXES) or name.endswith(EXCLUDED_SUFFIXES):
            continue
        safe_member_path(name)
        path = root / relative
        if path.is_file() and not path.is_symlink():
            selected.append(relative)
    return sorted(set(selected))


def create_snapshot(root: Path, archive: Path, snapshot_id: str) -> dict:
    if archive.exists():
        raise FileExistsError(f"refuse to overwrite archive: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    paths = selected_paths(root)
    entries = [{"path": path.as_posix(), "sha256": sha256(root / path), "size": (root / path).stat().st_size} for path in paths]
    patch = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=root)
    metadata = {"schema_version": 1, "snapshot_id": snapshot_id, "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(), "files": entries, "excluded_prefixes": EXCLUDED_PREFIXES, "excluded_suffixes": EXCLUDED_SUFFIXES}
    tracked_paths = {path.as_posix() for path in git_paths(root, "--cached")}
    with tarfile.open(archive, "x:gz", format=tarfile.PAX_FORMAT) as tar:
        for path in paths:
            tar.add(root / path, arcname=path.as_posix(), recursive=False)
        for name, data in (("SNAPSHOT_MANIFEST.json", json.dumps(metadata, indent=2, sort_keys=True).encode()), ("WORKTREE.patch", patch), ("NEW_FILES.json", json.dumps([p.as_posix() for p in paths if p.as_posix() not in tracked_paths], indent=2).encode())):
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
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
        manifest = json.load(manifest_stream)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("snapshot manifest is invalid") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("snapshot_id"), str) or not isinstance(manifest.get("files"), list):
        raise ValueError("snapshot manifest has an invalid schema")
    declared: dict[str, dict] = {}
    declared_windows_keys: set[str] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("snapshot manifest contains an invalid file entry")
        path = safe_member_path(entry.get("path"))
        if path.as_posix() in METADATA_FILES or path.as_posix() in declared or windows_key(path) in declared_windows_keys:
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
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create"); create.add_argument("--archive", type=Path, required=True); create.add_argument("--snapshot-id", required=True)
    extract = sub.add_parser("extract"); extract.add_argument("--archive", type=Path, required=True); extract.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = create_snapshot(ROOT, args.archive.resolve(), args.snapshot_id) if args.command == "create" else extract_verified(args.archive.resolve(), args.destination.resolve())
    print(json.dumps(result, indent=2, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
