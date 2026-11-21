"""Project-bound streaming file digests used by managed reload.

POSIX tests use disposable files. The Windows handle case is a fake API
exercising the real binding method and is not Windows/native evidence.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import ProjectRootBinding, open_project_root_binding


def test_digest_is_streaming_and_not_limited_to_java_source_size(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    content = (b"ABCD" * (1024 * 1024)) + b"end"
    (root / "sample.dat").write_bytes(content)
    binding = open_project_root_binding(root)
    calls = []
    real_read = os.read

    def read_spy(fd, count):
        calls.append(count)
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", read_spy)
    try:
        digest, size = binding.hash_file(("sample.dat",))
        assert digest == hashlib.sha256(content).hexdigest()
        assert size == len(content)
        assert len(calls) > 1 and max(calls) <= 1024 * 1024
    finally:
        binding.close()


def test_digest_read_is_bounded_by_initial_file_size(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample.dat").write_bytes(b"A")
    binding = open_project_root_binding(root)
    real_read = os.read
    calls = []

    def persistent_reader(fd, count):
        calls.append(count)
        if len(calls) == 1:
            return real_read(fd, count)
        if len(calls) > 2:
            raise AssertionError("read continued beyond the initial size")
        return b"extra"

    monkeypatch.setattr(os, "read", persistent_reader)
    try:
        digest, size = binding.hash_file(("sample.dat",))
        assert (digest, size) == (hashlib.sha256(b"A").hexdigest(), 1)
        assert len(calls) == 1
    finally:
        binding.close()


def test_digest_detects_file_change_during_read(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "sample.dat"
    source.write_bytes(b"A" * (2 * 1024 * 1024))
    binding = open_project_root_binding(root)
    real_read = os.read
    changed = []

    def read_and_change(fd, count):
        block = real_read(fd, count)
        if block and not changed:
            with source.open("ab") as handle:
                handle.write(b"changed")
            changed.append(True)
        return block

    monkeypatch.setattr(os, "read", read_and_change)
    try:
        with pytest.raises(ExecutionContractError) as exc:
            binding.hash_file(("sample.dat",))
        assert changed and exc.value.code == "ARTIFACT_CHANGED"
    finally:
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO behavior")
def test_digest_refuses_fifo_without_blocking(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    os.mkfifo(root / "pipe")
    binding = open_project_root_binding(root)
    try:
        with pytest.raises(ExecutionContractError) as exc:
            binding.hash_file(("pipe",))
        assert exc.value.code == "ACCESS_VIOLATION"
    finally:
        binding.close()


def test_windows_hash_uses_bound_handles_and_closes_them():
    content = b"synthetic windows data"

    class FakeWindowsAPI:
        def __init__(self):
            self.events = []
            self.chunks = [content, b""]

        def open_child_directory(self, parent, name):
            self.events.append(("directory", parent, name))
            return "child"

        def open_source_for_snapshot(self, parent, name):
            self.events.append(("file", parent, name))
            return "file"

        def source_fingerprint(self, handle):
            assert handle == "file"
            return ((1, 2), len(content), 3, 4, 1)

        def read_chunk(self, handle, count):
            assert handle == "file" and count <= 1024 * 1024
            return self.chunks.pop(0)

        def close(self, handle):
            self.events.append(("close", handle))

    api = FakeWindowsAPI()
    binding = ProjectRootBinding(Path("project"), (1, 2), api=api, handles=["root"])
    digest, size = binding.hash_file(("dir", "file.dat"))
    assert (digest, size) == (hashlib.sha256(content).hexdigest(), len(content))
    assert api.events == [("directory", "root", "dir"), ("file", "child", "file.dat"),
                          ("close", "file"), ("close", "child")]
    binding.close()
    assert api.events[-1] == ("close", "root")


def test_windows_hash_withholds_changed_file_and_closes_handles():
    class FakeWindowsAPI:
        def __init__(self):
            self.events = []
            self.fingerprints = iter([((1, 2), 1, 3, 4, 1), ((1, 2), 1, 3, 5, 1)])

        def open_child_directory(self, parent, name):
            return "child"

        def open_source_for_snapshot(self, parent, name):
            return "file"

        def source_fingerprint(self, handle):
            return next(self.fingerprints)

        def read_chunk(self, handle, count):
            return b"A"

        def close(self, handle):
            self.events.append(handle)

    api = FakeWindowsAPI()
    binding = ProjectRootBinding(Path("project"), (1, 2), api=api, handles=["root"])
    try:
        with pytest.raises(ExecutionContractError) as exc:
            binding.hash_file(("dir", "file.dat"))
        assert exc.value.code == "ARTIFACT_CHANGED"
        assert api.events == ["file", "child"]
    finally:
        binding.close()
    assert api.events == ["file", "child", "root"]
