from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from pathlib import Path
import sys
import struct
import tempfile
from types import ModuleType
import unittest
import zlib
from unittest import mock

from comsol_mcp import _artifact_store as artifact_store
from comsol_mcp._artifact_store import ArtifactStore, artifact_read, authorized_artifact_project_scope
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_artifact_reads import dispatch as dispatch_artifact_read
from comsol_mcp._g3_w18 import _stage_and_publish_image
from comsol_mcp._managed_backend import ManagedBackend


class FakeWorker:
    def __init__(self, root: Path) -> None:
        self.project_root = root


@unittest.skipUnless(
    os.name == "posix" and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd,
    "POSIX descriptor-relative scoped artifact tests",
)
class ScopedArtifactMigrationTests(unittest.TestCase):
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
    @contextmanager
    def _managed_scope(root: Path, project_id: str):
        class BackendStub:
            _project_root_context = ContextVar(f"test_project_root_{project_id}", default=None)
            _project_root_binding_context = ContextVar(f"test_project_binding_{project_id}", default=None)
            service = None

        state_stub = ModuleType("comsol_mcp._state")

        @contextmanager
        def state_scope(_root):
            yield _root

        state_stub.project_workflow_state_scope = state_scope
        with mock.patch.dict(sys.modules, {"comsol_mcp._state": state_stub}):
            with ManagedBackend.project_root_scope(BackendStub(), root, project_id=project_id):
                yield

    def test_legacy_register_preserves_missing_path_return_and_query_but_does_not_authorize_read(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "not-created-yet.bin"
            returned = ArtifactStore.register_artifact(target)
            self.assertEqual(returned, str(target.resolve()))
            self.assertTrue(ArtifactStore.is_registered_artifact(target))
            self.assertFalse(target.exists())

            target.write_bytes(b"synthetic")
            with self._managed_scope(root, "project-A"):
                with self.assertRaises(ExecutionContractError) as error:
                    artifact_read(FakeWorker(root), "", {
                        "artifact_id": str(target), "project_id": "project-A",
                        "require_registered": False, "offset": 0, "length": 4,
                    })
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_replaced_project_root_name_does_not_reuse_the_old_binding_registration(self) -> None:
        from comsol_mcp._project_root import close_binding_releasing, open_project_root_binding

        with self._tmpdir() as temp:
            parent = Path(temp)
            root = parent / "project-root"
            root.mkdir()
            target = root / "artifact.bin"
            target.write_bytes(b"registered bytes")
            binding = open_project_root_binding(root)
            try:
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                    moved = parent / "moved-original-root"
                    root.rename(moved)
                    root.mkdir()
                    replacement = root / "artifact.bin"
                    replacement.write_bytes(b"replacement bytes")

                    self.assertFalse(ArtifactStore.is_registered_for_current_project(
                        replacement, worker_root=root,
                    ))
                    with self.assertRaises(ExecutionContractError) as error:
                        artifact_read(FakeWorker(root), "", {
                            "artifact_id": str(replacement), "offset": 0, "length": 4,
                        })
                    self.assertEqual(error.exception.code, "ACCESS_VIOLATION")
            finally:
                close_binding_releasing(binding, None)

    def test_real_path_writers_register_in_current_project_and_block_same_or_nested_path_cross_project(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested-project"
            nested.mkdir()

            store = ArtifactStore(root)
            with self._managed_scope(root, "project-A"):
                exported = store.export_field_data(
                    "nested-project/from-export.json", {"values": [1, 2], "status": {"ok": True}}
                )
                target = Path(exported["file_path"])
                self.assertTrue(ArtifactStore.is_registered_artifact(target))
                self.assertTrue(ArtifactStore.is_registered_for_current_project(target, worker_root=root))
                read = artifact_read(FakeWorker(root), "", {
                    "artifact_id": str(target), "project_id": "project-B", "offset": 0, "length": 8,
                })
                self.assertEqual(base64.b64decode(read["data_base64"]), target.read_bytes()[:8])

                # The production W18 image publisher has the same legacy
                # registration call; the authenticated scope adds its project key.
                staging = root / ".render-stage.png"
                image_target = root / "nested-project" / "render.png"
                image_target.parent.mkdir(parents=True, exist_ok=True)
                staging.write_bytes(self._one_pixel_png())
                _stage_and_publish_image(
                    ArtifactStore(root), staging, image_target,
                    allow_overwrite=False, expected_fmt="png",
                )
                self.assertTrue(ArtifactStore.is_registered_for_current_project(image_target, worker_root=root))

            for project_id, current_root in (
                ("project-B", root),
                ("nested-project-B", nested),
            ):
                with self._managed_scope(current_root, project_id):
                    self.assertFalse(ArtifactStore.is_registered_for_current_project(target, worker_root=current_root))
                    with self.assertRaises(ExecutionContractError) as error:
                        artifact_read(FakeWorker(current_root), "", {
                            "artifact_id": str(target), "project_id": "project-A", "offset": 0, "length": 4,
                        })
                self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

            ArtifactStore.unregister_artifact(target)
            self.assertFalse(ArtifactStore.is_registered_artifact(target))
            with self._managed_scope(root, "project-A"):
                self.assertFalse(ArtifactStore.is_registered_for_current_project(target, worker_root=root))

    @staticmethod
    def _one_pixel_png() -> bytes:
        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF))

        header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        scanline = b"\x00\x11\x22\x33\xff"
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(scanline)) + chunk(b"IEND", b""))

    def test_g2_public_inspect_uses_project_authority_without_forcing_outer_project(self) -> None:
        class FakeAuthority:
            def __init__(self, root: Path) -> None:
                self.root = root
                self.calls = []

            def authorize_operation(self, project_id: str, permission: str):
                self.calls.append((project_id, permission))
                return {"project_id": project_id, "workspace": str(self.root)}

        class FakeStore:
            def __init__(self, artifact_id: str, record: dict):
                self.artifact_id = artifact_id
                self.record = record

            def get_project_artifact_metadata(self, artifact_id, *, project_id, **_scope):
                return self.record if project_id == "project-A" and artifact_id == self.artifact_id else None

            def get_operation(self, _operation_id):
                return None

            def operation_job(self, _operation_id):
                return None

        with self._tmpdir() as temp:
            root = Path(temp)
            digest = hashlib.sha256(b"fake artifact").hexdigest()
            record = {
                "schema_version": 2,
                "artifact_id": digest,
                "sha256": digest,
                "path": f"g2_artifacts/registered/{digest}.bin",
                "size": 13,
                "file_identity": {"device": 1, "inode": 2, "size": 13, "mtime_ns": 4},
                "role": "synthetic",
                "classification": "test-only",
            }

            class FakeDaemon:
                pass

            daemon = FakeDaemon()
            daemon.project_authority = FakeAuthority(root)
            daemon.store = FakeStore(digest, record)

            with mock.patch("comsol_mcp._g2_artifact_reads._capture_project_host",
                            return_value=("e" * 64, set(), [], None)), \
                 mock.patch("comsol_mcp._g2_artifact_reads._recheck_project_host"), \
                 mock.patch("comsol_mcp._artifact_store.resolve_registered_artifact",
                            return_value={"sha256": digest, "size": 13, "path": str(root / "fake.bin"),
                                          "relative_path": record["path"]}):
                result = dispatch_artifact_read(
                    daemon, "artifact.inspect",
                    {"project_id": "project-A", "artifact_id": digest},
                    {"request_id": "synthetic-request"},
                )
                self.assertEqual(result["data"]["project_id"], "project-A")
                self.assertEqual(daemon.project_authority.calls,
                                 [("project-A", "inspect"), ("project-A", "inspect")])

                with self.assertRaises(ExecutionContractError) as mismatch:
                    dispatch_artifact_read(
                        daemon, "artifact.inspect",
                        {"project_id": "project-A", "artifact_id": digest},
                        {"project_id": "project-B"},
                    )
                self.assertEqual(mismatch.exception.code, "PROJECT_IDENTITY_MISMATCH")

                # Same workspace path does not make project B inherit A's row.
                with self.assertRaises(ExecutionContractError) as cross_project:
                    dispatch_artifact_read(
                        daemon, "artifact.inspect",
                        {"project_id": "project-B", "artifact_id": digest},
                        {"project_id": "project-B"},
                    )
                self.assertEqual(cross_project.exception.code, "ARTIFACT_NOT_FOUND")

    def test_fake_windows_metadata_cannot_reuse_a_stale_whole_file_hash(self) -> None:
        with self._tmpdir() as temp:
            path = Path(temp) / "same-inode.bin"
            path.write_bytes(b"AAAA1111")
            old_mtime = path.stat().st_mtime_ns
            artifact_store._WHOLE_FILE_HASH_CACHE.clear()

            with mock.patch.object(artifact_store.sys, "platform", "win32"):
                first = artifact_store._read_pinned_chunk(path, 0, 4)
                first_identity = artifact_store._file_identity(path.stat())
                with path.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"BBBB2222")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.utime(path, ns=(path.stat().st_atime_ns, old_mtime))
                second_identity = artifact_store._file_identity(path.stat())
                second = artifact_store._read_pinned_chunk(path, 0, 4)

            self.assertEqual(first_identity, second_identity)
            self.assertEqual(first["whole_file_sha256"], hashlib.sha256(b"AAAA1111").hexdigest())
            self.assertEqual(second["whole_file_sha256"], hashlib.sha256(b"BBBB2222").hexdigest())
            self.assertEqual(second["data_bytes"], b"BBBB")
            self.assertNotEqual(first["whole_file_sha256"], second["whole_file_sha256"])

    def test_fake_windows_chunk_and_digest_come_from_the_same_stream_pass(self) -> None:
        with self._tmpdir() as temp:
            path = Path(temp) / "rewrite-during-read.bin"
            path.write_bytes(b"AAAA1111")
            original_mtime = path.stat().st_mtime_ns
            handle = path.open("rb")
            before = os.fstat(handle.fileno())

            class RewriteAtEof:
                changed = False

                def read(self, size=-1):
                    block = handle.read(size)
                    if block == b"" and not self.changed:
                        path.write_bytes(b"BBBB2222")
                        os.utime(path, ns=(path.stat().st_atime_ns, original_mtime))
                        self.changed = True
                    return block

                def fileno(self):
                    return handle.fileno()

                def seek(self, *args):
                    return handle.seek(*args)

                def close(self):
                    handle.close()

            artifact_store._WHOLE_FILE_HASH_CACHE.clear()
            with mock.patch.object(artifact_store.sys, "platform", "win32"), \
                 mock.patch.object(artifact_store, "_open_pinned_artifact",
                                  return_value=(RewriteAtEof(), before)):
                result = artifact_store._read_pinned_chunk(path, 0, 4)

            self.assertTrue(path.read_bytes().startswith(b"BBBB"))
            self.assertEqual(result["data_bytes"], b"AAAA")
            self.assertEqual(result["whole_file_sha256"], hashlib.sha256(b"AAAA1111").hexdigest())

    def test_current_overwrite_check_to_replace_race_is_reproducible_and_unresolved(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "replace-race.json"
            target.write_bytes(b"old value")
            real_replace = os.replace
            injected = False

            def race_before_replace(source, destination):
                nonlocal injected
                if Path(destination) == target and not injected:
                    target.write_bytes(b"concurrent writer")
                    injected = True
                return real_replace(source, destination)

            with mock.patch.object(artifact_store.os, "replace", side_effect=race_before_replace):
                result = ArtifactStore(root).export_field_data(
                    target.name, {"values": [9], "status": {"ok": True}}, allow_overwrite=True,
                )

            self.assertTrue(injected)
            self.assertNotEqual(target.read_bytes(), b"concurrent writer")
            self.assertEqual(result["file_path"], str(target))
            self.assertEqual(list(root.glob(f".{target.name}.backup.*")), [])

    def test_overwrite_exception_after_publication_reports_unknown(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "post-publish-failure.json"
            target.write_bytes(b"old value")
            real_replace = os.replace

            def publish_then_race_and_fail(source, destination):
                result = real_replace(source, destination)
                if Path(destination) == target:
                    target.write_bytes(b"concurrent writer")
                    raise OSError("synthetic failure after publication")
                return result

            with mock.patch.object(artifact_store.os, "replace", side_effect=publish_then_race_and_fail):
                with self.assertRaises(ExecutionContractError) as error:
                    ArtifactStore(root).export_field_data(
                        target.name, {"values": [10], "status": {"ok": True}}, allow_overwrite=True,
                    )

            self.assertEqual(error.exception.code, "ARTIFACT_PUBLISH_UNKNOWN")
            self.assertFalse(error.exception.safe_retry)
            self.assertEqual(error.exception.stage, "post_dispatch")
            self.assertEqual(target.read_bytes(), b"concurrent writer")
            self.assertEqual(list(root.glob(f".{target.name}.backup.*")), [])

    def test_overwrite_exception_preserves_uncommitted_staging_bytes(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "unconfirmed-overwrite.json"
            target.write_bytes(b"old value")
            with mock.patch.object(artifact_store.os, "replace", side_effect=OSError("synthetic interruption")):
                with self.assertRaises(ExecutionContractError) as error:
                    ArtifactStore(root).export_field_data(
                        target.name, {"values": [10], "status": {"ok": True}}, allow_overwrite=True,
                    )
            self.assertEqual(error.exception.code, "ARTIFACT_PUBLISH_UNKNOWN")
            self.assertEqual(target.read_bytes(), b"old value")
            stage = root / error.exception.details["staging_path"]
            self.assertTrue(stage.is_file())

    def test_no_clobber_restore_primitive_preserves_concurrent_target_and_backup(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "rollback-target.bin"
            backup = root / "rollback-backup.bin"
            target.write_bytes(b"concurrent writer")
            backup.write_bytes(b"old backup")
            with self.assertRaises(FileExistsError):
                artifact_store._rename_no_clobber(backup, target)
            self.assertEqual(target.read_bytes(), b"concurrent writer")
            self.assertEqual(backup.read_bytes(), b"old backup")


if __name__ == "__main__":
    unittest.main()
