import hashlib
from pathlib import Path
import tempfile
import unittest
import sys

CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_ROOT))

from comsol_mcp import _g2_docs as docs


OLD = (
    b"<html><body><h1>API A &amp; marker</h1>"
    b"<script>ignored()</script><p>alpha</p></body></html>"
)
NEW = b"<html><body><h1>API B marker</h1><p>beta</p></body></html>"
CLEAN_OLD = "API A & marker alpha"


class DocsSourceConsistencyTest(unittest.TestCase):
    def test_source_hash_uses_the_bytes_cleaned_for_indexing(self):
        self.assertEqual(Path(docs.__file__).resolve(), CANDIDATE_ROOT / "comsol_mcp" / "_g2_docs.py")
        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1] / "tests"
        ) as temp_name:
            root = Path(temp_name)
            source = root / "COMSOL64" / "guide.html"
            source.parent.mkdir()
            source.write_bytes(OLD)
            index = docs.OfflineDocsIndex(
                root / "index.sqlite3", allowed_roots=[root]
            )
            clean = docs._clean_text
            self.assertEqual(clean(source), CLEAN_OLD)

            def clean_then_replace(path, raw=None):
                text = clean(path, raw) if raw is not None else clean(path)
                path.write_bytes(NEW)
                return text

            docs._clean_text = clean_then_replace
            try:
                response = index.index(
                    runtime_id="COMSOL 6.4",
                    sources=[str(source)],
                    product="HeatTransfer",
                )
                result = index.get(document_ref=response["indexed"][0])
            finally:
                docs._clean_text = clean
                index.close()

            self.assertEqual(source.read_bytes(), NEW)
            self.assertEqual(
                set(response),
                {"status", "runtime_id", "version", "indexed", "indexed_count", "skipped"},
            )
            self.assertEqual(response["status"], "SUCCEEDED")
            self.assertEqual(response["version"], "6.4")
            self.assertEqual(response["indexed_count"], 1)
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(result["source_sha256"], hashlib.sha256(OLD).hexdigest())
            self.assertEqual(
                result["content_sha256"],
                hashlib.sha256(CLEAN_OLD.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(result["content"], CLEAN_OLD)
            self.assertEqual(result["version"], "6.4")
            self.assertEqual(result["product"], "HeatTransfer")
            self.assertNotIn("ignored", result["content"])


if __name__ == "__main__":
    unittest.main()
