"""Host CLI-only fresh runtime/source binding. This grants no MCP privileges."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import UUID

SCHEMA_V1 = "FRESH_NATIVE_RUNTIME_SOURCE_V1"
SCHEMA_V2 = "FRESH_NATIVE_RUNTIME_SOURCE_V2"
# Keep the public compatibility names on V1. W23 and existing callers remain
# V1 unless they explicitly request the new launcher-identity binding.
SCHEMA = SCHEMA_V1
KEYS_V1 = {"schema", "activity_id", "namespace", "source", "python", "comsol", "jdk", "budget", "binding_sha256"}
KEYS_V2 = KEYS_V1 | {"runtime_argv0"}
KEYS = KEYS_V1

class BindingError(ValueError):
    pass

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise BindingError("duplicate JSON key")
        result[key] = value
    return result

def read_owned(path: Path) -> bytes:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise BindingError("noncanonical owner-bound file")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
        raise BindingError("not an ordinary owner-bound file")
    identity = lambda s: (s.st_dev,s.st_ino,s.st_uid,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        opened = os.fstat(stream.fileno()); raw = stream.read(); after = os.fstat(stream.fileno())
    if any(identity(before) != identity(s) for s in (opened, after, path.lstat())):
        raise BindingError("file changed during same-FD read")
    return raw

def namespace(path: Path):
    if path.resolve(strict=True) != path or path.parent != Path("/private/tmp"):
        raise BindingError("namespace must be a canonical direct /private/tmp child")
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise BindingError("namespace must be owned mode 0700 directory")

def _capture_runtime_argv0(*, repo: Path,
                           expected_module: str | None) -> dict[str, str]:
    """Capture the actual no-site runtime binary, optionally pinning creation's module entry."""
    argv = getattr(sys, "orig_argv", None)
    if (not getattr(sys.flags, "no_site", False) or not isinstance(argv, list)
            or len(argv) < 2 or not all(isinstance(item, str) for item in argv[:2])
            or argv[1] != "-S"):
        raise BindingError("V2 runtime identity requires an explicit no-site -S caller")
    try:
        launcher = Path(argv[0]).resolve(strict=True)
    except (IndexError, OSError, RuntimeError) as exc:
        raise BindingError("V2 launcher path is unavailable") from exc
    if not launcher.is_file():
        raise BindingError("V2 original argv0 is not a runtime binary file")
    if expected_module is not None:
        if len(argv) < 4 or argv[1:4] != ["-S", "-m", expected_module]:
            raise BindingError("V2 binding creation requires the exact no-site -m caller")
        try:
            actual_script = Path(sys.argv[0]).resolve(strict=True)
        except (IndexError, OSError, RuntimeError) as exc:
            raise BindingError("V2 module caller path is unavailable") from exc
        expected_script = repo.resolve(strict=True) / (expected_module.replace(".", "/") + ".py")
        if actual_script != expected_script:
            raise BindingError("V2 binding creation module path differs")
    return {
        "source": "sys.orig_argv[0]",
        "path": str(launcher),
        "sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
    }

def source_inventory(repo: Path):
    root = repo.resolve(strict=True)
    files = {}
    for folder in ("comsol_mcp", "tools", "tests"):
        for p in sorted((root / folder).rglob("*")):
            if p.suffix not in {".py", ".java", ".json"} or "__pycache__" in p.parts:
                continue
            raw = read_owned(p)
            files[p.relative_to(root).as_posix()] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    documents = list((root / "docs/comsol_mcp_design_v1").rglob("*.json"))
    documents += [root / "docs/full_project_execution/w23_overlap/W23_TE_FIXTURE_PROPOSAL.md",
                  root / "docs/full_project_execution/w24/W24_STATIC_SHAPE_IMPLEMENTATION_DRAFT.md"]
    for path in sorted(documents):
        if path.is_file():
            raw = read_owned(path)
            files[path.relative_to(root).as_posix()] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    raw = read_owned(root / "pyproject.toml")
    files["pyproject.toml"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return {"root": str(root), "files": files, "sha256": digest(files)}

def observe(python: Path, packages: Path, install: Path, jdk: Path, repo: Path,
            *, runtime_argv0_module: str | None = None,
            validate_runtime_argv0: bool = False):
    from comsol_mcp._java_worker import JavaWorkerPaths
    if python.resolve(strict=True) != Path(sys.executable).resolve(strict=True) or sys.version_info[:2] != (3, 12):
        raise BindingError("declared Python differs from actual Python 3.12 interpreter")
    if str(python) != str(python.absolute()) or python.parent.name != "bin":
        raise BindingError("Python entry must be an explicit absolute bin entry")
    packages = packages.resolve(strict=True)
    prefix = python.parent.parent.resolve(strict=True)
    if not packages.is_dir() or not packages.is_relative_to(prefix) or packages.name != "site-packages":
        raise BindingError("site packages outside declared Python prefix")
    readme = install / "readme.txt"
    readme_bytes = readme.read_bytes()
    match = re.search(rb"COMSOL ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+) README", readme_bytes)
    if match is None or match.group(1) != b"6.4.0.293":
        raise BindingError("installed COMSOL readme must identify exact build 6.4.0.293")
    paths = JavaWorkerPaths(install, jdk, project_root=repo)
    jdk_identity = paths.jdk_version_info()
    if not jdk_identity.get("version", "").startswith("11."):
        raise BindingError("external JDK must be version 11")
    metadata = {str(p.relative_to(packages)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(packages.glob("*.dist-info/*")) if p.name in {"METADATA", "RECORD"} and p.is_file()}
    _, manifest, count, jars = paths.classpath()
    result = {
        "python": {"entry": str(python), "resolved": str(python.resolve(strict=True)), "prefix": str(prefix), "observed_prefix": str(Path(sys.prefix).resolve()), "version": sys.version.split()[0], "site_packages": str(packages), "no_site": bool(sys.flags.no_site), "executable_sha256": hashlib.sha256(python.resolve(strict=True).read_bytes()).hexdigest(), "distribution_metadata_sha256": digest(metadata)},
        "comsol": {"root": str(install.resolve(strict=True)), "version": paths.comsol_version_info(), "exact_build": match.group(1).decode(), "readme_sha256": hashlib.sha256(readme_bytes).hexdigest(), "classpath_manifest_sha256": manifest, "jar_count": count, "jar_content_sha256": jars},
        "jdk": {"home": str(jdk.resolve(strict=True)), "version": jdk_identity},
    }
    if runtime_argv0_module is not None or validate_runtime_argv0:
        result["runtime_argv0"] = _capture_runtime_argv0(
            repo=repo, expected_module=runtime_argv0_module)
    return result

def create(*, repo, authority, activity_id, python, packages, install, jdk, budget,
           schema=SCHEMA_V1):
    # UUID is a fresh activity identity; historical numbered identities cannot be replayed.
    if str(UUID(activity_id)) != activity_id:
        raise BindingError("activity must be a canonical fresh UUID")
    if schema not in {SCHEMA_V1, SCHEMA_V2}:
        raise BindingError("unsupported runtime binding schema")
    namespace(authority.parent)
    runtime_module = "tools.native_runtime_binding" if schema == SCHEMA_V2 else None
    observed = observe(python, packages, install, jdk, repo,
                       runtime_argv0_module=runtime_module)
    body = {"schema": schema, "activity_id": activity_id, "namespace": str(authority.parent), "source": source_inventory(repo), **observed, "budget": budget}
    packet = {**body, "binding_sha256": digest(body)}
    with authority.open("xb") as stream:
        stream.write(json.dumps(packet, sort_keys=True, indent=2, allow_nan=False).encode()); stream.flush(); os.fsync(stream.fileno())
    os.chmod(authority, 0o600)
    return packet

def validate(packet, *, repo, budget, install=None, jdk=None):
    schema = packet.get("schema") if isinstance(packet, dict) else None
    expected_keys = KEYS_V1 if schema == SCHEMA_V1 else KEYS_V2 if schema == SCHEMA_V2 else None
    if not isinstance(packet, dict) or expected_keys is None or set(packet) != expected_keys:
        raise BindingError("fresh binding schema is closed")
    if str(UUID(packet["activity_id"])) != packet["activity_id"]:
        raise BindingError("fresh activity UUID required")
    body = {k:v for k,v in packet.items() if k != "binding_sha256"}
    if packet["binding_sha256"] != digest(body) or packet["budget"] != budget:
        raise BindingError("immutable binding hash or phase budget differs")
    namespace(Path(packet["namespace"]))
    if packet["source"] != source_inventory(repo):
        raise BindingError("current source closure differs")
    install = install or Path(packet["comsol"]["root"])
    jdk = jdk or Path(packet["jdk"]["home"])
    current = observe(Path(packet["python"]["entry"]), Path(packet["python"]["site_packages"]),
                      install, jdk, repo,
                      validate_runtime_argv0=(schema == SCHEMA_V2))
    if any(packet[k] != current[k] for k in current):
        raise BindingError("Python/COMSOL/JDK identity differs from immutable binding")
    return packet

def load(path, *, repo, budget, install=None, jdk=None):
    packet = json.loads(read_owned(path), object_pairs_hook=_pairs)
    if path.parent != Path(packet.get("namespace", "")):
        raise BindingError("authority outside frozen namespace")
    return validate(packet, repo=repo, budget=budget, install=install, jdk=jdk)

def scoped_path(packet, path, *, absent=False):
    root = Path(packet["namespace"])
    namespace(root)
    if path.parent != root or path.resolve(strict=False) != path or path.is_symlink():
        raise BindingError("path outside exact fresh namespace")
    if absent and path.exists():
        raise BindingError("fresh path already exists")
    return path

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, type=Path); p.add_argument("--authority", required=True, type=Path)
    p.add_argument("--activity-id", required=True); p.add_argument("--python", required=True, type=Path)
    p.add_argument("--site-packages", required=True, type=Path); p.add_argument("--comsol-root", required=True, type=Path)
    p.add_argument("--jdk11", required=True, type=Path); p.add_argument("--phase", choices=("w23-readback", "w24-setup"), required=True)
    args = p.parse_args(argv)
    if args.phase == "w24-setup":
        from tools.run_native_w24_static_shape_setup_campaign import SETUP_BUDGET
        budget = SETUP_BUDGET
    else:
        from tools.run_native_w23_saved_mph_diagnostic import fresh_budget
        budget = fresh_budget()
    schema = SCHEMA_V2 if args.phase == "w24-setup" else SCHEMA_V1
    result = create(repo=args.repo, authority=args.authority, activity_id=args.activity_id, python=args.python, packages=args.site_packages, install=args.comsol_root, jdk=args.jdk11, budget=budget, schema=schema)
    print(json.dumps({"schema": result["schema"], "binding_sha256": result["binding_sha256"], "native": "NOT_RUN", "authority": str(args.authority)}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
