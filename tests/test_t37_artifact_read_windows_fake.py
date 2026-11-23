from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import tracemalloc
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comsol_mcp import _artifact_store as artifact_store
from comsol_mcp import _project_root as project_root
from comsol_mcp._artifact_store import ArtifactStore, artifact_read, authorized_artifact_project_scope
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import ProjectRootBinding, close_binding_releasing, open_project_root_binding


class FakeWorker:
    def __init__(self, root: Path) -> None:
        self.project_root = root


class FakeWindowsHandle:
    def __init__(self, api, path: Path, descriptor: int, directory: bool, serial: int) -> None:
        self.api = api
        self.path = path
        self.descriptor = descriptor
        self.directory = directory
        self.serial = serial
        self.closed = False
        self.read_calls = 0


class FakeWindowsHandleApi:
    """Strict descriptor-relative Windows API model backed by temporary files."""

    def __init__(self) -> None:
        self.handles: list[FakeWindowsHandle] = []
        self.close_calls: dict[int, int] = {}
        self.source_handles: list[FakeWindowsHandle] = []
        self.stream_bytes: dict[int, list[bytes]] = {}
        self.initial_fingerprints: dict[tuple[int, int], tuple] = {}
        self.restore_original_fingerprint = False
        self.capture_stream = False
        self.max_read_request = 0
        self.read_call_count = 0
        self.mutate_handle: int | None = None
        self.mutate_after_read_call: int | None = None
        self.fail_close_handle: int | None = None

    @staticmethod
    def _flags(directory: bool) -> int:
        return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | (getattr(os, "O_DIRECTORY", 0) if directory else 0)

    @staticmethod
    def _identity(info) -> tuple[int, int]:
        return int(info.st_dev), int(info.st_ino)

    @staticmethod
    def _component(leaf: str) -> None:
        if not isinstance(leaf, str) or not leaf or leaf in {".", ".."} or "/" in leaf or "\\" in leaf or "\x00" in leaf:
            raise OSError("invalid relative component")

    def _handle(self, path: Path, descriptor: int, directory: bool) -> FakeWindowsHandle:
        value = FakeWindowsHandle(self, path, descriptor, directory, len(self.handles) + 1)
        self.handles.append(value)
        self.close_calls[value.serial] = 0
        return value

    def open_root(self, path: Path) -> FakeWindowsHandle:
        descriptor = os.open(path, self._flags(True))
        return self._handle(path, descriptor, True)

    def open_child_directory(self, parent: FakeWindowsHandle, leaf: str) -> FakeWindowsHandle:
        self._component(leaf)
        descriptor = os.open(leaf, self._flags(True), dir_fd=parent.descriptor)
        try:
            opened = os.fstat(descriptor)
            named = os.stat(leaf, dir_fd=parent.descriptor, follow_symlinks=False)
            if (not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode)
                    or self._identity(opened) != self._identity(named)):
                raise OSError("directory entry changed or is a reparse point")
            return self._handle(parent.path / leaf, descriptor, True)
        except BaseException:
            os.close(descriptor)
            raise

    def open_source_for_snapshot(self, parent: FakeWindowsHandle, leaf: str) -> FakeWindowsHandle:
        self._component(leaf)
        descriptor = os.open(leaf, self._flags(False), dir_fd=parent.descriptor)
        try:
            opened = os.fstat(descriptor)
            named = os.stat(leaf, dir_fd=parent.descriptor, follow_symlinks=False)
            if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
                    or opened.st_nlink != 1 or named.st_nlink != 1
                    or self._identity(opened) != self._identity(named)):
                raise OSError("file entry changed, is linked, or is a reparse point")
            handle = self._handle(parent.path / leaf, descriptor, False)
            self.source_handles.append(handle)
            self.stream_bytes[handle.serial] = []
            return handle
        except BaseException:
            os.close(descriptor)
            raise

    def info(self, handle: FakeWindowsHandle, *, directory: bool = False):
        info = os.fstat(handle.descriptor)
        if (not handle.directory) == directory:
            raise OSError("unexpected object type")
        return self._identity(info), int(info.st_size)

    def full_identity(self, handle: FakeWindowsHandle, *, directory: bool = False):
        info = os.fstat(handle.descriptor)
        if stat.S_ISDIR(info.st_mode) != directory:
            raise OSError("unexpected object type")
        return self._identity(info)

    def full_fingerprint(self, handle: FakeWindowsHandle, *, directory: bool = False):
        info = os.fstat(handle.descriptor)
        if stat.S_ISDIR(info.st_mode) != directory or (not directory and not stat.S_ISREG(info.st_mode)):
            raise OSError("unexpected object type")
        created = int(getattr(info, "st_birthtime_ns", info.st_ctime_ns))
        fingerprint = (self._identity(info), int(info.st_size), created, int(info.st_ctime_ns),
                       int(info.st_mtime_ns), int(info.st_nlink))
        identity = self._identity(info)
        initial = self.initial_fingerprints.setdefault(identity, fingerprint)
        return initial if self.restore_original_fingerprint else fingerprint

    def relative_full_identity(self, parent: FakeWindowsHandle, leaf: str, *, directory: bool = False):
        self._component(leaf)
        info = os.stat(leaf, dir_fd=parent.descriptor, follow_symlinks=False)
        if (stat.S_ISDIR(info.st_mode) != directory
                or stat.S_ISLNK(info.st_mode)
                or (not directory and not stat.S_ISREG(info.st_mode))):
            raise OSError("relative name is not an ordinary expected object")
        return self._identity(info)

    def read_chunk(self, handle: FakeWindowsHandle, size: int) -> bytes:
        self.max_read_request = max(self.max_read_request, size)
        self.read_call_count += 1
        block = os.read(handle.descriptor, size)
        handle.read_calls += 1
        if self.capture_stream:
            self.stream_bytes[handle.serial].append(block)
        if (handle.serial == self.mutate_handle
                and handle.read_calls == self.mutate_after_read_call):
            before = os.stat(handle.path)
            with handle.path.open("r+b") as changed:
                changed.seek(0)
                changed.write(b"Z" * int(before.st_size))
                changed.flush()
                os.fsync(changed.fileno())
            os.utime(handle.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return block

    def close(self, handle: FakeWindowsHandle) -> None:
        self.close_calls[handle.serial] += 1
        if handle.closed:
            raise OSError("handle close attempted twice")
        os.close(handle.descriptor)
        handle.closed = True
        if handle.serial == self.fail_close_handle:
            self.fail_close_handle = None
            raise OSError("simulated close confirmation failure")


@contextmanager
def fake_windows_binding(root: Path):
    actual = open_project_root_binding(root)
    try:
        root_identity = tuple(actual.identity)
    finally:
        close_binding_releasing(actual, None)
    api = FakeWindowsHandleApi()
    binding = ProjectRootBinding(root, root_identity, api=api, handles=[api.open_root(root)])
    try:
        yield binding, api
    finally:
        close_binding_releasing(binding, None)


@unittest.skipUnless(
    os.name == "posix" and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd,
    "POSIX-backed fake Windows handle tests",
)
class WindowsArtifactReadFakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._legacy_before = set(ArtifactStore._REGISTERED_ARTIFACTS)
        self._scoped_before = set(artifact_store._PROJECT_REGISTERED_ARTIFACTS)
        self._identity_before = dict(artifact_store._PROJECT_ARTIFACT_IDENTITIES)
        self._digest_before = dict(artifact_store._PROJECT_ARTIFACT_DIGESTS)
        with artifact_store._ARTIFACT_REGISTRY_LOCK:
            ArtifactStore._REGISTERED_ARTIFACTS.clear()
            artifact_store._PROJECT_REGISTERED_ARTIFACTS.clear()
            artifact_store._PROJECT_ARTIFACT_IDENTITIES.clear()
            artifact_store._PROJECT_ARTIFACT_DIGESTS.clear()

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

    @staticmethod
    def _tmpdir():
        return tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())

    @staticmethod
    def _read(root: Path, target: Path, *, offset: int = 0, length: int = 64):
        return artifact_read(FakeWorker(root), None, {"artifact_id": str(target), "offset": offset, "length": length})

    def test_root_replacement_does_not_reuse_project_registration(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            target = root / "result.bin"
            target.write_bytes(b"registered bytes")
            with fake_windows_binding(root) as (binding, _api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(target)
                    moved = root.with_name("project-before-replacement")
                    os.replace(root, moved)
                    root.mkdir()
                    (root / target.name).write_bytes(b"replacement bytes")
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, root / target.name)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_leaf_replacement_is_rejected_by_registered_native_identity(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "result.bin"
            target.write_bytes(b"registered bytes")
            staged = nested / "replacement.bin"
            staged.write_bytes(b"replacement bytes")
            with fake_windows_binding(root) as (binding, _api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(target)
                    os.replace(staged, target)
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_same_file_id_with_restored_metadata_still_requires_registration_digest(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "same-file-id.bin"
            original = b"registered-content"
            replacement = b"changed-content!!!"
            self.assertEqual(len(original), len(replacement))
            target.write_bytes(original)
            original_stat = target.stat()
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(target)
                    api.restore_original_fingerprint = True
                    with target.open("r+b") as handle:
                        handle.write(replacement)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            self.assertEqual(error.exception.code, "ARTIFACT_CHANGED")

    def test_in_place_change_during_fake_windows_stream_suppresses_chunk(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "large.bin"
            target.write_bytes(b"A" * (2 * 1024 * 1024))
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(target)
                    api.restore_original_fingerprint = True
                    api.mutate_handle = len(api.handles) + 1
                    api.mutate_after_read_call = 1
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target, offset=0, length=1024)
            self.assertEqual(error.exception.code, "ARTIFACT_CHANGED")

    def test_change_during_fake_windows_registration_gains_no_read_authority(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "large.bin"
            target.write_bytes(b"A" * (2 * 1024 * 1024))
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    api.mutate_handle = len(api.handles) + 1
                    api.mutate_after_read_call = 1
                    ArtifactStore.register_artifact(target)
                    key = artifact_store._project_artifact_key(target)
                    self.assertTrue(ArtifactStore.is_registered_artifact(target))
                    self.assertNotIn(key, artifact_store._PROJECT_REGISTERED_ARTIFACTS)
                    self.assertNotIn(key, artifact_store._PROJECT_ARTIFACT_DIGESTS)
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_root_and_leaf_renames_during_fake_windows_read_suppress_chunk(self) -> None:
        for rename_kind in ("root", "leaf"):
            with self.subTest(rename_kind=rename_kind), self._tmpdir() as temp:
                parent = Path(temp)
                root = parent / "project-root"
                root.mkdir()
                target = root / "large.bin"
                target.write_bytes(b"A" * (2 * 1024 * 1024))
                with fake_windows_binding(root) as (binding, api):
                    with authorized_artifact_project_scope("windows-fake-project", binding):
                        ArtifactStore.register_artifact(target)
                        read_serial = len(api.handles) + 1
                        real_read = api.read_chunk

                        def rename_after_first_block(handle, size):
                            block = real_read(handle, size)
                            if handle.serial == read_serial and handle.read_calls == 1:
                                if rename_kind == "root":
                                    root.rename(parent / "renamed-root")
                                    root.mkdir()
                                    (root / target.name).write_bytes(b"R" * (2 * 1024 * 1024))
                                else:
                                    target.rename(root / "renamed-leaf.bin")
                            return block

                        with mock.patch.object(api, "read_chunk", side_effect=rename_after_first_block):
                            with self.assertRaises(ExecutionContractError) as error:
                                self._read(root, target, offset=0, length=16)
                self.assertIn(error.exception.code, {"ARTIFACT_CHANGED", "ACCESS_VIOLATION"})

    def test_scoped_registration_does_not_cross_fake_windows_project(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "result.bin"
            target.write_bytes(b"project-A-only")
            with fake_windows_binding(root) as (binding, _api):
                with authorized_artifact_project_scope("project-A", binding):
                    ArtifactStore.register_artifact(target)
                with authorized_artifact_project_scope("project-B", binding):
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_reparse_directory_does_not_receive_project_read_authority(self) -> None:
        with self._tmpdir() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            target = outside / "result.bin"
            target.write_bytes(b"outside project bytes")
            alias = root / "linked"
            alias.symlink_to(outside, target_is_directory=True)
            with fake_windows_binding(root) as (binding, _api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(alias / target.name)
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, alias / target.name)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_legacy_unscoped_registration_stays_queryable_but_cannot_authorize_windows_read(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "legacy.bin"
            target.write_bytes(b"legacy registration only")
            with fake_windows_binding(root) as (binding, _api):
                ArtifactStore.register_artifact(target)
                self.assertTrue(ArtifactStore.is_registered_artifact(target))
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")

    def test_same_file_id_content_change_during_stream_returns_no_chunk(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "result.bin"
            target.write_bytes(b"A" * (2 * 1024 * 1024))
            original = target.stat()
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(target)
                    handle_count = len(api.source_handles)
                    api.mutate_handle = api.source_handles[-1].serial + 1
                    api.mutate_after_read_call = 1
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target, offset=0, length=32)
                    api.mutate_handle = None
            changed = target.stat()
            self.assertEqual((changed.st_dev, changed.st_ino), (original.st_dev, original.st_ino))
            self.assertEqual(error.exception.code, "ARTIFACT_CHANGED")
            self.assertEqual(len(api.source_handles), handle_count + 1)

    def test_returned_hash_and_chunk_are_from_the_single_read_stream(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "result.bin"
            data = b"one registered stream, no second read" * 17000
            target.write_bytes(data)
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(target)
                    opens_before = len(api.source_handles)
                    api.capture_stream = True
                    result = self._read(root, target, offset=97, length=4096)
                    read_handles = api.source_handles[opens_before:]
            self.assertEqual(len(read_handles), 1)
            stream = b"".join(api.stream_bytes[read_handles[0].serial])
            chunk = base64.b64decode(result["data_base64"])
            self.assertEqual(stream, data)
            self.assertEqual(result["whole_file_sha256"], hashlib.sha256(stream).hexdigest())
            self.assertEqual(chunk, stream[97:97 + 4096])
            self.assertEqual(result["chunk_sha256"], hashlib.sha256(chunk).hexdigest())

    def test_close_exception_releases_each_owned_handle_once_and_returns_no_data(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "result.bin"
            target.write_bytes(b"close failure must suppress response")
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    ArtifactStore.register_artifact(target)
                    first_read_handle = len(api.handles)
                    api.fail_close_handle = len(api.handles) + 2
                    with self.assertRaises(ExecutionContractError) as error:
                        self._read(root, target)
                    read_handles = api.handles[first_read_handle:]
            self.assertEqual(error.exception.code, "ACCESS_VIOLATION")
            self.assertEqual(error.exception.details.get("cleanup_failure_count"), 1)
            self.assertTrue(read_handles)
            self.assertTrue(all(api.close_calls[handle.serial] == 1 for handle in read_handles))
            self.assertTrue(all(handle.closed for handle in read_handles))

    def _assert_registration_cleanup_error(self, error, root: Path, target: Path) -> None:
        self.assertEqual(error.code, "ACCESS_VIOLATION")
        self.assertEqual(error.details.get("cleanup_status"), "UNCONFIRMED")
        self.assertEqual(error.details.get("cleanup_failure_count"), 1)
        self.assertFalse(ArtifactStore.is_registered_artifact(target))
        key = artifact_store._project_artifact_key(target)
        self.assertNotIn(key, artifact_store._PROJECT_REGISTERED_ARTIFACTS)
        self.assertNotIn(key, artifact_store._PROJECT_ARTIFACT_IDENTITIES)
        self.assertNotIn(key, artifact_store._PROJECT_ARTIFACT_DIGESTS)
        with self.assertRaises(ExecutionContractError) as read_error:
            self._read(root, target)
        self.assertEqual(read_error.exception.code, "ACCESS_VIOLATION")

    def test_scoped_registration_propagates_temporary_windows_root_close_failure(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "result.bin"
            target.write_bytes(b"root close failure")
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    temporary_handle = api.open_root(root)
                    temporary = ProjectRootBinding(
                        root, tuple(api.full_identity(temporary_handle, directory=True)),
                        api=api, handles=[temporary_handle],
                    )
                    api.fail_close_handle = temporary_handle.serial
                    with mock.patch.object(project_root, "open_project_root_binding", return_value=temporary):
                        with self.assertRaises(ExecutionContractError) as error:
                            ArtifactStore.register_artifact(target)
                    self._assert_registration_cleanup_error(error.exception, root, target)
            self.assertTrue(all(api.close_calls[handle.serial] == 1 for handle in api.handles))
            self.assertTrue(all(handle.closed for handle in api.handles))

    def test_scoped_registration_propagates_capture_windows_root_close_failure(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "result.bin"
            target.write_bytes(b"capture root close failure")
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    first_serial = len(api.handles) + 1
                    api.fail_close_handle = first_serial + 1

                    def open_fake_root(path, **_kwargs):
                        fake_path = Path(path)
                        handle = api.open_root(fake_path)
                        return ProjectRootBinding(
                            fake_path, tuple(api.full_identity(handle, directory=True)),
                            api=api, handles=[handle],
                        )

                    with mock.patch.object(
                            project_root, "open_project_root_binding", side_effect=open_fake_root,
                    ) as open_binding:
                        with self.assertRaises(ExecutionContractError) as error:
                            ArtifactStore.register_artifact(target)
                    self.assertEqual(open_binding.call_count, 2)
                    self._assert_registration_cleanup_error(error.exception, root, target)
            self.assertTrue(all(api.close_calls[handle.serial] == 1 for handle in api.handles))
            self.assertTrue(all(handle.closed for handle in api.handles))

    def test_scoped_registration_propagates_windows_directory_close_failure(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "result.bin"
            target.write_bytes(b"directory close failure")
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    api.fail_close_handle = len(api.handles) + 1
                    with self.assertRaises(ExecutionContractError) as error:
                        ArtifactStore.register_artifact(target)
                    self._assert_registration_cleanup_error(error.exception, root, target)
            self.assertTrue(all(api.close_calls[handle.serial] == 1 for handle in api.handles))
            self.assertTrue(all(handle.closed for handle in api.handles))

    def test_scoped_registration_propagates_windows_file_close_failure(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "result.bin"
            target.write_bytes(b"file close failure")
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    api.fail_close_handle = len(api.handles) + 2
                    with self.assertRaises(ExecutionContractError) as error:
                        ArtifactStore.register_artifact(target)
                    self._assert_registration_cleanup_error(error.exception, root, target)
            self.assertTrue(all(api.close_calls[handle.serial] == 1 for handle in api.handles))
            self.assertTrue(all(handle.closed for handle in api.handles))

    def test_large_file_read_hashes_in_bounded_blocks_and_memory(self) -> None:
        with self._tmpdir() as temp:
            root = Path(temp)
            target = root / "large.bin"
            block = bytes(range(256)) * 4096
            blocks = 32
            with target.open("wb") as output:
                for _ in range(blocks):
                    output.write(block)
            expected_digest = hashlib.sha256()
            for _ in range(blocks):
                expected_digest.update(block)
            offset = 5 * 1024 * 1024 + 13
            length = 32 * 1024
            with fake_windows_binding(root) as (binding, api):
                with authorized_artifact_project_scope("windows-fake-project", binding):
                    tracemalloc.start()
                    try:
                        ArtifactStore.register_artifact(target)
                        result = self._read(root, target, offset=offset, length=length)
                        _current, peak = tracemalloc.get_traced_memory()
                    finally:
                        tracemalloc.stop()
            chunk = base64.b64decode(result["data_base64"])
            self.assertEqual(result["whole_file_sha256"], expected_digest.hexdigest())
            self.assertEqual(chunk, block[13:13 + length])
            self.assertLessEqual(api.max_read_request, 1024 * 1024)
            self.assertGreater(api.read_call_count, blocks - 1)
            self.assertLess(peak, 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
