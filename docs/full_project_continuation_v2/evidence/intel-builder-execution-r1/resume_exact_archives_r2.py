#!/usr/bin/env python3
"""One fixed R2 batch: reuse 18, acquire 25, at most two transport calls per row.

Only actual zero-body initial-response timeouts qualify for a second call.
Every call retains its own UUID partial and exclusive raw receipt directory.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
import uuid
from acquire_exact_archives import MANIFEST_SHA, Refusal, fetch, identity, manifest_rows, write_json
from resume_exact_archives_r1 import checked_regular, read_json_at, open_directory, verify_reused, validate_terminal, check_inactive_processes, verify_failed_partial
from security_snapshot._host_scoped_provider import verify_private_path_security

CONTEXT = Path(__file__).with_name("RESUME_CONTEXT_R2.json")
CONTEXT_SHA = "30412bacf97e1b25d2f4593b3b767aa600fe7d968d4105e20dd468f316a2e7f7"
WALL_CAP_S = 4800
CLEANUP_RESERVE_S = 15

class BatchDeadlineExceeded(Refusal):
    pass

class WallBudget:
    def __init__(self, started, cap=WALL_CAP_S, reserve=CLEANUP_RESERVE_S):
        self.started, self.cap, self.reserve = started, cap, reserve
        self.triggered = False
        self.installed = False
    def expire(self, signum, frame):
        self.triggered = True
        signal.setitimer(signal.ITIMER_REAL, 0)
        raise BatchDeadlineExceeded("batch work budget expired; stop new fetches and preserve originals")
    def install(self):
        if (os.name != "posix" or not hasattr(signal, "ITIMER_REAL")
                or threading.current_thread() is not threading.main_thread() or threading.active_count() != 1):
            raise Refusal("batch deadline requires a single-thread POSIX entrypoint")
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise Refusal("existing process timer prevents isolated deadline ownership")
        self.old_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, self.expire)
        self.installed = True
        remaining = self.cap - self.reserve - (time.monotonic() - self.started)
        if remaining <= 0:
            self.expire(None, None)
        signal.setitimer(signal.ITIMER_REAL, remaining)
    def check(self):
        if self.triggered or time.monotonic() - self.started >= self.cap - self.reserve:
            self.triggered = True
            raise BatchDeadlineExceeded("batch work budget exhausted before the next fetch")
    def close(self):
        if self.installed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.old_handler)
            self.installed = False
    def evidence(self, cleanup_complete):
        elapsed = time.monotonic() - self.started
        return {"wall_clock_elapsed_s": elapsed, "wall_clock_cap_s": self.cap, "cleanup_reserve_s": self.reserve,
                "deadline_triggered": self.triggered, "within_wall_cap": elapsed <= self.cap,
                "cleanup_complete": cleanup_complete,
                "elapsed_measured_before_final_receipt_io": True,
                "process_terminal": False,
                "final_terminal_claim_requires_wrapper_wait_and_elapsed": True}

def check_original_partials(context, archives_fd):
    for partial in context["original_failed_partials"]:
        verify_failed_partial({"original_failed_partial": partial}, archives_fd)

def initial_zero_body_timeout(exc, row, archives_fd, call_fd):
    if type(exc) is not TimeoutError:
        return False
    name = row["artifact_id"] + ".result.json"
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=call_fd)
    try:
        st = checked_regular(fd)
        if st.st_size > 1048576:
            raise Refusal("new transport result exceeded receipt limit")
        with os.fdopen(os.dup(fd), "r") as f:
            receipt = json.load(f)
    finally:
        os.close(fd)
    if (receipt.get("status") != "FAILED_RETAINED_PARTIAL" or receipt.get("rc") != 1
            or receipt.get("error_type") != "TimeoutError" or receipt.get("artifact_id") != row["artifact_id"]
            or receipt.get("url") != row["url"] or receipt.get("expected_bytes") != row["expected_bytes"]
            or receipt.get("expected_sha256") != row["expected_sha256"] or receipt.get("actual_bytes") != 0
            or receipt.get("actual_sha256") != hashlib.sha256(b"").hexdigest()
            or receipt.get("redirects") != [] or receipt.get("http_status") is not None
            or receipt.get("response_headers") is not None or receipt.get("final_filename") is not None):
        return False
    try:
        os.stat(row["filename"], dir_fd=archives_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        return False
    partial = receipt.get("partial")
    prefix = row["filename"] + "."
    if not isinstance(partial, str) or not partial.startswith(prefix) or not partial.endswith(".partial") or Path(partial).name != partial:
        raise Refusal("new timeout partial is not the exact owned child filename")
    fd = os.open(partial, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archives_fd)
    try:
        checked_regular(fd, size=0)
        opened = os.fstat(fd)
        named = os.stat(partial, dir_fd=archives_fd, follow_symlinks=False)
        if identity(opened) != identity(named) or not stat.S_ISREG(named.st_mode) or os.read(fd, 1):
            raise Refusal("new zero-byte timeout partial identity/body changed")
    finally:
        os.close(fd)
    return True

def acquire_remaining(rows, archives_fd, attempt_fd, budget, result):
    for row in rows:
        result["current_artifact_id"] = row["artifact_id"]
        for number in (1, 2):
            budget.check()
            if result["fetch_count"] >= 50:
                raise Refusal("batch maximum 50 new fetch calls exhausted")
            call_name = row["artifact_id"] + ".try-" + str(number)
            os.mkdir(call_name, 0o700, dir_fd=attempt_fd)
            call_fd = os.open(call_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=attempt_fd)
            result["fetch_count"] += 1
            try:
                fetch(row, archives_fd, call_fd)
                result["new_acquired_count"] += 1
                print(json.dumps({"artifact": row["artifact_id"], "status": "ACQUIRED_EXACT", "call": number}), flush=True)
                break
            except BaseException as exc:
                eligible = number == 1 and initial_zero_body_timeout(exc, row, archives_fd, call_fd)
                write_json(call_fd, "retry.decision.json", {"eligible_second_call": eligible, "exception_type": type(exc).__name__,
                           "first_call_original_preserved": True, "no_range_append": True, "calls_so_far": result["fetch_count"]})
                if not eligible:
                    raise
                budget.check()
                result["qualified_second_calls"] += 1
                print(json.dumps({"artifact": row["artifact_id"], "status": "QUALIFIED_INITIAL_ZERO_BODY_TIMEOUT",
                                  "next_call": 2, "prior_partial_preserved": True}), flush=True)
            finally:
                os.close(call_fd)

def execute(context, rows, budget):
    prefix = Path(context["prefix"])
    if prefix != prefix.resolve():
        raise Refusal("original task prefix is no longer canonical")
    descriptors = []
    attempt_fd = None
    result = {"reused_count": 18, "new_acquired_count": 0, "fetch_count": 0, "qualified_second_calls": 0,
              "started_unix": time.time(), "cleanup_complete": False}
    try:
        rootfd = open_directory(prefix, context["root_identity"]); descriptors.append(rootfd)
        owner = read_json_at(rootfd, "OWNER.json", context["owner_sha256"])
        if (owner["uid"] != context["uid"] or owner["prefix"] != str(prefix)
                or owner["security"]["identity"] != context["root_identity"] or owner["manifest_sha256"] != MANIFEST_SHA):
            raise Refusal("original OWNER authority mismatch")
        archives_fd = open_directory(prefix / "archives", context["archives_identity"]); descriptors.append(archives_fd)
        evidence_fd = open_directory(prefix / "evidence", context["evidence_identity"]); descriptors.append(evidence_fd)
        read_json_at(rootfd, context["previous_guard"]["name"], context["previous_guard"]["sha256"])
        previous = context["previous_resume_terminal"]
        previous_fd = open_directory(prefix / "evidence" / previous["parent"], previous["parent_identity"]); descriptors.append(previous_fd)
        previous_result = read_json_at(previous_fd, previous["name"], previous["sha256"])
        if previous_result.get("status") != "TERMINAL_FAIL_RETAINED" or previous_result.get("rc") != 1:
            raise Refusal("previous resume is not explicitly terminal")
        for terminal in context["original_terminals"]:
            fd = open_directory(Path(terminal["wrapper_path"]), terminal["wrapper_identity"]); descriptors.append(fd)
            validate_terminal(read_json_at(fd, "result.json", terminal["terminal_result_sha256"]), terminal)
        reused = []
        for existing in context["original_successful_rows"]:
            row = next((r for r in rows if r["artifact_id"] == existing["artifact_id"] and r["filename"] == existing["filename"]), None)
            if row is None: raise Refusal("pinned reusable row missing from manifest")
            parent_fd = evidence_fd
            if existing["receipt_parent"] is not None:
                parent_fd = open_directory(prefix / "evidence" / existing["receipt_parent"], existing["receipt_parent_identity"])
                descriptors.append(parent_fd)
            receipt = read_json_at(parent_fd, existing["receipt_name"], existing["receipt_sha256"])
            reused.append(verify_reused(row, receipt, archives_fd))
        check_original_partials(context, archives_fd)
        reuse_ids = {r["artifact_id"] for r in reused}
        remaining = [r for r in rows if r["artifact_id"] not in reuse_ids]
        if len(reused) != 18 or len(remaining) != 25: raise Refusal("R2 exact complement mismatch")
        for row in remaining:
            try: os.stat(row["filename"], dir_fd=archives_fd, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise Refusal("unrecorded destination exists; reconcile instead of re-download")
        attempt = "resume-attempt-r2-" + uuid.uuid4().hex
        write_json(rootfd, "RESUME_ATTEMPT_R2_GUARD.json", {"attempt": attempt, "pid": os.getpid(), "uid": os.getuid(),
                   "context_sha256": CONTEXT_SHA, "created_unix": time.time()})
        os.mkdir(attempt, 0o700, dir_fd=evidence_fd)
        attempt_fd = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=evidence_fd)
        write_json(attempt_fd, "resume.request.json", {"argv": sys.argv, "uid": os.getuid(), "pid": os.getpid(),
                   "prefix": str(prefix), "context_sha256": CONTEXT_SHA, "manifest_sha256": MANIFEST_SHA,
                   "reused": reused, "remaining_artifact_ids": [r["artifact_id"] for r in remaining],
                   "max_new_fetches": 50, "wall_clock_cap_s": WALL_CAP_S, "cleanup_reserve_s": CLEANUP_RESERVE_S})
        for terminal in context["original_terminals"]:
            check_inactive_processes(terminal, observer=lambda pid, observation:
                write_json(attempt_fd, "original_process_check." + str(pid) + ".json", observation))
        for row in reused: print(json.dumps(row), flush=True)
        print(json.dumps({"status": "STARTED_R2", "prefix": str(prefix), "attempt": attempt}), flush=True)
        acquire_remaining(remaining, archives_fd, attempt_fd, budget, result)
        check_original_partials(context, archives_fd)
        result.update(status="ACQUIRED_EXACT_ALL_43", rc=0)
        write_json(rootfd, "ACQUISITION_COMPLETE.json", {"status": "ACQUIRED_EXACT", "count": 43,
                   "bytes": sum(r["expected_bytes"] for r in rows), "manifest_sha256": MANIFEST_SHA,
                   "resume_attempt": attempt, "reused_count": 18, "install_or_build_started": False})
    except BaseException as exc:
        result.update(status="FAIL_RETAINED", rc=1, error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        cleanup_complete = True
        for fd in reversed(descriptors):
            try: os.close(fd)
            except OSError: cleanup_complete = False
        budget.close()
        result.update(budget.evidence(cleanup_complete))
        result["finished_unix"] = time.time()
        if not result["within_wall_cap"] or not cleanup_complete:
            result["status"] = "UNKNOWN_CAP_OR_CLEANUP_NOT_CONFIRMED"
        if attempt_fd is not None:
            try: write_json(attempt_fd, "resume.result.json", result)
            finally: os.close(attempt_fd)

def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-resume", action="store_true")
    parser.add_argument("--approved-context-sha256")
    args = parser.parse_args()
    budget = WallBudget(started)
    try:
        if args.execute_resume: budget.install()
        raw = CONTEXT.read_bytes()
        if hashlib.sha256(raw).hexdigest() != CONTEXT_SHA: raise Refusal("R2 context bytes changed")
        context = json.loads(raw)
        rows = manifest_rows()
        if (context["uid"] != os.getuid() or context["manifest_sha256"] != MANIFEST_SHA
                or context["expected_reused_count"] != 18 or context["expected_remaining_count"] != 25
                or context["max_attempts_per_remaining_row"] != 2 or context["max_total_fetches"] != 50
                or context["wall_clock_cap_s"] != WALL_CAP_S or context["cleanup_reserve_s"] != CLEANUP_RESERVE_S
                or len(context["original_terminals"]) != 2): raise Refusal("R2 frozen scope/budget mismatch")
        if not args.execute_resume:
            print(json.dumps({"status": "METADATA_ONLY_NOT_RESUMED", "reused": 18, "remaining": 25, "max_fetches": 50,
                              "context_sha256": CONTEXT_SHA, "wall_clock_cap_s": WALL_CAP_S, "cleanup_reserve_s": CLEANUP_RESERVE_S}))
            return
        if args.approved_context_sha256 != CONTEXT_SHA: raise Refusal("execution requires exact reviewed R2 context")
        execute(context, rows, budget)
    finally:
        budget.close()

if __name__ == "__main__":
    main()
