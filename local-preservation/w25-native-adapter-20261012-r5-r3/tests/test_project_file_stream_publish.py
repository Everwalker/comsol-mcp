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

    def _private_stage(self, payload):
        from comsol_mcp._host_scoped_provider import verify_private_path_security

        stage_root = Path(self.scratch.name) / "native-private-stage"
        stage_root.mkdir(mode=0o700, exist_ok=False)
        if os.name != "nt":
            stage_root.chmod(0o700)
        source = stage_root / "model.mph"
        source.write_bytes(payload)
        if os.name != "nt":
            source.chmod(0o600)
        root_identity = verify_private_path_security(stage_root, directory=True)["identity"]
        source_identity = verify_private_path_security(source, directory=False)["identity"]
        return stage_root, source, root_identity, source_identity

    def test_native_private_stage_publishes_through_pinned_artifact_store_source(self):
        payload = b"synthetic private staged model" * 4096
        stage_root, source, root_identity, source_identity = self._private_stage(payload)
        receipt = publish_project_file_no_clobber(
            self.root, "exports/migrated.mph", source,
            expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size=len(payload),
            owned_source_root=stage_root, expected_source_identity=source_identity,
            expected_source_root_identity=root_identity,
        )
        self.assertEqual((self.destination_dir / "migrated.mph").read_bytes(), payload)
        self.assertEqual(receipt["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(receipt["size_bytes"], len(payload))
        self.assertEqual(receipt["relative_path"], "exports/migrated.mph")
        self.assertEqual(source.read_bytes(), payload)

    def test_native_private_stage_preserves_foreign_destination_racing_publication(self):
        import comsol_mcp._artifact_store as artifact_store

        payload = b"staged source bytes"
        stage_root, source, root_identity, source_identity = self._private_stage(payload)
        destination = self.destination_dir / "raced.mph"
        original = artifact_store._publish_project_chunks_no_clobber

        def race_after_path_admission(root, relative, chunks, *, expected_sha256, expected_size):
            destination.write_bytes(b"foreign winner")
            return original(root, relative, chunks, expected_sha256=expected_sha256, expected_size=expected_size)

        artifact_store._publish_project_chunks_no_clobber = race_after_path_admission
        try:
            with self.assertRaises(Exception) as caught:
                publish_project_file_no_clobber(
                    self.root, "exports/raced.mph", source,
                    expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size=len(payload),
                    owned_source_root=stage_root, expected_source_identity=source_identity,
                    expected_source_root_identity=root_identity,
                )
        finally:
            artifact_store._publish_project_chunks_no_clobber = original
        self.assertIn(getattr(caught.exception, "code", ""), {"DESTINATION_EXISTS", "ACCESS_VIOLATION"})
        self.assertEqual(destination.read_bytes(), b"foreign winner")

    def test_native_private_stage_replacement_during_stream_is_rejected_before_commit(self):
        import comsol_mcp._artifact_store as artifact_store

        payload = b"a" * (2 * 1024 * 1024 + 17)
        stage_root, source, root_identity, source_identity = self._private_stage(payload)
        destination = self.destination_dir / "source-race.mph"
        original = artifact_store._publish_project_chunks_no_clobber

        def replace_after_first_chunk(root, relative, chunks, *, expected_sha256, expected_size):
            iterator = iter(chunks)

            def raced_chunks():
                yield next(iterator)
                displaced = source.with_name("model.original")
                source.replace(displaced)
                source.write_bytes(b"replacement source")
                if os.name != "nt":
                    source.chmod(0o600)
                yield from iterator

            return original(root, relative, raced_chunks(), expected_sha256=expected_sha256,
                            expected_size=expected_size)

        artifact_store._publish_project_chunks_no_clobber = replace_after_first_chunk
        try:
            with self.assertRaises(Exception) as caught:
                publish_project_file_no_clobber(
                    self.root, "exports/source-race.mph", source,
                    expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size=len(payload),
                    owned_source_root=stage_root, expected_source_identity=source_identity,
                    expected_source_root_identity=root_identity,
                )
        finally:
            artifact_store._publish_project_chunks_no_clobber = original
        self.assertIn(getattr(caught.exception, "code", None), {"AUTHORIZATION_STATE_UNKNOWN", "ARTIFACT_CHANGED"})
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
