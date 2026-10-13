#!/usr/bin/env python3
"""Acquire the frozen Intel inputs only. No installation, build or rustup.

Default validates metadata only. Each execution uses a fresh private prefix;
failed original partial bytes and durable receipts are retained there.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse
import uuid

from security_snapshot._host_scoped_provider import verify_private_path_security, _darwin_mount_flags, _darwin_require_no_extended_acl

MANIFEST = Path(__file__).parents[1] / "w25-native-delivery-preparation-r2/INTEL_X86_ARCHIVE_ACQUISITION_MANIFEST_R1.json"
MANIFEST_SHA = "28f53a6f03cc57383a433064bd2fb4459d301bcce46f846962e1af342a6e9c42"
TOTAL = 220243712
HOSTS = {
    "crates.io": {"crates.io", "static.crates.io"},
    "github.com": {"github.com", "release-assets.githubusercontent.com"},
    "files.pythonhosted.org": {"files.pythonhosted.org"},
    "www.python.org": {"www.python.org"},
    "static.rust-lang.org": {"static.rust-lang.org"},
}

class Refusal(RuntimeError):
    pass

def check_url(url, allowed):
    p = urlparse(url)
    if (p.scheme != "https" or p.hostname not in allowed or p.port is not None
            or p.username is not None or p.password is not None or p.fragment):
        raise Refusal("URL escaped exact HTTPS origin/redirect host policy")

def manifest_rows():
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise Refusal("manifest bytes changed")
    data = json.loads(raw)
    rows = data["artifacts"]
    if len(rows) != 43 or sum(r["expected_bytes"] for r in rows) != TOTAL:
        raise Refusal("frozen archive count or aggregate size changed")
    seen = set()
    for r in rows:
        name = r["filename"]
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", name) or name in seen
                or type(r["expected_bytes"]) is not int or r["expected_bytes"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", r["expected_sha256"])):
            raise Refusal("invalid artifact filename/hash/size")
        seen.add(name)
        origin = r["origin_host"]
        if origin not in HOSTS or urlparse(r["url"]).hostname != origin:
            raise Refusal("unlisted artifact origin")
        check_url(r["url"], {origin})
    return rows

def identity(st):
    return {"device": st.st_dev, "inode": st.st_ino}

def verify_temp_parent(parent):
    if parent != parent.resolve():
        raise Refusal("temporary parent must be canonical")
    if str(parent).startswith("/private/var/folders/"):
        return verify_private_path_security(parent, directory=True)
    if parent != Path("/private/tmp"):
        raise Refusal("unlisted private temporary parent")
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened, named = os.fstat(fd), parent.lstat()
        if (identity(opened) != identity(named) or not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != 0 or stat.S_IMODE(opened.st_mode) != 0o1777):
            raise Refusal("shared private tmp lacks root-owned sticky directory semantics")
        if sys.platform != "darwin":
            raise Refusal("shared private tmp ownership implementation is Darwin-only")
        flags, filesystem = _darwin_mount_flags(fd)
        if flags & 0x00200000 or filesystem.lower() not in {"apfs", "hfs"}:
            raise Refusal("temporary parent filesystem ownership unsupported")
        _darwin_require_no_extended_acl(fd, "trusted temporary parent")
        return {"identity": identity(opened), "filesystem": filesystem, "sticky_root_owned": True}
    finally:
        os.close(fd)

def write_json(fd, name, data):
    f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(f, "w", closefd=False) as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(f)
    finally:
        os.close(f)
    os.fsync(fd)

class StrictRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin):
        self.allowed = HOSTS[origin]
        self.redirects = []
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allowed)
        if len(self.redirects) >= 5:
            raise Refusal("redirect count exceeded")
        # Preserve complete redirects locally; GitHub signed query strings must
        # not be copied to public evidence summaries.
        self.redirects.append({"code": code, "url": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(row, archives_fd, evidence_fd):
    name = row["filename"]
    partial = name + "." + uuid.uuid4().hex + ".partial"
    f = os.open(partial, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=archives_fd)
    receipt = {"artifact_id": row["artifact_id"], "url": row["url"], "partial": partial,
               "expected_sha256": row["expected_sha256"], "expected_bytes": row["expected_bytes"],
               "started_unix": time.time(), "status": "RUNNING"}
    write_json(evidence_fd, row["artifact_id"] + ".request.json", receipt)
    redirect = StrictRedirect(row["origin_host"])
    h = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(row["url"], headers={"User-Agent": "comsol-mcp-exact-archive/1", "Accept-Encoding": "identity"})
        with urllib.request.build_opener(redirect).open(request, timeout=90) as response:
            check_url(response.geturl(), HOSTS[row["origin_host"]])
            receipt["http_status"] = response.status
            receipt["response_headers"] = dict(response.headers)
            if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                raise Refusal("unexpected HTTP status/content encoding")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != row["expected_bytes"]:
                raise Refusal("HTTP length differs from frozen archive")
            while True:
                chunk = response.read(min(1048576, row["expected_bytes"] + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                # Retain every received byte up to the one-byte overflow proof.
                view = memoryview(chunk)
                while view:
                    view = view[os.write(f, view):]
                h.update(chunk)
                if size > row["expected_bytes"]:
                    raise Refusal("archive exceeded frozen cap")
        if size != row["expected_bytes"] or h.hexdigest() != row["expected_sha256"]:
            raise Refusal("archive size/SHA mismatch")
        os.fsync(f)
        opened = os.fstat(f)
        named = os.stat(partial, dir_fd=archives_fd, follow_symlinks=False)
        if identity(opened) != identity(named) or not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
            raise Refusal("partial identity substituted")
        # Hard-link publication fails if destination already exists, unlike rename.
        os.link(partial, name, src_dir_fd=archives_fd, dst_dir_fd=archives_fd, follow_symlinks=False)
        os.unlink(partial, dir_fd=archives_fd)
        os.fsync(archives_fd)
        receipt.update(status="ACQUIRED_EXACT", final_filename=name, identity=identity(opened), rc=0)
    except BaseException as exc:
        receipt.update(status="FAILED_RETAINED_PARTIAL", error_type=type(exc).__name__, error=str(exc), rc=1)
        raise
    finally:
        os.fsync(f)
        os.close(f)
        receipt.update(actual_bytes=size, actual_sha256=h.hexdigest(), redirects=redirect.redirects, finished_unix=time.time())
        write_json(evidence_fd, row["artifact_id"] + ".result.json", receipt)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-download", action="store_true")
    parser.add_argument("--approved-manifest-sha256")
    args = parser.parse_args()
    rows = manifest_rows()
    if not args.execute_download:
        print(json.dumps({"status": "METADATA_VALIDATED_ONLY", "count": len(rows), "bytes": TOTAL, "manifest_sha256": MANIFEST_SHA}))
        return
    if args.approved_manifest_sha256 != MANIFEST_SHA:
        raise Refusal("execution requires reviewed exact manifest fingerprint")
    parent = Path(tempfile.gettempdir()).resolve()
    parent_security = verify_temp_parent(parent)
    prefix = Path(tempfile.mkdtemp(prefix="comsol-intel-inputs-r1-", dir=parent))
    os.chmod(prefix, 0o700)
    security = verify_private_path_security(prefix, directory=True)
    pfd = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    af = ef = None
    try:
        write_json(pfd, "OWNER.json", {"prefix": str(prefix), "uid": os.getuid(), "security": security,
                   "manifest_sha256": MANIFEST_SHA, "argv": sys.argv, "created_unix": time.time(), "parent_security": parent_security})
        for name in ("archives", "evidence"):
            os.mkdir(name, mode=0o700, dir_fd=pfd)
            verify_private_path_security(prefix / name, directory=True)
        af = os.open("archives", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        ef = os.open("evidence", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        print(json.dumps({"status": "STARTED", "prefix": str(prefix)}), flush=True)
        for row in rows:
            verify_private_path_security(prefix, directory=True, expected_identity=security["identity"])
            fetch(row, af, ef)
            print(json.dumps({"artifact": row["artifact_id"], "status": "ACQUIRED_EXACT"}), flush=True)
        write_json(pfd, "ACQUISITION_COMPLETE.json", {"status": "ACQUIRED_EXACT", "count": len(rows), "bytes": TOTAL,
                   "manifest_sha256": MANIFEST_SHA, "install_or_build_started": False})
    finally:
        for f in (af, ef, pfd):
            if f is not None:
                os.close(f)

if __name__ == "__main__":
    main()
