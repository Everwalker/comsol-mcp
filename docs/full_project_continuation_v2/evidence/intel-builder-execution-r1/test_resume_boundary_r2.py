"""Real production fetch/receipt/filesystem tests; transport alone is synthetic."""
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch
import acquire_exact_archives as a
import resume_exact_archives_r2 as r

class Response(io.BytesIO):
    status = 200
    headers = {}
    def geturl(self): return "https://files.pythonhosted.org/fixed.crate"

class BodyTimeout(Response):
    def read(self, size=-1):
        data = super().read(size)
        if data: return data
        raise TimeoutError("synthetic body timeout")

class NoDeadline:
    def check(self): pass

class ResumeR2Boundary(unittest.TestCase):
    def rows(self, count=1):
        return [{"artifact_id": "fixture-" + str(i), "filename": "fixture-" + str(i) + ".crate", "expected_bytes": 3,
                 "expected_sha256": hashlib.sha256(b"abc").hexdigest(), "origin_host": "files.pythonhosted.org",
                 "url": "https://files.pythonhosted.org/fixed-" + str(i)} for i in range(count)]

    def run_case(self, behaviors, rows=None, expected_exception=None, budget=None, precreate_final=False):
        rows = self.rows() if rows is None else rows
        with tempfile.TemporaryDirectory(prefix="comsol-resume-r2-test-") as temporary:
            p = Path(temporary).resolve()
            (p / "archives").mkdir()
            (p / "calls").mkdir()
            old = p / "archives/old-failed.partial"
            old.touch(mode=0o600)
            if precreate_final: (p / "archives" / rows[0]["filename"]).write_bytes(b"unrelated")
            af = os.open(p / "archives", os.O_RDONLY | os.O_DIRECTORY)
            ef = os.open(p / "calls", os.O_RDONLY | os.O_DIRECTORY)
            requests = []
            class Opener:
                def open(self, request, timeout):
                    requests.append(request.full_url)
                    behavior = behaviors[len(requests) - 1]
                    if isinstance(behavior, BaseException): raise behavior
                    if callable(behavior): return behavior()
                    return Response(behavior)
            result = {"fetch_count": 0, "new_acquired_count": 0, "qualified_second_calls": 0}
            try:
                with patch.object(a.urllib.request, "build_opener", return_value=Opener()):
                    if expected_exception:
                        with self.assertRaises(expected_exception): r.acquire_remaining(rows, af, ef, budget or NoDeadline(), result)
                    else: r.acquire_remaining(rows, af, ef, budget or NoDeadline(), result)
            finally:
                os.close(af); os.close(ef)
                if budget: budget.close()
            self.assertEqual(old.read_bytes(), b"")
            parts = list((p / "archives").glob("*.partial"))
            records = [json.loads(f.read_text()) for f in (p / "calls").glob("*/*.result.json")]
            decisions = [json.loads(f.read_text()) for f in (p / "calls").glob("*/retry.decision.json")]
            return {"requests": requests, "result": result, "partial_bytes": [f.read_bytes() for f in parts],
                    "records": records, "decisions": decisions,
                    "final_bytes": {f.name: f.read_bytes() for f in (p / "archives").glob("*.crate")}}

    def test_initial_timeout_has_one_second_call_and_preserves_partial(self):
        d = self.run_case([TimeoutError("initial headers timeout"), b"abc"])
        self.assertEqual(len(d["requests"]), 2)
        self.assertEqual(d["result"], {"fetch_count": 2, "new_acquired_count": 1, "qualified_second_calls": 1, "current_artifact_id": "fixture-0"})
        self.assertEqual(d["partial_bytes"], [b"", b""])
        self.assertEqual({x["status"] for x in d["records"]}, {"FAILED_RETAINED_PARTIAL", "ACQUIRED_EXACT"})

    def test_two_initial_timeouts_stop_without_third(self):
        d = self.run_case([TimeoutError("first"), TimeoutError("second")], expected_exception=TimeoutError)
        self.assertEqual(len(d["requests"]), 2)
        self.assertEqual(d["result"]["new_acquired_count"], 0)
        self.assertEqual(len(d["partial_bytes"]), 3)

    def test_hash_mismatch_stops_without_second(self):
        d = self.run_case([b"abd"], expected_exception=a.Refusal)
        self.assertEqual(len(d["requests"]), 1)
        self.assertIn(b"abd", d["partial_bytes"])
        self.assertFalse(d["decisions"][0]["eligible_second_call"])

    def test_size_overflow_stops_without_second(self):
        d = self.run_case([b"abcdef"], expected_exception=a.Refusal)
        self.assertEqual(len(d["requests"]), 1)
        self.assertIn(b"abcd", d["partial_bytes"])

    def test_body_timeout_stops_without_second(self):
        d = self.run_case([lambda: BodyTimeout(b"abc")], expected_exception=TimeoutError)
        self.assertEqual(len(d["requests"]), 1)
        self.assertEqual(d["records"][0]["actual_bytes"], 3)
        self.assertEqual(d["records"][0]["http_status"], 200)

    def test_permission_error_stops_without_second(self):
        d = self.run_case([PermissionError(errno.EPERM, "synthetic permission refusal")], expected_exception=PermissionError)
        self.assertEqual(len(d["requests"]), 1)

    def test_existing_final_disqualifies_timeout_and_is_preserved(self):
        d = self.run_case([TimeoutError("initial")], expected_exception=TimeoutError, precreate_final=True)
        self.assertEqual(len(d["requests"]), 1)
        self.assertEqual(d["final_bytes"]["fixture-0.crate"], b"unrelated")

    def test_redirect_timeout_disqualifies_second_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary).resolve();(p / "a").mkdir();(p / "e").mkdir()
            af = os.open(p / "a", os.O_RDONLY | os.O_DIRECTORY);ef = os.open(p / "e", os.O_RDONLY | os.O_DIRECTORY)
            row = self.rows()[0]
            def opener(handler):
                handler.redirects.append({"code": 302, "url": "https://files.pythonhosted.org/redirect"})
                class O:
                    def open(self, *args, **kwargs): raise TimeoutError("after redirect")
                return O()
            try:
                with patch.object(a.urllib.request, "build_opener", side_effect=opener):
                    with self.assertRaises(TimeoutError) as error: a.fetch(row, af, ef)
                self.assertFalse(r.initial_zero_body_timeout(error.exception, row, af, ef))
            finally: os.close(af);os.close(ef)

    def test_filesystem_nonzero_partial_disqualifies_zero_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary).resolve();(p / "a").mkdir();(p / "e").mkdir()
            af = os.open(p / "a", os.O_RDONLY | os.O_DIRECTORY);ef = os.open(p / "e", os.O_RDONLY | os.O_DIRECTORY)
            row = self.rows()[0]
            class O:
                def open(self, *args, **kwargs): raise TimeoutError("initial")
            try:
                with patch.object(a.urllib.request, "build_opener", return_value=O()):
                    with self.assertRaises(TimeoutError) as error: a.fetch(row, af, ef)
                receipt = json.loads((p / "e" / (row["artifact_id"] + ".result.json")).read_text())
                (p / "a" / receipt["partial"]).write_bytes(b"changed")
                with self.assertRaises(a.Refusal): r.initial_zero_body_timeout(error.exception, row, af, ef)
            finally: os.close(af);os.close(ef)

    def test_50_fetch_bound_uses_independent_receipts(self):
        behaviors = [item for i in range(25) for item in (TimeoutError("initial"), b"abc")]
        d = self.run_case(behaviors, rows=self.rows(26), expected_exception=a.Refusal)
        self.assertEqual(len(d["requests"]), 50)
        self.assertEqual(d["result"]["fetch_count"], 50)
        self.assertEqual(d["result"]["new_acquired_count"], 25)
        self.assertEqual(len(d["records"]), 50)
        self.assertEqual(len(d["partial_bytes"]), 26)

    def test_actual_posix_deadline_interrupts_transport_and_restores_handler(self):
        before = signal.getsignal(signal.SIGALRM)
        budget = r.WallBudget(time.monotonic(), cap=0.25, reserve=0.15)
        budget.install()
        def slow():
            time.sleep(0.5)
            return Response(b"abc")
        d = self.run_case([slow], expected_exception=r.BatchDeadlineExceeded, budget=budget)
        self.assertEqual(len(d["requests"]), 1)
        self.assertTrue(budget.triggered)
        self.assertFalse(d["decisions"][0]["eligible_second_call"])
        self.assertEqual(d["records"][0]["error_type"], "BatchDeadlineExceeded")
        self.assertEqual(signal.getsignal(signal.SIGALRM), before)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_multithreaded_deadline_install_refused_without_signal_change(self):
        before = signal.getsignal(signal.SIGALRM)
        with patch.object(r.threading, "active_count", return_value=2):
            with self.assertRaises(a.Refusal): r.WallBudget(time.monotonic()).install()
        self.assertEqual(signal.getsignal(signal.SIGALRM), before)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_actual_execute_reuses_18_checks_four_pids_and_fences_after_two_failures(self):
        with tempfile.TemporaryDirectory(prefix="comsol-r2-execute-test-") as temporary:
            top = Path(temporary).resolve();p = top / "prefix"
            for path in (p, p / "archives", p / "evidence", p / "evidence/old-attempt"):
                path.mkdir(mode=0o700)
            ident = lambda path: a.identity(path.stat())
            def record(path, data):
                path.write_text(json.dumps(data));path.chmod(0o600)
                return hashlib.sha256(path.read_bytes()).hexdigest()
            rows = self.rows(43)
            ctx = {"prefix": str(p), "uid": os.getuid(), "root_identity": ident(p), "archives_identity": ident(p / "archives"),
                   "evidence_identity": ident(p / "evidence"), "original_successful_rows": [], "original_terminals": [],
                   "original_failed_partials": [], "manifest_sha256": a.MANIFEST_SHA}
            ctx["owner_sha256"] = record(p / "OWNER.json", {"uid": os.getuid(), "prefix": str(p), "security": {"identity": ident(p)}, "manifest_sha256": a.MANIFEST_SHA})
            ctx["previous_guard"] = {"name": "RESUME_ATTEMPT_R1_GUARD.json", "sha256": record(p / "RESUME_ATTEMPT_R1_GUARD.json", {"attempt": "old-attempt"})}
            old = p / "evidence/old-attempt"
            ctx["previous_resume_terminal"] = {"parent": old.name, "parent_identity": ident(old), "name": "resume.result.json",
                "sha256": record(old / "resume.result.json", {"status": "TERMINAL_FAIL_RETAINED", "rc": 1})}
            for i in range(2):
                wrapper = top / ("old-wrapper-" + str(i));wrapper.mkdir(mode=0o700)
                terminal = {"original_child_pid": 1234 + i * 2, "original_wrapper_pid": 1233 + i * 2, "uid": os.getuid(),
                            "wrapper_path": str(wrapper), "wrapper_identity": ident(wrapper)}
                terminal["terminal_result_sha256"] = record(wrapper / "result.json", {"status": "TERMINAL_FAIL_RETAINED", "rc": 1,
                    "finished_unix": 1.0, "child_pid": terminal["original_child_pid"], "wrapper_pid": terminal["original_wrapper_pid"], "uid": os.getuid()})
                ctx["original_terminals"].append(terminal)
                partial = p / "archives" / ("old-failure-" + str(i) + ".partial");partial.touch(mode=0o600)
                ctx["original_failed_partials"].append({"name": partial.name, "bytes": 0, "sha256": hashlib.sha256(b"").hexdigest(), **ident(partial)})
            for row in rows[:18]:
                f = p / "archives" / row["filename"];f.write_bytes(b"abc");f.chmod(0o600)
                receipt = {"status": "ACQUIRED_EXACT", "rc": 0, "artifact_id": row["artifact_id"], "url": row["url"], "final_filename": row["filename"],
                    "actual_bytes": 3, "expected_bytes": 3, "actual_sha256": row["expected_sha256"], "expected_sha256": row["expected_sha256"], "identity": ident(f)}
                name = row["artifact_id"] + ".result.json"
                ctx["original_successful_rows"].append({"artifact_id": row["artifact_id"], "filename": row["filename"], "receipt_name": name,
                    "receipt_parent": None, "receipt_sha256": record(p / "evidence" / name, receipt)})
            requests, process_checks = [], []
            class O:
                def open(self, request, timeout):
                    requests.append(request.full_url)
                    raise TimeoutError("synthetic initial headers")
            def ps(argv, **kwargs):
                process_checks.append(argv)
                return subprocess.CompletedProcess(argv, 1, "", "")
            with patch.object(a.urllib.request, "build_opener", return_value=O()), patch("resume_exact_archives_r1.subprocess.run", side_effect=ps):
                with self.assertRaises(TimeoutError): r.execute(ctx, rows, r.WallBudget(time.monotonic()))
                with self.assertRaises(FileExistsError): r.execute(ctx, rows, r.WallBudget(time.monotonic()))
            self.assertEqual(requests, [rows[18]["url"], rows[18]["url"]])
            self.assertEqual(len(process_checks), 4)
            self.assertTrue((p / "RESUME_ATTEMPT_R2_GUARD.json").is_file())
            attempts = list((p / "evidence").glob("resume-attempt-r2-*"));self.assertEqual(len(attempts), 1)
            result = json.loads((attempts[0] / "resume.result.json").read_text())
            self.assertEqual(result["fetch_count"], 2);self.assertEqual(result["reused_count"], 18)
            self.assertFalse(result["process_terminal"]);self.assertTrue(result["within_wall_cap"])
            self.assertEqual(len(list(attempts[0].glob("original_process_check.*.json"))), 4)
            for row in rows[:18]: self.assertEqual((p / "archives" / row["filename"]).read_bytes(), b"abc")
            for partial in ctx["original_failed_partials"]: self.assertEqual((p / "archives" / partial["name"]).read_bytes(), b"")

if __name__ == "__main__": unittest.main()
