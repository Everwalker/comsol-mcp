"""Source-lock-bound primary notice inputs for any lock-pinned public component.

For a component contributing source notices, trusted anchors are the actual
source uv.lock (the build snapshot's lock) and the real target wheel identity;
a receipt only points at local material and can never self-attest. Each such
component must appear in the lock with a registry source, and its local sdist
bytes must hash to the lock's sdist hash.
Notice member bytes come from that lock-bound archive; the saved notice copy on
disk must equal them. The declared notice member is bounded by MAX_NOTICE_BYTES
before any unbounded read (header-size pre-check plus a bounded read), which
bounds only that member's payload read -- it does not bound expansion or
header traversal of the rest of the archive. Wheel filename identity takes the
distribution field from the structured wheel-filename decomposition (the same
5-or-6 '-'-separated field grammar that the release entry point's
_wheel_dist_version uses, closed by packaging's own parse_wheel_filename
grammar for malformed shapes) and compares it to the locked distribution
solely through packaging's canonicalize_name, so current-normalized, legacy
'.'/uppercase and mixed separator/case spellings match without enumerating
them; the filename version field must equal the lock/receipt version exactly,
as in the release route's pin/metadata comparisons. Every other
lock/version/hash binding is unchanged.
Wheel claims for rows with declared notices must equal a wheel entry of the
same lock package and are later bound to the actual wheelhouse wheel bytes by
the release manifest step. A target row with an empty notices list is instead
disclosed as declared_without_notices: the helper checks the component name and
version against the lock but does not validate the target wheel filename or hash
against the lock. The manifest separately checks the actual wheel against its
supplied requirements lock and requires a present, license-covered component;
that is not itself a direct source-lock wheel-entry check. Legitimately
different license file names/contents per component are expected; components
without a wheel for the selected target are disclosed, not invented. Outputs are
software provenance records, never legal, native-execution or installation
certification. No acquisition happens here.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
import tomllib
from pathlib import Path, PurePosixPath

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

MAX_RECEIPT_BYTES = 512 * 1024
MAX_LOCK_BYTES = 32 * 1024 * 1024
MAX_SDIST_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_NOTICE_BYTES = 256 * 1024
LICENSE_LIKE_BASENAMES = {
    "license", "license.txt", "license.md", "license.rst",
    "licence", "licence.txt", "copying", "copying.txt", "copying.md",
    "notice", "notice.txt", "unlicense", "unlicense.txt",
}
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+!-]*\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")


class SourceLockNoticeError(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_relative(value, label):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise SourceLockNoticeError(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise SourceLockNoticeError(f"{label} is not a safe relative path")
    return path


def _read_regular(path: Path, maximum: int, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SourceLockNoticeError(f"{label} input is missing, linked, or not a regular file")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SourceLockNoticeError(f"{label} input is unreadable: {exc}") from exc
    if size > maximum:
        raise SourceLockNoticeError(f"{label} input exceeds its byte maximum")
    # Unbuffered reads avoid stream prefetch beyond the validated stat size
    # plus one byte. Retry short reads until EOF or the budget is exhausted;
    # open/read/close OSError continues to propagate without wrapping.
    with path.open("rb", buffering=0) as handle:
        parts: list[bytes] = []
        remaining = size + 1
        while remaining > 0:
            chunk = handle.read(remaining)
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(parts)
    if len(raw) != size:
        raise SourceLockNoticeError(f"{label} input drifted while reading")
    return raw


def _read_contained(root: Path, relative, maximum: int, label: str) -> bytes:
    if root.is_symlink() or not root.is_dir():
        raise SourceLockNoticeError(f"{label} source root is missing or linked")
    joined = root / relative
    candidate = Path(os.path.normpath(str(joined)))
    if str(candidate) != str(joined):
        raise SourceLockNoticeError(f"{label} path left the source root")
    if not Path(os.path.realpath(candidate)).is_relative_to(Path(os.path.realpath(root))):
        raise SourceLockNoticeError(f"{label} path left the source root through a link")
    return _read_regular(candidate, maximum, label)


def _lock_index(source_lock: Path):
    raw = _read_regular(source_lock, MAX_LOCK_BYTES, "source lock")
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SourceLockNoticeError(f"source lock is not parseable TOML: {exc}") from exc
    packages = document.get("package")
    if not isinstance(packages, list):
        raise SourceLockNoticeError("source lock has no package list")
    by_name: dict[str, list[dict]] = {}
    for row in packages:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise SourceLockNoticeError("source lock package row is malformed")
        by_name.setdefault(canonicalize_name(row["name"]), []).append(row)
    return raw, _sha(raw), by_name


def _lock_sdist(row: dict, name: str):
    if row.get("source") != {"registry": "https://pypi.org/simple"}:
        raise SourceLockNoticeError(f"{name}: source lock package is not an approved registry source")
    sdist = row.get("sdist")
    if not isinstance(sdist, dict) or not isinstance(sdist.get("url"), str) or not isinstance(sdist.get("hash"), str):
        raise SourceLockNoticeError(f"{name}: source lock package has no hashable sdist entry")
    if not sdist["hash"].startswith("sha256:") or not HEX64_RE.match(sdist["hash"][7:]):
        raise SourceLockNoticeError(f"{name}: source lock sdist hash is not a sha256")
    return sdist


def _tar_license_member(sdist_raw: bytes, member: str, name: str) -> bytes:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(sdist_raw), mode="r:gz")
    except tarfile.TarError as exc:
        raise SourceLockNoticeError(f"{name}: lock-bound source archive is not a readable tar.gz: {exc}") from exc
    payload, seen = None, set()
    with archive:
        for item in archive:
            path = PurePosixPath(item.name)
            if (item.name in seen or path.is_absolute() or ".." in path.parts
                    or "\\" in item.name or not (item.isfile() or item.isdir())):
                raise SourceLockNoticeError(f"{name}: primary source tar traversal/link/membership refused")
            seen.add(item.name)
            if item.name == member:
                if not item.isfile():
                    raise SourceLockNoticeError(f"{name}: declared notice member is not a regular file")
                if item.size > MAX_NOTICE_BYTES:
                    raise SourceLockNoticeError(f"{name}: primary source notice exceeds its byte maximum")
                payload = archive.extractfile(item).read(MAX_NOTICE_BYTES + 1)
                if len(payload) > MAX_NOTICE_BYTES:
                    raise SourceLockNoticeError(f"{name}: primary source notice exceeds its byte maximum")
    if payload is None:
        raise SourceLockNoticeError(f"{name}: declared notice member absent from lock-bound source archive")
    return payload


def _wheel_filename_matches_identity(filename: str, name: str, version: str) -> bool:
    # Structured consumer-side filename check. The release entry point's
    # _wheel_dist_version decomposes {dist}-{version}(-{build})?-{python}-{abi}-{platform}.whl
    # into 5 or 6 '-'-separated fields; packaging's parse_wheel_filename
    # rejects malformed field counts, empty tags, invalid interpreter tags,
    # invalid versions/build tags, and '__' in the distribution field. It
    # does not validate every ABI/platform tag; the release route must also
    # perform its target-tag check. The suffix check below is case-insensitive
    # even though the release route requires literal '.whl'. The v4 enumeration
    # of {normalized, literal} spellings is replaced by the single canonical-form
    # comparison the parent adjudication directs: the distribution field from
    # the decomposition and the locked distribution name are both compared via
    # packaging's canonicalize_name, which makes current-normalized, legacy '.'/uppercase
    # and mixed separator/case spellings all match without hardcoding any of
    # them. The version field must equal the lock/receipt version exactly,
    # mirroring the release route's pin/METADATA version comparisons (v4's
    # case-folded prefix was a loosening with no spec basis). Identity stays
    # closed elsewhere by the exact lock-pinned wheel URL basename, the
    # lock/receipt sha256 match and the lock/receipt version equality.
    if not filename.lower().endswith(".whl"):
        return False
    stem = filename[:-4]
    try:
        parse_wheel_filename(stem + ".whl")
    except InvalidWheelFilename:
        return False
    fields = stem.split("-")
    return (canonicalize_name(fields[0]) == canonicalize_name(name)
            and fields[1] == version)


def lock_bound_notice_payloads(receipt_path: Path, *, source_lock: Path, target: str,
                               sources_root: Path | None = None):
    """Verify receipt-pointed material against the actual lock; return (payloads, route).

    payloads maps canonical package name to a list of notice payload dicts in
    the same shape as tools.w26_primary_notices.notice_payloads (one entry per
    declared notice). route carries sanitized applicability disclosures.
    """
    if target not in {"macos_arm64", "macos_x86_64", "win_amd64"}:
        raise SourceLockNoticeError(f"unknown target: {target}")
    receipt_raw = _read_regular(Path(receipt_path), MAX_RECEIPT_BYTES, "notice receipt")
    receipt_sha = _sha(receipt_raw)
    try:
        receipt = json.loads(receipt_raw)
    except json.JSONDecodeError as exc:
        raise SourceLockNoticeError(f"notice receipt is not JSON: {exc}") from exc
    if not isinstance(receipt, dict) or not isinstance(receipt.get("components"), list) \
            or not 1 <= len(receipt["components"]) <= 128:
        raise SourceLockNoticeError("notice receipt is missing its component list")
    lock_raw, lock_sha, by_name = _lock_index(Path(source_lock))
    claimed_lock = receipt.get("lock_sha256")
    if claimed_lock is not None and claimed_lock != lock_sha:
        raise SourceLockNoticeError("notice receipt lock_sha256 claim is stale for the selected source lock")
    root = Path(sources_root) if sources_root is not None else Path(receipt_path).resolve().parent

    payloads: dict[str, list[dict]] = {}
    applied: list[str] = []
    not_applicable: list[str] = []
    declared_without_notices: list[str] = []
    seen_components: set[str] = set()
    for row in receipt["components"]:
        if not isinstance(row, dict):
            raise SourceLockNoticeError("notice receipt component row is malformed")
        name, version = row.get("name"), row.get("version")
        if not isinstance(name, str) or not NAME_RE.match(name) or not name:
            raise SourceLockNoticeError("notice receipt component name is invalid")
        if not isinstance(version, str) or not VERSION_RE.match(version):
            raise SourceLockNoticeError(f"{name}: notice receipt component version is invalid")
        key = canonicalize_name(name)
        if key in seen_components:
            raise SourceLockNoticeError(f"{name}: duplicate notice receipt component")
        seen_components.add(key)
        rows = by_name.get(key, [])
        if not rows:
            raise SourceLockNoticeError(f"{name}: component is absent from the source lock (receipt cannot self-attest)")
        if len(rows) != 1:
            raise SourceLockNoticeError(f"{name}: source lock has an ambiguous duplicate package entry")
        lock_row = rows[0]
        if lock_row.get("version") != version:
            raise SourceLockNoticeError(f"{name}: receipt version differs from the source lock version")
        wheels_claim = row.get("wheels")
        if not isinstance(wheels_claim, dict):
            raise SourceLockNoticeError(f"{name}: notice receipt has no wheels mapping")
        wheel_claim = wheels_claim.get(target)
        if not isinstance(wheel_claim, dict):
            not_applicable.append(key)
            continue
        notices = row.get("notices")
        if not isinstance(notices, list):
            raise SourceLockNoticeError(f"{name}: notice receipt notices field is malformed")
        if not notices:
            declared_without_notices.append(key)
            continue
        filename = wheel_claim.get("filename")
        wheel_sha = wheel_claim.get("sha256")
        if not isinstance(filename, str) or _safe_relative(filename, f"{name} wheel filename").name != filename \
                or not _wheel_filename_matches_identity(filename, name, version):
            raise SourceLockNoticeError(f"{name}: receipt wheel filename disagrees with the component identity")
        if not isinstance(wheel_sha, str) or not HEX64_RE.match(wheel_sha):
            raise SourceLockNoticeError(f"{name}: receipt wheel sha256 is invalid")
        sdist = _lock_sdist(lock_row, name)
        lock_wheel_rows = [entry for entry in lock_row.get("wheels", [])
                           if isinstance(entry, dict) and isinstance(entry.get("url"), str)
                           and str(PurePosixPath(entry["url"]).name) == filename]
        if len(lock_wheel_rows) != 1 or lock_wheel_rows[0].get("hash") != "sha256:" + wheel_sha:
            raise SourceLockNoticeError(f"{name}: receipt wheel is not the lock-pinned wheel for this filename")
        if row.get("source_url") != sdist["url"] or row.get("source_sha256") != sdist["hash"][7:]:
            raise SourceLockNoticeError(f"{name}: receipt sdist URL/hash differs from the source lock entry")
        if isinstance(sdist.get("size"), int) and row.get("source_bytes") != sdist["size"]:
            raise SourceLockNoticeError(f"{name}: receipt sdist size differs from the source lock entry")
        component_dir = PurePosixPath(notices[0]["file"]).parent if isinstance(notices[0], dict) \
                and isinstance(notices[0].get("file"), str) else PurePosixPath(name)
        _safe_relative(str(component_dir), f"{name} material directory")
        archive_rel = component_dir / "source.tar.gz"
        if isinstance(row.get("source_archive"), str):
            archive_rel = _safe_relative(row["source_archive"], f"{name} source_archive")
        sdist_raw = _read_contained(root, str(archive_rel), MAX_SDIST_BYTES, f"{name} source archive")
        if _sha(sdist_raw) != sdist["hash"][7:]:
            raise SourceLockNoticeError(f"{name}: source archive bytes do not match the source lock sdist hash")
        if isinstance(sdist.get("size"), int) and len(sdist_raw) != sdist["size"]:
            raise SourceLockNoticeError(f"{name}: source archive size does not match the source lock entry")

        metadata_status = "ABSENT_LOCAL_COPY_CLAIM_UNVERIFIED"
        metadata_claim_sha = row.get("metadata_sha256")
        if isinstance(row.get("metadata_file"), str) or (root / component_dir / "metadata.json").is_file():
            metadata_rel = _safe_relative(row.get("metadata_file", str(component_dir / "metadata.json")),
                                          f"{name} metadata file")
            metadata_raw = _read_contained(root, str(metadata_rel), MAX_METADATA_BYTES, f"{name} metadata")
            if isinstance(metadata_claim_sha, str) and _sha(metadata_raw) != metadata_claim_sha:
                raise SourceLockNoticeError(f"{name}: local metadata copy differs from the receipt claim")
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError as exc:
                raise SourceLockNoticeError(f"{name}: local metadata copy is not JSON: {exc}") from exc
            urls = metadata.get("urls")
            info = metadata.get("info")
            match = [entry for entry in (urls or []) if isinstance(entry, dict) and entry.get("url") == sdist["url"]]
            if (not isinstance(info, dict) or canonicalize_name(info.get("name", "")) != key
                    or info.get("version") != version or len(match) != 1
                    or (match[0].get("digests") or {}).get("sha256") != sdist["hash"][7:]
                    or (isinstance(sdist.get("size"), int) and match[0].get("size") != sdist["size"])):
                raise SourceLockNoticeError(f"{name}: local metadata copy contradicts the source lock entry")
            metadata_status = "LOCAL_COPY_MATCHES_LOCK"

        component_payloads, used_members = [], set()
        for notice in notices:
            if not isinstance(notice, dict):
                raise SourceLockNoticeError(f"{name}: notice entry is malformed")
            member = _safe_relative(notice.get("archive_member"), f"{name} archive_member").as_posix()
            if PurePosixPath(member).name.lower() not in LICENSE_LIKE_BASENAMES:
                raise SourceLockNoticeError(f"{name}: declared notice member is not license-like")
            if member in used_members:
                raise SourceLockNoticeError(f"{name}: duplicate notice member")
            used_members.add(member)
            license_bytes = _tar_license_member(sdist_raw, member, name)
            if hashlib.sha256(license_bytes).hexdigest() != notice.get("sha256") \
                    or len(license_bytes) != notice.get("bytes"):
                raise SourceLockNoticeError(f"{name}: extracted notice bytes differ from the receipt claim")
            saved = _read_contained(root, _safe_relative(notice.get("file"), f"{name} notice file").as_posix(),
                                    MAX_NOTICE_BYTES, f"{name} saved notice")
            if saved != license_bytes:
                raise SourceLockNoticeError(f"{name}: saved notice copy differs from the lock-bound archive member")
            relative_path = "notices/" + key + "/" + PurePosixPath(member).name
            if any(item["relative_path"] == relative_path for item in component_payloads):
                raise SourceLockNoticeError(f"{name}: notice bundle member collision")
            component_payloads.append({
                "wheel_sha256": wheel_sha, "version": version,
                "license_bytes": license_bytes, "relative_path": relative_path,
                "provenance": {"name": lock_row["name"], "version": version,
                               "wheel_sha256": wheel_sha, "wheel_filename": filename,
                               "wheel_source_url": lock_wheel_rows[0]["url"],
                               "source_lock_sha256": lock_sha, "receipt_sha256": receipt_sha,
                               "sdist_url": sdist["url"], "sdist_sha256": sdist["hash"][7:],
                               "sdist_bytes": len(sdist_raw),
                               "metadata_url": row.get("metadata_url"),
                               "metadata_sha256": metadata_claim_sha,
                               "metadata_verification": metadata_status,
                               "archive_member": member,
                               "license_sha256": _sha(license_bytes), "license_bytes": len(license_bytes),
                               "separate_product_notice_retained": True,
                               "acceptance": "SOURCE_NOTICE_ASSOCIATION_NOT_LEGAL_OR_RUNTIME_CERTIFICATION"},
            })
        payloads[key] = component_payloads
        applied.append(key)
    route = {"kind": "SOURCE_LOCK_BOUND_PRIMARY_NOTICES", "target": target,
             "receipt_sha256": receipt_sha, "source_lock_sha256": lock_sha,
             "applied": sorted(applied), "not_applicable_to_target": sorted(not_applicable),
             "declared_without_notices": sorted(declared_without_notices)}
    return payloads, route
