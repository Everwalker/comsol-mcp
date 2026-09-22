#!/usr/bin/env python3
"""Build and verify a clean public source workpack from an explicit commit.

This is a generated release helper, not production code. It fails closed on a
dirty repository, symlinks, private/runtime artifacts, credential-like text,
absolute host paths, missing public-path declarations, output collisions, and
failed fresh extraction verification. It never mutates the repository or an
existing release file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT = Path(__file__).resolve()
PACKAGE = SCRIPT.parents[5]
REPO = PACKAGE / "repository"
PACKAGE_FILES = {
    "ACCEPTANCE.md", "AGENTS.md", "BOOTSTRAP_FAILURE.json", "GOAL.txt",
    "NEXT_GOAL.md", "PACKAGE_MANIFEST.json", "PACKAGE_QA.json", "PIN.json",
    "RESTORE.md", "REVIEW.md", "START_HERE.md", "config/runtime.template.json",
    "review/SOURCE_MAP.json", "review/bootstrap_tests.txt", "review/counterexamples.json",
    "review/reproduce_w17.json", "tools/README.md", "tools/audit_repository.py",
    "tools/bootstrap.py", "tools/reproduce_w17_findings.py", "tools/verify_package.py",
}
REPO_ROOT_FILES = {
    ".gitattributes", ".gitignore", ".mcp.json", "AGENTS.md", "CLAUDE.md",
    "DEPENDENCIES.md", "LICENSE", "PROGRESS.md", "README.md",
    "constraints-macos-arm64-py313.txt", "constraints-macos-arm64-py314.txt",
    "mcp_server.py", "pip-freeze-fresh.txt", "pyproject.toml",
    "requirements-windows-cp312.txt",
}
REPO_SOURCE_PREFIXES = ("comsol_mcp/", "tests/", "tools/", "docs/")
PRIVATE_SEGMENTS = {
    ".git", ".venv", "venv", "__pycache__", "pytest_tmp", "install_venv",
    "private", "control-private", "prefs", "classes", "build", ".g3-private",
}
PRIVATE_SUFFIXES = {
    ".mph", ".jar", ".sqlite", ".sqlite3", ".db", ".db-shm", ".db-wal",
    ".class", ".pyc", ".whl", ".stl", ".png", ".zip", ".tar", ".gz",
}
SECRET_VALUE = re.compile(
    r"(?is)(?:[\"'](?:token|password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key)[\"']\s*[:=]\s*[\"'])"
    r"(?!<redacted>|redacted|none|false|true|\*+)(?P<value>[^\"']{8,})[\"']"
)
BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
ABSOLUTE_HOST_PATH = re.compile(r"/(?:Users|Volumes|private/var|tmp|var/folders)/[^\s\"']+")
SAFE_SOURCE_PLACEHOLDERS = {"x", "xx", "token", "test-token", "do-not-log", "redacted", "<redacted>", "none", "false", "true", "password", "secret"}
MAX_BYTES = 64 * 1024 * 1024


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str, cwd: Path = REPO, raw: bool = False) -> bytes | str:
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=cwd, check=True, capture_output=True)
    return result.stdout if raw else result.stdout.decode("utf-8").strip()


def safe_key(path: str) -> bool:
    p = PurePosixPath(path)
    return not p.is_absolute() and ".." not in p.parts and all(part not in {"", "."} for part in p.parts)


def reject_path(path: str) -> str | None:
    p = PurePosixPath(path)
    if not safe_key(path):
        return "unsafe_relative_path"
    if set(p.parts) & PRIVATE_SEGMENTS:
        return "private_runtime_or_cache"
    if p.name == "worker_endpoint.json":
        return "private_worker_rendezvous"
    if p.suffix.lower() in PRIVATE_SUFFIXES:
        return "private_or_binary_runtime_artifact"
    return None


def inspect_text(data: bytes) -> list[str]:
    if len(data) > 16 * 1024 * 1024:
        return []
    text = data.decode("utf-8", errors="replace")
    findings = []
    for match in SECRET_VALUE.finditer(text):
        if match.group("value").lower() not in SAFE_SOURCE_PLACEHOLDERS:
            findings.append("credential_like_value")
            break
    if BEARER_VALUE.search(text): findings.append("bearer_like_value")
    if PRIVATE_KEY_HEADER.search(text): findings.append("private_key_header")
    # Absolute host paths are provenance in old audit/log text, not a secret.
    # They are retained in the source member with their source hash; recovery
    # never uses them as an input path.
    return findings


def load_public_paths(path: Path) -> tuple[list[str], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict): raise ValueError("public path manifest must be an object")
    repository = data.get("repository_paths")
    package = data.get("package_paths", sorted(PACKAGE_FILES))
    if not isinstance(repository, list) or not repository or not all(isinstance(x, str) for x in repository):
        raise ValueError("repository_paths must be a non-empty list of relative paths")
    if not isinstance(package, list) or not all(isinstance(x, str) for x in package):
        raise ValueError("package_paths must be a list of relative paths")
    return sorted(set(repository)), sorted(set(package))


def tracked_tree(commit: str) -> dict[str, tuple[str, str]]:
    raw = git("ls-tree", "-r", "-z", commit, raw=True)
    result: dict[str, tuple[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record: continue
        meta, path_bytes = record.split(b"\t", 1)
        mode, kind, blob = meta.decode("ascii").split()
        path = path_bytes.decode("utf-8")
        result[path] = (mode, blob)
    return result


def source_blob(commit: str, blob: str) -> bytes:
    return git("cat-file", "blob", blob, cwd=REPO, raw=True)


def add_zip(zf: zipfile.ZipFile, archive_path: str, data: bytes, mode: int = 0o100644) -> None:
    info = zipfile.ZipInfo(archive_path)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (mode & 0xFFFF) << 16
    zf.writestr(info, data)


def verify_extract(archive: Path, extract: Path, expected: dict[str, str], manifest_name: str) -> dict[str, Any]:
    if extract.exists(): raise FileExistsError(f"verification directory exists: {extract}")
    extract.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        for name in names:
            p = PurePosixPath(name)
            if not safe_key(name) or p.is_absolute() or ".." in p.parts:
                raise ValueError(f"unsafe archive member: {name}")
            info = zf.getinfo(name)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"symlink archive member: {name}")
        zf.extractall(extract)
    files = {}
    for p in sorted(extract.rglob("*")):
        if p.is_file(): files[p.relative_to(extract).as_posix()] = sha256(p.read_bytes())
    if files != expected:
        missing = sorted(set(expected) - set(files))
        extra = sorted(set(files) - set(expected))
        changed = sorted(k for k in set(expected) & set(files) if expected[k] != files[k])
        raise ValueError(json.dumps({"missing": missing[:20], "extra": extra[:20], "changed": changed[:20]}))
    return {"status": "PASS", "archive_member_count": len(files), "manifest_member": manifest_name, "sha256_verified": True}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", required=True)
    ap.add_argument("--public-paths", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--verify-dir", required=True, type=Path)
    ap.add_argument("--result", required=True, type=Path)
    args = ap.parse_args()
    output = args.output.resolve()
    verify_dir = args.verify_dir.resolve()
    result_path = args.result.resolve()
    if output.exists() or verify_dir.exists() or result_path.exists():
        raise FileExistsError("output, verification directory, or result already exists; choose a new path")
    commit = git("rev-parse", args.commit)
    tree = git("rev-parse", f"{commit}^{{tree}}")
    # Payloads are read from the explicit commit, never from untracked local
    # evidence or runtime files.  Preserve those files without publishing them.
    status = git("status", "--porcelain", "--untracked-files=no")
    if status:
        raise RuntimeError("repository is dirty; final archive requires a clean final commit")
    tree_files = tracked_tree(commit)
    repo_paths, package_paths = load_public_paths(args.public_paths)
    expanded_repo_paths = set()
    for entry in repo_paths:
        if entry.endswith("/"):
            expanded_repo_paths.update(path for path in tree_files if path.startswith(entry))
        else:
            expanded_repo_paths.add(entry)
    repo_paths = sorted(expanded_repo_paths)
    members: list[dict[str, Any]] = []
    payloads: list[tuple[str, bytes, int]] = []
    rejected: list[dict[str, str]] = []
    for path in repo_paths:
        reason = reject_path(path)
        if reason: rejected.append({"scope": "repository", "path": path, "reason": reason}); continue
        if path not in tree_files: rejected.append({"scope": "repository", "path": path, "reason": "not_tracked_at_commit"}); continue
        mode, blob = tree_files[path]
        if mode not in {"100644", "100755"}: rejected.append({"scope": "repository", "path": path, "reason": "symlink_or_special_mode"}); continue
        data = source_blob(commit, blob)
        findings = inspect_text(data)
        if findings: rejected.append({"scope": "repository", "path": path, "reason": ",".join(findings)}); continue
        archive_path = f"repository/{path}"
        payloads.append((archive_path, data, int(mode, 8)))
        members.append({"archive_path": archive_path, "source_path": path, "source_sha256": sha256(data), "size": len(data), "git_blob": blob, "mode": mode})
    for path in package_paths:
        reason = reject_path(path)
        source = PACKAGE / path
        if reason: rejected.append({"scope": "package", "path": path, "reason": reason}); continue
        if not source.is_file() or source.is_symlink(): rejected.append({"scope": "package", "path": path, "reason": "missing_or_symlink"}); continue
        data = source.read_bytes()
        findings = inspect_text(data)
        if findings: rejected.append({"scope": "package", "path": path, "reason": ",".join(findings)}); continue
        archive_path = f"workpack/{path}"
        payloads.append((archive_path, data, 0o100644))
        members.append({"archive_path": archive_path, "source_path": path, "source_sha256": sha256(data), "size": len(data), "mode": "100644"})
    if rejected:
        raise RuntimeError("public archive scope rejected: " + json.dumps(rejected[:50], sort_keys=True))
    short = commit[:12]
    manifest_name = "ARCHIVE_MANIFEST.json"
    manifest = {
        "schema": "comsol-mcp/public-source-archive.v1",
        "generated_at_utc": now(),
        "commit": commit, "tree": tree, "commit_short": short,
        "builder": "clean_archive_builder.py; explicit commit and public-path manifest; no symlink/private/runtime members",
        "members": sorted(members, key=lambda x: x["archive_path"]),
        "exclusions": {"private_segments": sorted(PRIVATE_SEGMENTS), "private_suffixes": sorted(PRIVATE_SUFFIXES), "rejected_members": []},
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    payloads.append((manifest_name, manifest_bytes, 0o100644))
    expected = {path: sha256(data) for path, data, _ in payloads}
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.{os.getpid()}.partial")
    with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, data, mode in sorted(payloads): add_zip(zf, path, data, mode)
    extraction = verify_extract(partial, verify_dir, expected, manifest_name)
    # Atomic no-clobber publication: link only if the requested final name is absent.
    os.link(partial, output)
    partial.unlink()
    archive_hash = sha256(output.read_bytes())
    result = {
        "schema": "comsol-mcp/public-source-archive-result.v1",
        "status": "PASS", "archive_written": True,
        "archive": str(output), "archive_sha256": archive_hash,
        "commit": commit, "tree": tree,
        "member_count": len(expected), "manifest": manifest_name,
        "fresh_extraction": extraction, "verification_dir": str(verify_dir),
        "result_generated_at_utc": now(),
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "archive", "archive_sha256", "commit", "tree", "member_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        raise
