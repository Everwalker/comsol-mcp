"""Current source acquisition and once-bound W26 runtime rebuild preparation.

Preparation never launches a compiler. Native dispatch requires a fresh Main
review and source/plan/root bound once receipt; historical controllers are not
run. All installation/build outputs belong to the new private prefix.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse
import uuid

from tools.w26_prepare_current_inputs import _helpers

SOURCE_SPEC = Path(__file__).with_name("w26_runtime_sources.lock.json")
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILES = 30000

class SourceRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, original):
        self.original = original
        self.chain = []
    def redirect_request(self, request, response, code, message, headers, newurl):
        url = urlparse(newurl)
        host = url.hostname or ""
        if url.scheme != "https" or url.username or url.password or url.port or not (
                host in {"github.com", "release-assets.githubusercontent.com", "www.python.org",
                         "ftp.gnu.org", "prdownloads.sourceforge.net", "downloads.sourceforge.net", "sourceforge.net"}
                or host.endswith(".dl.sourceforge.net")):
            raise RuntimeError("official source redirect escaped explicit HTTPS publisher policy")
        self.chain.append({"code": code, "url": newurl})
        return super().redirect_request(request, response, code, message, headers, newurl)

def acquire(row, target, *, timeout=30):
    helper = _helpers()
    redirects = SourceRedirect(row["url"])
    request = urllib.request.Request(row["url"], headers={"Accept-Encoding": "identity"})
    try:
        with urllib.request.build_opener(redirects).open(request, timeout=timeout) as response:
            raw = response.read(MAX_ARCHIVE + 1)
            headers = dict(response.headers)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        body=exc.read(1048577)
        helper.copy_blob(target.with_suffix(target.suffix+'.HTTP_FAILURE.bin'),body)
        helper.publish(target.with_suffix(target.suffix+'.HTTP_FAILURE.json'),{'type':type(exc).__name__,'status':exc.code,
            'requested_url':row['url'],'headers':dict(exc.headers),'redirect_chain':redirects.chain,
            'body_sha256':hashlib.sha256(body).hexdigest(),'signature_or_native_acceptance':'NOT_ESTABLISHED'})
        raise
    if len(raw) > MAX_ARCHIVE:
        raise RuntimeError("official source archive exceeds bounded byte envelope")
    # Retain the raw acquisition first, including rejected hash/partial content.
    helper.copy_blob(target, raw)
    record = helper.raw_file(target, maximum=MAX_ARCHIVE)[1]
    acquisition = {"requested_url": row["url"], "final_url": final_url, "redirect_chain": redirects.chain,
                   "HTTP_headers": headers, "actual_record": record, "TLS_verified": True}
    helper.publish(target.with_suffix(target.suffix + ".acquisition.json"), acquisition)
    if ("sha256" in row and row["sha256"] != record["sha256"]) or \
            ("bytes" in row and row["bytes"] != record["bytes"]):
        raise RuntimeError("original locked source bytes/hash differ")
    return acquisition

def safe_unpack(archive_path, root, expected_top):
    """Whole finite no-link tar source, exclusive writes and normalized mode."""
    helper = _helpers()
    raw, original = helper.raw_file(archive_path, maximum=MAX_ARCHIVE)
    import io
    rows = []
    total = 0
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        members = archive.getmembers()
        for member in members:
            name = member.name.rstrip("/")
            path = PurePosixPath(name)
            if not name or path.is_absolute() or str(path) != name or "\\" in name or \
                    any(c in ("", ".", "..") for c in name.split("/")) or path.parts[0] != expected_top or \
                    name in seen or not (member.isdir() or member.isfile()):
                raise RuntimeError("source tar path/link/type/duplicate/top-level refused")
            seen.add(name)
            total += member.size
            if member.size < 0 or total > MAX_EXPANDED or len(seen) > MAX_FILES:
                raise RuntimeError("source whole-member/expanded budget exceeded")
        root.mkdir(mode=0o700, exist_ok=False)
        root_identity = helper.secure_dir(root)
        for member in members:
            path = root / member.name.rstrip("/")
            if member.isdir():
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                rows.append({"path": member.name.rstrip("/"), "kind": "directory"})
            else:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                content = stream.read(member.size + 1)
                if len(content) != member.size:
                    raise RuntimeError("source member expanded size differs")
                helper.copy_blob(path, content)
                if member.mode & 0o111:
                    path.chmod(0o700)
                rows.append({"path": member.name, "kind": "regular", "record": helper.raw_file(path)[1]})
        helper.secure_dir(root, root_identity)
    helper.raw_file(archive_path, expected=original)
    return {"archive_first_record": original, "member_count": len(members), "expanded_bytes": total,
            "whole_rows": rows, "symlink_hardlink_special_members": 0}

def reuse_archive(row,source,target):
    """Read-only local blob reuse; the original first record is never new authority."""
    helper=_helpers();raw,first=helper.raw_file(Path(source),maximum=MAX_ARCHIVE)
    if first['sha256']!=row['sha256'] or first['bytes']!=row['bytes']:
        raise RuntimeError('local source archive locked bytes/hash differ')
    helper.copy_blob(target,raw);actual=helper.raw_file(target)[1]
    helper.raw_file(Path(source),expected=first)
    result={'method':'LOCAL_HASH_MATCHED_EXCLUSIVE_COPY','origin_url':row['url'],
            'reused_from_first_record':first,'actual_record':actual,'network_GET':0}
    helper.publish(target.with_suffix(target.suffix+'.acquisition.json'),result)
    return result

def prepare(root, spec_path=SOURCE_SPEC, *, local_archives=None):
    helper = _helpers()
    root = Path(root)
    if root != root.resolve() or root.parent != Path("/private/tmp") or not root.name.startswith("cvw26-runtime-"):
        raise RuntimeError("current runtime prefix must be fresh canonical /private/tmp/cvw26-runtime-*")
    spec_raw, spec_record = helper.raw_file(spec_path, maximum=1048576)
    spec = json.loads(spec_raw)
    if set(spec) != {"python", "openssl", "xz", "gdbm", "tcl", "tk"}:
        raise RuntimeError("complete six-source lock required")
    root.mkdir(mode=0o700, exist_ok=False)
    root_identity = helper.secure_dir(root)
    helper.publish(root / "PREPARE_FIRST_RECORD.json", {"root_identity": root_identity, "source_lock_first_record": spec_record,
                   "actor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "native_Popen": 0})
    archives, sources = root / "archives", root / "sources"
    archives.mkdir(mode=0o700); sources.mkdir(mode=0o700)
    began = time.monotonic()
    results = {}
    try:
        for name, row in spec.items():
            helper.secure_dir(root, root_identity)
            remaining = 600 - (time.monotonic() - began)
            if remaining <= 1:
                raise TimeoutError("whole six-source prepare deadline exhausted")
            target = archives / row["filename"]
            acquisition = (reuse_archive(row,Path(local_archives)/row['filename'],target) if local_archives is not None
                           else acquire(row, target, timeout=min(30, remaining)))
            unpacked = safe_unpack(target, sources / name, row["top"])
            results[name] = {"locked": row, "acquisition": acquisition, "unpacked": unpacked}
            helper.publish(root / ("SOURCE_" + name + ".json"), results[name])
        helper.raw_file(spec_path, expected=spec_record)
        helper.publish(root / "CURRENT_SOURCE_LOCK_CAPTURE.json", {name: {**result["locked"],
            "sha256": result["acquisition"]["actual_record"]["sha256"],
            "bytes": result["acquisition"]["actual_record"]["bytes"]} for name, result in results.items()})
        helper.publish(root / "PREPARED_SOURCES.json", {"status": "ACTUAL_SOURCE_BYTES_PREPARED_NOT_NATIVE_APPROVED",
                       "sources": results, "native_Popen": 0, "root_identity": root_identity})
        return root / "PREPARED_SOURCES.json"
    except BaseException as exc:
        helper.publish(root / "PREPARE_FIRST_FAILURE.json", {"type": type(exc).__name__, "error": str(exc),
                       "completed_sources": list(results), "native_Popen": 0})
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sources", type=Path, default=SOURCE_SPEC)
    parser.add_argument("--local-archives",type=Path)
    args = parser.parse_args()
    print(prepare(args.root, args.sources,local_archives=args.local_archives))
