import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comsol_mcp._artifact_store import publish_parameter_table_bytes_no_clobber, publish_project_file_no_clobber
from comsol_mcp._execution_contract import ExecutionContractError


class ProjectFileStreamPublish(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="project-file-stream-")
        self.root = Path(self.scratch.name) / "project"
        self.source_dir = self.root / ".g3-private" / "package-stage"
        self.destination_dir = self.root / "exports"
        self.source_dir.mkdir(parents=True)
        self.destination_dir.mkdir()

    def tearDown(self):
        self.scratch.cleanup()

    def test_stream_publish_pins_private_source_and_returns_verified_receipt(self):
        source = self.source_dir / "model.partial"
        payload = b"package source in bounded blocks" * 200_000
        source.write_bytes(payload)

        receipt = publish_project_file_no_clobber(
            self.root, "exports/model.mph", source,
            expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size=len(payload),
        )

        self.assertEqual((self.destination_dir / "model.mph").read_bytes(), payload)
        self.assertEqual(receipt["relative_path"], "exports/model.mph")
        self.assertEqual(receipt["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(receipt["size_bytes"], len(payload))
        self.assertEqual(source.read_bytes(), payload)
        self.assertEqual([item.name for item in self.destination_dir.iterdir()], ["model.mph"])

    def test_legacy_parameter_bytes_publisher_remains_no_clobber(self):
        payload = b"small parameter table"
        receipt = publish_parameter_table_bytes_no_clobber(self.root, "exports/parameters.json", payload)
        self.assertEqual((self.destination_dir / "parameters.json").read_bytes(), payload)
        self.assertEqual(receipt["sha256"], hashlib.sha256(payload).hexdigest())
        with self.assertRaises(ExecutionContractError) as caught:
            publish_parameter_table_bytes_no_clobber(self.root, "exports/parameters.json", b"replacement")
        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")

    def test_stream_publish_hash_mismatch_leaves_no_destination(self):
        source = self.source_dir / "model.partial"
        source.write_bytes(b"actual source bytes")

        with self.assertRaises(ExecutionContractError) as caught:
            publish_project_file_no_clobber(
                self.root, "exports/model.mph", source,
                expected_sha256=hashlib.sha256(b"different bytes").hexdigest(),
                expected_size=len(b"actual source bytes"),
            )

        self.assertEqual(caught.exception.code, "ARTIFACT_HASH_MISMATCH")
        self.assertEqual(list(self.destination_dir.iterdir()), [])

    def test_stream_publish_refuses_outside_source_path(self):
        outside = Path(self.scratch.name) / "outside.mph"
        outside.write_bytes(b"outside")
        digest = hashlib.sha256(b"outside").hexdigest()

        with self.assertRaises(ExecutionContractError) as caught:
            publish_project_file_no_clobber(
                self.root, "exports/outside.mph", outside,
                expected_sha256=digest, expected_size=7,
            )

        self.assertEqual(caught.exception.code, "ACCESS_VIOLATION")
        self.assertEqual(list(self.destination_dir.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "native Windows reparse controls cover source-link rejection")
    def test_stream_publish_refuses_source_symlink(self):
        outside = Path(self.scratch.name) / "outside.mph"
        outside.write_bytes(b"outside")
        link = self.source_dir / "source.partial"
        link.symlink_to(outside)

        with self.assertRaises(ExecutionContractError) as caught:
            publish_project_file_no_clobber(
                self.root, "exports/symlink.mph", link,
                expected_sha256=hashlib.sha256(b"outside").hexdigest(), expected_size=7,
            )

        self.assertEqual(caught.exception.code, "ACCESS_VIOLATION")
        self.assertEqual(list(self.destination_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
