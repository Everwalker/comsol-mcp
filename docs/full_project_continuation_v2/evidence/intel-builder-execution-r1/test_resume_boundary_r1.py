"""Focused same-prefix resume checks using actual file validation and fetch."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import acquire_exact_archives as a
import resume_exact_archives_r1 as r

def write(path, data):
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()

class ResumeBoundary(unittest.TestCase):
    def test_nonterminal_refused(self):
        ctx = {"original_child_pid": 1234, "original_wrapper_pid": 1233, "uid": os.getuid()}
        for status, rc in (("RUNNING", None), ("UNKNOWN", None), ("TERMINAL_FAIL_RETAINED", None)):
            with self.subTest(status=status), self.assertRaises(a.Refusal):
                r.validate_terminal({"status": status, "rc": rc}, ctx)

    def test_active_or_unknown_process_refused_and_observed(self):
        ctx = {"original_child_pid": 1234, "original_wrapper_pid": 1233}
        for rc, out, err in ((0, "1234 running", ""), (1, "", "operation not permitted"), (2, "", "error")):
            observations = []
            fake = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], rc, out, err)
            with self.subTest(rc=rc, err=err), self.assertRaises(a.Refusal):
                r.check_inactive_processes(ctx, runner=fake, observer=lambda pid, record: observations.append(record))
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["stderr"], err)

    def test_positive_process_absence(self):
        ctx = {"original_child_pid": 1234, "original_wrapper_pid": 1233}
        fake = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "")
        self.assertEqual(len(r.check_inactive_processes(ctx, runner=fake)), 2)

    def fixture(self, directory):
        root = Path(directory).resolve()
        p, wrapper = root / "prefix", root / "original-wrapper"
        for path in (p, wrapper, p / "archives", p / "evidence"):
            path.mkdir(mode=0o700)
        ident = lambda path: a.identity(path.stat())
        rows = [{"artifact_id": "fixture-" + str(i), "filename": "fixture-" + str(i) + ".crate",
                 "expected_bytes": 3, "expected_sha256": hashlib.sha256(b"abc").hexdigest(),
                 "origin_host": "files.pythonhosted.org", "url": "https://files.pythonhosted.org/fixed-" + str(i)} for i in range(43)]
        owner = {"uid": os.getuid(), "prefix": str(p), "security": {"identity": ident(p)}, "manifest_sha256": a.MANIFEST_SHA}
        ctx = {"prefix": str(p), "uid": os.getuid(), "root_identity": ident(p), "archives_identity": ident(p / "archives"),
               "evidence_identity": ident(p / "evidence"), "owner_sha256": write(p / "OWNER.json", owner),
               "manifest_sha256": a.MANIFEST_SHA, "original_wrapper": str(wrapper), "original_wrapper_identity": ident(wrapper),
               "original_child_pid": 1234, "original_wrapper_pid": 1233, "original_successful_rows": []}
        ctx["original_terminal_result_sha256"] = write(wrapper / "result.json", {"status": "TERMINAL_FAIL_RETAINED", "rc": 1,
                      "finished_unix": 1.0, "child_pid": 1234, "wrapper_pid": 1233, "uid": os.getuid()})
        for row in rows[:3]:
            f = p / "archives" / row["filename"]
            f.write_bytes(b"abc")
            f.chmod(0o600)
            receipt = {"status": "ACQUIRED_EXACT", "rc": 0, "artifact_id": row["artifact_id"], "url": row["url"],
                       "final_filename": row["filename"], "actual_bytes": 3, "actual_sha256": row["expected_sha256"],
                       "expected_bytes": 3, "expected_sha256": row["expected_sha256"], "identity": ident(f)}
            name = row["artifact_id"] + ".result.json"
            ctx["original_successful_rows"].append({"artifact_id": row["artifact_id"], "filename": row["filename"],
                           "receipt_name": name, "receipt_sha256": write(p / "evidence" / name, receipt)})
        partial = p / "archives/original-failed.partial"
        partial.touch(mode=0o600)
        ctx["original_failed_partial"] = {"name": partial.name, "bytes": 0, "sha256": hashlib.sha256(b"").hexdigest(), **ident(partial)}
        context_file = root / "context.json"
        context_sha = write(context_file, ctx)
        return p, rows, ctx, context_file, context_sha

    def test_existing_archive_and_receipt_drift_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            p, rows, ctx, cf, cs = self.fixture(directory)
            fd = os.open(p / "archives", os.O_RDONLY | os.O_DIRECTORY)
            try:
                receipt = json.loads((p / "evidence/fixture-0.result.json").read_text())
                self.assertEqual(r.verify_reused(rows[0], receipt, fd)["status"], "REUSED_EXACT")
                bad = dict(receipt, status="UNKNOWN")
                with self.assertRaises(a.Refusal): r.verify_reused(rows[0], bad, fd)
                (p / "archives/fixture-0.crate").write_bytes(b"abd")
                with self.assertRaises(a.Refusal): r.verify_reused(rows[0], receipt, fd)
            finally:
                os.close(fd)

    def test_hardlink_and_symlink_archive_refused(self):
        for kind in ("hardlink", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                p, rows, ctx, cf, cs = self.fixture(directory)
                f = p / "archives/fixture-0.crate"
                receipt = json.loads((p / "evidence/fixture-0.result.json").read_text())
                if kind == "hardlink": os.link(f, p / "archives/extra-hardlink")
                else:
                    f.unlink()
                    f.symlink_to("fixture-1.crate")
                fd = os.open(p / "archives", os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with self.assertRaises((a.Refusal, OSError)): r.verify_reused(rows[0], receipt, fd)
                finally: os.close(fd)

    def test_one_new_failure_stops_preserves_and_cannot_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            p, rows, ctx, cf, cs = self.fixture(directory)
            requests = []
            class Opener:
                def open(self, request, timeout):
                    requests.append(request.full_url)
                    raise TimeoutError("synthetic initial headers timeout")
            fake_ps = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "")
            with patch.object(r, "CONTEXT", cf), patch.object(r, "CONTEXT_SHA", cs), patch.object(r, "manifest_rows", return_value=rows), \
                 patch.object(r.subprocess, "run", side_effect=fake_ps), patch.object(a.urllib.request, "build_opener", return_value=Opener()), \
                 patch.object(r.sys, "argv", ["resume", "--execute-resume", "--approved-context-sha256", cs]):
                with self.assertRaises(TimeoutError): r.main()
                with self.assertRaises(FileExistsError): r.main()
            self.assertEqual(requests, [rows[3]["url"]])
            self.assertEqual((p / "archives/original-failed.partial").read_bytes(), b"")
            for row in rows[:3]: self.assertEqual((p / "archives" / row["filename"]).read_bytes(), b"abc")
            attempts = list((p / "evidence").glob("resume-attempt-*"))
            self.assertEqual(len(attempts), 1)
            result = json.loads((attempts[0] / "resume.result.json").read_text())
            self.assertEqual(result["status"], "TERMINAL_FAIL_RETAINED")
            self.assertEqual(result["new_acquired_count"], 0)
            self.assertEqual(len(list((p / "archives").glob("*.partial"))), 2)

if __name__ == "__main__":
    unittest.main()
