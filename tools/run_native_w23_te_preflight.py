#!/usr/bin/env python3
"""Compile the W23 native TE fixture against the installed COMSOL API only.

This runner intentionally does not start a COMSOL process, connect to a server,
build a model, or call a study/solver. The compiled Java fixture is invoked
later through the managed Worker only after the shared native slot is assigned.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
INSTALL_ROOT = Path("/Applications/COMSOL64/Multiphysics")
JAVA11 = Path("/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home")
FIXTURE_SOURCE = REPO / "tools/java/NativeW23TEFixture.java"
RUNNER_SOURCE = Path(__file__).resolve()
EVIDENCE_ROOT = REPO / "docs/full_project_execution/w23_overlap/evidence"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def make_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = EVIDENCE_ROOT / f"api_preflight_checkpoint_{stamp}"
    suffix = 1
    while path.exists():
        path = EVIDENCE_ROOT / f"api_preflight_checkpoint_{stamp}_{suffix:02d}"
        suffix += 1
    path.mkdir(parents=True)
    return path


def main() -> int:
    output = make_output_dir()
    class_dir = output / "classes"
    class_dir.mkdir()
    javac = JAVA11 / "bin/javac"
    javap = JAVA11 / "bin/javap"
    if not javac.is_file() or not javap.is_file():
        discovered = shutil.which("javac")
        javac = Path(discovered) if discovered else javac
        javap_found = shutil.which("javap")
        javap = Path(javap_found) if javap_found else javap
    if not javac.is_file() or not javap.is_file():
        print(f"JDK tools unavailable: javac={javac}; javap={javap}", file=sys.stderr)
        return 2

    # Reuse the task's official COMSOL client manifest resolver; this only
    # reads the classpath manifest and hashes installed API JARs.
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from comsol_mcp._java_worker import JavaWorkerPaths

    paths = JavaWorkerPaths(INSTALL_ROOT, JAVA11, project_root=REPO)
    classpath, manifest_hash, jar_count, jar_content_hash = paths.classpath()
    compile_command = [
        str(javac), "-encoding", "UTF-8", "-classpath", classpath,
        "-d", str(class_dir), str(FIXTURE_SOURCE),
    ]
    start = datetime.now(timezone.utc).isoformat()
    compile_result = subprocess.run(compile_command, cwd=REPO, text=True, capture_output=True)
    (output / "javac.stdout.txt").write_text(compile_result.stdout, encoding="utf-8")
    (output / "javac.stderr.txt").write_text(compile_result.stderr, encoding="utf-8")

    javap_result = None
    if compile_result.returncode == 0:
        inspect_cp = classpath + os.pathsep + str(class_dir)
        javap_result = subprocess.run(
            [str(javap), "-classpath", inspect_cp, "NativeW23TEFixture"],
            cwd=REPO, text=True, capture_output=True,
        )
        (output / "javap.stdout.txt").write_text(javap_result.stdout, encoding="utf-8")
        (output / "javap.stderr.txt").write_text(javap_result.stderr, encoding="utf-8")

    classes = {}
    for path in sorted(class_dir.rglob("*.class")):
        # AppleDouble sidecars are filesystem metadata, not compiler outputs.
        if path.name.startswith("._"):
            continue
        classes[path.relative_to(output).as_posix()] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    javac_version = subprocess.run([str(javac), "-version"], text=True, capture_output=True)
    finish = datetime.now(timezone.utc).isoformat()
    manifest = {
        "scope": "OFFLINE_COMSOL_API_COMPILE_ONLY",
        "fixture_id": "w23_planar_te_port_api_preflight_v2",
        "started_utc": start,
        "finished_utc": finish,
        "installed_comsol_root": str(INSTALL_ROOT),
        "installed_comsol_version": paths.comsol_version_info(),
        "jdk_home": str(JAVA11),
        "javac_version": (javac_version.stdout + javac_version.stderr).strip(),
        "classpath_manifest_sha256": manifest_hash,
        "classpath_jar_count": jar_count,
        "classpath_jar_content_fingerprint_sha256": jar_content_hash,
        "source_sha256": {
            str(FIXTURE_SOURCE.relative_to(REPO)): sha256(FIXTURE_SOURCE),
            str(RUNNER_SOURCE.relative_to(REPO)): sha256(RUNNER_SOURCE),
        },
        "compile_command": compile_command,
        "javac_returncode": compile_result.returncode,
        "javap_returncode": javap_result.returncode if javap_result else None,
        "compiled_classes": classes,
        "evidence_sha256": {
            name: sha256(output / name)
            for name in ["javac.stdout.txt", "javac.stderr.txt", "javap.stdout.txt", "javap.stderr.txt"]
            if (output / name).is_file()
        },
        "native_model_build": "NOT_RUN",
        "native_property_readback": "NOT_RUN",
        "numeric_port_gate": "NOT_RUN",
        "study_or_solver_invoked": False,
        "native_result": "NOT_RUN",
    }
    write_json(output / "manifest.json", manifest)
    print(json.dumps({
        "evidence_dir": str(output),
        "javac_returncode": compile_result.returncode,
        "javap_returncode": javap_result.returncode if javap_result else None,
        "class_count": len(classes),
        "native_model_build": "NOT_RUN",
        "native_property_readback": "NOT_RUN",
        "study_or_solver_invoked": False,
    }, indent=2))
    return compile_result.returncode or (javap_result.returncode if javap_result else 0)


if __name__ == "__main__":
    raise SystemExit(main())
