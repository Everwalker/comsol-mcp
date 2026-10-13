"""Focused production-extractor regressions for transitive archive links."""
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from acquire_exact_archives import Refusal
import prepare_offline_builder as production

class LinkBoundary(unittest.TestCase):
    def extract_fixture(self, entries, *, refused):
        with tempfile.TemporaryDirectory(prefix="comsol-link-r3-") as temporary:
            p = Path(temporary).resolve()
            archive = p / "fixture.tar"
            dest = p / "dest"
            dest.mkdir()
            with tarfile.open(archive, "w") as t:
                for kind, name, target in entries:
                    member = tarfile.TarInfo(name)
                    if kind == "link":
                        member.type = tarfile.SYMTYPE
                        member.linkname = target
                        t.addfile(member)
                    elif kind == "directory":
                        member.type = tarfile.DIRTYPE
                        t.addfile(member)
                    else:
                        member.size = len(target)
                        t.addfile(member, io.BytesIO(target))
            af = os.open(archive, os.O_RDONLY)
            df = os.open(dest, os.O_RDONLY | os.O_DIRECTORY)
            try:
                if refused:
                    with self.assertRaises(Refusal):
                        production.extract(af, df)
                    self.assertEqual(list(dest.iterdir()), [])
                else:
                    production.extract(af, df)
                    for kind, name, target in entries:
                        if kind == "link":
                            resolved = (dest / name).resolve(strict=True)
                            self.assertTrue(resolved.is_relative_to(dest))
                            self.assertNotEqual(resolved, dest)
            finally:
                os.close(af)
                os.close(df)

    def test_root_reported_transitive_escape_refused_before_writes(self):
        self.extract_fixture([("link", "top/deep/a/link", "../.."),
                              ("link", "top/deep/a/linkB", "link/../..")], refused=True)

    def test_transitive_root_resolution_refused(self):
        self.extract_fixture([("link", "top/deep/a/link", "../.."),
                              ("link", "top/deep/a/linkB", "link/..")], refused=True)

    def test_cycle_refused(self):
        self.extract_fixture([("link", "top/a", "b"), ("link", "top/b", "a")], refused=True)

    def test_dangling_refused(self):
        self.extract_fixture([("link", "top/a", "missing")], refused=True)

    def test_missing_directory_before_dotdot_refused(self):
        self.extract_fixture([("file", "top/valid", b"abc"),
                              ("link", "top/a", "missing/../valid")], refused=True)

    def test_file_traversal_refused(self):
        self.extract_fixture([("file", "top/valid", b"abc"),
                              ("link", "top/a", "valid/../valid")], refused=True)

    def test_symlink_parent_refused_before_writes(self):
        self.extract_fixture([("directory", "top/actual", ""),
                              ("link", "top/link", "actual"),
                              ("file", "top/link/file", b"abc")], refused=True)

    def test_valid_transitive_file_target(self):
        self.extract_fixture([("file", "top/target", b"abc"),
                              ("link", "top/a", "target"),
                              ("link", "top/b", "a")], refused=False)

    def test_valid_directory_and_dotdot(self):
        self.extract_fixture([("directory", "top/deep", ""),
                              ("file", "top/target", b"abc"),
                              ("link", "top/a", "deep/../target")], refused=False)

if __name__ == "__main__":
    unittest.main()
