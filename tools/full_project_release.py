#!/usr/bin/env python3
"""Build and verify offline COMSOL MCP release bundles.

This helper is deliberately separate from the engine and never starts COMSOL.
It builds from an allowlisted source snapshot, audits archive members, writes
hash/SBOM/license evidence, installs only from a local wheelhouse, supports
guarded cleanup of its marked isolated venvs, and can test an upgrade/rollback
transition in a disposable environment. Native engine and six-target
certification always remain external acceptance steps.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata as metadata
import json
import os
import pathlib
import re
import shutil
import socket
import stat as stat_module
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile
from itertools import product
from typing import Any, Iterable

try:
    import tomllib
except ImportError:  # pragma: no cover - retained for older host tooling
    tomllib = None

SCHEMA = "comsol-mcp-full-release/1"
STATE_SNAPSHOT_SCHEMA = "comsol-mcp-campaign-state-snapshot/1"
STATE_SNAPSHOT_SCOPE_SCHEMA = "comsol-mcp-campaign-state-scope/1"
PATH_REMAP_SCHEMA = "comsol-mcp-campaign-path-remap/1"
STATE_SNAPSHOT_KIND = "IN_PROGRESS_CAMPAIGN_SNAPSHOT"
INSTALL_MARKER_SCHEMA = "comsol-mcp-venv-install/1"
INSTALL_PROJECT_ID = "org.comsol-mcp.isolated-python-install"
INSTALL_MARKER_NAME = ".comsol-mcp-install.json"
SOURCE_DISTRIBUTION_CLASSES = {"LOCAL_RECOVERY_ONLY", "PUBLIC_SOURCE_CANDIDATE"}
USER_SCOPE_DECISIONS_PATH = "repository/docs/full_project_execution/USER_SCOPE_DECISIONS.json"
OPENSSL_402_SOURCE_URL = "https://www.openssl.org/source/openssl-4.0.2.tar.gz"
OPENSSL_402_SOURCE_SHA256 = "736b467530f916737b7031310ccb21d8218c6229e61e8e160cd1d3458cd543a8"
TARGETS = {
    "win_amd64": {"platform": ("win_amd64", "any"), "pip_platforms": ("win_amd64",), "python": "3.12",
                   "markers": {"sys_platform": "win32", "platform_system": "Windows", "platform_machine": "AMD64", "os_name": "nt"}},
    "macos_arm64": {"platform": ("arm64", "universal2", "any"), "pip_platforms": ("macosx_11_0_arm64", "macosx_11_0_universal2"), "python": "3.12",
                     "markers": {"sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": "arm64", "os_name": "posix"}},
    "macos_x86_64": {"platform": ("x86_64", "universal2", "any"), "pip_platforms": ("macosx_12_0_x86_64", "macosx_12_0_universal2"),
                      "minimum_os": "macOS 12.0 (COMSOL 6.3/6.4 Intel Mac requirement)", "minimum_platform": (12, 0), "python": "3.12",
                      "markers": {"sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": "x86_64", "os_name": "posix"}},
}
SOURCE_ROOT_FILES = ("pyproject.toml", "README.md", "LICENSE")
FORBIDDEN_SUFFIXES = {
    ".mph", ".mphbin", ".mphdata", ".mphbackup", ".jar", ".class",
    ".lic", ".license", ".chm", ".key", ".p12", ".pfx",
}
COMMERCIAL_DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".html", ".htm"}
FORBIDDEN_NAMES = {
    ".env", ".env.local", "usedlicenses.txt", "license.dat", "license.lic",
    "comsol.license", "license_manager", "id_rsa", "id_ed25519",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{96,}-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:api[_-]?key|secret[_-]?key|auth[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
)
MAX_FILE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 12 * 1024**3
MAX_ARCHIVE_DEPTH = 3


class ReleaseError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _in_approved_jpype_archive(rel: str) -> bool:
    parts = rel.split("!/")
    if len(parts) < 2 or parts[1].lower() != "org.jpype.jar":
        return False
    wheel_name = pathlib.PurePosixPath(parts[0]).name
    if not wheel_name.lower().endswith(".whl"):
        return False
    try:
        dist, _version, _tags = _wheel_dist_version(pathlib.Path(wheel_name))
    except ReleaseError:
        return False
    return dist == "jpype1"


def _in_approved_pywin32_help(rel: str) -> bool:
    parts = rel.split("!/")
    if len(parts) != 2 or parts[1].lower() != "pywin32.chm":
        return False
    wheel_name = pathlib.PurePosixPath(parts[0]).name
    try:
        dist, version, tags = _wheel_dist_version(pathlib.Path(wheel_name))
    except ReleaseError:
        return False
    # PyWin32 312's public help file is part of that exact locked Windows wheel.
    # Keep this exception narrow so arbitrary CHM or vendor documentation stays blocked.
    return dist == "pywin32" and version == "312" and tags == ("cp312", "cp312", "win_amd64")


def safe_relative(name: str) -> str:
    name = name.replace("\\", "/")
    if not name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise ReleaseError(f"unsafe archive path: {name!r}")
    canonical = name.rstrip("/")
    raw_parts = canonical.split("/")
    if not canonical or any(p in ("", ".", "..") for p in raw_parts):
        raise ReleaseError(f"unsafe archive path: {name!r}")
    parts = pathlib.PurePosixPath(canonical).parts
    return "/".join(parts)


def scan_member_name(name: str) -> str | None:
    rel = safe_relative(name.rstrip("/"))
    path = pathlib.PurePosixPath(rel)
    lower_parts = {p.lower() for p in path.parts}
    lower = path.name.lower()
    if any(p.startswith("._") for p in path.parts):
        return "APPLEDOUBLE_SIDECAR"
    if any(p.lower() in {".git", "_state", ".private", "private_state", "credentials", "secrets"} for p in path.parts):
        return "PRIVATE_OR_GIT_STATE"
    if lower in FORBIDDEN_NAMES:
        return "PRIVATE_OR_LICENSE_FILE"
    suffix = path.suffix.lower()
    allowed_jpype_bootstrap = _in_approved_jpype_archive(rel)
    allowed_pywin32_help = suffix == ".chm" and _in_approved_pywin32_help(rel)
    if suffix == ".jar" and allowed_jpype_bootstrap and len(rel.split("!/")) == 2:
        pass
    elif suffix == ".class" and allowed_jpype_bootstrap:
        pass
    elif allowed_pywin32_help:
        pass
    elif suffix in FORBIDDEN_SUFFIXES:
        return "PROHIBITED_VENDOR_OR_PRIVATE_BINARY"
    if path.suffix.lower() in COMMERCIAL_DOC_SUFFIXES and lower_parts & {"comsol_help", "vendor_docs", "commercial_docs", "license_docs"}:
        return "PROHIBITED_VENDOR_DOCUMENT"
    if any(p in {"private", "credentials", "secrets", "license_files", "comsol_help", "vendor_docs"} for p in lower_parts):
        return "PRIVATE_OR_VENDOR_DIRECTORY"
    return None


def _inspect_bytes(rel: str, data: bytes, findings: list[dict[str, str]], depth: int = 0) -> None:
    kind = scan_member_name(rel)
    if kind:
        findings.append({"path": rel, "kind": kind})
        return
    if len(data) > MAX_FILE_BYTES:
        findings.append({"path": rel, "kind": "FILE_SIZE_LIMIT"})
        return
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        findings.append({"path": rel, "kind": "SECRET_PATTERN"})
    if depth >= MAX_ARCHIVE_DEPTH:
        return
    lower = rel.lower()
    if lower.endswith((".whl", ".zip", ".jar")):
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
                seen: set[str] = set()
                for info in archive.infolist():
                    member = safe_relative(info.filename)
                    member_path = f"{rel}!/{member}"
                    dir_kind = scan_member_name(member_path)
                    if dir_kind:
                        findings.append({"path": member_path, "kind": dir_kind})
                    if info.is_dir():
                        continue
                    file_kind = stat_module.S_IFMT((info.external_attr >> 16) & 0xFFFF)
                    if file_kind not in (0, stat_module.S_IFREG):
                        findings.append({"path": f"{rel}!/{member}", "kind": "ARCHIVE_LINK_OR_SPECIAL_FILE"})
                        continue
                    if member in seen:
                        findings.append({"path": f"{rel}!/{member}", "kind": "DUPLICATE_ARCHIVE_MEMBER"})
                        continue
                    seen.add(member)
                    if info.file_size > MAX_FILE_BYTES:
                        findings.append({"path": f"{rel}!/{member}", "kind": "ARCHIVE_MEMBER_SIZE_LIMIT"})
                        continue
                    _inspect_bytes(member_path, archive.read(info), findings, depth + 1)
        except (OSError, zipfile.BadZipFile, ReleaseError) as exc:
            findings.append({"path": rel, "kind": "INVALID_ZIP", "detail": str(exc)[:240]})
    elif lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        try:
            with tarfile.open(fileobj=__import__("io").BytesIO(data), mode="r:*") as archive:
                for item in archive.getmembers():
                    member = safe_relative(item.name)
                    if item.issym() or item.islnk() or not (item.isfile() or item.isdir()):
                        findings.append({"path": f"{rel}!/{member}", "kind": "ARCHIVE_LINK_OR_SPECIAL_FILE"})
                    elif item.isfile():
                        if item.size > MAX_FILE_BYTES:
                            findings.append({"path": f"{rel}!/{member}", "kind": "ARCHIVE_MEMBER_SIZE_LIMIT"})
                        else:
                            stream = archive.extractfile(item)
                            if stream is not None:
                                _inspect_bytes(f"{rel}!/{member}", stream.read(), findings, depth + 1)
        except (OSError, tarfile.TarError, ReleaseError) as exc:
            findings.append({"path": rel, "kind": "INVALID_TAR", "detail": str(exc)[:240]})


def audit_tree(root: pathlib.Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ReleaseError(f"audit input is not a directory: {root}")
    files: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    total = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        here = pathlib.Path(current)
        retained: list[str] = []
        for name in sorted(dirs):
            candidate = here / name
            if name == ".git":
                findings.append({"path": candidate.relative_to(root).as_posix(), "kind": "PRIVATE_OR_GIT_STATE"})
                continue
            if name in {"__pycache__", ".pytest_cache", ".venv", "venv"}:
                continue
            if candidate.is_symlink():
                findings.append({"path": candidate.relative_to(root).as_posix(), "kind": "SYMLINK"})
            else:
                kind = scan_member_name(candidate.relative_to(root).as_posix())
                if kind:
                    findings.append({"path": candidate.relative_to(root).as_posix(), "kind": kind})
                else:
                    retained.append(name)
        dirs[:] = retained
        for name in sorted(names):
            candidate = here / name
            rel = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                findings.append({"path": rel, "kind": "SYMLINK"})
                continue
            kind = scan_member_name(rel)
            if kind:
                findings.append({"path": rel, "kind": kind})
                continue
            stat = candidate.stat()
            if not stat_module.S_ISREG(stat.st_mode):
                findings.append({"path": rel, "kind": "SPECIAL_FILE"})
                continue
            total += stat.st_size
            if stat.st_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                findings.append({"path": rel, "kind": "FILE_OR_TREE_SIZE_LIMIT"})
                continue
            digest = sha256_file(candidate)
            files.append({"path": rel, "bytes": stat.st_size, "sha256": digest})
            try:
                if stat.st_size <= MAX_FILE_BYTES:
                    _inspect_bytes(rel, candidate.read_bytes(), findings)
            except OSError as exc:
                findings.append({"path": rel, "kind": "READ_ERROR", "detail": str(exc)[:240]})
    findings = _dedupe_findings(findings)
    return {"status": "PASS" if not findings else "FAIL", "root_label": root.name,
            "file_count": len(files), "total_bytes": total, "findings": findings,
            "files": files}


def _dedupe_findings(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    result = {json.dumps(row, sort_keys=True): row for row in rows}
    return [result[key] for key in sorted(result)]


def _wheel_dist_version(path: pathlib.Path) -> tuple[str, str, tuple[str, str, str]]:
    if path.suffix != ".whl":
        raise ReleaseError(f"not a wheel: {path.name}")
    fields = path.name[:-4].split("-")
    if len(fields) == 5:
        name, version, python_tag, abi_tag, platform_tag = fields
    elif len(fields) == 6:
        name, version, _build, python_tag, abi_tag, platform_tag = fields
    else:
        raise ReleaseError(f"invalid wheel filename: {path.name}")
    return normalized_name(name), version, (python_tag, abi_tag, platform_tag)


def wheel_target_check(filename: str, target: str) -> bool:
    if target not in TARGETS:
        raise ReleaseError(f"unknown target: {target}")
    try:
        dist, version, tags = _wheel_dist_version(pathlib.Path(filename))
    except ReleaseError:
        return False
    python_tags = set(tags[0].lower().split("."))
    abi_tags = set(tags[1].lower().split("."))
    platform_tags = set(tags[2].lower().split("."))
    target_major, target_minor = (int(part) for part in TARGETS[target]["python"].split("."))

    def python_abi_ok(py_tag: str, abi_tag: str) -> bool:
        if py_tag in {"py2", "py3"}:
            return py_tag == "py3" and abi_tag == "none"
        match = re.fullmatch(r"cp([0-9])([0-9]{1,2})", py_tag)
        if not match or int(match.group(1)) != target_major:
            return False
        interpreter_minor = int(match.group(2))
        if abi_tag == "abi3":
            return interpreter_minor <= target_minor
        return py_tag == f"cp{target_major}{target_minor}" and abi_tag in {"none", f"cp{target_major}{target_minor}"}

    if not any(python_abi_ok(py_tag, abi_tag) for py_tag in python_tags for abi_tag in abi_tags):
        return False
    compatible_macos_arches = {"arm64", "universal2"} if target == "macos_arm64" else {"x86_64", "universal2"}

    def compatible_platform(platform: str) -> bool:
        if platform == "any":
            return any(abi_tag == "none" and py_tag in {"py2", "py3"} for py_tag in python_tags for abi_tag in abi_tags)
        if target == "win_amd64":
            return platform == "win_amd64"
        match = re.fullmatch(r"macosx_[0-9]+_[0-9]+_(arm64|x86_64|universal2)", platform)
        if not match or match.group(1) not in compatible_macos_arches:
            return False
        minimum = TARGETS[target].get("minimum_os")
        if minimum:
            version = re.fullmatch(r"macosx_([0-9]+)_([0-9]+)_(?:arm64|x86_64|universal2)", platform)
            assert version
            if (int(version.group(1)), int(version.group(2))) > TARGETS[target]["minimum_platform"]:
                return False
        return True

    return any(compatible_platform(platform) for platform in platform_tags)


def wheel_tag_identity_matches(path: pathlib.Path, meta: dict[str, Any]) -> bool:
    """Require WHEEL metadata to agree with the wheel filename; catches tag renaming."""
    try:
        _dist, _version, tags = _wheel_dist_version(path)
    except ReleaseError:
        return False
    filename_tags = {
        f"{py}-{abi}-{platform}"
        for py, abi, platform in product(*(component.split(".") for component in tags))
    }
    internal_tags = set(meta.get("wheel_tags", []))
    return bool(internal_tags) and internal_tags <= filename_tags


def _marker_matches(marker: str, target: str) -> bool:
    if not marker:
        return True
    try:
        try:
            from packaging.markers import Marker, default_environment
        except ImportError:
            from pip._vendor.packaging.markers import Marker, default_environment
    except ImportError as exc:
        raise ReleaseError("packaging marker evaluator unavailable; cannot filter target dependencies") from exc
    env = default_environment()
    env.update(TARGETS[target]["markers"])
    env["python_version"] = TARGETS[target]["python"]
    env["python_full_version"] = TARGETS[target]["python"] + ".0"
    return Marker(marker).evaluate(env)


def parse_hash_requirements(path: pathlib.Path, target: str | None = None) -> dict[str, dict[str, Any]]:
    """Read exact PEP 508 pins emitted by uv/pip and require at least one SHA256."""
    logical: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += " " + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            logical.append(pending.strip())
            pending = ""
    if pending:
        logical.append(pending.strip())
    result: dict[str, dict[str, Any]] = {}
    for line in logical:
        if line.startswith(("--", "-e ")):
            raise ReleaseError(f"non-portable install directive in requirements lock: {line[:120]}")
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*==\s*([^\s;\\]+)(.*)$", line)
        if not match:
            raise ReleaseError(f"requirements lock must use exact name==version pins: {line[:180]}")
        name, version, tail = match.groups()
        hashes = re.findall(r"--hash=sha256:([0-9a-fA-F]{64})", tail)
        if not hashes:
            raise ReleaseError(f"requirements lock has no SHA256 pin for {name}=={version}")
        marker = tail.split(";", 1)[1].split("--hash=", 1)[0].strip() if ";" in tail else ""
        if target and not _marker_matches(marker, target):
            continue
        key = normalized_name(name)
        if key in result:
            previous = result[key]
            if previous["version"] != version:
                raise ReleaseError(f"multiple versions in requirements lock for {name}")
            previous["hashes"].update(h.lower() for h in hashes)
        else:
            result[key] = {"name": name, "version": version, "hashes": set(h.lower() for h in hashes), "marker": marker}
    if not result:
        raise ReleaseError("requirements lock is empty")
    return result


def _locked_package(source_lock: pathlib.Path, name: str, version: str) -> dict[str, Any]:
    if tomllib is None:
        raise ReleaseError("tomllib is required to validate a derived wheel against uv.lock")
    try:
        document = tomllib.loads(source_lock.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"cannot parse source lock {source_lock.name}: {exc}") from exc
    matches = [row for row in document.get("package", [])
               if normalized_name(str(row.get("name", ""))) == normalized_name(name)
               and str(row.get("version", "")) == version]
    if len(matches) != 1:
        raise ReleaseError(f"source lock must contain exactly one {name}=={version} record")
    return matches[0]


def verify_derived_wheel(provenance_path: pathlib.Path, wheel_path: pathlib.Path,
                         source_lock: pathlib.Path, openssl_license_path: pathlib.Path,
                         target: str) -> dict[str, Any]:
    """Validate the separately attested cryptography source-build wheel.

    The repository lock remains immutable. The provenance binds the local wheel
    to its locked sdist and records the non-Python OpenSSL component and native
    audit needed to distinguish a build candidate from an upstream wheel.
    """
    provenance_path = provenance_path.resolve(strict=True)
    wheel_path = wheel_path.resolve(strict=True)
    source_lock = source_lock.resolve(strict=True)
    openssl_license_path = openssl_license_path.resolve(strict=True)
    if target != "macos_x86_64":
        raise ReleaseError("derived cryptography wheel provenance is only valid for macos_x86_64")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"derived wheel provenance is missing or invalid: {exc}") from exc
    if provenance.get("schema") != "comsol-mcp-derived-wheel/1" or provenance.get("status") != "BUILD_CANDIDATE":
        raise ReleaseError("derived wheel provenance must be a BUILD_CANDIDATE using the supported schema")
    if provenance.get("target") != target:
        raise ReleaseError("derived wheel provenance target does not match the requested target")
    package = provenance.get("package", {})
    if package.get("name") != "cryptography" or package.get("version") != "50.0.1":
        raise ReleaseError("derived wheel provenance must identify cryptography==50.0.1")
    wheel_row = provenance.get("wheel", {})
    digest = sha256_file(wheel_path)
    if (wheel_row.get("filename") != wheel_path.name or wheel_row.get("sha256") != digest or
            wheel_row.get("bytes") != wheel_path.stat().st_size):
        raise ReleaseError("derived wheel identity, size, or SHA256 does not match provenance")
    meta = wheel_metadata(wheel_path)
    dist, version, _tags = _wheel_dist_version(wheel_path)
    expected_tag = "cp312-abi3-macosx_12_0_x86_64"
    if (dist != "cryptography" or version != "50.0.1" or meta["normalized_name"] != dist or
            meta["version"] != version or meta["wheel_tags"] != [expected_tag] or
            wheel_row.get("tag") != expected_tag or not wheel_target_check(wheel_path.name, target) or
            not wheel_tag_identity_matches(wheel_path, meta)):
        raise ReleaseError("derived wheel filename or internal tag is not CPython 3.12 abi3 macOS 12 x86_64")

    source = provenance.get("source", {})
    source_lock_row = provenance.get("source_lock", {})
    lock_sha = sha256_file(source_lock)
    locked = _locked_package(source_lock, "cryptography", "50.0.1")
    locked_sdist = locked.get("sdist", {})
    locked_sdist_sha = str(locked_sdist.get("hash", "")).removeprefix("sha256:")
    if (source_lock_row.get("filename") != source_lock.name or source_lock_row.get("sha256") != lock_sha or
            source_lock_row.get("sdist_sha256") != locked_sdist_sha or
            source.get("url") != locked_sdist.get("url") or source.get("sha256") != locked_sdist_sha):
        raise ReleaseError("derived wheel source provenance is not bound to the selected uv.lock sdist record")
    if not re.fullmatch(r"[0-9a-f]{64}", locked_sdist_sha):
        raise ReleaseError("uv.lock cryptography sdist entry has no valid SHA256")

    openssl = provenance.get("native_component", {})
    license_row = openssl.get("license_file", {})
    if (openssl.get("name") != "OpenSSL" or openssl.get("version") != "4.0.2" or
            openssl.get("license") != "Apache-2.0" or openssl.get("linkage") != "static" or
            openssl.get("source_url") != OPENSSL_402_SOURCE_URL or
            openssl.get("source_sha256") != OPENSSL_402_SOURCE_SHA256 or
            license_row.get("filename") != openssl_license_path.name or
            license_row.get("sha256") != sha256_file(openssl_license_path) or
            license_row.get("bytes") != openssl_license_path.stat().st_size):
        raise ReleaseError("derived wheel OpenSSL version, static linkage, source hash, or license binding is invalid")
    native = provenance.get("native_audit", {})
    allowed_system_libraries = {"/usr/lib/libSystem.B.dylib", "/usr/lib/libiconv.2.dylib"}
    dynamic_libraries = set(native.get("dynamic_libraries", []))
    if (native.get("architecture") != "x86_64" or native.get("minimum_os") != "12.0" or
            native.get("openssl_dynamic_dependency") is not False or
            native.get("temporary_dylib_dependencies") != [] or
            not dynamic_libraries.issubset(allowed_system_libraries) or
            not dynamic_libraries):
        raise ReleaseError("derived wheel native audit is missing or reports an unapproved dynamic dependency")
    if provenance.get("uv_lock_modified") is not False or provenance.get("upstream_source_patched") is not False:
        raise ReleaseError("derived wheel provenance must confirm the upstream source and uv.lock were unchanged")
    return {"provenance": provenance, "provenance_sha256": sha256_file(provenance_path),
            "provenance_filename": provenance_path.name, "wheel": wheel_row,
            "native_component": openssl, "license_path": openssl_license_path,
            "source_lock_sha256": lock_sha}


def compose_derived_wheel_lock(requirements: pathlib.Path, wheel: pathlib.Path,
                              source_lock: pathlib.Path, provenance: pathlib.Path,
                              openssl_license: pathlib.Path, target: str,
                              output: pathlib.Path) -> dict[str, Any]:
    """Create a target requirements overlay without editing the source uv.lock."""
    if output.exists():
        raise ReleaseError(f"refusing to overwrite derived requirements lock: {output}")
    derived = verify_derived_wheel(provenance, wheel, source_lock, openssl_license, target)
    pins = parse_hash_requirements(requirements, target)
    pin = pins.get("cryptography")
    if pin is None or pin["version"] != "50.0.1":
        raise ReleaseError("base target requirements must contain cryptography==50.0.1")
    pin["hashes"].add(derived["wheel"]["sha256"])
    rows = [f"# Derived wheel overlay for {target}; uv.lock is unchanged."]
    for key in sorted(pins):
        row = pins[key]
        hashes = " ".join(f"--hash=sha256:{digest}" for digest in sorted(row["hashes"]))
        rows.append(f"{row['name']}=={row['version']} {hashes}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {"status": "DERIVED_TARGET_LOCK_HASHED", "filename": output.name,
            "sha256": sha256_file(output), "target": target,
            "cryptography_wheel_sha256": derived["wheel"]["sha256"],
            "source_lock_sha256": derived["source_lock_sha256"],
            "derived_provenance_sha256": derived["provenance_sha256"]}


def write_target_download_lock(requirements: pathlib.Path, target: str, output: pathlib.Path) -> dict[str, Any]:
    """Write the active target pins with markers resolved for cross-platform pip download.

    `pip download --platform` selects compatible wheel tags but evaluates PEP 508
    markers in the host interpreter. Resolve markers here using our explicit
    target environment, then give pip a marker-free, hash-complete target lock.
    """
    if output.exists():
        raise ReleaseError(f"refusing to overwrite target download lock: {output}")
    pins = parse_hash_requirements(requirements, target)
    rows = [f"# Target-specific download lock for {target}; derived from {requirements.name}."]
    for key in sorted(pins):
        row = pins[key]
        hashes = " ".join(f"--hash=sha256:{digest}" for digest in sorted(row["hashes"]))
        rows.append(f"{row['name']}=={row['version']} {hashes}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {"filename": output.name, "sha256": sha256_file(output), "active_package_count": len(pins)}


def wheel_metadata(path: pathlib.Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as wheel:
            infos = wheel.infolist()
            names = [safe_relative(info.filename) for info in infos if not info.is_dir()]
            if len(names) != len(set(names)):
                raise ReleaseError(f"duplicate members in {path.name}")
            metadata_name = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
            wheel_name = next((n for n in names if n.endswith(".dist-info/WHEEL")), None)
            if not metadata_name or not wheel_name:
                raise ReleaseError(f"missing dist-info metadata in {path.name}")
            text = wheel.read(metadata_name).decode("utf-8", "replace")
            headers: dict[str, list[str]] = {}
            for line in text.splitlines():
                if line.startswith((" ", "\t")) or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                headers.setdefault(key.lower(), []).append(value.strip())
            wheel_text = wheel.read(wheel_name).decode("utf-8", "replace")
            tags = [line[5:].strip() for line in wheel_text.splitlines() if line.startswith("Tag: ")]
            license_files = []
            approved_public_docs = []
            for name in names:
                if (".dist-info/licenses/" in name.lower() or
                        name.rsplit("/", 1)[-1].lower() in {"license", "license.txt", "copying", "notice", "notice.txt"}):
                    payload = wheel.read(name)
                    license_files.append({"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
                if _in_approved_pywin32_help(f"{path.name}!/{name}"):
                    payload = wheel.read(name)
                    approved_public_docs.append({"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
            return {"name": headers.get("name", [""])[0], "normalized_name": normalized_name(headers.get("name", [""])[0]),
                    "version": headers.get("version", [""])[0],
                    "license": headers.get("license-expression", headers.get("license", ["UNKNOWN"]))[0] or "UNKNOWN",
                    "license_classifiers": [v for v in headers.get("classifier", []) if v.lower().startswith("license ::")],
                    "license_files": license_files, "approved_public_docs": approved_public_docs,
                    "requires_dist": headers.get("requires-dist", []),
                    "wheel_tags": tags, "member_count": len(names), "members": names}
    except zipfile.BadZipFile as exc:
        raise ReleaseError(f"invalid wheel {path.name}: {exc}") from exc


def _json_write(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _without_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_timestamps(item) for key, item in value.items()
                if key not in {"timestamp", "created_utc"}}
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def create_bundle_manifest(bundle: pathlib.Path, target: str, requirements: pathlib.Path,
                           source_lock: pathlib.Path | None, output_dir: pathlib.Path,
                           derived_provenance: pathlib.Path | None = None,
                           openssl_license: pathlib.Path | None = None) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    requirements = requirements.resolve(strict=True)
    output_dir = output_dir.resolve()
    if target not in TARGETS:
        raise ReleaseError(f"unknown target: {target}")
    if not bundle.is_dir() or not requirements.is_file():
        raise ReleaseError("bundle and requirements lock must exist")
    if (derived_provenance is None) != (openssl_license is None):
        raise ReleaseError("derived wheel manifests require both provenance and the OpenSSL license file")
    if derived_provenance is not None and source_lock is None:
        raise ReleaseError("derived wheel provenance requires the source uv.lock")
    if output_dir == bundle or bundle in output_dir.parents:
        raise ReleaseError("manifest output must be outside the wheelhouse")
    output_files = [output_dir / n for n in ("PACKAGE_MANIFEST.json", "SBOM.cdx.json", "LICENSES.json")]
    if any(path.exists() for path in output_files):
        raise ReleaseError(f"manifest evidence already exists; choose a new output directory: {output_dir}")
    audit = audit_tree(bundle)
    if audit["status"] != "PASS":
        raise ReleaseError("release bundle scan failed: " + json.dumps(audit["findings"], ensure_ascii=False))
    unexpected = [p.name for p in bundle.iterdir() if not p.is_file() or p.suffix != ".whl"]
    if unexpected:
        raise ReleaseError("wheelhouse must contain only flat .whl files: " + ", ".join(sorted(unexpected)))
    wheels = sorted(bundle.glob("*.whl"))
    if not wheels:
        raise ReleaseError("wheelhouse is empty")
    pins = parse_hash_requirements(requirements, target)
    derived = None
    if derived_provenance is not None and source_lock is not None and openssl_license is not None:
        try:
            provenance_doc = json.loads(derived_provenance.read_text(encoding="utf-8"))
            derived_wheel_name = provenance_doc["wheel"]["filename"]
            if (not isinstance(derived_wheel_name, str) or safe_relative(derived_wheel_name) != derived_wheel_name or
                    pathlib.PurePosixPath(derived_wheel_name).name != derived_wheel_name):
                raise ReleaseError("derived wheel provenance has an unsafe wheel filename")
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ReleaseError(f"derived wheel provenance is missing or invalid: {exc}") from exc
        derived = verify_derived_wheel(derived_provenance, bundle / derived_wheel_name,
                                       source_lock, openssl_license, target)
    if derived is not None:
        derived_pin = pins.get("cryptography")
        if derived_pin is None or derived_pin["version"] != "50.0.1" or derived["wheel"]["sha256"] not in derived_pin["hashes"]:
            raise ReleaseError("derived cryptography wheel SHA256 is absent from the target requirements lock")
    components = []
    found: set[str] = set()
    for wheel in wheels:
        meta = wheel_metadata(wheel)
        if not wheel_target_check(wheel.name, target):
            raise ReleaseError(f"wheel tag is incompatible with {target}: {wheel.name}")
        if not wheel_tag_identity_matches(wheel, meta):
            raise ReleaseError(f"wheel filename tag disagrees with internal WHEEL metadata: {wheel.name}")
        dist, version, _ = _wheel_dist_version(wheel)
        if normalized_name(meta["name"]) != dist or meta["version"] != version:
            raise ReleaseError(f"wheel filename identity disagrees with METADATA: {wheel.name}")
        pin = pins.get(dist)
        if pin is None or pin["version"] != version:
            raise ReleaseError(f"wheel is not pinned by requirements lock: {wheel.name}")
        digest = sha256_file(wheel)
        if digest not in pin["hashes"]:
            raise ReleaseError(f"wheel SHA256 is absent from requirements lock: {wheel.name}")
        if dist in found:
            raise ReleaseError(f"multiple wheel variants for one distribution: {wheel.name}")
        found.add(dist)
        properties = [{"name": "wheel.tags", "value": ",".join(meta["wheel_tags"])}]
        if derived is not None and dist == "cryptography":
            properties.extend([
                {"name": "release.origin", "value": "derived-from-locked-sdist"},
                {"name": "release.source.sdist-sha256", "value": derived["provenance"]["source"]["sha256"]},
                {"name": "release.build-provenance-sha256", "value": derived["provenance_sha256"]},
            ])
        components.append({"type": "library", "name": meta["name"], "version": meta["version"],
                           "purl": f"pkg:pypi/{meta['normalized_name']}@{meta['version']}",
                           "hashes": [{"alg": "SHA-256", "content": digest}],
                           "licenses": [{"license": {"name": meta["license"]}}] if meta["license"] != "UNKNOWN" else [],
                           "properties": properties})
    missing = sorted(set(pins) - found)
    if missing:
        raise ReleaseError("locked distributions have no wheel in target wheelhouse: " + ", ".join(missing))
    manifest = {
        "schema": SCHEMA, "status": "ARTIFACTS_HASHED_NATIVE_UNVERIFIED", "created_utc": utc_now(),
        "target": target, "target_tags_verified": True, "target_execution_verified": False,
        "python": TARGETS[target]["python"], "minimum_os": TARGETS[target].get("minimum_os"),
        "requirements": {"filename": requirements.name, "sha256": sha256_file(requirements)},
        "source_lock": ({"filename": source_lock.name, "sha256": sha256_file(source_lock)} if source_lock else None),
        "wheel_count": len(wheels), "wheels": [], "scan": {"status": audit["status"], "file_count": audit["file_count"], "findings": audit["findings"]},
    }
    licenses = {"schema": SCHEMA, "created_utc": utc_now(), "target": target, "components": []}
    for wheel, component in zip(wheels, components):
        meta = wheel_metadata(wheel)
        row = {"file": wheel.name, "bytes": wheel.stat().st_size, "sha256": sha256_file(wheel),
               "name": meta["name"], "version": meta["version"], "wheel_tags": meta["wheel_tags"],
               "license": meta["license"], "license_classifiers": meta["license_classifiers"],
               "approved_public_docs": meta["approved_public_docs"],
               "license_files": meta["license_files"]}
        manifest["wheels"].append(row)
        licenses["components"].append({k: row[k] for k in ("name", "version", "license", "license_classifiers", "license_files")})
    if derived is not None:
        native = derived["native_component"]
        license_record = {"path": "licenses/openssl-4.0.2-LICENSE.txt",
                          "bytes": openssl_license.stat().st_size,
                          "sha256": sha256_file(openssl_license)}
        component = {"type": "library", "name": native["name"], "version": native["version"],
                     "purl": f"pkg:generic/openssl@{native['version']}",
                     "hashes": [{"alg": "SHA-256", "content": native["source_sha256"]}],
                     "licenses": [{"license": {"name": native["license"]}}],
                     "properties": [
                         {"name": "release.component-kind", "value": "statically-linked-native-dependency"},
                         {"name": "release.linkage", "value": "static"},
                         {"name": "release.source.url", "value": native["source_url"]},
                         {"name": "release.source.sha256", "value": native["source_sha256"]},
                         {"name": "release.linked-into", "value": "cryptography==50.0.1"},
                     ]}
        components.append(component)
        licenses["components"].append({"name": native["name"], "version": native["version"],
                                       "license": native["license"], "license_classifiers": [],
                                       "license_files": [license_record]})
        manifest["derived_wheel"] = {
            "package": "cryptography==50.0.1", "wheel": derived["wheel"],
            "provenance": {"filename": derived["provenance_filename"], "sha256": derived["provenance_sha256"]},
            "source_lock_sha256": derived["source_lock_sha256"],
            "native_component": {"name": native["name"], "version": native["version"],
                                  "source_sha256": native["source_sha256"], "linkage": native["linkage"],
                                  "license": native["license"], "license_file": license_record},
        }
    sbom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
            "metadata": {"timestamp": utc_now(), "component": {"type": "application", "name": "comsol-mcp-offline-bundle", "version": "candidate"}},
            "components": components}
    output_dir.mkdir(parents=True, exist_ok=True)
    _json_write(output_dir / "SBOM.cdx.json", sbom)
    _json_write(output_dir / "LICENSES.json", licenses)
    manifest["evidence_files"] = {
        "sbom": {"filename": "SBOM.cdx.json", "sha256": sha256_file(output_dir / "SBOM.cdx.json")},
        "licenses": {"filename": "LICENSES.json", "sha256": sha256_file(output_dir / "LICENSES.json")},
    }
    _json_write(output_dir / "PACKAGE_MANIFEST.json", manifest)
    return manifest


def compose_install_lock(base_requirements: pathlib.Path, application_wheel: pathlib.Path,
                        target: str, output: pathlib.Path) -> dict[str, Any]:
    if target not in TARGETS:
        raise ReleaseError(f"unknown target: {target}")
    base_requirements = base_requirements.resolve(strict=True)
    application_wheel = application_wheel.resolve(strict=True)
    app_meta = wheel_metadata(application_wheel)
    dist, version, _tags = _wheel_dist_version(application_wheel)
    if dist != "comsol-mcp" or app_meta["name"].lower() != "comsol-mcp":
        raise ReleaseError("application input is not the comsol-mcp wheel")
    if not wheel_target_check(application_wheel.name, target):
        raise ReleaseError(f"application wheel is not compatible with {target}")
    if not wheel_tag_identity_matches(application_wheel, app_meta) or app_meta["version"] != version:
        raise ReleaseError("application wheel filename tag/identity disagrees with internal metadata")
    pins = parse_hash_requirements(base_requirements, target)
    if dist in pins:
        raise ReleaseError("base dependency export already contains comsol-mcp; export with --no-emit-project")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ReleaseError(f"refusing to overwrite composed lock: {output}")
    base = base_requirements.read_text(encoding="utf-8").rstrip()
    line = f"comsol-mcp=={version} \\\n    --hash=sha256:{sha256_file(application_wheel)}"
    output.write_text(base + "\n\n# Locally built application wheel, pinned by the bundle manifest.\n" + line + "\n", encoding="utf-8")
    composed = parse_hash_requirements(output, target)
    if dist not in composed:
        raise ReleaseError("composed install lock lost the application wheel pin")
    return {"schema": SCHEMA, "status": "LOCK_COMPOSED", "target": target,
            "base_lock_sha256": sha256_file(base_requirements),
            "application_wheel": application_wheel.name,
            "application_wheel_sha256": sha256_file(application_wheel),
            "requirements": output.name, "requirements_sha256": sha256_file(output),
            "active_package_count": len(composed)}


def download_wheelhouse(python: pathlib.Path, target: str, requirements: pathlib.Path,
                        application_wheel: pathlib.Path, output: pathlib.Path,
                        evidence: pathlib.Path, source_lock: pathlib.Path) -> dict[str, Any]:
    if target not in TARGETS:
        raise ReleaseError(f"unknown target: {target}")
    requirements = requirements.resolve(strict=True)
    source_lock = source_lock.resolve(strict=True)
    pins = parse_hash_requirements(requirements, target)
    if "comsol-mcp" not in pins:
        raise ReleaseError("composed requirements must pin the application wheel")
    if output.exists():
        if any(output.iterdir()):
            raise ReleaseError(f"wheelhouse destination is not empty: {output}")
    else:
        output.mkdir(parents=True)
    application_wheel = application_wheel.resolve(strict=True)
    if not wheel_target_check(application_wheel.name, target):
        raise ReleaseError(f"application wheel tag does not match {target}: {application_wheel.name}")
    shutil.copyfile(application_wheel, output / application_wheel.name)
    evidence.mkdir(parents=True, exist_ok=True)
    download_lock_path = evidence / f"requirements-{target}.download.lock"
    download_lock = write_target_download_lock(requirements, target, download_lock_path)
    pyver = TARGETS[target]["python"]
    abi = f"cp{pyver.replace('.', '')}"
    args = [str(python), "-m", "pip", "download", "--index-url", "https://pypi.org/simple",
            "--only-binary=:all:", "--disable-pip-version-check", "--no-input", "--no-cache-dir",
            "--dest", str(output), "--find-links", str(output)]
    for platform in TARGETS[target]["pip_platforms"]:
        args.extend(("--platform", platform))
    args.extend(("--python-version", pyver, "--implementation", "cp", "--abi", abi,
                 "--abi", "abi3", "--abi", "none", "--require-hashes", "--requirement", str(download_lock_path)))
    record = run_record(args)
    _json_write(evidence / f"wheelhouse-download-{target}.json", {
        "schema": SCHEMA, "created_utc": utc_now(), "target": target,
        "target_execution_verified": False, "command": ["<python>", "-m", "pip", "download", "--index-url", "https://pypi.org/simple",
        "--only-binary=:all:", *[item for platform in TARGETS[target]["pip_platforms"] for item in ("--platform", platform)], "--python-version", pyver,
        "--implementation", "cp", "--abi", abi, "--abi", "abi3", "--abi", "none", "--require-hashes", "--requirement", download_lock_path.name],
        "source_requirements": {"filename": requirements.name, "sha256": sha256_file(requirements)},
        "target_download_lock": download_lock,
        "exit_code": record["exit_code"], "stdout": record["stdout"], "stderr": record["stderr"]})
    if record["exit_code"]:
        raise ReleaseError("target wheelhouse download failed; inspect recorded stdout/stderr")
    manifest = create_bundle_manifest(output, target, requirements, source_lock, evidence / target)
    return {"status": "WHEELHOUSE_HASHED_NATIVE_UNVERIFIED", "download": record,
            "manifest": manifest}


def package_wheelhouse_bundle(wheelhouse: pathlib.Path, requirements: pathlib.Path,
                              source_lock: pathlib.Path, metadata_dir: pathlib.Path,
                              operations_doc: pathlib.Path, target: str,
                              archive_path: pathlib.Path, evidence_dir: pathlib.Path,
                              build_evidence: pathlib.Path,
                              tool_path: pathlib.Path | None = None,
                              derived_provenance: pathlib.Path | None = None,
                              openssl_license: pathlib.Path | None = None) -> dict[str, Any]:
    """Package a target wheelhouse and its complete install/audit metadata."""
    wheelhouse = wheelhouse.resolve(strict=True)
    requirements = requirements.resolve(strict=True)
    source_lock = source_lock.resolve(strict=True)
    metadata_dir = metadata_dir.resolve(strict=True)
    operations_doc = operations_doc.resolve(strict=True)
    build_evidence = build_evidence.resolve(strict=True)
    tool_path = (tool_path or pathlib.Path(__file__)).resolve(strict=True)
    if (derived_provenance is None) != (openssl_license is None):
        raise ReleaseError("derived wheel bundles require both provenance and the OpenSSL license file")
    if derived_provenance is not None:
        derived_provenance = derived_provenance.resolve(strict=True)
        openssl_license = openssl_license.resolve(strict=True)
    archive_path = archive_path.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if target not in TARGETS:
        raise ReleaseError(f"unknown target: {target}")
    if archive_path.exists():
        raise ReleaseError(f"refusing to overwrite offline bundle: {archive_path}")
    evidence_path = evidence_dir / f"offline-bundle-{target}.json"
    if evidence_path.exists():
        raise ReleaseError(f"offline bundle evidence already exists: {evidence_path}")
    package_manifest = json.loads((metadata_dir / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    sbom_path = metadata_dir / "SBOM.cdx.json"
    licenses_path = metadata_dir / "LICENSES.json"
    build_receipt_path = build_evidence / "wheel-build-receipt.json"
    source_manifest_path = build_evidence / "source-snapshot-manifest.json"
    try:
        build_receipt = json.loads(build_receipt_path.read_text(encoding="utf-8"))
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"build receipt or source snapshot manifest is missing/invalid: {exc}") from exc
    source_rows = source_manifest.get("files")
    if (build_receipt.get("status") != "WHEEL_BUILT_NATIVE_UNVERIFIED" or
            source_manifest.get("kind") != "IMMUTABLE_RUNTIME_SOURCE_SNAPSHOT" or
            not isinstance(source_rows, list) or
            source_manifest.get("file_count") != len(source_rows) or
            source_manifest.get("file_count") != build_receipt.get("source_file_count") or
            source_manifest.get("manifest_sha256") != build_receipt.get("source_manifest_sha256") or
            hashlib.sha256(json.dumps(source_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != source_manifest.get("manifest_sha256")):
        raise ReleaseError("application wheel build receipt is not bound to a valid immutable source manifest")
    app_record = next((row for row in package_manifest.get("wheels", []) if row.get("name", "").lower().replace("_", "-") == "comsol-mcp"), None)
    if (not app_record or app_record.get("file") != build_receipt.get("wheel") or
            app_record.get("sha256") != build_receipt.get("sha256") or
            app_record.get("version") != build_receipt.get("wheel_metadata", {}).get("version")):
        raise ReleaseError("wheelhouse application wheel does not match the build receipt")
    if package_manifest.get("target") != target or package_manifest.get("target_execution_verified") is not False:
        raise ReleaseError("wheelhouse manifest target or native-state claim is inconsistent")
    if package_manifest.get("requirements", {}).get("sha256") != sha256_file(requirements):
        raise ReleaseError("wheelhouse manifest was created from a different requirements lock")
    source_lock_record = package_manifest.get("source_lock")
    if not isinstance(source_lock_record, dict) or source_lock_record.get("sha256") != sha256_file(source_lock):
        raise ReleaseError("wheelhouse manifest source lock hash does not match the selected source lock")
    if (package_manifest.get("derived_wheel") is not None) != (derived_provenance is not None):
        raise ReleaseError("derived wheel manifest and bundle inputs disagree")
    if derived_provenance is not None:
        if package_manifest.get("derived_wheel", {}).get("provenance", {}).get("sha256") != sha256_file(derived_provenance):
            raise ReleaseError("derived wheel provenance hash does not match the package manifest")
    # Re-audit immediately before packaging so an altered wheel cannot ride on an old manifest.
    with tempfile.TemporaryDirectory(prefix=f"manifest-recheck-{target}-", dir=evidence_dir) as temp:
        verified = create_bundle_manifest(wheelhouse, target, requirements, source_lock,
                                          pathlib.Path(temp) / "metadata", derived_provenance, openssl_license)
        rechecked_metadata = pathlib.Path(temp) / "metadata"
        rechecked_sbom = json.loads((rechecked_metadata / "SBOM.cdx.json").read_text(encoding="utf-8"))
        rechecked_licenses = json.loads((rechecked_metadata / "LICENSES.json").read_text(encoding="utf-8"))
    if [(r["file"], r["sha256"]) for r in verified["wheels"]] != [(r["file"], r["sha256"]) for r in package_manifest["wheels"]]:
        raise ReleaseError("wheelhouse contents changed since the supplied package manifest")
    if verified.get("derived_wheel") != package_manifest.get("derived_wheel"):
        raise ReleaseError("derived wheel/component metadata changed since the supplied package manifest")
    for filename, expected in (("SBOM.cdx.json", rechecked_sbom), ("LICENSES.json", rechecked_licenses)):
        try:
            supplied = json.loads((metadata_dir / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseError(f"cannot read bundle metadata {filename}: {exc}") from exc
        if _without_timestamps(supplied) != _without_timestamps(expected):
            raise ReleaseError(f"{filename} components do not match a fresh wheelhouse audit")
    if not sbom_path.is_file() or not licenses_path.is_file():
        raise ReleaseError("SBOM or license inventory missing from metadata directory")
    if sha256_file(sbom_path) != package_manifest.get("evidence_files", {}).get("sbom", {}).get("sha256"):
        raise ReleaseError("SBOM hash does not match package manifest")
    if sha256_file(licenses_path) != package_manifest.get("evidence_files", {}).get("licenses", {}).get("sha256"):
        raise ReleaseError("license inventory hash does not match package manifest")
    files: list[tuple[str, pathlib.Path]] = [
        ("tools/full_project_release.py", tool_path),
        ("locks/requirements.lock", requirements),
        ("locks/uv.lock", source_lock),
        ("PACKAGE_MANIFEST.json", metadata_dir / "PACKAGE_MANIFEST.json"),
        ("SBOM.cdx.json", sbom_path),
        ("LICENSES.json", licenses_path),
        ("evidence/wheel-build-receipt.json", build_receipt_path),
        ("evidence/source-snapshot-manifest.json", source_manifest_path),
        ("docs/RELEASE_OPERATIONS.md", operations_doc),
    ]
    if derived_provenance is not None and openssl_license is not None:
        files.extend([
            ("evidence/cryptography-x86-derived-provenance.json", derived_provenance),
            ("licenses/openssl-4.0.2-LICENSE.txt", openssl_license),
        ])
    files.extend((f"wheelhouse/{wheel.name}", wheel) for wheel in sorted(wheelhouse.glob("*.whl")))
    install_notes = (
        f"COMSOL MCP offline bundle for {target} / CPython {TARGETS[target]['python']}.\n"
        "Extract this archive to a clean directory. Use a separate Python 3.12 venv.\n"
        "Windows: py -3.12 -m venv .venv, then .venv\\Scripts\\python.exe -m pip install --no-index --only-binary=:all: --find-links wheelhouse --require-hashes -r locks\\requirements.lock\n"
        "macOS: python3.12 -m venv .venv, then .venv/bin/python -m pip install --no-index --only-binary=:all: --find-links wheelhouse --require-hashes -r locks/requirements.lock\n"
        "This package does not contain COMSOL, commercial JAR/help/license data, models, or credentials.\n"
        "Python package install/import checks do not certify COMSOL engine or scientific capabilities.\n"
    )
    install_notes_bytes = install_notes.encode("utf-8")
    records = []
    for arcname, path in files:
        kind = scan_member_name(arcname)
        if kind:
            raise ReleaseError(f"bundle member rejected {arcname}: {kind}")
        if not path.is_file() or path.is_symlink():
            raise ReleaseError(f"bundle input is missing or not a regular file: {arcname}")
        records.append({"path": arcname, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    records.append({"path": "README_OFFLINE_INSTALL.txt", "bytes": len(install_notes_bytes),
                    "sha256": hashlib.sha256(install_notes_bytes).hexdigest()})
    total_bytes = sum(row["bytes"] for row in records)
    if total_bytes > MAX_TOTAL_BYTES:
        raise ReleaseError(f"offline bundle exceeds the {MAX_TOTAL_BYTES}-byte expanded-size limit")
    bundle_manifest = {"schema": SCHEMA, "kind": "OFFLINE_INSTALL_BUNDLE",
                       "status": "PACKAGED_NATIVE_UNVERIFIED", "created_utc": utc_now(),
                       "target": target, "python": TARGETS[target]["python"],
                       "native_execution_verified": False,
                       "source_lock": {"filename": source_lock.name, "sha256": sha256_file(source_lock)},
                       "source_lock_included": "locks/uv.lock",
                       "requirements": {"filename": requirements.name, "sha256": sha256_file(requirements)},
                       "requirements_included": "locks/requirements.lock",
                       "source_lock_sha256": sha256_file(source_lock),
                       "requirements_sha256": sha256_file(requirements),
                       "files_excluding_this_manifest": records}
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_manifest_bytes = (json.dumps(bundle_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for arcname, path in files:
            archive.write(path, arcname=arcname)
        archive.writestr("README_OFFLINE_INSTALL.txt", install_notes_bytes)
        archive.writestr("OFFLINE_BUNDLE_MANIFEST.json", bundle_manifest_bytes)
    findings: list[dict[str, str]] = []
    expected_members = {row["path"]: row for row in records}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            seen: set[str] = set()
            member_total = 0
            for info in archive.infolist():
                arcname = safe_relative(info.filename)
                dir_kind = scan_member_name(arcname)
                if dir_kind:
                    findings.append({"path": arcname, "kind": dir_kind})
                if info.is_dir():
                    continue
                mode = stat_module.S_IFMT((info.external_attr >> 16) & 0xFFFF)
                if mode not in (0, stat_module.S_IFREG):
                    findings.append({"path": arcname, "kind": "ARCHIVE_LINK_OR_SPECIAL_FILE"})
                    continue
                if arcname in seen:
                    findings.append({"path": arcname, "kind": "DUPLICATE_ARCHIVE_MEMBER"})
                    continue
                seen.add(arcname)
                member_total += info.file_size
                if info.file_size > MAX_FILE_BYTES or member_total > MAX_TOTAL_BYTES:
                    findings.append({"path": arcname, "kind": "ARCHIVE_MEMBER_SIZE_LIMIT"})
                    continue
                payload = archive.read(info)
                if arcname == "OFFLINE_BUNDLE_MANIFEST.json":
                    try:
                        embedded_manifest = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        findings.append({"path": arcname, "kind": "INVALID_BUNDLE_MANIFEST"})
                    else:
                        if embedded_manifest.get("files_excluding_this_manifest") != records:
                            findings.append({"path": arcname, "kind": "BUNDLE_MANIFEST_CONTENT_MISMATCH"})
                    continue
                row = expected_members.get(arcname)
                if row is None:
                    findings.append({"path": arcname, "kind": "UNLISTED_BUNDLE_MEMBER"})
                elif row["bytes"] != len(payload) or row["sha256"] != hashlib.sha256(payload).hexdigest():
                    findings.append({"path": arcname, "kind": "BUNDLE_MEMBER_HASH_MISMATCH"})
                else:
                    _inspect_bytes(arcname, payload, findings)
            if seen != set(expected_members) | {"OFFLINE_BUNDLE_MANIFEST.json"}:
                findings.append({"path": archive_path.name, "kind": "BUNDLE_MEMBER_SET_MISMATCH"})
    except (OSError, zipfile.BadZipFile, ReleaseError) as exc:
        findings.append({"path": archive_path.name, "kind": "INVALID_OFFLINE_BUNDLE", "detail": str(exc)[:240]})
    if findings:
        raise ReleaseError("offline bundle recursive scan failed: " + json.dumps(findings, ensure_ascii=False))
    result = {"schema": SCHEMA, "status": "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED",
              "created_utc": utc_now(), "target": target, "archive": archive_path.name,
              "bytes": archive_path.stat().st_size, "sha256": sha256_file(archive_path),
              "member_count": len(records) + 1,
              "manifest_sha256": hashlib.sha256(bundle_manifest_bytes).hexdigest(),
              "native_execution_verified": False}
    _json_write(evidence_path, result)
    return result


def source_snapshot(source: pathlib.Path, stage: pathlib.Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    if not (source / "pyproject.toml").is_file() or not (source / "comsol_mcp").is_dir():
        raise ReleaseError("source must be a COMSOL MCP project root")
    files: list[pathlib.Path] = []
    for item in sorted((source / "comsol_mcp").rglob("*")):
        if item.is_symlink():
            raise ReleaseError(f"source symlink is not packageable: {item.relative_to(source)}")
        rel_parts = item.relative_to(source).parts
        if item.is_file() and not any(part.startswith("._") or part in {"__pycache__", ".pytest_cache"} for part in rel_parts) and item.suffix not in {".pyc", ".pyo"}:
            files.append(item)
    files.extend(source / n for n in SOURCE_ROOT_FILES if (source / n).is_file())
    if not all((source / name).is_file() and not (source / name).is_symlink() for name in SOURCE_ROOT_FILES):
        raise ReleaseError("runtime source package requires regular pyproject.toml, README.md, and LICENSE files")
    before = {}
    total_before = 0
    for item in files:
        rel = item.relative_to(source).as_posix()
        if item.is_symlink():
            raise ReleaseError(f"source symlink is not packageable: {rel}")
        size = item.stat().st_size
        total_before += size
        if size > MAX_FILE_BYTES or total_before > MAX_TOTAL_BYTES:
            raise ReleaseError(f"runtime source snapshot exceeds configured size limit at {rel}")
        before[rel] = {"path": rel, "bytes": size, "sha256": sha256_file(item)}
    records = []
    stage.mkdir(parents=True)
    for item in files:
        rel = item.relative_to(source)
        rel_text = rel.as_posix()
        kind = scan_member_name(rel_text)
        if kind:
            raise ReleaseError(f"prohibited source file {rel_text}: {kind}")
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        record = before[rel_text]
        records.append(record)
        if target.stat().st_size != record["bytes"] or sha256_file(target) != record["sha256"]:
            raise ReleaseError(f"source changed while snapshotting: {rel_text}")
    after_files = sorted(
        item.relative_to(source).as_posix()
        for item in (source / "comsol_mcp").rglob("*")
        if item.is_file() and not item.is_symlink()
        and not any(part.startswith("._") or part in {"__pycache__", ".pytest_cache"} for part in item.relative_to(source).parts)
        and item.suffix not in {".pyc", ".pyo"}
    ) + sorted(name for name in SOURCE_ROOT_FILES if (source / name).is_file())
    if set(after_files) != set(before):
        raise ReleaseError("source inventory changed while taking the wheel snapshot")
    for rel, record in before.items():
        current = source.joinpath(*pathlib.PurePosixPath(rel).parts)
        if not current.is_file() or current.is_symlink() or current.stat().st_size != record["bytes"] or sha256_file(current) != record["sha256"]:
            raise ReleaseError(f"source changed while snapshotting: {rel}")
    return {"files": records, "file_count": len(records),
            "manifest_sha256": hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def source_bundle(source: pathlib.Path, manifest_path: pathlib.Path, archive_path: pathlib.Path,
                  evidence: pathlib.Path) -> dict[str, Any]:
    """Package only explicit project-relative files; never traverses Git/private state."""
    source = source.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    archive_path = archive_path.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        raise ReleaseError(f"refusing to overwrite existing source bundle: {archive_path}")
    if (evidence / "source-bundle-receipt.json").exists():
        raise ReleaseError(f"source-bundle evidence already exists: {evidence}")
    try:
        contract = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"invalid source inclusion manifest: {exc}") from exc
    if not isinstance(contract, dict) or contract.get("schema") != "comsol-mcp-full-release-source-input/1":
        raise ReleaseError("source inclusion manifest has an unknown schema")
    includes = contract.get("files")
    if not isinstance(includes, list) or not includes:
        raise ReleaseError("source inclusion manifest must contain a nonempty files list")
    entries: list[tuple[str, pathlib.Path, dict[str, Any]]] = []
    seen: set[str] = set()
    total = 0
    for row in includes:
        value = row.get("path") if isinstance(row, dict) else row
        if not isinstance(value, str):
            raise ReleaseError("source inclusion entries must be relative path strings or {path,...} objects")
        rel = safe_relative(value)
        if rel in seen:
            raise ReleaseError(f"duplicate source inclusion path: {rel}")
        seen.add(rel)
        kind = scan_member_name(rel)
        if kind:
            raise ReleaseError(f"source inclusion rejected {rel}: {kind}")
        target = source.joinpath(*pathlib.PurePosixPath(rel).parts)
        cursor = target
        while cursor != source:
            if cursor.is_symlink():
                raise ReleaseError(f"source inclusion traverses a symlink: {rel}")
            cursor = cursor.parent
            if source not in cursor.parents and cursor != source:
                raise ReleaseError(f"source inclusion escapes source root: {rel}")
        if not target.is_file():
            raise ReleaseError(f"source inclusion is missing or not a regular file: {rel}")
        size = target.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ReleaseError(f"source bundle exceeds size budget at {rel}")
        digest = sha256_file(target)
        file_findings: list[dict[str, str]] = []
        _inspect_bytes(rel, target.read_bytes(), file_findings, 0)
        if file_findings:
            raise ReleaseError(f"source inclusion has prohibited content at {rel}: {file_findings[0]['kind']}")
        reason = row.get("reason") if isinstance(row, dict) else None
        distribution_class = row.get("distribution_class") if isinstance(row, dict) else None
        review_flags = row.get("public_source_review_flags") if isinstance(row, dict) else None
        if reason is not None and not isinstance(reason, str):
            raise ReleaseError(f"source inclusion reason must be text: {rel}")
        if distribution_class is not None and (not isinstance(distribution_class, str) or
                                                distribution_class not in SOURCE_DISTRIBUTION_CLASSES):
            raise ReleaseError(f"source inclusion has unknown distribution_class: {rel}")
        if review_flags is not None and (not isinstance(review_flags, list) or
                                         any(not isinstance(flag, str) or not flag.strip() for flag in review_flags)):
            raise ReleaseError(f"source inclusion public_source_review_flags must be nonempty text entries: {rel}")
        output_row = {"path": rel, "bytes": size, "sha256": digest, "reason": reason}
        if distribution_class is not None:
            output_row["distribution_class"] = distribution_class
        if review_flags is not None:
            output_row["public_source_review_flags"] = review_flags
        entries.append((rel, target, output_row))
    distribution_counts: dict[str, dict[str, int]] = {}
    for _rel, _target, row in entries:
        classification = row.get("distribution_class")
        if classification is not None:
            summary = distribution_counts.setdefault(classification, {"file_count": 0, "bytes": 0})
            summary["file_count"] += 1
            summary["bytes"] += row["bytes"]
    input_receipt = {"schema": SCHEMA, "kind": "EXPLICIT_SOURCE_EXPORT", "created_utc": utc_now(),
                     "native_certified": False, "git_history_included": False,
                     "source_root_label": source.name, "archive_scope": "LOCAL_RECOVERY_ARCHIVE",
                     "public_source_release_authorized": False,
                     "included_file_count": len(entries), "distribution_scope_counts": distribution_counts,
                     "included_files": [row for _, _, row in entries],
                     "manifest_source_sha256": sha256_file(manifest_path)}
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for rel, path, _row in entries:
            archive.write(path, arcname=rel)
        archive.writestr("RELEASE_INPUT_MANIFEST.json", json.dumps(input_receipt, ensure_ascii=False, indent=2) + "\n")
    # Bind the bytes actually read by zipfile to the earlier inclusion manifest.
    # This closes the source-mutation window between hashing a selected file and
    # zipfile opening it for packaging; a successful archive must be a byte-for-
    # byte capture of exactly the reviewed explicit input list.
    expected_paths = {rel for rel, _path, _row in entries}
    expected_paths.add("RELEASE_INPUT_MANIFEST.json")
    with zipfile.ZipFile(archive_path, "r") as packaged:
        names = packaged.namelist()
        if len(names) != len(set(names)) or set(names) != expected_paths:
            raise ReleaseError("source archive members do not exactly match the explicit inclusion manifest")
        for rel, _path, expected in entries:
            info = packaged.getinfo(rel)
            if info.file_size != expected["bytes"]:
                raise ReleaseError(f"source archive member size differs from input manifest: {rel}")
            digest = hashlib.sha256()
            with packaged.open(info, "r") as member:
                for chunk in iter(lambda: member.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected["sha256"]:
                raise ReleaseError(f"source archive member SHA-256 differs from input manifest: {rel}")
        try:
            embedded_receipt = json.loads(packaged.read("RELEASE_INPUT_MANIFEST.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ReleaseError("source archive input manifest is missing or invalid") from exc
        if embedded_receipt != input_receipt:
            raise ReleaseError("source archive input manifest differs from the reviewed source inventory")
    wrapper = archive_path.parent / (archive_path.name + ".audit")
    wrapper.mkdir()
    try:
        audit_copy = wrapper / archive_path.name
        shutil.copyfile(archive_path, audit_copy)
        audit = audit_tree(wrapper)
    finally:
        shutil.rmtree(wrapper)
    if audit["status"] != "PASS":
        raise ReleaseError("source archive scan failed: " + json.dumps(audit["findings"], ensure_ascii=False))
    result = {"schema": SCHEMA, "status": "SOURCE_ARCHIVE_HASHED_NATIVE_UNVERIFIED", "created_utc": utc_now(),
              "archive": archive_path.name, "bytes": archive_path.stat().st_size,
              "sha256": sha256_file(archive_path), "included_file_count": len(entries),
              "manifest_source_sha256": input_receipt["manifest_source_sha256"],
              "archive_scan": {"status": audit["status"], "member_count": len(audit["files"]),
                               "findings": audit["findings"]}}
    _json_write(evidence / "source-bundle-receipt.json", result)
    return result


def verify_source_bundle(bundle: pathlib.Path, extract_to: pathlib.Path | None = None) -> dict[str, Any]:
    """Verify an explicit source archive and optionally extract it to a new directory."""
    bundle = pathlib.Path(bundle).expanduser()
    if bundle.is_symlink():
        raise ReleaseError("source archive must be a regular, non-symlink file")
    bundle = bundle.resolve(strict=True)
    if not bundle.is_file():
        raise ReleaseError("source archive must be a regular, non-symlink file")
    archive_sha256 = sha256_file(bundle)
    output_root: pathlib.Path | None = None
    if extract_to is not None:
        output_root = extract_to.expanduser().absolute()
        if output_root.exists() or output_root.is_symlink():
            raise ReleaseError(f"source extraction destination already exists: {output_root}")

    try:
        archive = zipfile.ZipFile(bundle, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseError(f"invalid source archive: {exc}") from exc

    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ReleaseError("source archive contains duplicate member names")
        if "RELEASE_INPUT_MANIFEST.json" not in names:
            raise ReleaseError("source archive is missing RELEASE_INPUT_MANIFEST.json")
        if any(info.is_dir() for info in infos):
            raise ReleaseError("source archive must not contain directory entries")

        try:
            manifest_bytes = archive.read("RELEASE_INPUT_MANIFEST.json")
            receipt = json.loads(manifest_bytes)
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as exc:
            raise ReleaseError("source archive input manifest is missing or invalid") from exc
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if (not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA or
                receipt.get("kind") != "EXPLICIT_SOURCE_EXPORT" or
                receipt.get("native_certified") is not False or
                receipt.get("git_history_included") is not False):
            raise ReleaseError("source archive input manifest has an unknown or unsafe contract")
        rows = receipt.get("included_files")
        if not isinstance(rows, list) or not rows or receipt.get("included_file_count") != len(rows):
            raise ReleaseError("source archive input manifest has an invalid included_files list")

        expected: dict[str, dict[str, Any]] = {}
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                raise ReleaseError("source archive input entries must be objects")
            rel = row.get("path")
            if not isinstance(rel, str):
                raise ReleaseError("source archive input entry is missing a relative path")
            rel = safe_relative(rel)
            if rel == "RELEASE_INPUT_MANIFEST.json":
                raise ReleaseError("source archive manifest must not list itself as a source file")
            if rel in expected:
                raise ReleaseError(f"source archive input manifest duplicates path: {rel}")
            kind = scan_member_name(rel)
            if kind:
                raise ReleaseError(f"source archive input manifest rejects {rel}: {kind}")
            size = row.get("bytes")
            digest = row.get("sha256")
            if (not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_FILE_BYTES or
                    not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ReleaseError(f"source archive input manifest has invalid size or SHA-256 for {rel}")
            reason = row.get("reason")
            classification = row.get("distribution_class")
            flags = row.get("public_source_review_flags")
            if reason is not None and not isinstance(reason, str):
                raise ReleaseError(f"source archive input manifest has invalid reason for {rel}")
            if classification is not None and (not isinstance(classification, str) or
                                               classification not in SOURCE_DISTRIBUTION_CLASSES):
                raise ReleaseError(f"source archive input manifest has invalid distribution_class for {rel}")
            if flags is not None and (not isinstance(flags, list) or
                                      any(not isinstance(flag, str) or not flag.strip() for flag in flags)):
                raise ReleaseError(f"source archive input manifest has invalid public-source review flags for {rel}")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise ReleaseError("source archive input manifest exceeds the expanded-size limit")
            expected[rel] = row

        distribution_counts: dict[str, dict[str, int]] = {}
        for row in rows:
            classification = row.get("distribution_class")
            if classification is not None:
                summary = distribution_counts.setdefault(classification, {"file_count": 0, "bytes": 0})
                summary["file_count"] += 1
                summary["bytes"] += row["bytes"]
        if (receipt.get("archive_scope") != "LOCAL_RECOVERY_ARCHIVE" or
                receipt.get("public_source_release_authorized") is not False or
                receipt.get("distribution_scope_counts") != distribution_counts):
            raise ReleaseError("source archive input manifest has an invalid local-recovery scope summary")

        manifest_name = "RELEASE_INPUT_MANIFEST.json"
        expected_members = set(expected) | {manifest_name}
        if set(names) != expected_members:
            raise ReleaseError("source archive members do not exactly match the embedded input manifest")

        findings: list[dict[str, str]] = []
        verified_rows: list[dict[str, Any]] = []
        for info in infos:
            rel = safe_relative(info.filename)
            kind = scan_member_name(rel)
            if kind:
                raise ReleaseError(f"source archive contains prohibited member {rel}: {kind}")
            mode = info.external_attr >> 16
            file_type = stat_module.S_IFMT(mode)
            if file_type not in (0, stat_module.S_IFREG):
                raise ReleaseError(f"source archive contains a link or special file: {rel}")
            if info.flag_bits & 0x1:
                raise ReleaseError(f"source archive contains an encrypted member: {rel}")
            if info.file_size > MAX_FILE_BYTES:
                raise ReleaseError(f"source archive member exceeds size limit: {rel}")
            digest = hashlib.sha256()
            content = bytearray()
            with archive.open(info, "r") as member:
                for chunk in iter(lambda: member.read(1024 * 1024), b""):
                    digest.update(chunk)
                    content.extend(chunk)
            _inspect_bytes(rel, bytes(content), findings, 0)
            if rel == manifest_name and (digest.hexdigest() != manifest_sha256 or bytes(content) != manifest_bytes):
                raise ReleaseError("source archive input manifest changed during verification")
            if rel != manifest_name:
                row = expected[rel]
                if info.file_size != row["bytes"]:
                    raise ReleaseError(f"source archive member size differs from input manifest: {rel}")
                if digest.hexdigest() != row["sha256"]:
                    raise ReleaseError(f"source archive member SHA-256 differs from input manifest: {rel}")
                verified_rows.append({"path": rel, "bytes": info.file_size, "sha256": digest.hexdigest()})
        if findings:
            raise ReleaseError("source archive content scan failed: " + json.dumps(_dedupe_findings(findings), ensure_ascii=False))

        extraction_result = None
        if output_root is not None:
            output_root.parent.mkdir(parents=True, exist_ok=True)
            stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{output_root.name}.source-extract-", dir=output_root.parent))
            try:
                for info in infos:
                    rel = safe_relative(info.filename)
                    target = stage.joinpath(*pathlib.PurePosixPath(rel).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    written = 0
                    with archive.open(info, "r") as member, target.open("xb") as output:
                        for chunk in iter(lambda: member.read(1024 * 1024), b""):
                            output.write(chunk)
                            digest.update(chunk)
                            written += len(chunk)
                    if written != info.file_size:
                        raise ReleaseError(f"source archive member changed during extraction: {rel}")
                    if rel == manifest_name and digest.hexdigest() != manifest_sha256:
                        raise ReleaseError("source archive input manifest changed during extraction")
                    if rel != manifest_name and digest.hexdigest() != expected[rel]["sha256"]:
                        raise ReleaseError(f"source archive member SHA-256 changed during extraction: {rel}")
                extracted_audit = audit_tree(stage)
                if extracted_audit["status"] != "PASS":
                    raise ReleaseError("extracted source tree scan failed: " + json.dumps(extracted_audit["findings"], ensure_ascii=False))
                os.rename(stage, output_root)
                extraction_result = {"path": str(output_root), "status": "PASS", "file_count": extracted_audit["file_count"],
                                     "total_bytes": extracted_audit["total_bytes"], "findings": extracted_audit["findings"]}
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise

    if sha256_file(bundle) != archive_sha256:
        raise ReleaseError("source archive changed while it was being verified or extracted")
    status = "PASS_SOURCE_ARCHIVE_HASHED_AND_EXTRACTED" if extraction_result else "PASS_SOURCE_ARCHIVE_HASHED"
    return {"schema": SCHEMA, "status": status, "native_certified": False, "git_history_included": False,
            "archive": bundle.name, "archive_bytes": bundle.stat().st_size, "archive_sha256": archive_sha256,
            "manifest_sha256": manifest_sha256,
            "included_file_count": len(expected), "included_bytes": total,
            "distribution_scope_counts": distribution_counts,
            "archive_member_count": len(infos), "verified_files": verified_rows,
            "extraction": extraction_result}


def _state_scope_rows(scope_path: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"campaign state scope is missing or invalid: {exc}") from exc
    if not isinstance(scope, dict) or scope.get("schema") != STATE_SNAPSHOT_SCOPE_SCHEMA:
        raise ReleaseError("campaign state scope has an unknown schema")
    files = scope.get("files")
    pointers = scope.get("external_pointers", [])
    if not isinstance(files, list) or not files or not isinstance(pointers, list):
        raise ReleaseError("campaign state scope requires files and external_pointers lists")
    seen: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or row.get("distribution_class") != "LOCAL_RECOVERY_ONLY":
            raise ReleaseError("campaign state payloads must be explicitly classified LOCAL_RECOVERY_ONLY")
        rel = row.get("path")
        if not isinstance(rel, str):
            raise ReleaseError("campaign state scope file is missing its path")
        rel = safe_relative(rel)
        path = pathlib.PurePosixPath(rel)
        if (rel in seen or not rel.startswith("repository/docs/full_project_execution/") or
                path.suffix.lower() not in {".json", ".md"} or any(part.startswith("._") for part in path.parts)):
            raise ReleaseError(f"campaign state payload path is outside the narrow JSON/Markdown scope: {rel}")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ReleaseError(f"campaign state scope requires a reason for {rel}")
        seen.add(rel)
    if USER_SCOPE_DECISIONS_PATH not in seen:
        raise ReleaseError(
            f"campaign state scope must include {USER_SCOPE_DECISIONS_PATH}"
        )
    pointer_names: set[str] = set()
    for row in pointers:
        if not isinstance(row, dict) or row.get("included") is not False:
            raise ReleaseError("external campaign dependencies must be recorded as pointers only")
        name = row.get("name")
        if not isinstance(name, str) or not name.strip() or name in pointer_names:
            raise ReleaseError("external campaign pointers require unique names")
        pointer_names.add(name)
        availability = row.get("availability", "PRESENT")
        if availability == "PRESENT":
            rel = row.get("path")
            digest = row.get("sha256")
            size = row.get("bytes")
            if (not isinstance(rel, str) or not isinstance(digest, str) or
                    not re.fullmatch(r"[0-9a-f]{64}", digest) or
                    not isinstance(size, int) or isinstance(size, bool) or size < 0):
                raise ReleaseError(f"present external pointer {name!r} needs path, byte count, and SHA-256")
            row["path"] = safe_relative(rel)
        elif availability not in {"NOT_PRESENT_IN_CURRENT_INVENTORY", "NOT_YET_CREATED"}:
            raise ReleaseError(f"external pointer {name!r} has an unknown availability state")
    return scope, files, pointers


def _validate_user_scope_decisions_payload(payloads: dict[str, bytes]) -> None:
    """Require the explicit user-scope decisions to survive campaign recovery."""
    data = payloads.get(USER_SCOPE_DECISIONS_PATH)
    if data is None:
        raise ReleaseError(
            f"campaign state payload is missing {USER_SCOPE_DECISIONS_PATH}"
        )
    try:
        decision_record = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReleaseError("campaign user-scope decisions JSON is invalid") from exc
    decisions = decision_record.get("decisions") if isinstance(decision_record, dict) else None
    if (not isinstance(decisions, list) or not decisions or
            any(not isinstance(row, dict) for row in decisions)):
        raise ReleaseError("campaign user-scope decisions must contain a nonempty decisions list")


def _stable_read_under(root: pathlib.Path, rel: str) -> tuple[bytes, dict[str, int]]:
    """Read one regular file while rejecting symlinks and in-read mutation."""
    candidate = root
    for part in pathlib.PurePosixPath(rel).parts:
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise ReleaseError(f"campaign snapshot input is unavailable: {rel}: {exc}") from exc
        if stat_module.S_ISLNK(info.st_mode):
            raise ReleaseError(f"campaign snapshot input contains a symlink: {rel}")
    if not stat_module.S_ISREG(info.st_mode):
        raise ReleaseError(f"campaign snapshot input is not a regular file: {rel}")
    before = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    try:
        with candidate.open("rb") as stream:
            data = stream.read()
    except OSError as exc:
        raise ReleaseError(f"campaign snapshot input could not be read: {rel}: {exc}") from exc
    try:
        after_info = candidate.lstat()
    except OSError as exc:
        raise ReleaseError(f"campaign snapshot input changed while being read: {rel}: {exc}") from exc
    after = (after_info.st_dev, after_info.st_ino, after_info.st_size, after_info.st_mtime_ns)
    if (before != after or not stat_module.S_ISREG(after_info.st_mode) or len(data) != after_info.st_size):
        raise ReleaseError(f"campaign snapshot input changed while being read: {rel}")
    return data, {"bytes": len(data), "mtime_ns": after_info.st_mtime_ns}


def _campaign_path_strings(data: bytes) -> list[str]:
    text = data.decode("utf-8", "replace")
    patterns = (
        re.compile(r"(?<![A-Za-z0-9._:/\\-])/(?:Volumes|Users|private|Applications|Library|System|opt|usr)/[^\s\"'<>`,;)}\]]+"),
        re.compile(r"(?i)\b[A-Z]:[/\\][^\s\"'<>`,;)}\]]+"),
        re.compile(r"(?<![A-Za-z0-9._-])\\\\[^\s\"'<>`,;)}\]]+"),
    )
    candidates: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            candidate = match.group(0).rstrip(".,:")
            remainder = text[match.end():]
            if re.match(r"\s+[^\s/\\]+[/\\]", remainder):
                candidate += " [path contains spaces; inspect source context]"
            candidates.add(candidate)
    return sorted(candidates)


def _path_mapping_report(payloads: dict[str, bytes], mapping_path: pathlib.Path | None,
                          extract_root: pathlib.Path | None) -> dict[str, Any]:
    mappings: list[dict[str, str]] = []
    if mapping_path is not None:
        try:
            overlay = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseError(f"path remapping overlay is missing or invalid: {exc}") from exc
        if not isinstance(overlay, dict) or overlay.get("schema") != PATH_REMAP_SCHEMA:
            raise ReleaseError("path remapping overlay has an unknown schema")
        raw_rows = overlay.get("mappings")
        if not isinstance(raw_rows, list):
            raise ReleaseError("path remapping overlay requires a mappings list")
        seen: set[str] = set()
        for row in raw_rows:
            if not isinstance(row, dict):
                raise ReleaseError("path remapping entries must be objects")
            source = row.get("from_prefix")
            destination = row.get("to_prefix")
            if (not isinstance(source, str) or not isinstance(destination, str) or
                    not source.strip() or not destination.strip() or source in seen):
                raise ReleaseError("path remapping entries require unique nonempty prefixes")
            if not (source.startswith("/") or re.match(r"^[A-Za-z]:[/\\]", source) or source.startswith("\\\\")):
                raise ReleaseError(f"path remapping source must be absolute: {source!r}")
            if not (destination.startswith("/") or re.match(r"^[A-Za-z]:[/\\]", destination) or destination.startswith("\\\\")):
                raise ReleaseError(f"path remapping target must be absolute: {destination!r}")
            if extract_root is not None and destination.startswith("/"):
                target = pathlib.Path(destination).resolve(strict=True)
                root = extract_root.resolve(strict=True)
                if target != root and root not in target.parents:
                    raise ReleaseError("path remapping targets must stay within the fresh extraction directory")
            seen.add(source)
            mappings.append({"from_prefix": source.rstrip("/\\"), "to_prefix": destination.rstrip("/\\")})
    rows: list[dict[str, str]] = []
    for source_file, data in sorted(payloads.items()):
        for raw_path in _campaign_path_strings(data):
            match = next((row for row in sorted(mappings, key=lambda item: len(item["from_prefix"]), reverse=True)
                          if raw_path == row["from_prefix"] or raw_path.startswith(row["from_prefix"] + ("\\" if "\\" in row["from_prefix"] else "/"))), None)
            mapped = None
            if match is not None:
                mapped = match["to_prefix"] + raw_path[len(match["from_prefix"]):]
            rows.append({"source_file": source_file, "source_path": raw_path,
                         "mapping_status": "MAPPED_IN_DETACHED_OVERLAY" if mapped else "UNRESOLVED_REQUIRES_MANUAL_REVIEW",
                         **({"mapped_path": mapped} if mapped else {})})
    return {"schema": PATH_REMAP_SCHEMA, "mode": "READ_ONLY_DETACHED_OVERLAY",
            "payload_path_reference_count": len(rows),
            "mapped_count": sum(row["mapping_status"] == "MAPPED_IN_DETACHED_OVERLAY" for row in rows),
            "unresolved_count": sum(row["mapping_status"] != "MAPPED_IN_DETACHED_OVERLAY" for row in rows),
            "mappings": mappings, "references": rows,
            "source_bytes_rewritten": False}


def campaign_state_bundle(source_root: pathlib.Path, scope_path: pathlib.Path, out: pathlib.Path,
                          evidence: pathlib.Path, max_attempts: int = 3) -> dict[str, Any]:
    """Capture an exact local-only in-progress state snapshot after two stable full-group reads."""
    source_root = pathlib.Path(source_root).expanduser()
    if source_root.is_symlink():
        raise ReleaseError("campaign state source root must not be a symlink")
    source_root = source_root.resolve(strict=True)
    if not source_root.is_dir():
        raise ReleaseError("campaign state source root must be a directory")
    scope, file_rows, pointer_rows = _state_scope_rows(scope_path)
    out = pathlib.Path(out).expanduser().absolute()
    evidence = pathlib.Path(evidence).expanduser().absolute()
    if out.exists() or out.is_symlink():
        raise ReleaseError(f"campaign state archive destination already exists: {out}")
    if evidence.exists() and (not evidence.is_dir() or any(evidence.iterdir())):
        raise ReleaseError(f"campaign state evidence destination is not empty: {evidence}")
    evidence.mkdir(parents=True, exist_ok=True)

    last_error: str | None = None
    stable_payloads: dict[str, bytes] | None = None
    stable_rows: list[dict[str, Any]] = []
    pointer_capture: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        try:
            passes: list[dict[str, Any]] = []
            pass_payloads: list[dict[str, bytes]] = []
            pass_rows: list[list[dict[str, Any]]] = []
            pass_pointers: list[list[dict[str, Any]]] = []
            for pass_number in (1, 2):
                current_payloads: dict[str, bytes] = {}
                current_rows: list[dict[str, Any]] = []
                for row in file_rows:
                    rel = row["path"]
                    data, identity = _stable_read_under(source_root, rel)
                    if len(data) > 4 * 1024**2:
                        raise ReleaseError(f"campaign state file exceeds 4 MiB limit: {rel}")
                    findings: list[dict[str, str]] = []
                    _inspect_bytes(rel, data, findings)
                    if findings:
                        raise ReleaseError(f"campaign state content scan failed for {rel}: {json.dumps(findings, ensure_ascii=False)}")
                    current_payloads[rel] = data
                    current_rows.append({"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                         "distribution_class": row["distribution_class"], "reason": row["reason"],
                                         "mtime_ns": identity["mtime_ns"]})
                current_pointers: list[dict[str, Any]] = []
                for pointer in pointer_rows:
                    pointer_copy = dict(pointer)
                    if pointer.get("availability", "PRESENT") == "PRESENT":
                        data, identity = _stable_read_under(source_root, pointer["path"])
                        digest = hashlib.sha256(data).hexdigest()
                        if len(data) != pointer["bytes"] or digest != pointer["sha256"]:
                            raise ReleaseError(f"external pointer hash differs from reviewed scope: {pointer['name']}")
                        pointer_copy["observed_mtime_ns"] = identity["mtime_ns"]
                        pointer_copy["capture_sha256"] = digest
                    current_pointers.append(pointer_copy)
                passes.append({"pass": pass_number,
                               "payload": [{"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"],
                                            "mtime_ns": row["mtime_ns"]} for row in current_rows],
                               "external_pointers": [{"name": row["name"], "path": row.get("path"),
                                                      "sha256": row.get("capture_sha256"), "bytes": row.get("bytes"),
                                                      "observed_mtime_ns": row.get("observed_mtime_ns")} for row in current_pointers]})
                pass_payloads.append(current_payloads)
                pass_rows.append(current_rows)
                pass_pointers.append(current_pointers)
            stable = pass_rows[0] == pass_rows[1] and pass_pointers[0] == pass_pointers[1] and all(
                pass_payloads[0][path] == pass_payloads[1][path] for path in pass_payloads[0])
            attempts.append({"attempt": attempt, "stable": stable, "passes": passes})
            if stable:
                stable_payloads = pass_payloads[1]
                stable_rows = pass_rows[1]
                pointer_capture = pass_pointers[1]
                break
            last_error = "payload or external-pointer bytes changed between complete sampling passes"
        except ReleaseError as exc:
            last_error = str(exc)
            attempts.append({"attempt": attempt, "stable": False, "error": last_error})
        if attempt < max_attempts:
            time.sleep(0.25)
    if stable_payloads is None:
        raise ReleaseError(f"campaign snapshot could not obtain two stable full-group samples after {max_attempts} attempts: {last_error}")

    _validate_user_scope_decisions_payload(stable_payloads)

    source_manifest_archive = next((row for row in pointer_capture if row.get("role") == "SOURCE_RECOVERY_ARCHIVE"), None)
    resume_row = next((row for row in stable_rows if row["path"].endswith("/state/RESUME.json")), None)
    if resume_row is None:
        raise ReleaseError("campaign snapshot scope must include the authoritative state/RESUME.json")
    resume_payload = json.loads(stable_payloads[resume_row["path"]])
    if not isinstance(resume_payload, dict):
        raise ReleaseError("captured authoritative RESUME.json must contain an object")
    jobs = resume_payload.get("active_jobs", [])
    if not isinstance(jobs, list):
        raise ReleaseError("captured RESUME.json active_jobs must be a list")
    resume_preparation = "RECONCILE_REQUIRED_READ_ONLY" if jobs else "PREPARED_READ_ONLY"

    readme = (
        "# IN_PROGRESS_CAMPAIGN_SNAPSHOT\n\n"
        "This is a hash-verified local recovery snapshot of explicitly listed campaign state. "
        "It is not a final scientific delivery or acceptance.\n\n"
        "The archive preserves source files byte-for-byte. It does not include Git history, mutable databases, "
        "COMSOL MPH models, credentials, or the source/science dependencies listed as external pointers. "
        "It does not check process quiescence, reconnect jobs, rewrite paths, run bootstrap, launch tests, or start COMSOL.\n\n"
        "After extraction, verify hashes and create a detached path-remapping overlay. Do not use that overlay to "
        "rewrite captured files. Recorded `active_jobs: []` is not proof that host processes are absent. "
        "The external job store and live process ownership remain unverified; this blocks job reconnection or reassignment, "
        "not reading the hash-verified files. Never relaunch or reassign a job from this snapshot.\n\n"
        "Statuses are independent: snapshot integrity is established by the embedded manifest; resume preparation is read-only; "
        "`final_delivery_acceptance` remains `INCOMPLETE` until remaining model work and independent review are complete.\n"
    ).encode("utf-8")
    remap_template = {
        "schema": PATH_REMAP_SCHEMA,
        "mode": "READ_ONLY_DETACHED_OVERLAY",
        "note": "Fill only reviewed original-prefix to fresh-extraction-root mappings. This file never rewrites payload bytes.",
        "mappings": [],
    }
    remap_bytes = (json.dumps(remap_template, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    supporting = [
        {"path": "CAMPAIGN_STATE_SNAPSHOT_README.md", "bytes": len(readme), "sha256": hashlib.sha256(readme).hexdigest()},
        {"path": "PATH_REMAP_TEMPLATE.json", "bytes": len(remap_bytes), "sha256": hashlib.sha256(remap_bytes).hexdigest()},
    ]
    manifest = {
        "schema": STATE_SNAPSHOT_SCHEMA,
        "kind": STATE_SNAPSHOT_KIND,
        "created_utc": utc_now(),
        "snapshot_integrity": "DOUBLE_SAMPLED_LOCAL_ONLY_PAYLOAD",
        "resume_preparation": resume_preparation,
        "final_delivery_acceptance": "INCOMPLETE",
        "host_process_quiescence": "UNVERIFIED",
        "external_job_store": "NOT_INCLUDED_AND_NOT_VERIFIED",
        "job_reconnection_or_reassignment": "BLOCKED_PENDING_EXTERNAL_STORE_AND_LIVE_PROCESS_RECONCILIATION",
        "job_relaunch_or_reassignment_permitted": False,
        "unfinished_activity_recoverability": {
            "state_and_todo_files_preserved": True,
            "pending_work": ["W23", "W24", "independent final review"],
            "note": "Pending work is recoverable from the captured state and plans; completion artifacts and acceptance remain pending.",
        },
        "recorded_campaign_state": {
            "status": resume_payload.get("status", "UNKNOWN"),
            "phase": resume_payload.get("phase"),
            "task": resume_payload.get("task"),
            "recorded_active_jobs_count": len(jobs),
            "recorded_active_jobs": jobs,
            "main_context_id_present": bool(resume_payload.get("main_context_id")),
            "reviewer_context_id_present": bool(resume_payload.get("reviewer_context_id")),
        },
        "capture": {"method": "two complete hash-and-byte samples of all payload and present pointers",
                    "max_attempts": max_attempts, "successful_attempt": attempts[-1]["attempt"],
                    "attempt_log": attempts},
        "included_files": stable_rows,
        "supporting_files": supporting,
        "external_pointers": pointer_capture,
        "source_archive_pointer": source_manifest_archive,
        "contract_policy": "Original contract files are hash-checked in the referenced source archive; not duplicated here.",
        "science_policy": "Scientific MPH artifacts are pointers only; W23/W24 final artifacts remain pending.",
        "review_policy": "Current reviewer state and intake are preserved as unfinished; reviewer work is not fabricated.",
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    temp_out = out.with_name(f".{out.name}.capture-{os.getpid()}.tmp")
    if temp_out.exists():
        raise ReleaseError(f"temporary campaign archive path already exists: {temp_out}")
    try:
        with zipfile.ZipFile(temp_out, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            entries = {**stable_payloads,
                       "CAMPAIGN_STATE_SNAPSHOT_MANIFEST.json": manifest_bytes,
                       "CAMPAIGN_STATE_SNAPSHOT_README.md": readme,
                       "PATH_REMAP_TEMPLATE.json": remap_bytes}
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat_module.S_IFREG | 0o644) << 16
                archive.writestr(info, data)
        if out.exists():
            raise ReleaseError(f"campaign state archive destination appeared during creation: {out}")
        os.rename(temp_out, out)
    except Exception:
        temp_out.unlink(missing_ok=True)
        raise
    result = verify_campaign_state_bundle(out)
    receipt = {
        "schema": STATE_SNAPSHOT_SCHEMA, "kind": STATE_SNAPSHOT_KIND,
        "status": result["status"], "archive": out.name, "archive_bytes": out.stat().st_size,
        "archive_sha256": sha256_file(out), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "payload_file_count": len(stable_rows), "payload_bytes": sum(row["bytes"] for row in stable_rows),
        "release_helper_sha256": sha256_file(pathlib.Path(__file__).resolve()),
        "manifest_capture_attempts": attempts,
        "scope_manifest_sha256": sha256_file(scope_path),
        "result": result,
    }
    _json_write(evidence / "campaign-state-capture-log.json", {
        "schema": STATE_SNAPSHOT_SCHEMA, "attempts": attempts, "all_payloads_stable": True,
        "source_root_label": source_root.name, "scope_manifest_sha256": receipt["scope_manifest_sha256"]})
    _json_write(evidence / "campaign-state-bundle-receipt.json", receipt)
    return receipt


def verify_campaign_state_bundle(bundle: pathlib.Path, extract_to: pathlib.Path | None = None,
                                 mapping_path: pathlib.Path | None = None) -> dict[str, Any]:
    """Verify and optionally extract/map a state capsule without executing or rewriting it."""
    bundle = pathlib.Path(bundle).expanduser()
    if bundle.is_symlink():
        raise ReleaseError("campaign state archive must be a regular, non-symlink file")
    bundle = bundle.resolve(strict=True)
    if not bundle.is_file():
        raise ReleaseError("campaign state archive must be a regular file")
    archive_hash_before = sha256_file(bundle)
    extraction_root: pathlib.Path | None = None
    if extract_to is not None:
        extraction_root = pathlib.Path(extract_to).expanduser().absolute()
        if extraction_root.exists() or extraction_root.is_symlink():
            raise ReleaseError(f"campaign state extraction destination already exists: {extraction_root}")
    try:
        archive = zipfile.ZipFile(bundle, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseError(f"invalid campaign state archive: {exc}") from exc
    manifest_name = "CAMPAIGN_STATE_SNAPSHOT_MANIFEST.json"
    readme_name = "CAMPAIGN_STATE_SNAPSHOT_README.md"
    remap_template_name = "PATH_REMAP_TEMPLATE.json"
    with archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)) or any(item.is_dir() for item in infos):
            raise ReleaseError("campaign state archive contains duplicate names or directory entries")
        if manifest_name not in names:
            raise ReleaseError("campaign state archive is missing its manifest")
        try:
            manifest_bytes = archive.read(manifest_name)
            manifest = json.loads(manifest_bytes)
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as exc:
            raise ReleaseError("campaign state archive manifest is invalid") from exc
        if (not isinstance(manifest, dict) or manifest.get("schema") != STATE_SNAPSHOT_SCHEMA or
                manifest.get("kind") != STATE_SNAPSHOT_KIND or manifest.get("final_delivery_acceptance") != "INCOMPLETE" or
                manifest.get("host_process_quiescence") != "UNVERIFIED" or
                manifest.get("job_relaunch_or_reassignment_permitted") is not False):
            raise ReleaseError("campaign state archive has an unknown or unsafe acceptance contract")
        rows = manifest.get("included_files")
        supporting = manifest.get("supporting_files")
        pointers = manifest.get("external_pointers")
        if not isinstance(rows, list) or not rows or not isinstance(supporting, list) or not isinstance(pointers, list):
            raise ReleaseError("campaign state manifest has invalid file or pointer lists")
        expected: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("distribution_class") != "LOCAL_RECOVERY_ONLY":
                raise ReleaseError("campaign state manifest payloads must be LOCAL_RECOVERY_ONLY")
            rel = row.get("path")
            if not isinstance(rel, str):
                raise ReleaseError("campaign state manifest entry has no path")
            rel = safe_relative(rel)
            if (rel in expected or not rel.startswith("repository/docs/full_project_execution/") or
                    pathlib.PurePosixPath(rel).suffix.lower() not in {".json", ".md"} or
                    any(part.startswith("._") for part in pathlib.PurePosixPath(rel).parts)):
                raise ReleaseError(f"campaign state manifest contains a duplicate or out-of-scope path: {rel}")
            size, digest = row.get("bytes"), row.get("sha256")
            if (not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > 4 * 1024**2 or
                    not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ReleaseError(f"campaign state manifest has invalid size or SHA-256 for {rel}")
            if not isinstance(row.get("reason"), str) or not row["reason"].strip():
                raise ReleaseError(f"campaign state manifest entry has no local-recovery reason: {rel}")
            expected[rel] = row
        if USER_SCOPE_DECISIONS_PATH not in expected:
            raise ReleaseError(
                f"campaign state manifest must include {USER_SCOPE_DECISIONS_PATH}"
            )
        supporting_names: set[str] = set()
        for row in supporting:
            if not isinstance(row, dict) or row.get("path") not in {readme_name, remap_template_name}:
                raise ReleaseError("campaign state archive has an unknown supporting file")
            rel = row["path"]
            if rel in expected or rel in supporting_names:
                raise ReleaseError("campaign state archive duplicates a supporting file")
            supporting_names.add(rel)
            size, digest = row.get("bytes"), row.get("sha256")
            if (not isinstance(size, int) or isinstance(size, bool) or size < 0 or
                    not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ReleaseError(f"campaign state supporting file has invalid metadata: {rel}")
            expected[rel] = row
        if supporting_names != {readme_name, remap_template_name}:
            raise ReleaseError("campaign state archive must include its README and path-remap template")
        if set(names) != set(expected) | {manifest_name}:
            raise ReleaseError("campaign state archive members do not exactly match its manifest")
        capture = manifest.get("capture")
        if not isinstance(capture, dict) or capture.get("method") != "two complete hash-and-byte samples of all payload and present pointers":
            raise ReleaseError("campaign state manifest does not attest the required double-sample method")
        attempt_log = capture.get("attempt_log")
        if not isinstance(attempt_log, list) or not attempt_log:
            raise ReleaseError("campaign state manifest is missing its double-sample attempt log")
        successful_attempt = capture.get("successful_attempt")
        successful = next((item for item in attempt_log if isinstance(item, dict) and item.get("attempt") == successful_attempt), None)
        if (successful is None or successful.get("stable") is not True or
                not isinstance(successful.get("passes"), list) or len(successful["passes"]) != 2):
            raise ReleaseError("campaign state manifest has no successful pair of full-group samples")
        expected_samples = [{"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"],
                             "mtime_ns": row.get("mtime_ns")} for row in rows]
        for sample_pass in successful["passes"]:
            sampled = sample_pass.get("payload") if isinstance(sample_pass, dict) else None
            if sampled != expected_samples:
                raise ReleaseError("campaign state double-sample log does not exactly bind all payload files")
        if not isinstance(pointers, list) or any(not isinstance(row, dict) or row.get("included") is not False for row in pointers):
            raise ReleaseError("campaign state external dependencies must remain pointers only")
        pointer_samples = [sample_pass.get("external_pointers") for sample_pass in successful["passes"]]
        if len(pointer_samples) != 2 or pointer_samples[0] != pointer_samples[1]:
            raise ReleaseError("campaign state external pointers were not stable across both sample passes")
        for pointer in pointers:
            if pointer.get("availability") == "PRESENT":
                if (not isinstance(pointer.get("sha256"), str) or
                        not re.fullmatch(r"[0-9a-f]{64}", pointer["sha256"]) or
                        pointer.get("capture_sha256") != pointer.get("sha256") or
                        not isinstance(pointer.get("bytes"), int) or isinstance(pointer.get("bytes"), bool) or
                        pointer.get("bytes") < 0 or not isinstance(pointer.get("path"), str)):
                    raise ReleaseError(f"present external pointer is not hash-bound: {pointer.get('name')}")
            elif pointer.get("availability") not in {"NOT_PRESENT_IN_CURRENT_INVENTORY", "NOT_YET_CREATED"}:
                raise ReleaseError(f"external pointer has unknown availability: {pointer.get('name')}")
        first_pointer_samples = pointer_samples[0]
        if not isinstance(first_pointer_samples, list) or len(first_pointer_samples) != len(pointers):
            raise ReleaseError("campaign state double-sample log does not cover every external pointer")
        for pointer in pointers:
            sampled_pointer = next((item for item in first_pointer_samples
                                    if isinstance(item, dict) and item.get("name") == pointer.get("name")), None)
            if sampled_pointer is None or sampled_pointer.get("path") != pointer.get("path"):
                raise ReleaseError(f"campaign state pointer sample does not bind {pointer.get('name')}")
            if pointer.get("availability") == "PRESENT":
                if (sampled_pointer.get("bytes") != pointer.get("bytes") or
                        sampled_pointer.get("sha256") != pointer.get("capture_sha256")):
                    raise ReleaseError(f"campaign state pointer samples differ from manifest: {pointer.get('name')}")
            elif sampled_pointer.get("sha256") is not None or sampled_pointer.get("bytes") is not None:
                raise ReleaseError(f"missing external pointer unexpectedly has file data: {pointer.get('name')}")
        payloads: dict[str, bytes] = {}
        total = 0
        verified: list[dict[str, Any]] = []
        for info in infos:
            rel = safe_relative(info.filename)
            if rel == manifest_name:
                mode = stat_module.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat_module.S_IFREG) or info.flag_bits & 0x1 or info.file_size > 2 * 1024**2:
                    raise ReleaseError("campaign state manifest member is encrypted, oversized, or not a regular file")
                if hashlib.sha256(archive.read(info)).hexdigest() != hashlib.sha256(manifest_bytes).hexdigest():
                    raise ReleaseError("campaign state manifest changed during verification")
                continue
            kind = scan_member_name(rel)
            if kind:
                raise ReleaseError(f"campaign state archive member is prohibited: {rel}: {kind}")
            if info.file_size > 4 * 1024**2 or info.flag_bits & 0x1:
                raise ReleaseError(f"campaign state archive member exceeds bounds or is encrypted: {rel}")
            mode = stat_module.S_IFMT(info.external_attr >> 16)
            if mode not in (0, stat_module.S_IFREG):
                raise ReleaseError(f"campaign state archive contains a link or special file: {rel}")
            data = archive.read(info)
            row = expected[rel]
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != row["bytes"] or digest != row["sha256"]:
                raise ReleaseError(f"campaign state archive member differs from its manifest: {rel}")
            findings: list[dict[str, str]] = []
            _inspect_bytes(rel, data, findings)
            if findings:
                raise ReleaseError(f"campaign state archive content scan failed: {json.dumps(findings, ensure_ascii=False)}")
            if row.get("distribution_class") == "LOCAL_RECOVERY_ONLY":
                payloads[rel] = data
                total += len(data)
            verified.append({"path": rel, "bytes": len(data), "sha256": digest})
        if total > 64 * 1024**2:
            raise ReleaseError("campaign state archive exceeds its expanded payload limit")
        _validate_user_scope_decisions_payload(payloads)
        resume_row = next((row for row in rows if row["path"].endswith("/state/RESUME.json")), None)
        if resume_row is None:
            raise ReleaseError("campaign state archive must include authoritative state/RESUME.json")
        try:
            resume = json.loads(payloads[resume_row["path"]])
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReleaseError("campaign state authoritative RESUME.json is invalid") from exc
        active_jobs = resume.get("active_jobs", []) if isinstance(resume, dict) else None
        if not isinstance(active_jobs, list):
            raise ReleaseError("campaign state authoritative RESUME.json active_jobs is not a list")
        resume_preparation = "RECONCILE_REQUIRED_READ_ONLY" if active_jobs else "PREPARED_READ_ONLY"
        if manifest.get("resume_preparation") != resume_preparation:
            raise ReleaseError("campaign state resume-preparation summary differs from captured active jobs")
        manifest_state = manifest.get("recorded_campaign_state", {})
        if (manifest_state.get("recorded_active_jobs_count") != len(active_jobs) or
                manifest_state.get("recorded_active_jobs") != active_jobs):
            raise ReleaseError("campaign state manifest active-job summary differs from captured RESUME.json")
        if (manifest.get("external_job_store") != "NOT_INCLUDED_AND_NOT_VERIFIED" or
                manifest.get("job_reconnection_or_reassignment") !=
                "BLOCKED_PENDING_EXTERNAL_STORE_AND_LIVE_PROCESS_RECONCILIATION" or
                manifest.get("job_relaunch_or_reassignment_permitted") is not False):
            raise ReleaseError("campaign state job-recovery boundary is missing or unsafe")
        extraction = None
        mapping_report = None
        if extraction_root is not None:
            extraction_root.parent.mkdir(parents=True, exist_ok=True)
            stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{extraction_root.name}.state-extract-", dir=extraction_root.parent))
            try:
                for rel, data in {**payloads,
                                  manifest_name: manifest_bytes,
                                  readme_name: archive.read(readme_name),
                                  remap_template_name: archive.read(remap_template_name)}.items():
                    target = stage.joinpath(*pathlib.PurePosixPath(rel).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(data)
                os.rename(stage, extraction_root)
                extraction = {"path": str(extraction_root), "status": "PASS_EXTRACTED_READ_ONLY",
                              "payload_file_count": len(payloads), "payload_bytes": total}
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise
            mapping_report = _path_mapping_report(payloads, mapping_path, extraction_root)
        elif mapping_path is not None:
            mapping_report = _path_mapping_report(payloads, mapping_path, None)
        if sha256_file(bundle) != archive_hash_before:
            raise ReleaseError("campaign state archive changed during verification")
        return {
            "schema": STATE_SNAPSHOT_SCHEMA,
            "status": "PASS_CAMPAIGN_STATE_ARCHIVE_HASHED",
            "snapshot_integrity": "PASS",
            "snapshot_kind": STATE_SNAPSHOT_KIND,
            "archive": bundle.name,
            "archive_bytes": bundle.stat().st_size,
            "archive_sha256": archive_hash_before,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "payload_file_count": len(rows), "payload_bytes": total,
            "archive_member_count": len(infos), "verified_files": verified,
            "resume_preparation": resume_preparation,
            "recorded_active_jobs_count": len(active_jobs),
            "host_process_quiescence": "UNVERIFIED",
            "external_job_store": "NOT_INCLUDED_AND_NOT_VERIFIED",
            "job_reconnection_or_reassignment": "BLOCKED_PENDING_EXTERNAL_STORE_AND_LIVE_PROCESS_RECONCILIATION",
            "job_relaunch_or_reassignment_permitted": False,
            "unfinished_activity_recoverability": manifest.get("unfinished_activity_recoverability"),
            "final_delivery_acceptance": "INCOMPLETE",
            "external_pointers": [{key: value for key, value in row.items() if key not in {"observed_mtime_ns"}}
                                   for row in pointers],
            "extraction": extraction,
            "path_mapping": mapping_report,
            "execution_performed": False,
            "release_helper_sha256": sha256_file(pathlib.Path(__file__).resolve()),
        }


def build_wheel(source: pathlib.Path, out: pathlib.Path, python: pathlib.Path,
                evidence: pathlib.Path) -> dict[str, Any]:
    if out.exists() and any(out.iterdir()):
        raise ReleaseError(f"output directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    if any((evidence / name).exists() for name in ("wheel-build-log.json", "wheel-build-receipt.json", "source-snapshot-manifest.json")):
        raise ReleaseError(f"wheel build evidence already exists: {evidence}")
    with tempfile.TemporaryDirectory(prefix="comsol-release-src-") as temp:
        stage = pathlib.Path(temp) / "source"
        src = source_snapshot(source, stage)
        _json_write(evidence / "source-snapshot-manifest.json", {
            "schema": SCHEMA, "kind": "IMMUTABLE_RUNTIME_SOURCE_SNAPSHOT", "created_utc": utc_now(),
            "source_root_label": source.resolve().name, "file_count": src["file_count"],
            "manifest_sha256": src["manifest_sha256"], "files": src["files"],
        })
        check = subprocess.run([str(python), "-c", "import setuptools, wheel; print(setuptools.__version__, wheel.__version__)"],
                               capture_output=True, text=True)
        if check.returncode:
            raise ReleaseError("builder Python needs setuptools and wheel installed; use a separate build environment. " + check.stderr[-1000:])
        proc = subprocess.run([str(python), "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--disable-pip-version-check", "--wheel-dir", str(out), str(stage)],
                              capture_output=True, text=True)
        log = {"command": "<builder-python> -m pip wheel --no-deps --no-build-isolation --wheel-dir <out> <sanitized-source-snapshot>",
               "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        _json_write(evidence / "wheel-build-log.json", log)
        if proc.returncode:
            raise ReleaseError("wheel build failed; see wheel-build-log.json")
        audit = audit_tree(out)
        if audit["status"] != "PASS":
            raise ReleaseError("built wheel audit failed: " + json.dumps(audit["findings"], ensure_ascii=False))
        wheels = sorted(path for path in out.glob("*.whl") if not path.name.startswith("._"))
        if len(wheels) != 1:
            raise ReleaseError(f"expected exactly one application wheel, found {len(wheels)}")
        result = {"schema": SCHEMA, "status": "WHEEL_BUILT_NATIVE_UNVERIFIED", "created_utc": utc_now(),
                  "wheel": wheels[0].name, "bytes": wheels[0].stat().st_size, "sha256": sha256_file(wheels[0]),
                  "source_manifest_sha256": src["manifest_sha256"], "source_file_count": src["file_count"],
                  "wheel_metadata": {k: wheel_metadata(wheels[0])[k] for k in ("name", "version", "license", "wheel_tags", "member_count")},
                  "audit": {"status": audit["status"], "file_count": audit["file_count"], "findings": audit["findings"]},
                  "native_execution": "NOT_RUN", "platform_matrix": "UNVERIFIED"}
        _json_write(evidence / "wheel-build-receipt.json", result)
        return result


def run_record(command: list[str], *, cwd: pathlib.Path | None = None) -> dict[str, Any]:
    start = time.monotonic()
    proc = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    return {"argv": command, "cwd": str(cwd) if cwd else None,
            "exit_code": proc.returncode, "stdout": proc.stdout,
            "stderr": proc.stderr, "duration_seconds": round(time.monotonic() - start, 3)}


def _canonical_install_root(path: pathlib.Path) -> pathlib.Path:
    raw = pathlib.Path(os.path.abspath(os.fspath(path)))
    if raw.is_symlink():
        raise ReleaseError("install root itself must not be a symlink")
    root = raw.resolve(strict=False)
    filesystem_root = pathlib.Path(root.anchor)
    home = pathlib.Path.home().resolve(strict=False)
    source = pathlib.Path(__file__).resolve().parents[1]
    if root == filesystem_root or root in home.parents or root == home:
        raise ReleaseError("refusing an install root at or above the filesystem/home boundary")
    if root == source or source in root.parents or root in source.parents:
        raise ReleaseError("refusing an install root inside, equal to, or containing the source tree")
    return root


def _install_marker_path(root: pathlib.Path) -> pathlib.Path:
    return root / INSTALL_MARKER_NAME


def _load_install_marker(root: pathlib.Path) -> dict[str, Any]:
    marker_path = _install_marker_path(root)
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ReleaseError("isolated install marker is missing or is not a regular file")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("isolated install marker is unreadable or malformed") from exc
    if not isinstance(marker, dict):
        raise ReleaseError("isolated install marker must be a JSON object")
    if marker.get("schema") != INSTALL_MARKER_SCHEMA or marker.get("project_id") != INSTALL_PROJECT_ID:
        raise ReleaseError("isolated install marker does not identify this project")
    if marker.get("install_root") != str(root):
        raise ReleaseError("isolated install marker canonical path does not match the requested root")
    if marker.get("state") not in {"INITIALIZING", "READY"}:
        raise ReleaseError("isolated install marker has an unsupported state")
    try:
        uuid.UUID(str(marker.get("install_id", "")))
    except (ValueError, AttributeError) as exc:
        raise ReleaseError("isolated install marker has an invalid installation identity") from exc
    if not isinstance(marker.get("created_utc"), str) or not marker["created_utc"].strip():
        raise ReleaseError("isolated install marker has no creation timestamp")
    return marker


def _write_install_marker(root: pathlib.Path, *, state: str, install_id: str | None = None) -> dict[str, Any]:
    marker_path = _install_marker_path(root)
    if state == "INITIALIZING":
        marker = {
            "schema": INSTALL_MARKER_SCHEMA,
            "project_id": INSTALL_PROJECT_ID,
            "install_id": install_id or str(uuid.uuid4()),
            "install_root": str(root),
            "state": state,
            "created_utc": utc_now(),
        }
        _json_write(marker_path, marker)
    else:
        # Replace only the marker we just validated; a unique temporary name
        # prevents a partially written marker from being treated as authority.
        previous = _load_install_marker(root)
        marker = dict(previous)
        marker["state"] = state
        marker["ready_utc"] = utc_now()
        if install_id and install_id != previous["install_id"]:
            raise ReleaseError("cannot change the isolated install identity")
        temporary = root / f"{INSTALL_MARKER_NAME}.{uuid.uuid4().hex}.tmp"
        try:
            _json_write(temporary, marker)
            os.replace(temporary, marker_path)
        finally:
            temporary.unlink(missing_ok=True)
    return marker


def _installation_inventory(root: pathlib.Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    protected_suffixes = {".mph", ".mphbin", ".mphdata", ".mphbackup", ".mphbak", ".mphx", ".mphtxt"}
    protected_count = 0
    def fail_walk(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(root, followlinks=False, onerror=fail_walk):
        current_path = pathlib.Path(current)
        kept_directories: list[str] = []
        for name in sorted(directories):
            child = current_path / name
            rel = child.relative_to(root).as_posix()
            if child.is_symlink():
                rows.append({"path": rel, "type": "directory_symlink", "target": os.readlink(child)})
                continue
            if child.suffix.lower() in protected_suffixes:
                protected_count += 1
            if "__pycache__" not in child.parts:
                rows.append({"path": rel, "type": "directory"})
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(files):
            path = current_path / name
            rel_path = path.relative_to(root)
            if rel_path.as_posix() == INSTALL_MARKER_NAME:
                continue
            if path.suffix.lower() in protected_suffixes:
                protected_count += 1
            if "__pycache__" in rel_path.parts and path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                rows.append({"path": rel_path.as_posix(), "type": "file_symlink", "target": os.readlink(path)})
            elif path.is_file():
                rows.append({"path": rel_path.as_posix(), "type": "file", "bytes": path.stat(follow_symlinks=False).st_size,
                             "sha256": sha256_file(path)})
            else:
                raise ReleaseError("install tree contains a non-regular file")
    rows.sort(key=lambda row: (row["path"], row["type"]))
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return rows, protected_count


def _record_install_inventory(venv_python: pathlib.Path) -> dict[str, Any]:
    root = venv_python.absolute().parent.parent
    rows, protected_count = _installation_inventory(root)
    if protected_count:
        raise ReleaseError("install tree contains model-like artifacts; refusing to record them as owned package contents")
    current = _load_install_marker(root)
    current["owned_inventory"] = {
        "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "entry_count": len(rows),
        "protected_artifact_count_at_capture": protected_count,
    }
    temporary = root / f"{INSTALL_MARKER_NAME}.{uuid.uuid4().hex}.tmp"
    try:
        _json_write(temporary, current)
        os.replace(temporary, _install_marker_path(root))
    finally:
        temporary.unlink(missing_ok=True)
    return current["owned_inventory"]


def _content_guard(root: pathlib.Path, marker: dict[str, Any]) -> dict[str, Any]:
    expected = marker.get("owned_inventory")
    if not isinstance(expected, dict) or not isinstance(expected.get("sha256"), str) or not isinstance(expected.get("entry_count"), int):
        return {"status": "UNKNOWN", "reason": "install marker has no usable owned-content baseline",
                "expected_entry_count": None, "current_entry_count": None, "protected_artifact_count": None}
    try:
        rows, protected_count = _installation_inventory(root)
    except (OSError, ReleaseError):
        return {"status": "UNKNOWN", "reason": "install content inventory failed", "expected_entry_count": expected["entry_count"],
                "current_entry_count": None, "protected_artifact_count": None}
    actual_digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if protected_count:
        status, reason = "BLOCKED_PROTECTED_ARTIFACT", "COMSOL model-like artifacts are present in the install tree"
    elif actual_digest != expected["sha256"] or len(rows) != expected["entry_count"]:
        status, reason = "BLOCKED_CONTENT_DRIFT", "install contents differ from the helper-recorded ownership baseline"
    else:
        status, reason = "CLEAR", "install contents match the helper-recorded ownership baseline"
    return {"status": status, "reason": reason, "expected_entry_count": expected["entry_count"],
            "current_entry_count": len(rows), "expected_sha256": expected["sha256"],
            "current_sha256": actual_digest, "protected_artifact_count": protected_count}


def _mark_install_ready(venv_python: pathlib.Path) -> None:
    root = venv_python.absolute().parent.parent
    _record_install_inventory(venv_python)
    marker = _load_install_marker(root)
    _write_install_marker(root, state="READY", install_id=marker["install_id"])


def create_venv(python: pathlib.Path, venv: pathlib.Path) -> pathlib.Path:
    root = _canonical_install_root(venv)
    if root.exists():
        raise ReleaseError(f"installation target already exists; refusing to overwrite: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    record = run_record([str(python), "-m", "venv", str(root)])
    if record["exit_code"]:
        raise ReleaseError("venv creation failed: " + record["stderr"][-1000:])
    candidate = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise ReleaseError("created venv has no Python interpreter")
    _write_install_marker(root, state="INITIALIZING")
    _record_install_inventory(candidate)
    return candidate


def _process_path_reference(root_text: str, value: str | None) -> bool:
    if not value:
        return False
    text = os.path.normcase(value).replace("\\", "/")
    start = 0
    while (index := text.find(root_text, start)) >= 0:
        end = index + len(root_text)
        if end == len(text) or text[end] in "/\"' \t,:;)":
            return True
        start = end
    return False


def _windows_process_rows_guard(rows: Any, root_text: str, current_pid: int) -> dict[str, Any]:
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        return {"status": "UNKNOWN", "reason": "PowerShell process inventory had an unexpected shape", "pids": []}
    by_pid: dict[int, dict[str, Any]] = {}
    uncertain = False
    self_seen = False
    for row in rows:
        if not isinstance(row, dict):
            uncertain = True
            continue
        try:
            pid_value = int(row.get("ProcessId"))
            parent_value = int(row.get("ParentProcessId"))
        except (TypeError, ValueError):
            uncertain = True
            continue
        if pid_value in by_pid:
            uncertain = True
            continue
        name_value = row.get("Name")
        if not isinstance(name_value, str) or not name_value.strip():
            uncertain = True
            continue
        row["_pid"] = pid_value
        row["_ppid"] = parent_value
        by_pid[pid_value] = row
        if pid_value == current_pid:
            self_seen = True
    if not self_seen:
        return {"status": "UNKNOWN", "reason": "process inventory did not include this process", "pids": []}
    ancestors: set[int] = set()
    parent = by_pid.get(current_pid, {}).get("_ppid")
    while isinstance(parent, int) and parent not in ancestors and parent in by_pid:
        ancestors.add(parent)
        parent = by_pid[parent].get("_ppid")
    busy: list[int] = []
    shell_names = {"powershell.exe", "pwsh.exe", "cmd.exe"}
    for row in by_pid.values():
        pid_value = row["_pid"]
        if pid_value == current_pid:
            continue
        name = str(row.get("Name") or "").lower()
        executable = row.get("ExecutablePath")
        command = row.get("CommandLine")
        if (executable is not None and not isinstance(executable, str)) or (
                command is not None and not isinstance(command, str)):
            uncertain = True
            continue
        matches = (_process_path_reference(root_text, str(executable) if executable else None) or
                   _process_path_reference(root_text, str(command) if command else None))
        if matches:
            if pid_value in ancestors and name in shell_names:
                continue
            busy.append(pid_value)
            continue
        # An incomplete row cannot prove that the target path is absent.
        exact_kernel_identity = ((pid_value == 0 and name == "system idle process") or
                                 (pid_value == 4 and name == "system"))
        if (not executable or not command) and not exact_kernel_identity:
            uncertain = True
    if busy:
        return {"status": "BUSY", "reason": "a process references the install root", "pids": sorted(set(busy))}
    if uncertain:
        return {"status": "UNKNOWN", "reason": "a process inventory row could not be identified completely", "pids": []}
    return {"status": "CLEAR", "reason": "all process rows were complete and no process references the install root", "pids": []}


def _process_guard(root: pathlib.Path) -> dict[str, Any]:
    """Fail closed if a process may still be using the isolated interpreter."""
    root_text = os.path.normcase(str(root)).replace("\\", "/")
    if pathlib.Path(sys.prefix).resolve(strict=False) == root:
        return {"status": "BUSY", "reason": "release command is running from the install being removed", "pids": [os.getpid()]}

    if os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            return {"status": "UNKNOWN", "reason": "PowerShell process inventory is unavailable", "pids": []}
        script = (
            "$rows = @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine); "
            "ConvertTo-Json -InputObject $rows -Compress"
        )
        try:
            proc = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return {"status": "UNKNOWN", "reason": "PowerShell process inventory failed or timed out", "pids": []}
        if proc.returncode != 0 or proc.stderr.strip():
            return {"status": "UNKNOWN", "reason": "PowerShell process inventory returned an error", "pids": []}
        try:
            rows = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"status": "UNKNOWN", "reason": "PowerShell process inventory was not valid JSON", "pids": []}
        return _windows_process_rows_guard(rows, root_text, os.getpid())

    try:
        proc = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "UNKNOWN", "reason": "ps process inventory failed or timed out", "pids": []}
    if proc.returncode != 0 or proc.stderr.strip():
        return {"status": "UNKNOWN", "reason": "ps process inventory returned an error", "pids": []}
    busy: list[int] = []
    uncertain = False
    parsed: list[tuple[int, int, str]] = []
    self_seen = False
    interpreter = re.compile(r"^python(?:w)?(?:\d+(?:\.\d+)*)?$", re.IGNORECASE)
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)$", line)
        if not match:
            uncertain = True
            continue
        pid = int(match.group(1))
        ppid = int(match.group(2))
        command = match.group(3).strip()
        if not command:
            uncertain = True
            continue
        parsed.append((pid, ppid, command))
        if pid == os.getpid():
            self_seen = True
    if not self_seen:
        return {"status": "UNKNOWN", "reason": "ps inventory did not include this process", "pids": []}
    pid_map = {pid: (ppid, command) for pid, ppid, command in parsed}
    ancestors: set[int] = set()
    parent = pid_map.get(os.getpid(), (None, ""))[0]
    while isinstance(parent, int) and parent not in ancestors and parent in pid_map:
        ancestors.add(parent)
        parent = pid_map[parent][0]
    shell_names = {"sh", "bash", "zsh", "dash", "fish", "powershell", "pwsh", "cmd"}
    for pid, _ppid, command in parsed:
        if pid == os.getpid():
            continue
        first = command.split(maxsplit=1)[0].strip("\"'") if command else ""
        first_name = pathlib.PurePosixPath(first.replace("\\", "/")).name.lower()
        if _process_path_reference(root_text, command):
            if pid in ancestors and first_name in shell_names:
                continue
            busy.append(pid)
            continue
        candidate = bool(interpreter.fullmatch(first_name) or first_name.lower() == "comsol-mcp")
        if first_name in {"env"}:
            tail = command.split(maxsplit=1)[1] if len(command.split(maxsplit=1)) == 2 else ""
            second = tail.split(maxsplit=1)[0].strip("\"'") if tail else ""
            second_name = pathlib.PurePosixPath(second.replace("\\", "/")).name
            candidate = bool(interpreter.fullmatch(second_name))
            first = second
            first_name = second_name
        if candidate and first and "/" not in first.replace("\\", "/"):
            uncertain = True
    if busy:
        return {"status": "BUSY", "reason": "a process command references the install root", "pids": sorted(set(busy))}
    if uncertain:
        return {"status": "UNKNOWN", "reason": "a Python process has no attributable executable path", "pids": []}
    return {"status": "CLEAR", "reason": "ps completed and no process references the install root", "pids": []}


def _validate_removal_tree(root: pathlib.Path) -> None:
    root_stat = root.stat(follow_symlinks=False)
    def fail_walk(error: OSError) -> None:
        raise error

    for current, directories, _files in os.walk(root, followlinks=False, onerror=fail_walk):
        for name in directories:
            child = pathlib.Path(current) / name
            if child.is_symlink():
                # shutil.rmtree removes the link itself and does not descend it.
                continue
            child_stat = child.stat(follow_symlinks=False)
            if child_stat.st_dev != root_stat.st_dev or os.path.ismount(child):
                raise ReleaseError("install tree contains a nested mount; refusing recursive cleanup")


def uninstall_venv(venv: pathlib.Path, evidence: pathlib.Path, *, confirm: bool = False) -> dict[str, Any]:
    root = _canonical_install_root(venv)
    if not root.is_dir() or root.is_symlink():
        raise ReleaseError("requested install root is not an ordinary directory")
    marker = _load_install_marker(root)
    config = root / "pyvenv.cfg"
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not config.is_file() or not python.exists():
        raise ReleaseError("install root does not have the expected virtual-environment structure")
    evidence_path = pathlib.Path(os.path.abspath(os.fspath(evidence))).resolve(strict=False)
    if evidence_path == root or root in evidence_path.parents:
        raise ReleaseError("uninstall evidence must remain outside the install root")
    if evidence_path.exists():
        raise ReleaseError(f"uninstall evidence path already exists: {evidence_path}")
    _validate_removal_tree(root)
    content_guard = _content_guard(root, marker)
    guard = _process_guard(root)
    if content_guard["status"] == "CLEAR":
        blocker = None if guard["status"] == "CLEAR" else f"BLOCKED_{guard['status']}"
    else:
        blocker = content_guard["status"] if content_guard["status"].startswith("BLOCKED_") else f"BLOCKED_{content_guard['status']}"
    plan = {
        "schema": SCHEMA,
        "operation": "UNINSTALL_ISOLATED_VENV",
        "created_utc": utc_now(),
        "status": "UNINSTALL_PLAN_READY" if blocker is None else blocker,
        "confirmation_required": True,
        "confirmed": bool(confirm),
        "project_id": marker["project_id"],
        "install_id": marker["install_id"],
        "install_state": marker["state"],
        "target_root": str(root),
        "deletion_scope": {"exact_directory": str(root), "recursive": True,
                           "outside_paths_touched": False,
                           "ownership_status": content_guard["status"],
                           "scope": "helper-recorded virtual-environment contents only; content drift blocks deletion",
                           "nested_mounts": "REFUSED"},
        "preserved_evidence_directory": str(evidence_path),
        "content_guard": content_guard,
        "process_guard": guard,
        "native_engine": "NOT_PROBED",
    }
    evidence_path.mkdir(parents=True)
    _json_write(evidence_path / "uninstall-plan.json", plan)
    if blocker is not None:
        return plan
    if not confirm:
        return plan
    # Recheck the marker and process state at the last point before deletion.
    fresh_marker = _load_install_marker(root)
    if fresh_marker["install_id"] != marker["install_id"]:
        plan["status"] = "FAIL_INSTALL_IDENTITY_CHANGED"
        _json_write(evidence_path / "uninstall-result.json", plan)
        return plan
    fresh_content_guard = _content_guard(root, fresh_marker)
    if fresh_content_guard["status"] != "CLEAR":
        plan["status"] = f"BLOCKED_{fresh_content_guard['status']}"
        plan["content_guard_before_delete"] = fresh_content_guard
        _json_write(evidence_path / "uninstall-result.json", plan)
        return plan
    second_guard = _process_guard(root)
    if second_guard["status"] != "CLEAR":
        plan["status"] = f"BLOCKED_{second_guard['status']}"
        plan["process_guard_before_delete"] = second_guard
        _json_write(evidence_path / "uninstall-result.json", plan)
        return plan
    try:
        _validate_removal_tree(root)
        shutil.rmtree(root)
    except OSError as exc:
        plan["status"] = "FAIL_DELETE"
        plan["error"] = f"{type(exc).__name__}: {exc}"
        plan["target_root_exists_after_attempt"] = root.exists()
        _json_write(evidence_path / "uninstall-result.json", plan)
        return plan
    plan["status"] = "PASS_UNINSTALLED" if not root.exists() else "FAIL_DELETE_INCOMPLETE"
    plan["target_root_exists_after_attempt"] = root.exists()
    _json_write(evidence_path / "uninstall-result.json", plan)
    return plan


def pip_install_lock(venv_python: pathlib.Path, requirements: pathlib.Path, wheelhouse: pathlib.Path,
                     *, force_reinstall: bool = False) -> dict[str, Any]:
    args = [str(venv_python), "-m", "pip", "install", "--no-index", "--only-binary=:all:",
            "--disable-pip-version-check", "--no-input", "--find-links", str(wheelhouse),
            "--require-hashes", "--requirement", str(requirements)]
    if force_reinstall:
        args.insert(5, "--force-reinstall")
    return run_record(args)


def doctor(venv_python: pathlib.Path) -> dict[str, Any]:
    # Venv Python is commonly a symlink to the base interpreter.  `resolve()`
    # follows that link and silently selects the base interpreter directory,
    # so use the lexical executable path when deriving the venv root.
    executable = venv_python.absolute()
    isolated_cwd = executable.parent.parent
    code = """import importlib.metadata as md, json, pathlib, sys
import comsol_mcp, comsol_mcp.mcp_server
d=md.distribution('comsol-mcp')
eps=[{'name':e.name,'value':e.value} for e in md.entry_points(group='console_scripts') if e.name=='comsol-mcp']
pkg=pathlib.Path(comsol_mcp.__file__).resolve().parent
files=[p for p in pkg.rglob('*') if p.is_file() and not p.name.startswith('._') and '__pycache__' not in p.parts]
launcher=pathlib.Path(sys.executable).parent / ('comsol-mcp.exe' if sys.platform=='win32' else 'comsol-mcp')
print(json.dumps({'python':sys.version.split()[0],'executable':sys.executable,'prefix':sys.prefix,'version':d.version,'module':str(pkg),'entry_points':eps,'entry_point_path':str(launcher),'entry_point_exists':launcher.is_file(),'package_file_count':len(files),'java_sources':[p.relative_to(pkg).as_posix() for p in files if p.suffix=='.java'],'action_catalogs':[p.relative_to(pkg).as_posix() for p in files if p.name.lower()=='02_action_catalog.json'],'schemas':[p.relative_to(pkg).as_posix() for p in files if 'schema' in p.name.lower() and p.suffix=='.json']}))"""
    identity = run_record([str(venv_python), "-I", "-c", code], cwd=isolated_cwd)
    check = run_record([str(venv_python), "-I", "-m", "pip", "check"], cwd=isolated_cwd)
    parsed: dict[str, Any] | None = None
    if identity["exit_code"] == 0:
        try:
            parsed = json.loads(identity["stdout"].splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            parsed = None
    module = pathlib.Path(parsed["module"]) if parsed and parsed.get("module") else None
    prefix = pathlib.Path(parsed["prefix"]) if parsed and parsed.get("prefix") else venv_python.parent.parent
    inside_prefix = bool(module and prefix in module.parents)
    passed = bool(parsed and identity["exit_code"] == 0 and check["exit_code"] == 0 and
                  inside_prefix and parsed["entry_points"] and parsed["entry_point_exists"] and parsed["java_sources"] and
                  parsed["action_catalogs"] and parsed["schemas"])
    return {"schema": SCHEMA, "status": "PASS" if passed else "FAIL",
            "native_engine": "NOT_PROBED", "capabilities": "UNVERIFIED",
            "source": "installed environment only", "identity": parsed,
            "installed_under_prefix": inside_prefix,
            "import": identity, "pip_check": check}


def package_fingerprint(venv_python: pathlib.Path) -> dict[str, Any]:
    code = """import hashlib,json,pathlib,comsol_mcp\npkg=pathlib.Path(comsol_mcp.__file__).resolve().parent\nrows=[]\nfor p in sorted(pkg.rglob('*')):\n if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'}:\n  rows.append({'path':p.relative_to(pkg).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})\nprint(json.dumps({'root':str(pkg),'files':rows,'digest':hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()}))"""
    record = run_record([str(venv_python), "-I", "-c", code], cwd=venv_python.absolute().parent.parent)
    if record["exit_code"]:
        raise ReleaseError("installed package fingerprint failed: " + record["stderr"][-1000:])
    try:
        return json.loads(record["stdout"].splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ReleaseError("installed package fingerprint returned invalid output") from exc


def distribution_fingerprint(venv_python: pathlib.Path) -> dict[str, Any]:
    """Return a path-free inventory of every installed distribution in a venv."""
    code = """import hashlib,json,re
from importlib import metadata
rows=[]
for dist in metadata.distributions():
 name=dist.metadata.get('Name')
 version=dist.version
 if not name or not version: raise RuntimeError('distribution metadata is incomplete')
 rows.append({'name':re.sub(r'[-_.]+','-',name).lower(),'version':version})
rows.sort(key=lambda row:(row['name'],row['version']))
payload=json.dumps(rows,sort_keys=True,separators=(',',':'))
print(json.dumps({'distributions':rows,'digest':hashlib.sha256(payload.encode()).hexdigest()}))"""
    record = run_record([str(venv_python), "-I", "-c", code], cwd=venv_python.absolute().parent.parent)
    if record["exit_code"]:
        raise ReleaseError("installed distribution inventory failed: " + record["stderr"][-1000:])
    try:
        parsed = json.loads(record["stdout"].splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ReleaseError("installed distribution inventory returned invalid output") from exc
    distributions = parsed.get("distributions") if isinstance(parsed, dict) else None
    digest = parsed.get("digest") if isinstance(parsed, dict) else None
    if (not isinstance(distributions, list) or not isinstance(digest, str) or
            any(not isinstance(row, dict) or not isinstance(row.get("name"), str) or
                not isinstance(row.get("version"), str) for row in distributions)):
        raise ReleaseError("installed distribution inventory has an invalid structure")
    expected = hashlib.sha256(json.dumps(distributions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != expected:
        raise ReleaseError("installed distribution inventory digest does not match its rows")
    return parsed


def _network_denial_child(port: int) -> dict[str, Any]:
    probe: dict[str, Any]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            probe = {"status": "FAIL", "detail": "sandbox child connected to parent loopback listener"}
    except OSError as exc:
        denied = exc.errno in {1, 13, 10013} or "operation not permitted" in str(exc).lower() or "permission" in str(exc).lower()
        probe = {"status": "PASS" if denied else "INCONCLUSIVE", "errno": exc.errno, "detail": str(exc)[:240]}
    return probe


def _offline_child(args: argparse.Namespace, port: int) -> dict[str, Any]:
    probe = _network_denial_child(port)
    report: dict[str, Any] = {"schema": SCHEMA, "status": "FAIL", "created_utc": utc_now(),
                              "isolation": "macOS sandbox-exec process tree; deny network*",
                              "network_probe": probe, "native_engine": "NOT_RUN",
                              "full_small_model": "NOT_RUN", "pip_download": "DISABLED_NO_INDEX",
                              "steps": []}
    if probe["status"] != "PASS":
        report["status"] = "BLOCKED_ISOLATION_UNPROVEN"
        return report
    try:
        py = create_venv(args.python, args.venv)
        install = pip_install_lock(py, args.requirements, args.wheelhouse)
        report["steps"].append({"step": "offline_install", **install})
        _record_install_inventory(py)
        if install["exit_code"]:
            report["status"] = "FAIL_INSTALL"
            return report
        report["doctor"] = doctor(py)
        report["steps"].append({"step": "offline_pip_check", **report["doctor"]["pip_check"]})
        report["steps"].append({"step": "installed_entrypoint_import", **report["doctor"]["import"]})
        report["status"] = "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT" if report["doctor"]["status"] == "PASS" else "FAIL_DOCTOR"
        if report["status"] == "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT":
            _mark_install_ready(py)
    except Exception as exc:
        report["status"] = "FAIL_EXCEPTION"
        report["exception"] = f"{type(exc).__name__}: {exc}"
    return report


def offline_install(args: argparse.Namespace, script: pathlib.Path) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise ReleaseError("native process-level offline isolation is implemented only for macOS sandbox-exec")
    sandbox = shutil.which("sandbox-exec")
    if not sandbox:
        raise ReleaseError("sandbox-exec is unavailable; no process-level offline claim can be made")
    if args.venv.exists():
        raise ReleaseError(f"offline install target already exists: {args.venv}")
    args.evidence.mkdir(parents=True, exist_ok=True)
    expected = ("network-deny.sb", "offline-install-process.log.json", "offline-install-result.json")
    if any((args.evidence / name).exists() for name in expected):
        raise ReleaseError(f"offline-install evidence already exists; choose a new directory: {args.evidence}")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.25)
        port = listener.getsockname()[1]
        profile = args.evidence / "network-deny.sb"
        with profile.open("x", encoding="utf-8") as stream:
            stream.write("(version 1)\n(deny network*)\n(allow default)\n")
        child_argv = [str(args.python), str(script), "_offline_child", "--python", str(args.python),
                      "--requirements", str(args.requirements), "--wheelhouse", str(args.wheelhouse),
                      "--venv", str(args.venv), "--port", str(port)]
        proc = subprocess.run([sandbox, "-f", str(profile), *child_argv], capture_output=True, text=True)
        try:
            accepted, _ = listener.accept()
            accepted.close()
            accepted_any = True
        except TimeoutError:
            accepted_any = False
        except OSError:
            accepted_any = False
    _json_write(args.evidence / "offline-install-process.log.json", {
        "argv": ["sandbox-exec", "-f", str(profile), "<release-python>", "full_project_release.py", "_offline_child", "..."],
        "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
        "listener_accepted": accepted_any, "profile_sha256": sha256_file(profile),
    })
    try:
        child_report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        try:
            child_report = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            child_report = {"status": "FAIL_INVALID_CHILD_REPORT"}
    if proc.returncode != 0 and child_report.get("status", "").startswith("PASS"):
        child_report["status"] = "FAIL_CHILD_EXIT"
    child_report["listener_accepted"] = accepted_any
    child_report["status"] = "FAIL_NETWORK_RULE_NOT_ENFORCED" if accepted_any else child_report.get("status", "FAIL")
    _json_write(args.evidence / "offline-install-result.json", child_report)
    return child_report


def _capture_transition_state(venv_python: pathlib.Path) -> dict[str, Any]:
    state: dict[str, Any] = {"capture_errors": {}}
    try:
        state["installed_version"] = _installed_version(venv_python)
    except (OSError, ReleaseError) as exc:
        state["installed_version"] = None
        state["capture_errors"]["installed_version"] = type(exc).__name__
    for key, capture in (("package_fingerprint", package_fingerprint),
                         ("distribution_fingerprint", distribution_fingerprint)):
        try:
            state[key] = capture(venv_python)
        except (OSError, ReleaseError) as exc:
            state["capture_errors"][key] = type(exc).__name__
    return state


def _transition_identity_matches(observed: dict[str, Any], original: dict[str, Any]) -> dict[str, bool | None]:
    matches: dict[str, bool | None] = {}
    for key in ("package_fingerprint", "distribution_fingerprint"):
        current = observed.get(key)
        expected = original.get(key)
        matches[key] = (current.get("digest") == expected.get("digest")
                        if isinstance(current, dict) and isinstance(expected, dict) else None)
    return matches


def transition_check(args: argparse.Namespace, script: pathlib.Path) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise ReleaseError("transition-check requires process-level network isolation on macOS")
    sandbox = shutil.which("sandbox-exec")
    if not sandbox:
        raise ReleaseError("sandbox-exec unavailable; transition test would not prove offline behavior")
    if args.work_root.exists():
        raise ReleaseError(f"transition work root already exists: {args.work_root}")
    args.work_root.mkdir(parents=True)
    venv = args.work_root / "transition-venv"
    evidence = args.work_root / "transition-evidence"
    evidence.mkdir()
    inputs = evidence / "inputs"
    inputs.mkdir()
    old_lock_bytes = args.old_requirements.read_bytes()
    new_lock_bytes = args.new_requirements.read_bytes()
    old_lock_snapshot = inputs / "old-requirements.lock"
    new_lock_snapshot = inputs / "candidate-requirements.lock"
    old_lock_snapshot.write_bytes(old_lock_bytes)
    new_lock_snapshot.write_bytes(new_lock_bytes)
    result: dict[str, Any] = {
        "schema": SCHEMA, "created_utc": utc_now(), "status": "FAIL",
        "transition_mode": "disposable hash-locked artifact transition with full distribution restore",
        "native_engine": "NOT_RUN", "states": [],
        "version_migration_verified": False,
        "migration_scope": "package artifact/version state only; application data/schema migration is not exercised",
        "input_locks": [
            {"source_path": str(args.old_requirements.resolve()), "snapshot_path": str(old_lock_snapshot),
             "bytes": len(old_lock_bytes), "sha256": hashlib.sha256(old_lock_bytes).hexdigest()},
            {"source_path": str(args.new_requirements.resolve()), "snapshot_path": str(new_lock_snapshot),
             "bytes": len(new_lock_bytes), "sha256": hashlib.sha256(new_lock_bytes).hexdigest()},
        ],
    }
    old_version = _installed_version_from_lock(old_lock_snapshot)
    new_version = _installed_version_from_lock(new_lock_snapshot)
    result["expected_versions"] = {"old": old_version, "candidate": new_version}
    if not old_version or not new_version:
        result["status"] = "BLOCKED_LOCK_VERSION_UNRESOLVED"
        return result

    # Use the same network-denying supervisor as offline-install; a local
    # listener probe proves the child process actually inherited the profile.
    inner = argparse.Namespace(python=args.python, requirements=old_lock_snapshot,
                               wheelhouse=args.old_wheelhouse, venv=venv, evidence=evidence)
    first = offline_install(inner, script)
    if first.get("status") != "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT":
        result["status"] = "BLOCKED_OR_FAIL_INITIAL_INSTALL"
        result["initial_install"] = first
        return result

    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    original = _capture_transition_state(py)
    result["states"].append({"state": "old_release_installed", **original})
    if original.get("capture_errors"):
        result["status"] = "UNKNOWN_INITIAL_STATE"
        return result
    if original.get("installed_version") != old_version:
        result["status"] = "FAIL_INITIAL_VERSION_MISMATCH"
        return result
    if original.get("capture_errors") or not isinstance(original.get("package_fingerprint"), dict) or not isinstance(
            original.get("distribution_fingerprint"), dict):
        result["status"] = "UNKNOWN_INITIAL_STATE"
        return result

    profile = evidence / "network-deny-transition.sb"
    with profile.open("x", encoding="utf-8") as stream:
        stream.write("(version 1)\n(deny network*)\n(allow default)\n")
    candidate_args = [str(py), "-m", "pip", "install", "--no-index", "--only-binary=:all:",
                      "--disable-pip-version-check", "--no-input", "--force-reinstall", "--find-links",
                      str(args.new_wheelhouse), "--require-hashes", "--requirement", str(new_lock_snapshot)]
    try:
        upgrade = run_record([sandbox, "-f", str(profile), *candidate_args])
    except OSError as exc:
        # Preserve the invocation failure, but still run rollback in case pip
        # changed part of the disposable environment before the failure surfaced.
        upgrade = {"argv": [sandbox, "-f", str(profile), *candidate_args], "exit_code": None,
                   "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "duration_seconds": None}
    result["upgrade"] = upgrade
    candidate = _capture_transition_state(py)
    result["states"].append({"state": "candidate_attempted", "exit_code": upgrade.get("exit_code"), **candidate})

    rollback_args = [str(py), "-m", "pip", "install", "--no-index", "--only-binary=:all:",
                     "--disable-pip-version-check", "--no-input", "--force-reinstall", "--find-links",
                     str(args.old_wheelhouse), "--require-hashes", "--requirement", str(old_lock_snapshot)]
    try:
        rollback = run_record([sandbox, "-f", str(profile), *rollback_args])
    except OSError as exc:
        rollback = {"argv": [sandbox, "-f", str(profile), *rollback_args], "exit_code": None,
                    "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "duration_seconds": None}
    result["rollback"] = rollback

    # pip's old-lock install does not uninstall dependencies introduced only by
    # the candidate. Remove only such distributions, and only inside this newly
    # created disposable venv, then verify the entire distribution set.
    before_cleanup = _capture_transition_state(py)
    original_rows = original["distribution_fingerprint"]["distributions"]
    current_inventory = before_cleanup.get("distribution_fingerprint")
    original_names = {row["name"] for row in original_rows}
    current_names = ({row["name"] for row in current_inventory["distributions"]}
                     if isinstance(current_inventory, dict) else set())
    candidate_only_names = sorted(current_names - original_names)
    if candidate_only_names:
        cleanup_args = [str(py), "-m", "pip", "uninstall", "--yes", *candidate_only_names]
        try:
            cleanup = run_record([sandbox, "-f", str(profile), *cleanup_args])
        except OSError as exc:
            cleanup = {"argv": [sandbox, "-f", str(profile), *cleanup_args], "exit_code": None,
                       "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "duration_seconds": None}
        cleanup["attempted"] = True
        cleanup["package_names"] = candidate_only_names
        result["rollback_candidate_only_cleanup"] = cleanup
    else:
        result["rollback_candidate_only_cleanup"] = {"status": "NOT_NEEDED", "attempted": False,
                                                     "package_names": []}

    restored = _capture_transition_state(py)
    result["states"].append({"state": "old_release_restored", **restored})
    result["candidate_only_distribution_names_attempted"] = candidate_only_names
    restored_inventory = restored.get("distribution_fingerprint")
    if isinstance(restored_inventory, dict):
        restored_names = {row["name"] for row in restored_inventory["distributions"]}
        result["candidate_only_distribution_names_removed_confirmed"] = sorted(
            name for name in candidate_only_names if name not in restored_names)
    else:
        result["candidate_only_distribution_names_removed_confirmed"] = None
    result["rollback_identity_matches"] = _transition_identity_matches(restored, original)
    result["rollback_version_matches_lock"] = restored.get("installed_version") == old_version
    result["artifact_version_transition_verified"] = (
        upgrade.get("exit_code") == 0 and candidate.get("installed_version") == new_version and
        original.get("installed_version") == old_version)
    result["same_version"] = old_version == new_version
    cleanup_record = result["rollback_candidate_only_cleanup"]
    cleanup_attempted = cleanup_record.get("attempted") is True
    cleanup_exit = cleanup_record.get("exit_code") if cleanup_attempted else 0
    identity_values = list(result["rollback_identity_matches"].values())
    if rollback.get("exit_code") is None or any(value is None for value in identity_values) or restored.get("capture_errors"):
        result["status"] = "UNKNOWN_ROLLBACK_STATE"
    elif rollback.get("exit_code") != 0:
        result["status"] = "FAIL_ROLLBACK_COMMAND"
    elif cleanup_attempted and cleanup_exit is None:
        result["status"] = "UNKNOWN_ROLLBACK_CLEANUP"
    elif cleanup_attempted and cleanup_exit != 0:
        result["status"] = "FAIL_ROLLBACK_CLEANUP"
    elif not all(identity_values) or not result["rollback_version_matches_lock"]:
        result["status"] = "FAIL_ROLLBACK_STATE_MISMATCH"
    elif upgrade.get("exit_code") != 0:
        result["status"] = "FAIL_UPGRADE_ROLLED_BACK"
    elif candidate.get("capture_errors") or candidate.get("installed_version") is None:
        result["status"] = "UNKNOWN_CANDIDATE_STATE_ROLLED_BACK"
    elif candidate.get("installed_version") != new_version:
        result["status"] = "FAIL_CANDIDATE_VERSION_ROLLED_BACK"
    elif not result["artifact_version_transition_verified"]:
        result["status"] = "FAIL_CANDIDATE_VERSION_ROLLED_BACK"
    else:
        result["status"] = "PASS_ARTIFACT_ROLLBACK_MIGRATION_UNVERIFIED"
    return result


def _installed_version(py: pathlib.Path) -> str | None:
    record = run_record([str(py), "-I", "-c", "import importlib.metadata as m; print(m.version('comsol-mcp'))"],
                        cwd=py.absolute().parent.parent)
    return record["stdout"].strip().splitlines()[-1] if record["exit_code"] == 0 and record["stdout"].strip() else None


def _installed_version_from_lock(lock: pathlib.Path) -> str | None:
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*comsol[-_.]mcp\s*==\s*([^\s;\\]+)", line, re.I)
        if match:
            return match.group(1)
    return None


def command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan directory and nested archive members")
    scan.add_argument("input", type=pathlib.Path)
    scan.add_argument("--output", type=pathlib.Path)
    source = sub.add_parser("source-bundle", help="zip exact files listed in a reviewed source inclusion manifest")
    source.add_argument("--source", type=pathlib.Path, required=True)
    source.add_argument("--manifest", type=pathlib.Path, required=True)
    source.add_argument("--out", type=pathlib.Path, required=True)
    source.add_argument("--evidence", type=pathlib.Path, required=True)
    source_verify = sub.add_parser("verify-source-bundle", help="verify exact source member hashes and optionally extract to a new directory")
    source_verify.add_argument("bundle", type=pathlib.Path)
    source_verify.add_argument("--extract-to", type=pathlib.Path,
                               help="extract verified contents into a destination that does not already exist")
    source_verify.add_argument("--output", type=pathlib.Path)
    state_bundle = sub.add_parser("campaign-state-bundle", help="capture an exact two-sample local-only campaign state capsule")
    state_bundle.add_argument("--source-root", type=pathlib.Path, required=True,
                              help="workpack root containing the explicitly listed state files and external pointers")
    state_bundle.add_argument("--scope", type=pathlib.Path, required=True,
                              help="reviewed JSON scope manifest; only LOCAL_RECOVERY_ONLY JSON/Markdown files are accepted")
    state_bundle.add_argument("--out", type=pathlib.Path, required=True)
    state_bundle.add_argument("--evidence", type=pathlib.Path, required=True)
    state_bundle.add_argument("--max-attempts", type=int, default=3)
    state_verify = sub.add_parser("verify-campaign-state-bundle", help="verify/extract a campaign state capsule without executing or rewriting it")
    state_verify.add_argument("bundle", type=pathlib.Path)
    state_verify.add_argument("--extract-to", type=pathlib.Path,
                              help="extract verified bytes into a fresh directory")
    state_verify.add_argument("--path-map", type=pathlib.Path,
                              help="detached read-only path-remapping overlay JSON")
    state_verify.add_argument("--output", type=pathlib.Path,
                              help="write the integrity and path-mapping report")
    build = sub.add_parser("build-wheel", help="build from an allowlisted source snapshot")
    build.add_argument("--source", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    build.add_argument("--out", type=pathlib.Path, required=True)
    build.add_argument("--python", type=pathlib.Path, default=pathlib.Path(sys.executable))
    build.add_argument("--evidence", type=pathlib.Path, required=True)
    manifest = sub.add_parser("manifest", help="audit target wheelhouse and write hashes, SBOM and licenses")
    manifest.add_argument("--bundle", type=pathlib.Path, required=True)
    manifest.add_argument("--target", choices=sorted(TARGETS), required=True)
    manifest.add_argument("--requirements", type=pathlib.Path, required=True)
    manifest.add_argument("--source-lock", type=pathlib.Path)
    manifest.add_argument("--derived-provenance", type=pathlib.Path,
                          help="provenance for a locally built cryptography x86_64 wheel")
    manifest.add_argument("--openssl-license", type=pathlib.Path,
                          help="exact license file for the statically linked OpenSSL component")
    manifest.add_argument("--out", type=pathlib.Path, required=True)
    derived_lock = sub.add_parser("compose-derived-lock", help="hash-pin an attested target derived wheel without changing uv.lock")
    derived_lock.add_argument("--requirements", type=pathlib.Path, required=True)
    derived_lock.add_argument("--wheel", type=pathlib.Path, required=True)
    derived_lock.add_argument("--source-lock", type=pathlib.Path, required=True)
    derived_lock.add_argument("--derived-provenance", type=pathlib.Path, required=True)
    derived_lock.add_argument("--openssl-license", type=pathlib.Path, required=True)
    derived_lock.add_argument("--target", choices=sorted(TARGETS), required=True)
    derived_lock.add_argument("--out", type=pathlib.Path, required=True)
    compose = sub.add_parser("compose-lock", help="append the exact application wheel SHA256 to a uv export")
    compose.add_argument("--dependencies", type=pathlib.Path, required=True)
    compose.add_argument("--application-wheel", type=pathlib.Path, required=True)
    compose.add_argument("--target", choices=sorted(TARGETS), required=True)
    compose.add_argument("--out", type=pathlib.Path, required=True)
    download = sub.add_parser("download-wheelhouse", help="download hashed target wheels without relabeling")
    download.add_argument("--python", type=pathlib.Path, required=True)
    download.add_argument("--target", choices=sorted(TARGETS), required=True)
    download.add_argument("--requirements", type=pathlib.Path, required=True)
    download.add_argument("--application-wheel", type=pathlib.Path, required=True)
    download.add_argument("--out", type=pathlib.Path, required=True)
    download.add_argument("--evidence", type=pathlib.Path, required=True)
    download.add_argument("--source-lock", type=pathlib.Path, required=True)
    bundle = sub.add_parser("package-bundle", help="create a hash-indexed self-contained offline install archive")
    bundle.add_argument("--wheelhouse", type=pathlib.Path, required=True)
    bundle.add_argument("--requirements", type=pathlib.Path, required=True)
    bundle.add_argument("--source-lock", type=pathlib.Path, required=True)
    bundle.add_argument("--metadata", type=pathlib.Path, required=True)
    bundle.add_argument("--operations-doc", type=pathlib.Path, required=True)
    bundle.add_argument("--build-evidence", type=pathlib.Path, required=True,
                        help="directory from build-wheel containing wheel receipt and source snapshot manifest")
    bundle.add_argument("--target", choices=sorted(TARGETS), required=True)
    bundle.add_argument("--out", type=pathlib.Path, required=True)
    bundle.add_argument("--evidence", type=pathlib.Path, required=True)
    bundle.add_argument("--tool", type=pathlib.Path, help="release helper copy embedded in the bundle")
    bundle.add_argument("--derived-provenance", type=pathlib.Path,
                        help="provenance for a locally built cryptography x86_64 wheel")
    bundle.add_argument("--openssl-license", type=pathlib.Path,
                        help="exact license file for the statically linked OpenSSL component")
    doc = sub.add_parser("doctor", help="inspect an installed venv without starting COMSOL")
    doc.add_argument("--python", type=pathlib.Path, required=True)
    inst = sub.add_parser("install", help="fresh offline install from a hash-locked wheelhouse")
    inst.add_argument("--python", type=pathlib.Path, required=True, help="base Python used only to create the venv")
    inst.add_argument("--venv", type=pathlib.Path, required=True)
    inst.add_argument("--requirements", type=pathlib.Path, required=True)
    inst.add_argument("--wheelhouse", type=pathlib.Path, required=True)
    inst.add_argument("--evidence", type=pathlib.Path, required=True)
    off = sub.add_parser("offline-install", help="process-isolated offline venv install plus package doctor")
    off.add_argument("--python", type=pathlib.Path, required=True)
    off.add_argument("--venv", type=pathlib.Path, required=True)
    off.add_argument("--requirements", type=pathlib.Path, required=True)
    off.add_argument("--wheelhouse", type=pathlib.Path, required=True)
    off.add_argument("--evidence", type=pathlib.Path, required=True)
    remove = sub.add_parser("uninstall", help="plan or remove one marked isolated virtual environment")
    remove.add_argument("--venv", type=pathlib.Path, required=True, help="exact isolated install root created by this helper")
    remove.add_argument("--evidence", type=pathlib.Path, required=True, help="new directory outside the install root for preserved receipts")
    remove.add_argument("--confirm", action="store_true", help="remove exactly the marked venv after the process guard passes")
    trans = sub.add_parser("transition-check", help="isolated old->new->old artifact transition and rollback")
    trans.add_argument("--python", type=pathlib.Path, required=True)
    trans.add_argument("--work-root", type=pathlib.Path, required=True)
    trans.add_argument("--old-requirements", type=pathlib.Path, required=True)
    trans.add_argument("--old-wheelhouse", type=pathlib.Path, required=True)
    trans.add_argument("--new-requirements", type=pathlib.Path, required=True)
    trans.add_argument("--new-wheelhouse", type=pathlib.Path, required=True)
    internal = sub.add_parser("_offline_child", help=argparse.SUPPRESS)
    internal.add_argument("--python", type=pathlib.Path, required=True)
    internal.add_argument("--venv", type=pathlib.Path, required=True)
    internal.add_argument("--requirements", type=pathlib.Path, required=True)
    internal.add_argument("--wheelhouse", type=pathlib.Path, required=True)
    internal.add_argument("--port", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = command_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            result = audit_tree(args.input)
            if args.output:
                _json_write(args.output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "PASS" else 2
        if args.command == "source-bundle":
            result = source_bundle(args.source, args.manifest, args.out, args.evidence)
        elif args.command == "verify-source-bundle":
            result = verify_source_bundle(args.bundle, args.extract_to)
            if args.output:
                _json_write(args.output, result)
        elif args.command == "campaign-state-bundle":
            if args.max_attempts < 1 or args.max_attempts > 5:
                raise ReleaseError("campaign state max-attempts must be between 1 and 5")
            result = campaign_state_bundle(args.source_root, args.scope, args.out, args.evidence,
                                           args.max_attempts)
        elif args.command == "verify-campaign-state-bundle":
            result = verify_campaign_state_bundle(args.bundle, args.extract_to, args.path_map)
            if args.output:
                _json_write(args.output, result)
        elif args.command == "build-wheel":
            result = build_wheel(args.source, args.out, args.python, args.evidence)
        elif args.command == "manifest":
            result = create_bundle_manifest(args.bundle, args.target, args.requirements, args.source_lock, args.out,
                                            args.derived_provenance, args.openssl_license)
        elif args.command == "compose-derived-lock":
            result = compose_derived_wheel_lock(args.requirements, args.wheel, args.source_lock,
                                                args.derived_provenance, args.openssl_license,
                                                args.target, args.out)
        elif args.command == "compose-lock":
            result = compose_install_lock(args.dependencies, args.application_wheel, args.target, args.out)
        elif args.command == "download-wheelhouse":
            result = download_wheelhouse(args.python, args.target, args.requirements,
                                         args.application_wheel, args.out, args.evidence, args.source_lock)
        elif args.command == "package-bundle":
            result = package_wheelhouse_bundle(args.wheelhouse, args.requirements, args.source_lock,
                                               args.metadata, args.operations_doc, args.target,
                                               args.out, args.evidence, args.build_evidence, args.tool,
                                               args.derived_provenance, args.openssl_license)
        elif args.command == "doctor":
            result = doctor(args.python)
        elif args.command == "install":
            if (args.evidence / "install-result.json").exists():
                raise ReleaseError(f"install evidence already exists: {args.evidence}")
            py = create_venv(args.python, args.venv)
            install = pip_install_lock(py, args.requirements, args.wheelhouse)
            _record_install_inventory(py)
            result = {"status": "FAIL_INSTALL" if install["exit_code"] else "INSTALLED", "install": install}
            if install["exit_code"] == 0:
                result["doctor"] = doctor(py)
                result["status"] = "PASS" if result["doctor"]["status"] == "PASS" else "FAIL_DOCTOR"
                if result["status"] == "PASS":
                    _mark_install_ready(py)
            args.evidence.mkdir(parents=True, exist_ok=True)
            _json_write(args.evidence / "install-result.json", result)
        elif args.command == "offline-install":
            result = offline_install(args, pathlib.Path(__file__).resolve())
        elif args.command == "uninstall":
            result = uninstall_venv(args.venv, args.evidence, confirm=args.confirm)
        elif args.command == "transition-check":
            result = transition_check(args, pathlib.Path(__file__).resolve())
            _json_write(args.work_root / "transition-result.json", result)
        elif args.command == "_offline_child":
            result = _offline_child(args, args.port)
        else:
            parser.error("unsupported command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        successful_artifact_states = {
            "ARTIFACTS_HASHED_NATIVE_UNVERIFIED", "WHEELHOUSE_HASHED_NATIVE_UNVERIFIED",
            "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED", "SOURCE_ARCHIVE_HASHED_NATIVE_UNVERIFIED",
            "WHEEL_BUILT_NATIVE_UNVERIFIED", "LOCK_COMPOSED",
        }
        return 0 if (result.get("status", "").startswith("PASS") or
                     result.get("status") in successful_artifact_states | {"UNINSTALL_PLAN_READY"}) else 1
    except (OSError, ReleaseError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
