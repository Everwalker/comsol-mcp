#!/usr/bin/env python3
"""Fetch only the eight frozen W25 ARM64 wheels into the prepared task wheelhouse.

This script is intentionally dry-run unless --execute-download is provided.
It never installs packages and never removes a substituted path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]
PLAN_PATH = Path(__file__).with_name("ISOLATED_ARM64_RUNTIME_PLAN_R2.json")
FROZEN_WHEELS_PATH = ROOT / "docs/full_project_continuation_v2/evidence/w25-native-delivery-preparation-r1/macos-arm64-w25-runtime-candidate-r1.json"
FROZEN_WHEELS_SHA256 = "0cff395ea963af307d9c8f6bc5bb293028ae843ff4b94e24e08270884b36e7af"
PLAN_SCHEMA = "comsol-mcp.w25-arm64-task-runtime/2"
MAX_TOTAL_BYTES = 7388559
ALLOWED_HOST = "files.pythonhosted.org"


class Refusal(RuntimeError):
    pass


def _sha256_fd(fd: int, maximum: int) -> tuple[str, int]:
    os.lseek(fd, 0, os.SEEK_SET)
    h = hashlib.sha256()
    total = 0
    while True:
        data = os.read(fd, min(1024 * 1024, maximum + 1 - total))
        if not data:
            break
        total += len(data)
        if total > maximum:
            raise Refusal("file exceeded the frozen byte limit")
        h.update(data)
    return h.hexdigest(), total


def _identity(st: os.stat_result) -> dict[str, int]:
    return {"device": int(st.st_dev), "inode": int(st.st_ino)}


def _verify_security(path: Path, expected: dict[str, int], directory: bool) -> None:
    sys.path.insert(0, str(ROOT))
    from comsol_mcp._host_scoped_provider import verify_private_path_security
    result = verify_private_path_security(path, directory=directory, expected_identity=expected)
    if result.get("identity") != expected:
        raise Refusal(f"trusted path identity changed: {path}")


def _open_child(parent_fd: int, name: str, expected: dict[str, int]) -> int:
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                 getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0), dir_fd=parent_fd)
    st = os.fstat(fd)
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(named.st_mode)
            or _identity(st) != expected or _identity(named) != expected):
        os.close(fd)
        raise Refusal(f"task child directory changed: {name}")
    return fd


def _validate_wheel_rows(plan: dict, raw: dict) -> list[dict]:
    rows = plan["wheelhouse"]["wheels"]
    source_rows = [x for x in raw["required_runtime_wheel_closure"] if "macos_arm64" in x["targets"]]
    projected = [{"filename": x["filename"], "name": x["name"], "version": x["version"],
                  "sha256": x["sha256"], "size_bytes": x["size_bytes"], "url": x["url"]}
                 for x in source_rows]
    if rows != projected or len(rows) != 8:
        raise Refusal("R2 plan wheel list differs from exact frozen R1 candidate")
    if sum(int(x["size_bytes"]) for x in rows) != MAX_TOTAL_BYTES:
        raise Refusal("aggregate wheel byte count differs from pinned limit")
    seen = set()
    for row in rows:
        filename = row["filename"]
        url = urlparse(row["url"])
        if (Path(filename).name != filename or not filename.endswith(".whl")
                or filename in seen or url.scheme != "https" or url.hostname != ALLOWED_HOST
                or url.port is not None or url.username is not None or url.password is not None
                or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or type(row["size_bytes"]) is not int or row["size_bytes"] <= 0):
            raise Refusal("wheel row failed strict host/path/hash/size validation")
        seen.add(filename)
    return rows


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.port is not None:
            raise Refusal("wheel redirect escaped the pinned HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _hash_named(wheel_fd: int, filename: str, expected_size: int, expected_hash: str) -> bool:
    named = os.stat(filename, dir_fd=wheel_fd, follow_symlinks=False)
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
        raise Refusal(f"existing destination is not a private regular file: {filename}")
    fd = os.open(filename, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                 getattr(os, "O_NOFOLLOW", 0), dir_fd=wheel_fd)
    try:
        opened = os.fstat(fd)
        if _identity(opened) != _identity(named) or opened.st_nlink != 1:
            raise Refusal(f"existing destination identity changed: {filename}")
        digest, size = _sha256_fd(fd, expected_size)
        if digest != expected_hash or size != expected_size:
            raise Refusal(f"existing destination bytes do not match frozen wheel: {filename}")
        return True
    finally:
        os.close(fd)


def _remove_owned_partial(partial_fd: int, name: str, fd: int) -> None:
    try:
        opened = os.fstat(fd)
        named = os.stat(name, dir_fd=partial_fd, follow_symlinks=False)
        if (not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(named.st_mode)
                or _identity(opened) != _identity(named) or opened.st_nlink != 1):
            return
        os.unlink(name, dir_fd=partial_fd)
        os.fsync(partial_fd)
    except OSError:
        return


def _download_one(row: dict, partial_fd: int, wheel_fd: int) -> dict:
    filename = row["filename"]
    try:
        os.stat(filename, dir_fd=wheel_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        _hash_named(wheel_fd, filename, row["size_bytes"], row["sha256"])
        return {"filename": filename, "status": "REUSED_EXACT_EXISTING", "bytes": row["size_bytes"], "sha256": row["sha256"]}

    temporary = f"{filename}.{uuid.uuid4().hex}.partial"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600, dir_fd=partial_fd)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_uid != os.geteuid():
            raise Refusal("new temporary wheel is not an owned single-link regular file")
        request = urllib.request.Request(row["url"], headers={"Accept-Encoding": "identity", "User-Agent": "comsol-w25-pinned-acquisition/2"})
        opener = urllib.request.build_opener(SameHostRedirect())
        h = hashlib.sha256()
        total = 0
        with opener.open(request, timeout=45) as response:
            final = urlparse(response.geturl())
            if response.status != 200 or final.scheme != "https" or final.hostname != ALLOWED_HOST or final.port is not None:
                raise Refusal("wheel response status/final origin failed the pinned policy")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and content_length != str(row["size_bytes"]):
                raise Refusal("wheel response Content-Length differs from frozen metadata")
            while True:
                chunk = response.read(min(1024 * 1024, row["size_bytes"] + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > row["size_bytes"]:
                    raise Refusal("wheel response exceeded frozen byte limit")
                h.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short write to pinned partial file")
                    view = view[written:]
        digest = h.hexdigest()
        if total != row["size_bytes"] or digest != row["sha256"]:
            raise Refusal(f"wheel body did not match exact SHA-256/size: {filename}")
        os.fsync(fd)
        after = os.fstat(fd)
        named = os.stat(temporary, dir_fd=partial_fd, follow_symlinks=False)
        if (_identity(after) != _identity(opened) or _identity(named) != _identity(opened)
                or not stat.S_ISREG(named.st_mode) or stat.S_ISLNK(named.st_mode) or after.st_nlink != 1):
            raise Refusal("partial wheel was substituted before publication")
        try:
            os.link(temporary, filename, src_dir_fd=partial_fd, dst_dir_fd=wheel_fd, follow_symlinks=False)
        except FileExistsError as exc:
            raise Refusal(f"no-clobber publication found an existing destination: {filename}") from exc
        os.fsync(wheel_fd)
        final_fd = os.open(filename, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                           getattr(os, "O_NOFOLLOW", 0), dir_fd=wheel_fd)
        try:
            final_st = os.fstat(final_fd)
            if _identity(final_st) != _identity(after) or final_st.st_nlink != 2:
                raise Refusal("published wheel identity does not match staged bytes")
            final_hash, final_size = _sha256_fd(final_fd, row["size_bytes"])
            if final_hash != digest or final_size != total:
                raise Refusal("published wheel readback hash/size mismatch")
        finally:
            os.close(final_fd)
        os.unlink(temporary, dir_fd=partial_fd)
        os.fsync(partial_fd)
        return {"filename": filename, "status": "DOWNLOADED_VERIFIED_NO_CLOBBER", "bytes": total, "sha256": digest}
    except BaseException:
        _remove_owned_partial(partial_fd, temporary, fd)
        raise
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-download", action="store_true", help="perform the exact eight pinned HTTPS downloads; no install")
    args = parser.parse_args()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if plan.get("schema") != PLAN_SCHEMA or plan.get("status") != "PREPARED_NO_DOWNLOAD_NO_INSTALL":
        raise Refusal("task runtime plan is not the expected prepared state")
    raw_manifest = FROZEN_WHEELS_PATH.read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != FROZEN_WHEELS_SHA256:
        raise Refusal("frozen original wheel candidate changed")
    rows = _validate_wheel_rows(plan, json.loads(raw_manifest))
    parent_path = Path(plan["trusted_parent_path"])
    task_path = parent_path / plan["task_directory_leaf"]
    parent_id = plan["trusted_parent_identity"]
    task_id = plan["task_directory_identity"]
    _verify_security(parent_path, parent_id, True)
    _verify_security(task_path, task_id, True)
    parent_fd = os.open(parent_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                        getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0))
    task_fd = -1
    wheel_fd = partial_fd = -1
    try:
        if _identity(os.fstat(parent_fd)) != parent_id:
            raise Refusal("trusted task parent changed after security verification")
        task_fd = _open_child(parent_fd, plan["task_directory_leaf"], task_id)
        wheel_fd = _open_child(task_fd, "wheelhouse", plan["subdirectories"]["wheelhouse"])
        partial_fd = _open_child(task_fd, "download.partial", plan["subdirectories"]["download.partial"])
        _verify_security(parent_path, parent_id, True)
        _verify_security(task_path, task_id, True)
        result = {"status": "DRY_RUN_NO_NETWORK" if not args.execute_download else "DOWNLOADING_EXACT_PINNED_WHEELS",
                  "wheel_count": len(rows), "total_bytes": sum(x["size_bytes"] for x in rows),
                  "files": [{"filename": x["filename"], "size_bytes": x["size_bytes"], "sha256": x["sha256"], "url": x["url"]} for x in rows]}
        if args.execute_download:
            result["results"] = [_download_one(x, partial_fd, wheel_fd) for x in rows]
            _verify_security(parent_path, parent_id, True)
            _verify_security(task_path, task_id, True)
            for row in rows:
                _hash_named(wheel_fd, row["filename"], row["size_bytes"], row["sha256"])
            result["status"] = "ALL_EIGHT_WHEELS_HASH_AND_SIZE_VERIFIED"
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return 0
    finally:
        for fd in (partial_fd, wheel_fd, task_fd, parent_fd):
            if fd >= 0:
                os.close(fd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, urllib.error.URLError, Refusal) as exc:
        sys.stderr.write(json.dumps({"status": "REFUSED", "error_type": type(exc).__name__, "message": str(exc)}) + "\n")
        raise SystemExit(2)
