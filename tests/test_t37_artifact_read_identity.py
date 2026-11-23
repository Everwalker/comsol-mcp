from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from comsol_mcp import _artifact_store as artifact_store
from comsol_mcp._artifact_store import ArtifactStore, artifact_read, authorized_artifact_project_scope
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import close_binding_releasing, open_project_root_binding


class FakeWorker:
    def __init__(self, root: Path) -> None:
        self.project_root = root


@unittest.skipUnless(
    os.name == "posix" and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd,
    "POSIX descriptor-relative artifact-read tests",
)
class ArtifactReadIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._legacy_before = set(ArtifactStore._REGISTERED_ARTIFACTS)
        self._scoped_before = set(artifact_store._PROJECT_REGISTERED_ARTIFACTS)
        self._identity_before = dict(artifact_store._PROJECT_ARTIFACT_IDENTITIES)
        self._digest_before = dict(artifact_store._PROJECT_ARTIFACT_DIGESTS)
        self._hash_before = dict(artifact_store._WHOLE_FILE_HASH_CACHE)
        with artifact_store._ARTIFACT_REGISTRY_LOCK:
            ArtifactStore._REGISTERED_ARTIFACTS.clear()
            artifact_store._PROJECT_REGISTERED_ARTIFACTS.clear()
            artifact_store._PROJECT_ARTIFACT_IDENTITIES.clear()
            artifact_store._PROJECT_ARTIFACT_DIGESTS.clear()
        artifact_store._WHOLE_FILE_HASH_CACHE.clear()

    def tearDown(self) -> None:
        with artifact_store._ARTIFACT_REGISTRY_LOCK:
            ArtifactStore._REGISTERED_ARTIFACTS.clear()
            ArtifactStore._REGISTERED_ARTIFACTS.update(self._legacy_before)
            artifact_store._PROJECT_REGISTERED_ARTIFACTS.clear()
            artifact_store._PROJECT_REGISTERED_ARTIFACTS.update(self._scoped_before)
            artifact_store._PROJECT_ARTIFACT_IDENTITIES.clear()
            artifact_store._PROJECT_ARTIFACT_IDENTITIES.update(self._identity_before)
            artifact_store._PROJECT_ARTIFACT_DIGESTS.clear()
            artifact_store._PROJECT_ARTIFACT_DIGESTS.update(self._digest_before)
        artifact_store._WHOLE_FILE_HASH_CACHE.clear()
        artifact_store._WHOLE_FILE_HASH_CACHE.update(self._hash_before)

    @staticmethod
    def _tmpdir():
        return tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())

    @staticmethod
    def _read(root: Path, target: Path, *, offset: int = 0, length: int = 64):
        return artifact_read(FakeWorker(root), None, {
            "artifact_id": str(target), "offset": offset, "length": length,
        })

    def test_scoped_nested_regular_file_reads_from_registered_descriptor(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested" / "deeper"
            nested.mkdir(parents=True)
            target = nested / "result.bin"
            data = b"registered-project-result"
            target.write_bytes(data)
            binding = open_project_root_binding(root)
            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                    result = self._read(root, target, offset=4, length=9)
            finally:
                close_binding_releasing(binding, None)

            chunk = base64.b64decode(result["data_base64"])
            self.assertEqual(chunk, data[4:13])
            self.assertEqual(result["whole_file_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(result["chunk_sha256"], hashlib.sha256(chunk).hexdigest())

    def test_name_replacement_after_project_validation_before_open_is_rejected(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "result.bin"
            target.write_bytes(b"registered-data")
            staged_replacement = nested / "replacement-stage.bin"
            staged_replacement.write_bytes(b"unregistered-data")
            binding = open_project_root_binding(root)
            did_replace = False
            real_lookup = ArtifactStore.registered_project_artifact

            def validate_then_replace(cls, path, *, worker_root):
                nonlocal did_replace
                registration = real_lookup(path, worker_root=worker_root)
                if registration is not None and not did_replace:
                    os.replace(staged_replacement, target)
                    did_replace = True
                return registration

            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                    with mock.patch.object(
                        ArtifactStore,
                        "registered_project_artifact",
                        classmethod(validate_then_replace),
                    ):
                        with self.assertRaises(ExecutionContractError) as error:
                            self._read(root, target)
            finally:
                close_binding_releasing(binding, None)

            self.assertTrue(did_replace, "replacement did not land after validation and before descriptor open")
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_same_file_identity_with_restored_metadata_still_requires_registered_digest(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "result.bin"
            original = b"registered-content"
            replacement = b"changed-content!!!"
            self.assertEqual(len(original), len(replacement))
            target.write_bytes(original)
            original_stat = target.stat()
            binding = open_project_root_binding(root)
            real_identity = artifact_store._file_identity
            pinned: dict[tuple[int, int], tuple[int, ...]] = {}

            def restored_identity(info):
                key = (int(info.st_dev), int(info.st_ino))
                return pinned.setdefault(key, real_identity(info))

            try:
                with authorized_artifact_project_scope("project-A", binding), \
                     mock.patch.object(artifact_store, "_file_identity", side_effect=restored_identity):
                    ArtifactStore.register_artifact(target)
                    key = artifact_store._project_artifact_key(target)
                    self.assertEqual(artifact_store._PROJECT_ARTIFACT_DIGESTS[key], hashlib.sha256(original).hexdigest())
                    target.write_bytes(replacement)
                    os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            finally:
                close_binding_releasing(binding, None)

            self.assertEqual(error.exception.code, "ARTIFACT_CHANGED")

    def test_in_place_change_during_bounded_read_returns_no_chunk(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "large.bin"
            original = b"A" * (2 * 1024 * 1024)
            target.write_bytes(original)
            original_stat = target.stat()
            binding = open_project_root_binding(root)
            real_identity = artifact_store._file_identity
            pinned: dict[tuple[int, int], tuple[int, ...]] = {}
            real_read = os.read
            read_count = 0

            def restored_identity(info):
                key = (int(info.st_dev), int(info.st_ino))
                return pinned.setdefault(key, real_identity(info))

            def change_after_first_block(fd: int, size: int) -> bytes:
                nonlocal read_count
                block = real_read(fd, size)
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    read_count += 1
                    if read_count == 1:
                        with target.open("r+b") as handle:
                            handle.write(b"B" * len(original))
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                return block

            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                    with mock.patch.object(artifact_store, "_file_identity", side_effect=restored_identity), \
                         mock.patch.object(artifact_store.os, "read", side_effect=change_after_first_block):
                        with self.assertRaises(ExecutionContractError) as error:
                            self._read(root, target, offset=0, length=1024)
            finally:
                close_binding_releasing(binding, None)

            self.assertGreaterEqual(read_count, 2)
            self.assertEqual(error.exception.code, "ARTIFACT_CHANGED")

    def test_registration_change_during_stream_gains_no_project_read_authority(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "large.bin"
            original = b"A" * (2 * 1024 * 1024)
            target.write_bytes(original)
            original_stat = target.stat()
            binding = open_project_root_binding(root)
            real_read = os.read
            read_count = 0

            def change_during_registration(fd: int, size: int) -> bytes:
                nonlocal read_count
                block = real_read(fd, size)
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    read_count += 1
                    if read_count == 1:
                        with target.open("r+b") as handle:
                            handle.write(b"B" * len(original))
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                return block

            try:
                with authorized_artifact_project_scope("project-A", binding):
                    with mock.patch.object(artifact_store.os, "read", side_effect=change_during_registration):
                        ArtifactStore.register_artifact(target)
                    key = artifact_store._project_artifact_key(target)
                    self.assertTrue(ArtifactStore.is_registered_artifact(target))
                    self.assertNotIn(key, artifact_store._PROJECT_REGISTERED_ARTIFACTS)
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            finally:
                close_binding_releasing(binding, None)

            self.assertGreaterEqual(read_count, 2)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_root_and_leaf_renames_during_read_suppress_the_chunk(self) -> None:
        for rename_kind in ("root", "leaf"):
            with self.subTest(rename_kind=rename_kind), self._tmpdir() as temp:
                parent = Path(temp)
                root = parent / "project-root"
                root.mkdir()
                target = root / "large.bin"
                target.write_bytes(b"A" * (2 * 1024 * 1024))
                binding = open_project_root_binding(root)
                real_read = os.read
                read_count = 0

                def rename_during_read(fd: int, size: int) -> bytes:
                    nonlocal read_count
                    block = real_read(fd, size)
                    if stat.S_ISREG(os.fstat(fd).st_mode):
                        read_count += 1
                        if read_count == 1:
                            if rename_kind == "root":
                                root.rename(parent / "renamed-root")
                                root.mkdir()
                                (root / target.name).write_bytes(b"R" * (2 * 1024 * 1024))
                            else:
                                target.rename(root / "renamed-leaf.bin")
                    return block

                try:
                    with authorized_artifact_project_scope("project-A", binding):
                        ArtifactStore.register_artifact(target)
                        with mock.patch.object(artifact_store.os, "read", side_effect=rename_during_read):
                            with self.assertRaises(ExecutionContractError) as error:
                                self._read(root, target, offset=0, length=16)
                finally:
                    close_binding_releasing(binding, None)

                self.assertGreaterEqual(read_count, 2)
                self.assertIn(error.exception.code, {"ARTIFACT_CHANGED", "ACCESS_VIOLATION"})

    def test_project_root_rename_does_not_reuse_scoped_registration(self) -> None:
        with self._tmpdir() as temp:
            parent = Path(temp)
            root = parent / "project-root"
            root.mkdir()
            target = root / "result.bin"
            target.write_bytes(b"registered-root-content")
            binding = open_project_root_binding(root)
            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                    root.rename(parent / "renamed-root")
                    root.mkdir()
                    replacement = root / target.name
                    replacement.write_bytes(b"replacement-root-content")
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, replacement)
            finally:
                close_binding_releasing(binding, None)

            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_leaf_rename_and_same_name_replacement_are_rejected(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "result.bin"
            target.write_bytes(b"registered-leaf-content")
            binding = open_project_root_binding(root)
            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                    target.rename(root / "renamed-result.bin")
                    target.write_bytes(b"replacement-leaf-content")
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            finally:
                close_binding_releasing(binding, None)

            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_project_registration_does_not_cross_project_identity(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "result.bin"
            target.write_bytes(b"project-A-only")
            binding = open_project_root_binding(root)
            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                with authorized_artifact_project_scope("project-B", binding):
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            finally:
                close_binding_releasing(binding, None)

            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_nested_symlink_does_not_receive_project_read_authority(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            real_dir = root / "real"
            real_dir.mkdir()
            target = real_dir / "result.bin"
            target.write_bytes(b"registered-through-link")
            (root / "nested-link").symlink_to(real_dir, target_is_directory=True)
            alias = root / "nested-link" / target.name
            binding = open_project_root_binding(root)
            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(alias)
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, alias)
            finally:
                close_binding_releasing(binding, None)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_legacy_unscoped_registration_remains_queryable_but_cannot_authorize_read(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "legacy.bin"
            target.write_bytes(b"legacy-registration")
            ArtifactStore.register_artifact(target)
            self.assertTrue(ArtifactStore.is_registered_artifact(target))
            binding = open_project_root_binding(root)
            try:
                with authorized_artifact_project_scope("project-A", binding):
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            finally:
                close_binding_releasing(binding, None)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")


if __name__ == "__main__":
    unittest.main()
