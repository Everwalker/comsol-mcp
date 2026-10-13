#!/usr/bin/env python3
"""Verify acquired inputs and safely unpack into a private offline work tree.

No commands from archives are executed. No installation/network is performed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
from acquire_exact_archives import MANIFEST_SHA, Refusal, identity, manifest_rows, write_json
from security_snapshot._host_scoped_provider import verify_private_path_security

def parts(name):
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(x in ("..", "") for x in p.parts):
        raise Refusal("archive member escapes extraction root")
    return p.parts

def link_target(member):
    if PurePosixPath(member.linkname).is_absolute():
        raise Refusal("absolute archive symlink")
    combined = list(parts(member.name)[:-1])
    for x in PurePosixPath(member.linkname).parts:
        if x == "..":
            if not combined:
                raise Refusal("archive symlink escapes extraction root")
            combined.pop()
        elif x != ".":
            combined.append(x)
    if not combined:
        raise Refusal("archive symlink resolves to extraction root")

def directory(root_fd, path):
    fd = os.dup(root_fd)
    try:
        for x in path:
            try:
                os.mkdir(x, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            nf = os.open(x, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nf
        return fd
    except BaseException:
        os.close(fd)
        raise

def extract(archive_fd, root_fd):
    with os.fdopen(os.dup(archive_fd), "rb") as stream, tarfile.open(fileobj=stream, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > 200000 or sum(m.size for m in members) > 2000000000:
            raise Refusal("archive unpack bounds exceeded")
        seen = set()
        for m in members:
            path = parts(m.name)
            if path in seen:
                raise Refusal("duplicate archive member")
            seen.add(path)
            if not (m.isdir() or m.isfile() or m.issym()):
                raise Refusal("archive device/hardlink/special member forbidden")
            if m.issym():
                link_target(m)
        # Symlinks are created last, so no extracted file traversal follows them.
        for m in [m for m in members if not m.issym()]:
            path = parts(m.name)
            fd = directory(root_fd, path if m.isdir() else path[:-1])
            try:
                if m.isfile():
                    target = os.open(path[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o700 if m.mode & 0o111 else 0o600, dir_fd=fd)
                    try:
                        source = archive.extractfile(m)
                        if source is None:
                            raise Refusal("regular tar member has no body")
                        count = 0
                        with source:
                            while chunk := source.read(1048576):
                                count += len(chunk)
                                view = memoryview(chunk)
                                while view:
                                    view = view[os.write(target, view):]
                        if count != m.size:
                            raise Refusal("short archive member")
                        os.fsync(target)
                    finally:
                        os.close(target)
            finally:
                os.close(fd)
        for m in [m for m in members if m.issym()]:
            path = parts(m.name)
            fd = directory(root_fd, path[:-1])
            try:
                os.symlink(m.linkname, path[-1], dir_fd=fd)
            finally:
                os.close(fd)
    os.fsync(root_fd)
    return {"members": len(members), "expanded_bytes": sum(m.size for m in members)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    args = parser.parse_args()
    prefix = args.prefix
    if prefix != prefix.resolve() or not prefix.name.startswith("comsol-intel-inputs-r1-"):
        raise Refusal("prefix must be canonical acquired task prefix")
    security = verify_private_path_security(prefix, directory=True)
    pfd = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        owner_fd = os.open("OWNER.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
        with os.fdopen(owner_fd) as source:
            owner = json.load(source)
        if owner["security"]["identity"] != security["identity"] or owner["uid"] != os.getuid() or owner["manifest_sha256"] != MANIFEST_SHA:
            raise Refusal("prefix owner/fingerprint changed")
        # Verify every archive first; a failed acquisition cannot produce a build tree.
        archives = os.open("archives", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        try:
            opened = []
            try:
                for row in manifest_rows():
                    fd = os.open(row["filename"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archives)
                    opened.append((row, fd))
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_size != row["expected_bytes"]:
                        raise Refusal("archive ordinary-file size/owner/link check failed")
                    h = hashlib.sha256()
                    while data := os.read(fd, 1048576):
                        h.update(data)
                    if h.hexdigest() != row["expected_sha256"]:
                        raise Refusal("archive SHA mismatch")
                    os.lseek(fd, 0, os.SEEK_SET)
                os.mkdir("sources", mode=0o700, dir_fd=pfd)
                sources = os.open("sources", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
                try:
                    results = []
                    for row, fd in opened:
                        if row["filename"].endswith(".whl"):
                            continue
                        os.mkdir(row["artifact_id"], mode=0o700, dir_fd=sources)
                        dest = os.open(row["artifact_id"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=sources)
                        try:
                            results.append({"artifact_id": row["artifact_id"], **extract(fd, dest)})
                        finally:
                            os.close(dest)
                    write_json(pfd, "OFFLINE_SOURCES_PREPARED.json", {"status": "VERIFIED_UNPACKED_ONLY", "manifest_sha256": MANIFEST_SHA,
                               "artifacts": results, "network_or_build_started": False})
                    print(json.dumps({"status": "VERIFIED_UNPACKED_ONLY", "prefix": str(prefix)}))
                finally:
                    os.close(sources)
            finally:
                for row, fd in opened:
                    os.close(fd)
        finally:
            os.close(archives)
    finally:
        os.close(pfd)

if __name__ == "__main__":
    main()
