#!/usr/bin/env python3
"""Task-owned CPython universal2 stages with actual process and Mach-O evidence.

No network, system install, Intel binary execution or synthesized Python config.
UNKNOWN process/group ownership leaves the active-stage guard in place.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import struct
import subprocess
import sys
import time
import uuid
from acquire_exact_archives import MANIFEST_SHA, Refusal, identity, write_json
from resume_exact_archives_r1 import checked_regular
from security_snapshot._host_scoped_provider import verify_private_path_security

HERE = Path(__file__).parent
SDK = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
CLANG = "/Library/Developer/CommandLineTools/usr/bin/clang"
UNPACK_REVIEW_SHA = "ff41a19edcdb29761d4edb0e6b410d003217dc000dd501bd23efe6fdadf7d465"
UNPACK_FREEZE_SHA = "5d3e9815eb31e1da0a561dce78044ffe075b94f621e69680477b53edd3c3944b"
STAGES = ("configure", "headers", "build", "install")
PROBE = '''#include "Python.h"
_Static_assert(sizeof(void*) == 8, "64-bit pointers required");
_Static_assert(sizeof(Py_ssize_t) == 8, "64-bit Python ssize required");
_Static_assert(sizeof(long) == 8, "LP64 long required");
_Static_assert(SIZEOF_VOID_P == 8, "generated Python pointer config required");
_Static_assert(PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION == 12 && PY_MICRO_VERSION == 13, "exact Python headers required");
#if !defined(__APPLE__) || !defined(__MACH__)
#error Darwin target required
#endif
int main(void) { return 0; }
'''

class StageUnknown(Refusal):
    def __init__(self, message, *, process=None, token=None, result=None):
        super().__init__(message)
        self.process = process
        self.token = token
        self.original_result = result
        self.original_cause = None
        self.diagnostic_errors = []

class TerminalCommandFailure(Refusal):
    def __init__(self, result):
        super().__init__("owned command ended with a nonzero exit; original raw logs retained")
        self.result = result

def file_record(path, maximum=104857600):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = checked_regular(fd)
        if st.st_size > maximum: raise Refusal("stage file exceeds its bound")
        h = hashlib.sha256()
        while data := os.read(fd, 1048576): h.update(data)
        return {"path": str(path), "bytes": st.st_size, "sha256": h.hexdigest(), "identity": identity(st)}
    finally: os.close(fd)

def read_record(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = checked_regular(fd)
        if st.st_size > 1048576: raise Refusal("stage receipt exceeds bound")
        with os.fdopen(os.dup(fd), "r") as f: return json.load(f)
    finally: os.close(fd)

def check_records(records):
    for prior in records:
        if file_record(Path(prior["path"])) != prior: raise Refusal("stage prerequisite file bytes/identity changed")

def minos(value):
    return [value >> 16, (value >> 8) & 255, value & 255]

def parse_macho_probe(raw, arch):
    if len(raw) < 32 or len(raw) > 2097152: raise Refusal("probe Mach-O size is outside bound")
    header = struct.unpack_from("<IiiIIIII", raw)
    cpu = {"arm64": 0x0100000C, "x86_64": 0x01000007}[arch]
    magic, cputype, subtype, filetype, ncmds, command_bytes, flags, reserved = header
    if magic != 0xFEEDFACF or cputype != cpu or filetype != 2 or not 1 <= ncmds <= 512 or 32 + command_bytes > len(raw):
        raise Refusal("probe is not the exact thin 64-bit executable architecture")
    offset, versions = 32, []
    for _ in range(ncmds):
        if offset + 8 > 32 + command_bytes: raise Refusal("truncated Mach-O command")
        command, size = struct.unpack_from("<II", raw, offset)
        if size < 8 or offset + size > 32 + command_bytes: raise Refusal("invalid Mach-O command bound")
        if command == 0x32:
            if size < 24: raise Refusal("truncated LC_BUILD_VERSION")
            _, _, target_platform, minimum, sdk, tools = struct.unpack_from("<IIIIII", raw, offset)
            if target_platform != 1 or size != 24 + tools * 8: raise Refusal("probe build platform/tools mismatch")
            versions.append({"command": "LC_BUILD_VERSION", "minimum_os": minos(minimum), "sdk": minos(sdk)})
        elif command == 0x24:
            if size != 16: raise Refusal("invalid LC_VERSION_MIN_MACOSX")
            _, _, minimum, sdk = struct.unpack_from("<IIII", raw, offset)
            versions.append({"command": "LC_VERSION_MIN_MACOSX", "minimum_os": minos(minimum), "sdk": minos(sdk)})
        offset += size
    if offset != 32 + command_bytes or len(versions) != 1 or versions[0]["minimum_os"] != [11, 0, 0]:
        raise Refusal("probe minimum macOS is not the configured exact 11.0 floor")
    return {"architecture": arch, "cpu_type": cputype, "thin_macho": True, **versions[0], "executed": False}

def read_probe(path, arch):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = checked_regular(fd)
        if st.st_size > 2097152: raise Refusal("probe exceeds bound")
        data = bytearray()
        while chunk := os.read(fd, 65536): data.extend(chunk)
        record = {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "identity": identity(st)}
        return {"file": record, "parsed": parse_macho_probe(data, arch)}
    finally: os.close(fd)

def actual_birth(process, argv):
    command = ["/bin/ps", "-p", str(process.pid), "-o", "pid=,ppid=,uid=,lstart=,command="]
    response = subprocess.run(command, capture_output=True, text=True, timeout=10, env=dict(os.environ, LC_ALL="C"))
    raw = {"argv": command, "rc": response.returncode, "stdout": response.stdout, "stderr": response.stderr}
    fields = response.stdout.strip().split(None, 8)
    if response.returncode != 0 or response.stderr.strip() or len(fields) != 9:
        return {"status": "UNKNOWN", "reason": "fresh actual process birth/UID cannot be established", "raw": raw}
    try:
        owned = int(fields[0]) == process.pid and int(fields[1]) == os.getpid() and int(fields[2]) == os.getuid()
    except ValueError:
        owned = False
    if not owned:
        return {"status": "UNKNOWN", "reason": "fresh process ownership differs from the created child", "raw": raw}
    return {"status": "OBSERVED", "pid": process.pid, "ppid": int(fields[1]), "uid": int(fields[2]),
            "birth_observed": " ".join(fields[3:8]), "actual_ps_command": fields[8], "invocation_argv": argv, "raw": raw}

def group_absent(pgid):
    try: os.killpg(pgid, 0)
    except ProcessLookupError: return True
    except PermissionError: return False
    return False

def observe_created_group(process):
    try:
        pgid, sid, parent_sid = os.getpgid(process.pid), os.getsid(process.pid), os.getsid(0)
    except OSError as exc:
        return {"status": "UNKNOWN", "error_type": type(exc).__name__, "error": str(exc)}
    independent = pgid == process.pid and sid == process.pid and sid != parent_sid
    return {"status": "OBSERVED" if independent else "UNKNOWN", "actual_pgid": pgid, "actual_sid": sid,
            "parent_sid": parent_sid, "independent_session_observed": independent, "observed_unix": time.time()}

class DiagnosticErrors(list):
    def __init__(self):
        super().__init__()
        self.causes = []

def failure_detail(phase, exc):
    return {"phase": phase, "error_type": type(exc).__name__, "error": str(exc)}

def fallback(message):
    # Diagnostics must never discard an already-created child handle.
    try: os.write(2, (message + "\n").encode("utf-8", "backslashreplace"))
    except BaseException: pass

def diagnostic_write(fd, name, payload, errors):
    try: write_json(fd, name, payload)
    except BaseException as exc:
        errors.append(failure_detail("write:" + name, exc))
        if hasattr(errors, "causes"): errors.causes.append(exc)
        fallback("UNKNOWN diagnostic failure: " + repr(errors[-1]))

def diagnostic_print(payload, errors):
    try: print(json.dumps(payload), flush=True)
    except BaseException as exc:
        errors.append(failure_detail("stdout", exc))
        if hasattr(errors, "causes"): errors.causes.append(exc)
        fallback(repr(payload))

def release_log(fd, errors):
    for phase, operation in (("fsync", os.fsync), ("close", os.close)):
        try: operation(fd)
        except BaseException as exc:
            errors.append(failure_detail(phase + ":" + str(fd), exc))
            if hasattr(errors, "causes"): errors.causes.append(exc)
            fallback(repr(errors[-1]))

def run(logfd, label, argv, cwd, env, timeout=600, birth_reader=None):
    token = label + "-" + uuid.uuid4().hex
    stdout_name, stderr_name = token + ".stdout", token + ".stderr"
    request = {"argv": argv, "cwd": str(cwd), "environment": env, "timeout_s": timeout,
               "stdout": stdout_name, "stderr": stderr_name, "started_unix": time.time()}
    write_json(logfd, token + ".request.json", request)
    fds, errors, process, primary = [], DiagnosticErrors(), None, None
    result = {"argv": argv, "stdout": stdout_name, "stderr": stderr_name, "status": "UNKNOWN", "rc": None,
              "owned_process_terminal": False, "owned_group_absent": False, "diagnostic_errors": errors}
    try:
        for name in (stdout_name, stderr_name):
            fds.append(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=logfd))
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=fds[0], stderr=fds[1], start_new_session=True)
        result.update(pid=process.pid, requested_new_session=True, uid_at_spawn=os.getuid(), parent_pid=os.getpid())
        write_json(logfd, token + ".spawn.json", {**result, "spawned_unix": time.time()})
        group = observe_created_group(process)
        result["group"] = group
        write_json(logfd, token + ".group.json", group)
        if group.get("status") != "OBSERVED": raise Refusal("new process group observation incomplete")
        birth = (actual_birth if birth_reader is None else birth_reader)(process, argv)
        result["birth"] = birth
        write_json(logfd, token + ".birth.json", birth)
        if birth.get("status") != "OBSERVED": raise Refusal("fresh birth/UID observation incomplete")
        result["rc"] = process.wait(timeout=timeout)
        result["owned_process_terminal"] = True
        result["owned_group_absent"] = group_absent(group["actual_pgid"])
        if not result["owned_group_absent"]: raise Refusal("direct child ended but owned group may remain")
        result["status"] = "PASS" if result["rc"] == 0 else "TERMINAL_FAIL"
        if result["rc"] != 0: primary = TerminalCommandFailure(result)
    except BaseException as exc:
        primary = exc
        result.update(error_type=type(exc).__name__, error=str(exc),
                      status="UNKNOWN_TIMEOUT_ACTIVE_PROCESS" if isinstance(exc, subprocess.TimeoutExpired) else "UNKNOWN_OWNERSHIP_OR_INTERRUPTION")
    finally:
        for fd in fds: release_log(fd, errors)
        result["finished_unix"] = time.time()
        if errors: result["status"] = "UNKNOWN_DIAGNOSTICS"
        diagnostic_write(logfd, token + ".result.json", result, errors)
    if process is not None and (errors or (primary is not None and not isinstance(primary, TerminalCommandFailure))):
        result["status"] = "UNKNOWN_DIAGNOSTICS" if errors else result["status"]
        unknown = StageUnknown("created command requires same-handle observation; retain stage fence", process=process, token=token, result=result)
        unknown.original_cause = primary or (errors.causes[0] if errors.causes else None)
        unknown.diagnostic_errors = errors
        raise unknown from unknown.original_cause
    if primary is not None: raise primary
    if errors: raise Refusal("pre-spawn diagnostics failed; no child was created")
    diagnostic_print({"command": label, "status": "PASS", "receipt": token + ".result.json"}, errors)
    if errors:
        result["status"] = "UNKNOWN_DIAGNOSTICS"
        unknown = StageUnknown("command output diagnostics failed", process=process, token=token, result=result)
        unknown.diagnostic_errors = errors
        unknown.original_cause = errors.causes[0] if errors.causes else None
        raise unknown from unknown.original_cause
    return {"result_name": token + ".result.json", **result}

def observe_unknown(logfd, error, interval=10):
    """Keep the exact Popen despite any diagnostic failure; never clear its fence."""
    if error.process is None or error.token is None: raise error
    count = 0
    errors = error.diagnostic_errors
    while True:
        count += 1
        try:
            rc = error.process.wait(timeout=interval)
        except subprocess.TimeoutExpired:
            observation = {"status": "SAME_OWNED_PROCESS_STILL_ACTIVE", "pid": error.process.pid,
                           "original_unknown_result": error.token + ".result.json", "observed_unix": time.time()}
            diagnostic_write(logfd, error.token + ".observation." + str(count) + ".json", observation, errors)
            diagnostic_print(observation, errors)
            continue
        except BaseException as exc:
            errors.append(failure_detail("same_handle_wait", exc));fallback(repr(errors[-1]))
            time.sleep(interval)
            continue
        group = error.original_result.get("group", {}) if error.original_result else {}
        birth = error.original_result.get("birth", {}) if error.original_result else {}
        owned = group.get("status") == "OBSERVED" and birth.get("status") == "OBSERVED"
        try: absent = group_absent(group["actual_pgid"]) if owned else None
        except BaseException as exc:
            errors.append(failure_detail("group_absence", exc));fallback(repr(errors[-1]));absent = None
        if owned and absent is not True:
            observation = {"status": "OBSERVED_DIRECT_CHILD_TERMINAL_GROUP_REMAINS_UNKNOWN", "pid": error.process.pid,
                           "rc": rc, "actual_pgid": group["actual_pgid"], "owned_group_absent": absent,
                           "original_unknown_result": error.token + ".result.json", "observed_unix": time.time()}
            diagnostic_write(logfd, error.token + ".observation." + str(count) + ".json", observation, errors)
            diagnostic_print(observation, errors);time.sleep(interval);continue
        observation = {"status": "OBSERVED_DIRECT_CHILD_TERMINAL_NOT_STAGE_ACCEPTED", "pid": error.process.pid,
                       "rc": rc, "owned_group_absent": absent, "group_ownership_confirmed": owned, "observed_unix": time.time(),
                       "original_unknown_result": error.token + ".result.json", "original_unknown_unchanged": True,
                       "active_stage_guard_retained": True, "stage_acceptance": "NOT_RUN"}
        diagnostic_write(logfd, error.token + ".terminal_observation.json", observation, errors)
        diagnostic_print(observation, errors)
        return observation

def generated_records(build):
    return [file_record(build / name) for name in ("pyconfig.h", "Makefile", "config.status")]

def verify_command_records(build, results):
    logs = build.parent / "build-logs"
    verify_private_path_security(logs, directory=True)
    for result in results:
        name = result.get("result_name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.result\.json", name):
            raise Refusal("stage command receipt name is not an owned child")
        raw = read_record(logs / name)
        if raw != {k: v for k, v in result.items() if k != "result_name"}:
            raise Refusal("stage command copy disagrees with actual raw result")
        token = name[:-len(".result.json")]
        birth = read_record(logs / (token + ".birth.json"))
        request = read_record(logs / (token + ".request.json"))
        if (birth != raw.get("birth") or birth.get("status") != "OBSERVED"
                or request.get("argv") != raw["argv"] or request.get("cwd") != str(build)
                or raw["stdout"] != token + ".stdout" or raw["stderr"] != token + ".stderr"):
            raise Refusal("stage command birth/request/log linkage differs")
        file_record(logs / raw["stdout"], maximum=268435456)
        file_record(logs / raw["stderr"], maximum=268435456)

def require_stage(build, stage):
    receipt = read_record(build / (stage + ".PASS.json"))
    if receipt.get("stage") != stage or receipt.get("status") != "PASS" or receipt.get("manifest_sha256") != MANIFEST_SHA:
        raise Refusal("required successful stage identity missing")
    results = receipt.get("commands", [])
    if not results or any(r.get("rc") != 0 or r.get("owned_process_terminal") is not True or r.get("owned_group_absent") is not True for r in results):
        raise Refusal("stage commands are not confirmed terminal with an empty owned group")
    verify_command_records(build, results)
    config = receipt["generated_config"]
    expected = [str(build / name) for name in ("pyconfig.h", "Makefile", "config.status")]
    if [r["path"] for r in config] != expected: raise Refusal("generated configuration references escape the exact stage files")
    check_records(config)
    if stage == "headers":
        probes = receipt.get("probes", [])
        if len(probes) != 2 or {r["parsed"]["architecture"] for r in probes} != {"arm64", "x86_64"}:
            raise Refusal("both actually inspected probe architectures are required")
        for probe in probes:
            arch = probe["parsed"]["architecture"]
            path = build / ("header_probe-" + arch)
            if probe["file"]["path"] != str(path) or read_probe(path, arch) != probe:
                raise Refusal("probe inspection disagrees with stored header evidence")
    return receipt

def task_environment(prefix):
    # Keep inherited HOME unchanged. Store task settings using dedicated names
    # and absolute paths; no system/user Python or Rust location is repurposed.
    env = {key: os.environ[key] for key in ("HOME", "TMPDIR") if key in os.environ}
    env.update(PATH="/Library/Developer/CommandLineTools/usr/bin:/usr/bin:/bin:/usr/sbin:/sbin", LANG="C", LC_ALL="C",
               SDKROOT=str(SDK), MACOSX_DEPLOYMENT_TARGET="11.0", CONFIG_SITE="/dev/null", PYTHONDONTWRITEBYTECODE="1",
               COMSOL_TASK_BUILD_PREFIX=str(prefix / "cpython-universal2"), COMSOL_TASK_BUILD_LOGS=str(prefix / "build-logs"))
    return env

def stage_commands(stage, source, build, install):
    return {
        "configure": [[str(source / "configure"), "--prefix=" + str(install), "--enable-universalsdk=" + str(SDK),
                       "--with-universal-archs=universal2", "--without-ensurepip"]],
        "headers": [[CLANG, "-arch", arch, "-isysroot", str(SDK), "-mmacosx-version-min=11.0", "-I" + str(build),
                     "-I" + str(source / "Include"), str(build / "header_probe.c"), "-o", str(build / ("header_probe-" + arch))]
                    for arch in ("arm64", "x86_64")],
        "build": [["/usr/bin/make", "-j2"]], "install": [["/usr/bin/make", "altinstall"]],
    }[stage]

def main():
    p = argparse.ArgumentParser(description=__doc__);p.add_argument("--prefix", type=Path, required=True)
    p.add_argument("--stage", choices=STAGES, required=True);p.add_argument("--execute-stage", action="store_true");a = p.parse_args()
    prefix = a.prefix;source = prefix / "sources/cpython-source-3.12.13/Python-3.12.13";build = prefix / "cpython-universal2-build";install = prefix / "cpython-universal2"
    commands = stage_commands(a.stage, source, build, install)
    if not a.execute_stage:
        print(json.dumps({"status": "PREPARED_NOT_RUN", "stage": a.stage, "argv": commands, "intel_runtime_executed": False}));return
    if platform.system() != "Darwin" or platform.machine() != "arm64" or prefix != prefix.resolve() or not prefix.name.startswith("comsol-intel-inputs-r1-"):
        raise Refusal("stage is pinned to canonical ARM64 Darwin acquired task root")
    for name, sha in (("ROOT_UNPACK_SOURCE_REVIEW_R3.json", UNPACK_REVIEW_SHA), ("SOURCE_FREEZE_R3.json", UNPACK_FREEZE_SHA)):
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != sha: raise Refusal("reviewed R3 unpack dependency changed")
    security = verify_private_path_security(prefix, directory=True);owner = read_record(prefix / "OWNER.json")
    if owner["uid"] != os.getuid() or owner["security"]["identity"] != security["identity"] or owner["manifest_sha256"] != MANIFEST_SHA:
        raise Refusal("original owner authority mismatch")
    complete = read_record(prefix / "ACQUISITION_COMPLETE.json");prepared = read_record(prefix / "OFFLINE_SOURCES_PREPARED.json")
    if (complete.get("status") != "ACQUIRED_EXACT" or complete.get("count") != 43 or complete.get("bytes") != 220243712
            or complete.get("manifest_sha256") != MANIFEST_SHA or prepared.get("status") != "VERIFIED_UNPACKED_ONLY" or prepared.get("manifest_sha256") != MANIFEST_SHA):
        raise Refusal("actual complete acquisition and reviewed offline unpack are required")
    verify_private_path_security(source, directory=True)
    if not SDK.resolve().is_dir() or not Path(CLANG).is_file(): raise Refusal("pinned SDK/compiler unavailable")
    if a.stage == "configure": build.mkdir(mode=0o700);(prefix / "build-logs").mkdir(mode=0o700)
    for path in (build, prefix / "build-logs"): verify_private_path_security(path, directory=True)
    if a.stage != "configure": require_stage(build, "configure")
    if a.stage in ("build", "install"): require_stage(build, "headers")
    if a.stage == "install": require_stage(build, "build")
    bfd = os.open(build, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW);logfd = os.open(prefix / "build-logs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    active_name = "ACTIVE_STAGE.json";active_fd = None;known_terminal = False;command_results = [];probes = []
    try:
        # Permanent per-stage guard prohibits replay; active guard fences later
        # stages while a previous owned command or session is unresolved.
        try: os.stat(a.stage + ".GUARD.json", dir_fd=bfd, follow_symlinks=False)
        except FileNotFoundError: pass
        else: raise Refusal("stage already admitted; preserve prior evidence instead of replay")
        active_fd = os.open(active_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=bfd)
        active_identity = identity(os.fstat(active_fd))
        write_json(bfd, a.stage + ".GUARD.json", {"stage": a.stage, "owner_pid": os.getpid(), "uid": os.getuid(), "started_unix": time.time()})
        os.write(active_fd, json.dumps({"stage": a.stage, "owner_pid": os.getpid(), "uid": os.getuid(), "argv": sys.argv}).encode());os.fsync(active_fd)
        if a.stage == "headers":
            fd = os.open("header_probe.c", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=bfd)
            try: os.write(fd, PROBE.encode());os.fsync(fd)
            finally: os.close(fd)
        env = task_environment(prefix)
        for i, argv in enumerate(commands):
            command_results.append(run(logfd, a.stage + str(i), argv, build, env, timeout=1800 if a.stage in ("build", "install") else 600))
        if a.stage == "headers":
            for arch in ("arm64", "x86_64"):
                path = build / ("header_probe-" + arch)
                probes.append(read_probe(path, arch))
        receipt = {"stage": a.stage, "status": "PASS", "manifest_sha256": MANIFEST_SHA, "generated_config": generated_records(build),
                   "commands": command_results, "probes": probes, "intel_runtime_executed": False, "scientific_acceptance": "NOT_RUN"}
        write_json(bfd, a.stage + ".PASS.json", receipt);known_terminal = True
    except TerminalCommandFailure as exc:
        write_json(bfd, a.stage + ".FAIL.json", {"stage": a.stage, "status": "TERMINAL_FAIL", "failed_command": exc.result,
                   "known_completed_commands": command_results, "permanent_stage_guard_retained": True})
        known_terminal = True
        raise
    except StageUnknown as exc:
        if active_fd is None: raise
        diagnostic_write(bfd, a.stage + ".UNKNOWN.json", {"stage": a.stage, "status": "UNKNOWN", "known_completed_commands": command_results,
                   "raw_process_records_in": str(prefix / "build-logs"), "active_guard_retained": True}, exc.diagnostic_errors)
        if exc.process is not None: observe_unknown(logfd, exc)
        raise
    except BaseException as exc:
        if active_fd is None: raise
        # A failed command records its actual terminal status separately. Do not
        # infer terminal ownership for an unreturned command or interruption.
        write_json(bfd, a.stage + ".FAIL.json", {"stage": a.stage, "error_type": type(exc).__name__, "error": str(exc),
                   "known_completed_commands": command_results, "active_guard_retained": True});raise
    finally:
        if active_fd is not None:
            if known_terminal:
                named = os.stat(active_name, dir_fd=bfd, follow_symlinks=False)
                if identity(named) != active_identity or not stat.S_ISREG(named.st_mode): raise StageUnknown("active stage guard identity changed")
                os.unlink(active_name, dir_fd=bfd);os.fsync(bfd)
            try: os.close(active_fd)
            except BaseException as exc: fallback("active guard close failed: " + repr(exc))
        for fd in (logfd, bfd):
            try: os.close(fd)
            except BaseException as exc: fallback("stage directory close failed: " + repr(exc))

if __name__ == "__main__": main()
