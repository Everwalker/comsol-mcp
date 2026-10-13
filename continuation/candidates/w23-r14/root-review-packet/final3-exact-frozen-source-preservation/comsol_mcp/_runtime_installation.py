"""Read-only COMSOL installation inventory for the runtime control plane.

This module never starts COMSOL, Java, a license checkout, or a render. It
reads install metadata and native executable headers from the current host's
Windows or macOS installations. Unknown or conflicting metadata stays
unknown; directory names and host architecture are not used as substitutes.
"""

from __future__ import annotations

import os
import platform
import plistlib
import re
import struct
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, unquote


_README_VERSION = re.compile(r"^\s*COMSOL\s+(?P<version>[0-9]+(?:\.[0-9]+){1,3})\s+README\b", re.IGNORECASE)
_JAVA_RELEASE_FIELD = re.compile(r'^\s*(JAVA_VERSION|OS_ARCH)="?([^"\r\n]+)"?\s*$')
_ARCHITECTURES = {
    0x01000007: "x86_64",
    0x0100000C: "arm64",
    0x00000007: "x86",
    0x0000000C: "arm",
    0x8664: "x86_64",
    0xAA64: "arm64",
    0x014C: "x86",
}
_SAFE_DOCTOR_CHECKS = frozenset({
    "installation", "version", "build", "launcher", "architecture", "java",
    "directories", "configuration", "license", "port", "render",
})
_COMPATIBILITY_REQUIREMENTS = frozenset({
    "minimum_version", "maximum_version", "minimum_build", "architectures",
    "minimum_java_version",
})


class RuntimeInstallationError(ValueError):
    """A runtime id or discovery request cannot be inspected safely."""


def _target_platform(system: str | None = None) -> str | None:
    name = system if system is not None else platform.system()
    if name == "Darwin":
        return "macos"
    if name == "Windows":
        return "windows"
    return None


def _canonical_directory(value: str | os.PathLike[str]) -> Path:
    text = os.fspath(value)
    if not text or any(ord(char) < 32 for char in text):
        raise RuntimeInstallationError("installation root must be a non-empty path without control characters")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise RuntimeInstallationError("installation roots must be absolute paths")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeInstallationError("installation root is missing or inaccessible") from exc
    if not resolved.is_dir():
        raise RuntimeInstallationError("installation root is not a directory")
    return resolved


def runtime_id_for_root(root: str | os.PathLike[str]) -> str:
    """Return a stable local identifier that binds the canonical install path."""
    canonical = _canonical_directory(root)
    return "comsol-install:" + quote(str(canonical), safe="")


def _root_from_runtime_id(runtime_id: str) -> Path:
    if not isinstance(runtime_id, str) or not runtime_id.startswith("comsol-install:"):
        raise RuntimeInstallationError("runtime_id is not a local COMSOL installation identifier")
    encoded = runtime_id.removeprefix("comsol-install:")
    if not encoded:
        raise RuntimeInstallationError("runtime_id does not identify an installation path")
    decoded = unquote(encoded)
    if any(ord(char) < 32 for char in decoded):
        raise RuntimeInstallationError("runtime_id contains control characters")
    try:
        path = _canonical_directory(decoded)
    except RuntimeInstallationError:
        raise
    if runtime_id_for_root(path) != runtime_id:
        raise RuntimeInstallationError("runtime_id path is not in canonical encoded form")
    return path


def _launcher_candidates(root: Path, target: str) -> list[Path]:
    if target == "macos":
        candidates = (
            root / "bin" / "macarm64" / "comsollauncher",
            root / "bin" / "macosx64" / "comsollauncher",
            root / "bin" / "comsol",
        )
    else:
        candidates = (
            root / "bin" / "win64" / "comsolmphserver.exe",
            root / "bin" / "win64" / "comsol.exe",
            root / "bin" / "win64" / "comsolbatch.exe",
        )
    return [path for path in candidates if path.is_file()]


def _readme_release(root: Path) -> dict[str, Any]:
    path = root / "readme.txt"
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            first = stream.readline(4096).strip()
    except OSError:
        return {"status": "UNKNOWN", "version": None, "build": None, "source": None}
    match = _README_VERSION.match(first)
    if match is None:
        return {"status": "UNKNOWN", "version": None, "build": None, "source": str(path)}
    components = match.group("version").split(".")
    if len(components) >= 4:
        version, build = ".".join(components[:3]), components[3]
    else:
        version, build = ".".join(components), None
    return {
        "status": "OBSERVED",
        "version": version,
        "full_release": match.group("version"),
        "build": build,
        "source": str(path),
    }


def _bundle_versions(root: Path) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for app_name in ("COMSOL Multiphysics.app", "COMSOL Multiphysics Server.app"):
        plist_path = root / app_name / "Contents" / "Info.plist"
        try:
            with plist_path.open("rb") as stream:
                data = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        version = data.get("CFBundleShortVersionString")
        if isinstance(version, str) and version.strip():
            values.append({"version": version.strip(), "source": str(plist_path)})
    return values


def _version_observation(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    readme = _readme_release(root)
    bundles = _bundle_versions(root)
    observed_versions = []
    if readme.get("status") == "OBSERVED":
        observed_versions.append((str(readme["version"]), str(readme["source"])))
    observed_versions.extend((row["version"], row["source"]) for row in bundles)
    normalized = {tuple(int(part) for part in version.split(".")) for version, _source in observed_versions}
    if not normalized:
        version_row = {"status": "UNKNOWN", "value": None, "sources": []}
    elif len(normalized) != 1:
        version_row = {
            "status": "CONFLICT",
            "value": None,
            "observations": [{"value": version, "source": source} for version, source in observed_versions],
        }
    else:
        version_row = {
            "status": "OBSERVED",
            "value": ".".join(str(part) for part in next(iter(normalized))),
            "sources": [{"value": version, "path": source} for version, source in observed_versions],
        }
    build_value = readme.get("build")
    build_row = {
        "status": "OBSERVED" if isinstance(build_value, str) and build_value.isdigit() else "UNKNOWN",
        "value": int(build_value) if isinstance(build_value, str) and build_value.isdigit() else None,
        "source": readme.get("source") if build_value is not None else None,
    }
    return version_row, build_row


def _cpu_architecture(cpu_type: int) -> str | None:
    return _ARCHITECTURES.get(cpu_type)


def _macho_architectures(path: Path) -> list[str]:
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            magic = header[:4]
            if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
                endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
                if len(header) < 8:
                    return []
                arch = _cpu_architecture(struct.unpack(f"{endian}I", header[4:8])[0])
                return [arch] if arch else []
            fat_formats = {
                b"\xca\xfe\xba\xbe": (">", 20),
                b"\xbe\xba\xfe\xca": ("<", 20),
                b"\xca\xfe\xba\xbf": (">", 32),
                b"\xbf\xba\xfe\xca": ("<", 32),
            }
            if magic not in fat_formats or len(header) < 8:
                return []
            endian, entry_size = fat_formats[magic]
            count = struct.unpack(f"{endian}I", header[4:8])[0]
            if count == 0 or count > 32:
                return []
            stream.seek(8)
            table = stream.read(count * entry_size)
            if len(table) != count * entry_size:
                return []
            result = set()
            for offset in range(0, len(table), entry_size):
                arch = _cpu_architecture(struct.unpack(f"{endian}I", table[offset:offset + 4])[0])
                if arch:
                    result.add(arch)
            return sorted(result)
    except OSError:
        return []


def _pe_architectures(path: Path) -> list[str]:
    try:
        with path.open("rb") as stream:
            dos = stream.read(64)
            if len(dos) < 64 or dos[:2] != b"MZ":
                return []
            pe_offset = struct.unpack("<I", dos[0x3C:0x40])[0]
            if pe_offset < 64 or pe_offset > 16 * 1024 * 1024:
                return []
            stream.seek(pe_offset)
            signature_and_machine = stream.read(6)
            if len(signature_and_machine) != 6 or signature_and_machine[:4] != b"PE\0\0":
                return []
            machine = struct.unpack("<H", signature_and_machine[4:6])[0]
            arch = _cpu_architecture(machine)
            return [arch] if arch else []
    except OSError:
        return []


def binary_architectures(path: Path) -> list[str]:
    """Read architecture from a Mach-O or PE header without executing it."""
    if not path.is_file():
        return []
    return _macho_architectures(path) or _pe_architectures(path)


def _java_candidates(root: Path, target: str) -> list[Path]:
    if target == "macos":
        homes = (
            root / "java" / "macarm64" / "jre" / "Contents" / "Home",
            root / "java" / "macosx64" / "jre" / "Contents" / "Home",
            root / "java" / "macarm64" / "jdk" / "Contents" / "Home",
            root / "java" / "macosx64" / "jdk" / "Contents" / "Home",
        )
        exe_name = "java"
    else:
        homes = (
            root / "java" / "win64" / "jre",
            root / "java" / "win64" / "jdk",
        )
        exe_name = "java.exe"
    return [home for home in homes if (home / "bin" / exe_name).is_file()]


def _java_installations(root: Path, target: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    exe_name = "java" if target == "macos" else "java.exe"
    for home in _java_candidates(root, target):
        java_path = home / "bin" / exe_name
        release_path = home / "release"
        release_fields: dict[str, str] = {}
        try:
            for line in release_path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = _JAVA_RELEASE_FIELD.match(line)
                if match:
                    release_fields[match.group(1)] = match.group(2)
        except OSError:
            pass
        architecture = binary_architectures(java_path)
        if not architecture:
            os_arch = release_fields.get("OS_ARCH", "").lower()
            fallback = {"aarch64": "arm64", "arm64": "arm64", "amd64": "x86_64", "x86_64": "x86_64"}.get(os_arch)
            architecture = [fallback] if fallback else []
        version = release_fields.get("JAVA_VERSION")
        rows.append({
            "home": str(home.resolve()),
            "java_executable": str(java_path.resolve()),
            "version": {"status": "OBSERVED" if version else "UNKNOWN", "value": version},
            "architecture": {
                "status": "OBSERVED" if architecture else "UNKNOWN",
                "values": architecture,
                "source": str(java_path.resolve()) if binary_architectures(java_path) else str(release_path),
            },
            "release_metadata": str(release_path) if release_path.is_file() else None,
        })
    return rows


def _is_installation_root(root: Path, target: str) -> bool:
    if not root.is_dir() or not _launcher_candidates(root, target):
        return False
    readme = _readme_release(root)
    has_bundle_metadata = bool(_bundle_versions(root))
    return readme.get("status") == "OBSERVED" or has_bundle_metadata


def _inspect_root(root: Path, target: str) -> dict[str, Any]:
    if not _is_installation_root(root, target):
        raise RuntimeInstallationError("path does not contain a recognizable COMSOL installation")
    canonical = _canonical_directory(root)
    launchers = _launcher_candidates(canonical, target)
    version, build = _version_observation(canonical)
    launcher_rows = [{
        "path": str(path.resolve()),
        "executable": os.access(path, os.X_OK) if target == "macos" else True,
        "architectures": binary_architectures(path),
    } for path in launchers]
    observed_arches = sorted({arch for row in launcher_rows for arch in row["architectures"]})
    java_rows = _java_installations(canonical, target)
    markers = {
        "readme": (canonical / "readme.txt").is_file(),
        "api_plugins": (canonical / "apiplugins").is_dir(),
        "launcher_count": len(launcher_rows),
        "bundled_java_count": len(java_rows),
    }
    return {
        "runtime_id": runtime_id_for_root(canonical),
        "root": str(canonical),
        "platform": target,
        "version": version,
        "build": build,
        "architecture": {"status": "OBSERVED" if observed_arches else "UNKNOWN", "values": observed_arches, "sources": launcher_rows},
        "launchers": launcher_rows,
        "bundled_java": java_rows,
        "markers": markers,
        "metadata_only": True,
        "comsol_started": False,
        "license_checked_out": False,
    }


def _default_roots(target: str, environ: Mapping[str, str]) -> list[Path]:
    roots: list[Path] = []
    configured = environ.get("COMSOL_ROOT")
    if isinstance(configured, str) and configured.strip():
        roots.append(Path(configured).expanduser())
    if target == "macos":
        roots.append(Path("/Applications"))
    else:
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            value = environ.get(key)
            if value:
                roots.append(Path(value))
        roots.append(Path("C:/Program Files/COMSOL"))
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        normalized = os.path.normcase(os.path.abspath(os.fspath(root)))
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(root)
    return deduped


def _candidate_directories(scan_root: Path, target: str, *, explicit: bool) -> Iterable[Path]:
    if _is_installation_root(scan_root, target):
        yield scan_root
    try:
        children = sorted(scan_root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return
    for child in children:
        if child.is_symlink() or not child.is_dir():
            continue
        if not explicit and not child.name.casefold().startswith("comsol"):
            continue
        if _is_installation_root(child, target):
            yield child
        multiphysics = child / "Multiphysics"
        if _is_installation_root(multiphysics, target):
            yield multiphysics


def discover_installations(
    roots: Sequence[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Inspect the current host's known install roots without executing files."""
    target = _target_platform(system)
    if target is None:
        return {"status": "UNSUPPORTED_PLATFORM", "platform": system or platform.system(), "installations": [], "errors": [], "comsol_started": False}
    env = os.environ if environ is None else environ
    explicit = roots is not None
    if roots is not None:
        if not isinstance(roots, Sequence) or isinstance(roots, (str, bytes)):
            raise RuntimeInstallationError("roots must be an array of absolute paths")
        if any(not isinstance(item, str) or not item.strip() for item in roots):
            raise RuntimeInstallationError("every roots item must be a non-empty absolute path")
        search_roots = [Path(item).expanduser() for item in roots]
    else:
        search_roots = _default_roots(target, env)

    errors: list[dict[str, str]] = []
    found: dict[str, dict[str, Any]] = {}
    scanned: list[dict[str, str]] = []
    for requested in search_roots:
        try:
            scan_root = _canonical_directory(requested)
        except RuntimeInstallationError as exc:
            errors.append({"path": str(requested), "status": "UNKNOWN", "reason": str(exc)})
            continue
        scanned.append({"path": str(scan_root), "status": "SCANNED"})
        try:
            candidates = list(_candidate_directories(scan_root, target, explicit=explicit))
        except OSError as exc:
            errors.append({"path": str(scan_root), "status": "UNKNOWN", "reason": type(exc).__name__})
            continue
        for candidate in candidates:
            try:
                row = _inspect_root(candidate, target)
            except (OSError, RuntimeInstallationError) as exc:
                errors.append({"path": str(candidate), "status": "UNKNOWN", "reason": type(exc).__name__})
                continue
            found[row["runtime_id"]] = row
    rows = [found[key] for key in sorted(found)]
    if errors:
        status = "PARTIAL" if rows else "UNKNOWN"
    elif rows:
        status = "OBSERVED"
    else:
        status = "NOT_FOUND"
    return {
        "schema_version": "comsol-mcp.runtime-discover/1.0.0",
        "status": status,
        "platform": target,
        "host_architecture": platform.machine(),
        "roots_examined": scanned,
        "errors": errors,
        "installations": rows,
        "comsol_started": False,
        "license_checked_out": False,
    }


def inspect_installation(runtime_id: str, *, system: str | None = None) -> dict[str, Any]:
    target = _target_platform(system)
    if target is None:
        raise RuntimeInstallationError("runtime inspection is supported only on the Windows and macOS targets")
    root = _root_from_runtime_id(runtime_id)
    return {
        "schema_version": "comsol-mcp.runtime-inspect/1.0.0",
        "status": "OBSERVED",
        "installation": _inspect_root(root, target),
    }


def doctor_installation(
    runtime_id: str | None = None,
    checks: Sequence[str] | None = None,
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    target = _target_platform(system)
    selected_checks = list(checks) if checks else ["installation", "version", "build", "launcher", "architecture", "java", "directories", "configuration", "license", "port", "render"]
    if any(not isinstance(name, str) or name not in _SAFE_DOCTOR_CHECKS for name in selected_checks):
        bad = sorted(str(name) for name in selected_checks if not isinstance(name, str) or name not in _SAFE_DOCTOR_CHECKS)
        raise RuntimeInstallationError("unsupported doctor checks: " + ", ".join(bad))
    if target is None:
        return {"schema_version": "comsol-mcp.runtime-doctor/1.0.0", "status": "UNKNOWN", "platform": system or platform.system(), "checks": [], "comsol_started": False}
    env = os.environ if environ is None else environ
    if runtime_id:
        installation = inspect_installation(runtime_id, system=system)["installation"]
        candidates = [installation]
    else:
        candidates = discover_installations(system=system, environ=env)["installations"]
    if not candidates:
        return {
            "schema_version": "comsol-mcp.runtime-doctor/1.0.0",
            "status": "UNKNOWN",
            "platform": target,
            "installation": None,
            "checks": [{"name": name, "status": "UNKNOWN", "reason": "no recognizable local installation was found"} for name in selected_checks],
            "comsol_started": False,
        }
    rows = []
    for installation in candidates:
        version = installation["version"]
        build = installation["build"]
        launcher_ok = any(row["executable"] for row in installation["launchers"])
        java = installation["bundled_java"]
        checks_out: list[dict[str, Any]] = []
        for name in selected_checks:
            if name == "installation":
                row = {"name": name, "status": "PASS", "evidence": installation["markers"]}
            elif name == "version":
                row = {"name": name, "status": "PASS" if version["status"] == "OBSERVED" else "UNKNOWN", "value": version["value"], "evidence": version["sources"] if version["status"] == "OBSERVED" else []}
            elif name == "build":
                row = {"name": name, "status": "PASS" if build["status"] == "OBSERVED" else "UNKNOWN", "value": build["value"], "evidence": build["source"]}
            elif name == "launcher":
                row = {"name": name, "status": "PASS" if launcher_ok else "UNKNOWN", "evidence": installation["launchers"]}
            elif name == "architecture":
                row = {"name": name, "status": "PASS" if installation["architecture"]["status"] == "OBSERVED" else "UNKNOWN", "evidence": installation["architecture"]}
            elif name == "java":
                observed = bool(java) and all(item["version"]["status"] == "OBSERVED" and item["architecture"]["status"] == "OBSERVED" for item in java)
                row = {"name": name, "status": "PASS" if observed else "UNKNOWN", "evidence": java}
            elif name == "directories":
                root_path = Path(installation["root"])
                row = {"name": name, "status": "PASS" if root_path.is_dir() and os.access(root_path, os.R_OK) else "UNKNOWN", "readable": root_path.is_dir() and os.access(root_path, os.R_OK), "write_test": "NOT_RUN"}
            elif name == "configuration":
                configured_root = env.get("COMSOL_ROOT")
                configured_java = env.get("COMSOL_JAVA_HOME") or env.get("JAVA_HOME")
                row = {"name": name, "status": "OBSERVED" if configured_root or configured_java else "NOT_CONFIGURED", "comsol_root_set": bool(configured_root), "java_home_set": bool(configured_java), "values_redacted": True}
            else:
                row = {"name": name, "status": "NOT_RUN", "reason": {"license": "use runtime.license_inspect; no seat is checked out by doctor", "port": "no endpoint was supplied", "render": "requires runtime.render_probe with explicit authorization and a finite execution budget"}[name]}
            checks_out.append(row)
        static_rows = [row for row in checks_out if row["status"] not in {"NOT_RUN", "NOT_CONFIGURED"}]
        status = "PASS_STATIC" if static_rows and all(row["status"] == "PASS" for row in static_rows) else "UNKNOWN"
        rows.append({"runtime_id": installation["runtime_id"], "status": status, "checks": checks_out})
    overall = "PASS_STATIC" if all(item["status"] == "PASS_STATIC" for item in rows) else "UNKNOWN"
    return {"schema_version": "comsol-mcp.runtime-doctor/1.0.0", "status": overall, "platform": target, "installations": rows, "comsol_started": False, "license_checked_out": False}


def _version_tuple(value: str) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", value):
        return None
    parts = tuple(int(part) for part in value.split("."))
    # Numeric version precision is not meaningful when the remaining
    # components are all zero: 6.4 and 6.4.0 compare as the same version.
    # COMSOL's fourth README component is split into the separate build field
    # before this comparison and is never folded into the product version.
    end = len(parts)
    while end > 1 and parts[end - 1] == 0:
        end -= 1
    return parts[:end]


def compatibility_report(runtime_ids: Sequence[str], requirements: Mapping[str, Any], *, system: str | None = None) -> dict[str, Any]:
    if not isinstance(runtime_ids, Sequence) or isinstance(runtime_ids, (str, bytes)):
        raise RuntimeInstallationError("runtime_ids must be an array of installation identifiers")
    if not isinstance(requirements, Mapping):
        raise RuntimeInstallationError("requirements must be an object")
    unknown_fields = sorted(set(requirements) - _COMPATIBILITY_REQUIREMENTS)
    rows = []
    for runtime_id in runtime_ids:
        try:
            installation = inspect_installation(runtime_id, system=system)["installation"]
        except (RuntimeInstallationError, OSError) as exc:
            rows.append({"runtime_id": runtime_id, "status": "UNKNOWN", "reason": type(exc).__name__, "checks": []})
            continue
        checks_out: list[dict[str, Any]] = []
        version = installation["version"]
        build = installation["build"]
        if "minimum_version" in requirements:
            actual = _version_tuple(version.get("value")) if version["status"] == "OBSERVED" else None
            required = _version_tuple(requirements["minimum_version"])
            checks_out.append({"name": "minimum_version", "status": "UNKNOWN" if actual is None or required is None else "PASS" if actual >= required else "INCOMPATIBLE", "actual": version.get("value"), "required": requirements["minimum_version"]})
        if "maximum_version" in requirements:
            actual = _version_tuple(version.get("value")) if version["status"] == "OBSERVED" else None
            required = _version_tuple(requirements["maximum_version"])
            checks_out.append({"name": "maximum_version", "status": "UNKNOWN" if actual is None or required is None else "PASS" if actual <= required else "INCOMPATIBLE", "actual": version.get("value"), "required": requirements["maximum_version"]})
        if "minimum_build" in requirements:
            actual_build = build.get("value") if build["status"] == "OBSERVED" else None
            minimum = requirements["minimum_build"]
            valid = isinstance(minimum, int) and not isinstance(minimum, bool) and minimum >= 0
            checks_out.append({"name": "minimum_build", "status": "UNKNOWN" if actual_build is None or not valid else "PASS" if actual_build >= minimum else "INCOMPATIBLE", "actual": actual_build, "required": minimum})
        if "architectures" in requirements:
            required_arches = requirements["architectures"]
            valid = isinstance(required_arches, list) and all(isinstance(item, str) and item for item in required_arches)
            actual_arches = installation["architecture"]["values"] if installation["architecture"]["status"] == "OBSERVED" else None
            checks_out.append({"name": "architectures", "status": "UNKNOWN" if actual_arches is None or not valid else "PASS" if set(required_arches).issubset(actual_arches) else "INCOMPATIBLE", "actual": actual_arches, "required": required_arches})
        if "minimum_java_version" in requirements:
            minimum = _version_tuple(requirements["minimum_java_version"])
            observed = [
                parsed for item in installation["bundled_java"]
                if item["version"]["status"] == "OBSERVED"
                for parsed in [_version_tuple(item["version"]["value"])]
                if parsed is not None
            ]
            actual = max(observed) if observed else None
            valid = minimum is not None
            checks_out.append({"name": "minimum_java_version", "status": "UNKNOWN" if actual is None or not valid else "PASS" if actual >= minimum else "INCOMPATIBLE", "actual": ".".join(map(str, actual)) if actual else None, "required": requirements["minimum_java_version"]})
        for field in unknown_fields:
            checks_out.append({"name": field, "status": "UNVERIFIED", "reason": "requirement field is outside the supported static compatibility contract"})
        statuses = [check["status"] for check in checks_out]
        status = "INCOMPATIBLE" if "INCOMPATIBLE" in statuses else "UNVERIFIED" if any(item in {"UNKNOWN", "UNVERIFIED"} for item in statuses) else "COMPATIBLE" if statuses else "UNVERIFIED"
        rows.append({"runtime_id": runtime_id, "status": status, "checks": checks_out, "evidence": {"version": version, "build": build, "architecture": installation["architecture"], "bundled_java": installation["bundled_java"]}})
    if not rows:
        overall = "UNVERIFIED"
    elif any(item["status"] == "INCOMPATIBLE" for item in rows):
        overall = "INCOMPATIBLE"
    elif any(item["status"] != "COMPATIBLE" for item in rows):
        overall = "UNVERIFIED"
    else:
        overall = "COMPATIBLE"
    return {"schema_version": "comsol-mcp.runtime-compatibility/1.0.0", "status": overall, "requirements": dict(requirements), "supported_requirements": sorted(_COMPATIBILITY_REQUIREMENTS), "runtimes": rows, "comsol_started": False, "license_checked_out": False, "render_tested": False}


__all__ = [
    "RuntimeInstallationError",
    "binary_architectures",
    "compatibility_report",
    "discover_installations",
    "doctor_installation",
    "inspect_installation",
    "runtime_id_for_root",
]
