#!/usr/bin/env python3
"""One bounded same-prefix attempt, preserving all original acquisition evidence.

Default metadata-only. No Range requests, retries, mirrors, unpack or install.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid
from acquire_exact_archives import MANIFEST_SHA, Refusal, fetch, identity, manifest_rows, write_json
from security_snapshot._host_scoped_provider import verify_private_path_security

CONTEXT = Path(__file__).with_name("RESUME_CONTEXT_R1.json")
CONTEXT_SHA = "22c88eb98735bfc0373ebe9744c0593724dbef931334de42ae3c8346be9e1f13"

def checked_regular(fd, *, size=None, expected_identity=None):
    st = os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1
            or stat.S_IMODE(st.st_mode) & 0o022 or (size is not None and st.st_size != size)
            or (expected_identity is not None and identity(st) != expected_identity)):
        raise Refusal("existing ordinary file owner/mode/link/size/identity mismatch")
    return st

def read_json_at(parent_fd, name, expected_sha):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        st = checked_regular(fd)
        if st.st_size > 1048576:
            raise Refusal("receipt exceeded maximum")
        raw = bytearray()
        while data := os.read(fd, 65536):
            raw.extend(data)
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise Refusal("original receipt bytes changed")
        return json.loads(raw)
    finally:
        os.close(fd)

def open_directory(path, expected):
    verify_private_path_security(path, directory=True, expected_identity=expected)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    if identity(os.fstat(fd)) != expected:
        os.close(fd)
        raise Refusal("directory changed between verification and open")
    return fd

def verify_reused(row, receipt, archives_fd):
    if (receipt.get("status") != "ACQUIRED_EXACT" or receipt.get("rc") != 0
            or receipt.get("artifact_id") != row["artifact_id"] or receipt.get("url") != row["url"]
            or receipt.get("final_filename") != row["filename"]
            or receipt.get("actual_bytes") != row["expected_bytes"]
            or receipt.get("actual_sha256") != row["expected_sha256"]
            or receipt.get("expected_bytes") != row["expected_bytes"]
            or receipt.get("expected_sha256") != row["expected_sha256"]
            or not isinstance(receipt.get("identity"), dict)):
        raise Refusal("original successful receipt disagrees with exact manifest row")
    fd = os.open(row["filename"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archives_fd)
    try:
        checked_regular(fd, size=row["expected_bytes"], expected_identity=receipt["identity"])
        h = hashlib.sha256()
        total = 0
        while data := os.read(fd, 1048576):
            total += len(data)
            if total > row["expected_bytes"]:
                raise Refusal("existing exact archive grew during verification")
            h.update(data)
        if total != row["expected_bytes"] or h.hexdigest() != row["expected_sha256"]:
            raise Refusal("existing exact archive bytes changed")
        return {"artifact_id": row["artifact_id"], "filename": row["filename"], "status": "REUSED_EXACT",
                "bytes": total, "sha256": h.hexdigest(), "identity": identity(os.fstat(fd))}
    finally:
        os.close(fd)

def validate_terminal(result, context):
    if (result.get("status") != "TERMINAL_FAIL_RETAINED" or type(result.get("rc")) is not int
            or result["rc"] == 0 or type(result.get("finished_unix")) not in (int, float)
            or result.get("child_pid") != context["original_child_pid"]
            or result.get("wrapper_pid") != context["original_wrapper_pid"]
            or result.get("uid") != context["uid"]):
        raise Refusal("original wrapper is not explicitly terminal with the pinned process identities")

def check_inactive_processes(context, runner=None, observer=None):
    runner = subprocess.run if runner is None else runner
    observations = []
    for pid in (context["original_child_pid"], context["original_wrapper_pid"]):
        if type(pid) is not int or pid <= 1:
            raise Refusal("invalid pinned original process PID")
        argv = ["/bin/ps", "-p", str(pid), "-o", "pid=,ppid=,uid=,lstart=,command="]
        result = runner(argv, capture_output=True, text=True, timeout=10)
        observation = {"argv": argv, "rc": result.returncode, "stdout": result.stdout,
                       "stderr": result.stderr, "observed_unix": time.time()}
        observations.append(observation)
        if observer is not None:
            observer(pid, observation)
        # A reused PID is conservatively refused too; never treat a new process
        # with that number as the original owned child or terminate it.
        if result.returncode != 1 or result.stdout.strip() or result.stderr.strip():
            raise Refusal("original process is active or fresh process absence cannot be established")
    return observations

def verify_failed_partial(context, archives_fd):
    original = context["original_failed_partial"]
    fd = os.open(original["name"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archives_fd)
    try:
        checked_regular(fd, size=original["bytes"], expected_identity={"device": original["device"], "inode": original["inode"]})
        h = hashlib.sha256()
        while data := os.read(fd, 1048576):
            h.update(data)
        if h.hexdigest() != original["sha256"]:
            raise Refusal("original failed partial changed")
    finally:
        os.close(fd)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-resume", action="store_true")
    parser.add_argument("--approved-context-sha256")
    args = parser.parse_args()
    raw = CONTEXT.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONTEXT_SHA:
        raise Refusal("resume context bytes changed")
    context = json.loads(raw)
    rows = manifest_rows()
    if context["manifest_sha256"] != MANIFEST_SHA or context["uid"] != os.getuid():
        raise Refusal("resume manifest/UID mismatch")
    reuse_ids = [r["artifact_id"] for r in context["original_successful_rows"]]
    if len(set(reuse_ids)) != 3 or len(rows) != 43:
        raise Refusal("pinned reuse set is not exactly the original three")
    remaining = [r for r in rows if r["artifact_id"] not in reuse_ids]
    if len(remaining) != 40:
        raise Refusal("remaining rows are not exactly the manifest complement")
    if not args.execute_resume:
        print(json.dumps({"status": "METADATA_ONLY_NOT_RESUMED", "prefix": context["prefix"], "reuse_count": 3,
                          "remaining_count": 40, "context_sha256": CONTEXT_SHA}))
        return
    if args.approved_context_sha256 != CONTEXT_SHA:
        raise Refusal("execution requires the reviewed exact resume context")
    prefix = Path(context["prefix"])
    if prefix != prefix.resolve():
        raise Refusal("original task prefix is no longer canonical")
    rootfd = archives_fd = evidence_fd = wrapper_fd = attempt_fd = None
    try:
        rootfd = open_directory(prefix, context["root_identity"])
        owner = read_json_at(rootfd, "OWNER.json", context["owner_sha256"])
        if (owner["uid"] != context["uid"] or owner["prefix"] != str(prefix)
                or owner["security"]["identity"] != context["root_identity"] or owner["manifest_sha256"] != MANIFEST_SHA):
            raise Refusal("original OWNER authority mismatch")
        archives_fd = open_directory(prefix / "archives", context["archives_identity"])
        evidence_fd = open_directory(prefix / "evidence", context["evidence_identity"])
        wrapper_fd = open_directory(Path(context["original_wrapper"]), context["original_wrapper_identity"])
        original = read_json_at(wrapper_fd, "result.json", context["original_terminal_result_sha256"])
        validate_terminal(original, context)
        reused = []
        for existing in context["original_successful_rows"]:
            row = next((r for r in rows if r["artifact_id"] == existing["artifact_id"] and r["filename"] == existing["filename"]), None)
            if row is None:
                raise Refusal("pinned reusable row missing from manifest")
            receipt = read_json_at(evidence_fd, existing["receipt_name"], existing["receipt_sha256"])
            reused.append(verify_reused(row, receipt, archives_fd))
        verify_failed_partial(context, archives_fd)
        for row in remaining:
            try:
                os.stat(row["filename"], dir_fd=archives_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Refusal("unrecorded remaining destination exists; reconcile instead of re-download")
        attempt = "resume-attempt-r1-" + uuid.uuid4().hex
        # One no-clobber admission marker remains after either success or failure.
        # A second invocation of this context cannot start concurrent or blind
        # retry work; Root must reconcile and issue a new context if appropriate.
        write_json(rootfd, "RESUME_ATTEMPT_R1_GUARD.json", {"attempt": attempt, "pid": os.getpid(),
                   "uid": os.getuid(), "context_sha256": CONTEXT_SHA, "created_unix": time.time()})
        os.mkdir(attempt, mode=0o700, dir_fd=evidence_fd)
        attempt_fd = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=evidence_fd)
        write_json(attempt_fd, "resume.request.json", {"argv": sys.argv, "uid": os.getuid(), "pid": os.getpid(),
                   "prefix": str(prefix), "context_sha256": CONTEXT_SHA, "manifest_sha256": MANIFEST_SHA,
                   "reused": reused, "remaining_artifact_ids": [r["artifact_id"] for r in remaining], "started_unix": time.time()})
        result = {"reused_count": 3, "new_acquired_count": 0, "started_unix": time.time()}
        try:
            observations = check_inactive_processes(context, observer=lambda pid, observation:
                write_json(attempt_fd, "original_process_check." + str(pid) + ".json", observation))
            write_json(attempt_fd, "original_process_absence.json", {"observations": observations})
            for row in reused:
                print(json.dumps(row), flush=True)
            print(json.dumps({"status": "STARTED_RESUME", "prefix": str(prefix), "attempt": attempt}), flush=True)
            for row in remaining:
                result["current_artifact_id"] = row["artifact_id"]
                verify_private_path_security(prefix, directory=True, expected_identity=context["root_identity"])
                fetch(row, archives_fd, attempt_fd)
                result["new_acquired_count"] += 1
                print(json.dumps({"artifact": row["artifact_id"], "status": "ACQUIRED_EXACT"}), flush=True)
            verify_failed_partial(context, archives_fd)
            result.update(status="ACQUIRED_EXACT_ALL_43", rc=0, network_retries=0)
            write_json(rootfd, "ACQUISITION_COMPLETE.json", {"status": "ACQUIRED_EXACT", "count": 43,
                       "bytes": sum(r["expected_bytes"] for r in rows), "manifest_sha256": MANIFEST_SHA,
                       "resume_attempt": attempt, "reused_count": 3, "install_or_build_started": False})
        except BaseException as exc:
            result.update(status="TERMINAL_FAIL_RETAINED", rc=1, error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            result["finished_unix"] = time.time()
            write_json(attempt_fd, "resume.result.json", result)
    finally:
        for fd in (attempt_fd, wrapper_fd, evidence_fd, archives_fd, rootfd):
            if fd is not None:
                os.close(fd)

if __name__ == "__main__":
    main()
