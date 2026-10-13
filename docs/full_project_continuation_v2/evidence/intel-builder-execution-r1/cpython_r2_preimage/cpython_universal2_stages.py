#!/usr/bin/env python3
"""Task-local universal2 CPython stages with durable actual command evidence.

No network, system install, Intel execution, or synthesized Python config.
Each stage is explicit; failed commands stop the stage and retain all files.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid
from acquire_exact_archives import MANIFEST_SHA, Refusal, write_json
from security_snapshot._host_scoped_provider import verify_private_path_security

SDK = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
CLANG = "/Library/Developer/CommandLineTools/usr/bin/clang"

def run(logfd, label, argv, cwd, env, timeout=600):
    token = label + "-" + uuid.uuid4().hex
    write_json(logfd, token + ".request.json", {"argv": argv, "cwd": str(cwd), "env": env,
                                              "timeout_s": timeout, "started_unix": time.time()})
    stdout = os.open(token + ".stdout", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=logfd)
    stderr = os.open(token + ".stderr", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=logfd)
    result = {"argv": argv, "stdout": token + ".stdout", "stderr": token + ".stderr"}
    try:
        # Build tools may have descendants; a timeout preserves the process and
        # escalates for ownership reconciliation rather than claiming termination.
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        result["pid"] = process.pid
        try:
            result["rc"] = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result.update(status="TIMEOUT_OWNERSHIP_RECONCILIATION_REQUIRED", rc=None)
            # Do not kill unknown descendants or represent a timeout as terminal.
            raise Refusal(f"stage timeout; owned command PID {process.pid} needs reconciliation")
        result["status"] = "PASS" if result["rc"] == 0 else "FAIL"
        if result["rc"] != 0:
            raise Refusal(f"command {label} failed rc={result['rc']}; see raw logs")
    except BaseException as exc:
        result.setdefault("status", "LAUNCH_FAILED")
        result["error"] = str(exc)
        raise
    finally:
        for fd in (stdout, stderr):
            os.fsync(fd)
            os.close(fd)
        result["finished_unix"] = time.time()
        write_json(logfd, token + ".result.json", result)
    print(json.dumps({"command": label, "status": "PASS", "receipt": token + ".result.json"}), flush=True)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefix", type=Path, required=True)
    p.add_argument("--stage", choices=("configure", "headers", "build", "install"), required=True)
    p.add_argument("--execute-stage", action="store_true")
    a = p.parse_args()
    prefix = a.prefix
    source = prefix / "sources/cpython-source-3.12.13/Python-3.12.13"
    build = prefix / "cpython-universal2-build"
    install = prefix / "cpython-universal2"
    commands = {
        "configure": [[str(source / "configure"), "--prefix=" + str(install),
                       "--enable-universalsdk=" + str(SDK), "--with-universal-archs=universal2", "--without-ensurepip"]],
        "headers": [[CLANG, "-arch", arch, "-isysroot", str(SDK), "-mmacosx-version-min=11.0",
                     "-I" + str(build), "-I" + str(source / "Include"), str(build / "header_probe.c"),
                     "-o", str(build / ("header_probe-" + arch))] for arch in ("arm64", "x86_64")],
        "build": [["/usr/bin/make", "-j2"]],
        "install": [["/usr/bin/make", "altinstall"]],
    }[a.stage]
    if not a.execute_stage:
        print(json.dumps({"status": "PREPARED_NOT_RUN", "stage": a.stage, "argv": commands,
                          "evidence_boundary": "header probe is compile/link only; no Intel runtime proof"}))
        return
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise Refusal("stage is pinned to observed ARM64 Darwin host")
    if prefix != prefix.resolve() or not prefix.name.startswith("comsol-intel-inputs-r1-"):
        raise Refusal("prefix is not canonical acquired task root")
    security = verify_private_path_security(prefix, directory=True)
    owner = json.loads((prefix / "OWNER.json").read_text())
    if owner["uid"] != os.getuid() or owner["security"]["identity"] != security["identity"] or owner["manifest_sha256"] != MANIFEST_SHA:
        raise Refusal("owner/fingerprint mismatch")
    prepared = json.loads((prefix / "OFFLINE_SOURCES_PREPARED.json").read_text())
    if prepared["manifest_sha256"] != MANIFEST_SHA or prepared["status"] != "VERIFIED_UNPACKED_ONLY":
        raise Refusal("offline source preparation missing")
    verify_private_path_security(source, directory=True)
    if not SDK.resolve().is_dir() or not Path(CLANG).is_file():
        raise Refusal("pinned SDK/compiler unavailable")
    if a.stage == "configure":
        build.mkdir(mode=0o700)
        (prefix / "build-home").mkdir(mode=0o700)
        (prefix / "build-logs").mkdir(mode=0o700)
    for path in (build, prefix / "build-home", prefix / "build-logs"):
        verify_private_path_security(path, directory=True)
    env = {"PATH": "/Library/Developer/CommandLineTools/usr/bin:/usr/bin:/bin:/usr/sbin:/sbin",
           "HOME": str(prefix / "build-home"), "LANG": "C", "LC_ALL": "C",
           "SDKROOT": str(SDK), "MACOSX_DEPLOYMENT_TARGET": "11.0", "CONFIG_SITE": "/dev/null",
           "PYTHONDONTWRITEBYTECODE": "1"}
    if a.stage != "configure" and not (build / "configure.PASS.json").is_file():
        raise Refusal("actual successful universal2 configuration required")
    if a.stage == "install" and not (build / "build.PASS.json").is_file():
        raise Refusal("actual successful universal2 build required")
    if a.stage == "headers":
        probe = build / "header_probe.c"
        with probe.open("x") as f:
            f.write('#include "Python.h"\nint main(void) { return sizeof(Py_ssize_t) == 8 ? 0 : 1; }\n')
    logfd = os.open(prefix / "build-logs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for i, argv in enumerate(commands):
            run(logfd, a.stage + str(i), argv, build, env, timeout=1800 if a.stage in ("build", "install") else 600)
        if a.stage == "headers":
            for arch in ("arm64", "x86_64"):
                run(logfd, "lipo-header-" + arch, ["/usr/bin/lipo", "-info", str(build / ("header_probe-" + arch))], build, env)
                run(logfd, "otool-header-" + arch, ["/usr/bin/otool", "-l", str(build / ("header_probe-" + arch))], build, env)
        fd = os.open(build, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            write_json(fd, a.stage + ".PASS.json", {"stage": a.stage, "commands_rc_zero": True,
                       "manifest_sha256": MANIFEST_SHA, "header_sha256": hashlib.sha256((build / "pyconfig.h").read_bytes()).hexdigest(),
                       "intel_runtime_executed": False, "scientific_acceptance": "NOT_RUN"})
        finally:
            os.close(fd)
    finally:
        os.close(logfd)

if __name__ == "__main__":
    main()
