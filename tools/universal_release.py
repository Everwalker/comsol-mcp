"""Build a normal offline release from a fresh, dynamically hashed source snapshot.

Example: python -m tools.universal_release --source . --lock uv.lock
  --builder-python /path/to/builder/bin/python --uv /path/to/uv
  --operations docs/full_project_execution/release/RELEASE_OPERATIONS.md
  --wheelhouse macos_arm64=/path/to/prepared/wheels --output /new/output

Installation, engine control and lifecycle transitions remain in full_project_release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import full_project_release as release


def publish(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def environment(target):
    python = release.TARGETS[target]["python"]
    return {**default_environment(), **release.TARGETS[target]["markers"],
            "implementation_name": "cpython", "platform_python_implementation": "CPython",
            "implementation_version": python + ".0", "python_version": python,
            "python_full_version": python + ".0", "extra": ""}


def active_requirements(texts, env):
    result = set()
    for text in texts:
        req = Requirement(text)
        if req.url:
            raise release.ReleaseError("direct URL project dependencies are not offline lock pins")
        if not req.marker or req.marker.evaluate(env):
            result.add((release.normalized_name(req.name), str(req.specifier), tuple(sorted(req.extras))))
    return result


def validate_metadata(project, name, version, requirements, extras):
    """Compare semantic dependency metadata across all targets and declared extras."""
    if release.normalized_name(name) != release.normalized_name(project["name"]) or version != project["version"]:
        raise release.ReleaseError("project and distribution name/version metadata mismatch")
    optional = project.get("optional-dependencies", {})
    if set(extras) != set(optional):
        raise release.ReleaseError("project optional dependency metadata omission/mismatch")
    for target in release.TARGETS:
        for extra in ("", *optional):
            expected = project.get("dependencies", []) + optional.get(extra, [])
            env = {**environment(target), "extra": extra}
            if active_requirements(expected, env) != active_requirements(requirements, env):
                raise release.ReleaseError(f"dependency metadata mismatch: {target}/{extra or 'base'}")


def validate_lock(project, lock):
    rows = [p for p in lock.get("package", []) if p["name"] == release.normalized_name(project["name"])]
    if len(rows) != 1 or lock.get("requires-python") != project.get("requires-python"):
        raise release.ReleaseError("project/source lock identity mismatch")
    root = rows[0]
    metadata = root.get("metadata", {})
    texts = []
    for row in metadata.get("requires-dist", []):
        extra = "[" + ",".join(row["extras"]) + "]" if row.get("extras") else ""
        texts.append(row["name"] + extra + row.get("specifier", "") +
                     ("; " + row["marker"] if row.get("marker") else ""))
    validate_metadata(project, root["name"], root["version"], texts, metadata.get("provides-extras", []))
    for row in lock["package"]:
        if row is not root and row.get("source") != {"registry": "https://pypi.org/simple"}:
            raise release.ReleaseError("source lock requires an unapproved registry or local dependency")


def source_files(root):
    files = {}
    for p in (root / "comsol_mcp").rglob("*"):
        rel = p.relative_to(root)
        if p.is_symlink():
            raise release.ReleaseError("source snapshot contains a symlink")
        if any(x.startswith("._") or x in {"__pycache__", ".pytest_cache"} for x in rel.parts) or p.suffix in {".pyc", ".pyo"}:
            continue
        if p.is_file():
            files[rel.as_posix()] = p
    files.update({name: root / name for name in release.SOURCE_ROOT_FILES})
    return files


def verify_snapshot(root, snapshot):
    files = source_files(root)
    if set(files) != {r["path"] for r in snapshot["files"]}:
        raise release.ReleaseError("source snapshot content drift: member set")
    for row in snapshot["files"]:
        path = files[row["path"]]
        if path.is_symlink() or path.stat().st_size != row["bytes"] or release.sha256_file(path) != row["sha256"]:
            raise release.ReleaseError("source snapshot content drift: " + row["path"])


def validate_wheel(project, wheel, snapshot):
    meta = release.wheel_metadata(wheel)
    with zipfile.ZipFile(wheel) as archive:
        headers = BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))))
        validate_metadata(project, meta["name"], meta["version"], meta["requires_dist"], headers.get_all("Provides-Extra", []))
        for row in snapshot["files"]:
            if row["path"].startswith("comsol_mcp/"):
                try:
                    payload = archive.read(row["path"])
                except KeyError as exc:
                    raise release.ReleaseError("source package member omitted from wheel: " + row["path"]) from exc
                if hashlib.sha256(payload).hexdigest() != row["sha256"]:
                    raise release.ReleaseError("source/wheel content mismatch: " + row["path"])
    return meta


def dependency_graph(project, by_name, target):
    todo = [("project", text, "desktop") for text in project.get("dependencies", []) + project.get("optional-dependencies", {}).get("desktop", [])]
    seen = set()
    missing = set()
    edges = []
    while todo:
        parent, text, extra = todo.pop()
        req = Requirement(text)
        if req.url:
            raise release.ReleaseError("dependency graph contains a direct URL")
        if req.marker and not req.marker.evaluate({**environment(target), "extra": extra}):
            continue
        name = release.normalized_name(req.name)
        if name not in by_name:
            missing.add(name)
            continue
        meta = by_name[name]["metadata"]
        if meta["version"] not in req.specifier:
            raise release.ReleaseError("dependency graph version mismatch: " + text)
        edges.append({"parent": parent, "requirement": text, "name": name})
        for child_extra in {"", *req.extras}:
            key = (name, child_extra)
            if key not in seen:
                seen.add(key)
                todo.extend((name, dep, child_extra) for dep in meta["requires_dist"])
    reachable = {name for name, _ in seen}
    unresolved = sorted(set(by_name) - reachable)
    # Without a missing parent's metadata its transitive edges are unknown.
    # Keep an incomplete input set inspectable, but never package that set.
    if unresolved and not missing:
        raise release.ReleaseError("unreachable distributions in target closure: " + ",".join(unresolved))
    return {"missing": sorted(missing), "reachable": sorted(reachable),
            "unresolved_reachability": unresolved, "edges": edges}


def audit_inputs(project, requirements, house, target, derived=None):
    pins = release.parse_hash_requirements(requirements, target)
    paths = sorted(house.glob("*.whl"))
    if derived:
        paths.append(derived)
    rows = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise release.ReleaseError("wheel input is not a regular file")
        meta = release.wheel_metadata(path)
        name = meta["normalized_name"]
        if name in rows:
            raise release.ReleaseError("duplicate target distribution: " + name)
        if not release.wheel_target_check(path.name, target) or not release.wheel_tag_identity_matches(path, meta):
            raise release.ReleaseError("target wheel tag mismatch: " + path.name)
        dist, version, _ = release._wheel_dist_version(path)
        pin = pins.get(name)
        if name != dist or meta["version"] != version or not pin or pin["version"] != version:
            raise release.ReleaseError("target wheel identity/lock mismatch: " + path.name)
        digest = release.sha256_file(path)
        if digest not in pin["hashes"]:
            raise release.ReleaseError("target wheel hash mismatch: " + path.name)
        rows[name] = {"path": path, "sha256": digest, "metadata": meta}
    graph = dependency_graph(project, rows, target)
    missing = sorted(set(pins) - set(rows) | set(graph["missing"]))
    return pins, rows, graph, missing


def build_target(project, target, house, exported, lock, app, output, build_evidence, operations,
                 derived=None, provenance=None, openssl_license=None, primary_notices=None):
    output.mkdir()
    basis = exported
    options = {}
    if derived:
        basis = output / "requirements-derived.lock"
        release.compose_derived_wheel_lock(exported, derived, lock, provenance, openssl_license, target, basis)
        options = {"derived_provenance": provenance, "openssl_license": openssl_license}
    pins, rows, graph, missing = audit_inputs(project, basis, house, target, derived)
    summary = {"status": "MISSING_INPUT" if missing else "INPUTS_VERIFIED", "target": target,
               "expected_dependency_count": len(pins), "dependency_count": len(rows), "missing": missing,
               "graph": graph, "target_install": "NOT_RUN", "native_engine": "NOT_RUN"}
    if missing:
        publish(output / "TARGET.json", summary)
        return summary
    runtime_lock = output / "requirements-runtime.lock"
    with runtime_lock.open("x") as stream:
        stream.write("# Generated from official uv export and exact verified target wheels.\n")
        for name, row in sorted(rows.items()):
            stream.write(f"{name}=={pins[name]['version']} --hash=sha256:{row['sha256']}\n")
    wheelhouse = output / "wheelhouse"
    wheelhouse.mkdir()
    for row in rows.values():
        destination = wheelhouse / row["path"].name
        shutil.copyfile(row["path"], destination)
        if release.sha256_file(destination) != row["sha256"]:
            raise release.ReleaseError("target input changed during copy")
    shutil.copyfile(app, wheelhouse / app.name)
    install_lock = output / "requirements.lock"
    release.compose_install_lock(runtime_lock, app, target, install_lock)
    if primary_notices and target != "win_amd64":
        options["primary_notice_manifest"] = primary_notices
    manifest = release.create_bundle_manifest(wheelhouse, target, install_lock, lock, output / "metadata", **options)
    if derived:
        summary["derived_input"] = manifest["derived_wheel"]
    bundle = release.package_wheelhouse_bundle(wheelhouse, install_lock, lock, output / "metadata", operations,
                                               target, output / "offline.zip", output / "evidence", build_evidence, **options)
    summary.update(status="PACKAGED_NATIVE_UNVERIFIED", bundle=bundle, wheel_count=manifest["wheel_count"],
                   requirements_sha256=release.sha256_file(install_lock))
    publish(output / "TARGET.json", summary)
    return summary


def run(args):
    source = args.source.resolve(strict=True)
    lock = args.lock.resolve(strict=True)
    project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
    validate_lock(project, tomllib.loads(lock.read_text()))
    output = args.output.absolute()
    if output.parent.resolve(strict=True) != output.parent or output.exists() or output.is_symlink():
        raise release.ReleaseError("output must be a fresh path under a canonical existing parent; no overwrite")
    derived_args = (args.derived_intel_wheel, args.derived_provenance, args.openssl_license)
    if any(derived_args) and not all(derived_args):
        raise release.ReleaseError("derived Intel input requires wheel, provenance and OpenSSL license")
    output.mkdir(mode=0o700)
    try:
        snapshot = release.source_snapshot(source, output / "source")
        publish(output / "SOURCE.json", snapshot)
        for path in source_files(output / "source").values():
            path.chmod(0o444)
        for path in sorted((output / "source").rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o555)
        (output / "source").chmod(0o555)
        inputs = output / "inputs"
        inputs.mkdir()
        shutil.copyfile(lock, inputs / "uv.lock")
        shutil.copyfile(output / "source/pyproject.toml", inputs / "pyproject.toml")
        exported = inputs / "requirements-export.lock"
        export = release.run_record([str(args.uv.resolve(strict=True)), "export", "--directory", str(inputs),
                                     "--locked", "--offline", "--no-config", "--no-dev", "--no-default-groups",
                                     "--extra", "desktop", "--no-emit-project", "--no-annotate", "--no-header",
                                     "--format", "requirements.txt", "--output-file", str(exported),
                                     "--cache-dir", str(output / "uv-cache"), "--python", str(args.builder_python.absolute())])
        publish(output / "uv-export.json", export)
        if export["exit_code"]:
            raise release.ReleaseError("official uv locked export failed; see uv-export.json")
        verify_snapshot(output / "source", snapshot)
        build = release.build_wheel(output / "source", output / "wheel", args.builder_python.absolute(), output / "build-evidence")
        verify_snapshot(output / "source", snapshot)
        if build["source_manifest_sha256"] != snapshot["manifest_sha256"]:
            raise release.ReleaseError("wheel build source snapshot identity drift")
        app = output / "wheel" / build["wheel"]
        validate_wheel(project, app, snapshot)
        results = {}
        for target, house in args.wheelhouse.items():
            derived = args.derived_intel_wheel if target == "macos_x86_64" else None
            results[target] = build_target(project, target, house, exported, inputs / "uv.lock", app,
                output / target, output / "build-evidence", args.operations.resolve(strict=True), derived,
                args.derived_provenance, args.openssl_license, args.primary_notices)
        verify_snapshot(output / "source", snapshot)
        result = {"schema": "comsol-mcp-universal-release/1", "source_manifest_sha256": snapshot["manifest_sha256"],
                  "source_file_count": snapshot["file_count"], "wheel": build, "targets": results,
                  "source_lock_sha256": release.sha256_file(inputs / "uv.lock"),
                  "release_tool_sha256": release.sha256_file(Path(__file__)),
                  "release_api_sha256": release.sha256_file(Path(release.__file__)), "native_engine": "NOT_RUN"}
        publish(output / "RELEASE.json", result)
        return result
    except Exception as exc:
        publish(output / "FAILURE.json", {"type": type(exc).__name__, "error": str(exc)})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("source", "lock", "builder-python", "uv", "operations", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--wheelhouse", action="append", required=True, metavar="TARGET=PATH")
    for name in ("derived-intel-wheel", "derived-provenance", "openssl-license", "primary-notices"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    houses = {}
    for value in args.wheelhouse:
        target, separator, path = value.partition("=")
        if not separator or target not in release.TARGETS or target in houses:
            parser.error("wheelhouse must specify a unique supported TARGET=PATH")
        houses[target] = Path(path).resolve(strict=True)
    args.wheelhouse = houses
    try:
        result = run(args)
    except (OSError, ValueError, release.ReleaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(args.output.absolute() / "RELEASE.json")
    return 2 if any(t["status"] == "MISSING_INPUT" for t in result["targets"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
