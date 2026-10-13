"""Adversarial local checks; no network or dependency installation."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import tarfile
from unittest.mock import patch
import acquire_exact_archives as a
import prepare_offline_builder as b

class Response(io.BytesIO):
    status = 200
    headers = {}
    def geturl(self):
        return "https://files.pythonhosted.org/fixed.tar.gz"

class Boundary(unittest.TestCase):
    def test_frozen_manifest(self):
        self.assertEqual(len(a.manifest_rows()), 43)

    def test_untrusted_temp_parent_refused(self):
        with self.assertRaises(a.Refusal):
            a.verify_temp_parent(Path("/tmp/untrusted"))

    def test_actual_private_temp_parent_security(self):
        parent = Path(tempfile.gettempdir()).resolve()
        result = a.verify_temp_parent(parent)
        self.assertEqual(result["identity"], a.identity(parent.stat()))

    def test_unsafe_urls(self):
        for url in ("http://crates.io/x", "https://evil.invalid/x", "https://u@crates.io/x", "https://crates.io:443/x", "https://crates.io/x#fragment"):
            with self.subTest(url=url), self.assertRaises(a.Refusal):
                a.check_url(url, a.HOSTS["crates.io"])

    def test_redirect_policy(self):
        r = a.StrictRedirect("crates.io")
        with self.assertRaises(a.Refusal):
            r.redirect_request(None, None, 302, "", {}, "https://github.com/source")
        a.check_url("https://static.crates.io/crates/example.crate", r.allowed)

    def run_fetch(self, content, preexisting=False):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "archives").mkdir()
            (p / "evidence").mkdir()
            row = {"artifact_id": "fixture", "filename": "fixed.tar.gz", "expected_bytes": 3,
                   "expected_sha256": hashlib.sha256(b"abc").hexdigest(),
                   "origin_host": "files.pythonhosted.org", "url": "https://files.pythonhosted.org/fixed.tar.gz"}
            if preexisting:
                (p / "archives/fixed.tar.gz").write_bytes(b"unrelated")
            af = os.open(p / "archives", os.O_RDONLY | os.O_DIRECTORY)
            ef = os.open(p / "evidence", os.O_RDONLY | os.O_DIRECTORY)
            try:
                opener = type("FakeOpener", (), {"open": lambda self, *args, **kwargs: Response(content)})()
                with patch.object(a.urllib.request, "build_opener", return_value=opener):
                    if content != b"abc" or preexisting:
                        with self.assertRaises((a.Refusal, FileExistsError)):
                            a.fetch(row, af, ef)
                    else:
                        a.fetch(row, af, ef)
            finally:
                os.close(af)
                os.close(ef)
            receipt = json.loads((p / "evidence/fixture.result.json").read_text())
            if content != b"abc" or preexisting:
                self.assertEqual(receipt["status"], "FAILED_RETAINED_PARTIAL")
                self.assertEqual((p / "archives" / receipt["partial"]).read_bytes(), content[:4])
                if preexisting:
                    self.assertEqual((p / "archives/fixed.tar.gz").read_bytes(), b"unrelated")
                else:
                    self.assertFalse((p / "archives/fixed.tar.gz").exists())
            else:
                self.assertEqual(receipt["status"], "ACQUIRED_EXACT")
                self.assertEqual((p / "archives/fixed.tar.gz").read_bytes(), b"abc")
                self.assertEqual((p / "archives/fixed.tar.gz").stat().st_nlink, 1)

    def test_hash_mismatch_preserved(self):
        self.run_fetch(b"abd")
    def test_cap_overflow_preserved(self):
        self.run_fetch(b"abcdef")
    def test_no_clobber(self):
        self.run_fetch(b"abc", preexisting=True)
    def test_success_exact(self):
        self.run_fetch(b"abc")

    def test_archive_path_rejection(self):
        for name in ("../escape", "/absolute", "safe/../../escape"):
            with self.subTest(name=name), self.assertRaises(a.Refusal):
                b.parts(name)

    def test_archive_symlink_escape(self):
        for target in ("/absolute", "../../escape"):
            m = tarfile.TarInfo("root/link")
            m.linkname = target
            with self.subTest(target=target), self.assertRaises(a.Refusal):
                b.link_target(m)

    def test_archive_symlink_parent_write_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            archive_path = p / "fixture.tar"
            with tarfile.open(archive_path, "w") as t:
                m = tarfile.TarInfo("root/link")
                m.type = tarfile.SYMTYPE
                m.linkname = "actual"
                t.addfile(m)
                f = tarfile.TarInfo("root/link/file")
                f.size = 3
                t.addfile(f, io.BytesIO(b"abc"))
            (p / "dest").mkdir()
            af = os.open(archive_path, os.O_RDONLY)
            df = os.open(p / "dest", os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises((FileExistsError, a.Refusal)):
                    b.extract(af, df)
            finally:
                os.close(af)
                os.close(df)
            self.assertFalse((p / "dest/root/link").is_symlink())

    def test_archive_hardlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            with tarfile.open(p / "fixture.tar", "w") as t:
                m = tarfile.TarInfo("root/hard")
                m.type = tarfile.LNKTYPE
                m.linkname = "root/file"
                t.addfile(m)
            (p / "dest").mkdir()
            af = os.open(p / "fixture.tar", os.O_RDONLY)
            df = os.open(p / "dest", os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(a.Refusal):
                    b.extract(af, df)
            finally:
                os.close(af)
                os.close(df)

if __name__ == "__main__":
    unittest.main()
