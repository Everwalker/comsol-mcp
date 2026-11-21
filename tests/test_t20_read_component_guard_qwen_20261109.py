r"""T20 read_file component-atomicity guard; Windows routes are fake-API only.

A single ``read_file`` component that embeds ``/``, ``\`` or NUL is not atomic:
POSIX openat resolves `..`/separators beneath dir_fd and absolute parts ignore
dir_fd entirely, while `hash_file` already refuses such parts. These controls
require the read guard to refuse every such shape with ACCESS_VIOLATION before
any OS or Windows-API dispatch, and keep ordinary atomic multi-component reads
working. The fake Windows API proves only that no open call is attempted; it
does not establish native Windows behavior.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from comsol_mcp import _project_root as project_root
from comsol_mcp._execution_contract import ExecutionContractError


_ESCAPE_CONTENT = b"OUTSIDE-CONTENT"
_BOUND_CONTENT = b"class Main {}\n"


def _escape_fixture(tmp_path):
    root = tmp_path / "project"
    source_dir = root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "Main.java").write_bytes(_BOUND_CONTENT)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(_ESCAPE_CONTENT)
    return root, outside


def _malicious_shapes(root, outside):
    return {
        "single_ascending": ("../outside.txt",),
        "absolute": (str(outside),),
        "intermediate_separator": ("src/../..", "outside.txt"),
        "null_byte": ("bad\x00name.java",),
        "backslash": ("src\\..\\..\\outside.txt",),
    }


class _DispatchProbeOS:
    """Delegates to the real os but fails loudly on any open-family call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name == "supports_dir_fd":
            return os.supports_dir_fd | {self.open}
        return getattr(os, name)

    def dup(self, fd):
        self.calls.append(("dup", fd))
        raise AssertionError("read_file dispatch reached the OS before the guard")

    def open(self, path, flags, *args, **kwargs):
        self.calls.append(("open", os.fspath(path)))
        raise AssertionError("read_file dispatch reached the OS before the guard")


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
@pytest.mark.parametrize("shape", ["single_ascending", "absolute",
                                   "intermediate_separator", "null_byte", "backslash"])
def test_read_file_refuses_non_atomic_component_before_any_os_call(tmp_path, monkeypatch, shape):
    root, outside = _escape_fixture(tmp_path)
    binding = project_root.open_project_root_binding(root)
    probe = _DispatchProbeOS()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(project_root, "os", probe)
            with pytest.raises(ExecutionContractError) as caught:
                binding.read_file(_malicious_shapes(root, outside)[shape], max_bytes=1024)
        assert caught.value.code == "ACCESS_VIOLATION", caught.value.as_dict()
        assert "source path is not a project-relative file" in str(caught.value)
        assert probe.calls == [], probe.calls
    finally:
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
def test_multi_component_read_still_returns_bound_bytes(tmp_path):
    root, _outside = _escape_fixture(tmp_path)
    binding = project_root.open_project_root_binding(root)
    try:
        data, identity = binding.read_file(("src", "Main.java"), max_bytes=1024)
        assert data == _BOUND_CONTENT
        assert identity["size"] == len(_BOUND_CONTENT)
        digest, size = binding.hash_file(("src", "Main.java"))
        assert size == len(_BOUND_CONTENT)
        assert len(digest) == 64
    finally:
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
def test_atomic_components_containing_dots_remain_allowed(tmp_path):
    root, _outside = _escape_fixture(tmp_path)
    (root / "...").write_bytes(b"dot-dot-dot\n")
    (root / "src" / "a.b.java").write_bytes(b"class Ab {}\n")
    binding = project_root.open_project_root_binding(root)
    try:
        assert binding.read_file(("...",), max_bytes=64)[0] == b"dot-dot-dot\n"
        assert binding.read_file(("src", "a.b.java"), max_bytes=64)[0] == b"class Ab {}\n"
    finally:
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX dirfd fixture")
@pytest.mark.parametrize("shape", ["single_ascending", "absolute",
                                   "intermediate_separator", "null_byte", "backslash"])
def test_read_file_guard_matches_hash_file_guard_on_same_shapes(tmp_path, shape):
    root, outside = _escape_fixture(tmp_path)
    binding = project_root.open_project_root_binding(root)
    parts = _malicious_shapes(root, outside)[shape]
    try:
        with pytest.raises(ExecutionContractError) as read_error:
            binding.read_file(parts, max_bytes=1024)
        with pytest.raises(ExecutionContractError) as hash_error:
            binding.hash_file(parts)
        assert read_error.value.code == "ACCESS_VIOLATION", read_error.value.as_dict()
        assert hash_error.value.code == "ACCESS_VIOLATION", hash_error.value.as_dict()
    finally:
        binding.close()


class _FakeWindowsReadAPI:
    """Fake-only Windows stand-in; records dispatch instead of touching native APIs."""

    def __init__(self):
        self.open_attempts = []
        self.close_attempts = []
        self.live = {"root"}
        self.data = b"fixture data\n"
        self.offset = 0

    def open_child_directory(self, parent, name):
        self.open_attempts.append(("directory", parent, name))
        if parent == "root" and name == "source_dir":
            self.live.add("child")
            return "child"
        raise ExecutionContractError("ACCESS_VIOLATION", "injected fake Windows open refusal")

    def open_source_for_snapshot(self, parent, name):
        self.open_attempts.append(("file", parent, name))
        if parent == "child" and name == "source.dat":
            self.live.add("file")
            return "file"
        raise ExecutionContractError("ACCESS_VIOLATION", "injected fake Windows open refusal")

    def source_fingerprint(self, handle):
        assert handle == "file"
        return ((1, 2), len(self.data), 3, 116444736000000000, 1)

    def read_chunk(self, handle, count):
        assert handle == "file"
        chunk = self.data[self.offset:self.offset + count]
        self.offset += len(chunk)
        return chunk

    def close(self, handle):
        self.close_attempts.append(handle)
        self.live.discard(handle)


@pytest.mark.parametrize("parts", [("..\\outside.txt",), ("../outside.txt",), ("bad\x00name",)])
def test_fake_windows_read_guard_refuses_before_any_api_open(parts):
    api = _FakeWindowsReadAPI()
    binding = project_root.ProjectRootBinding(Path("project"), (1, 2), api=api, handles=["root"])
    try:
        with pytest.raises(ExecutionContractError) as caught:
            binding.read_file(parts, max_bytes=1024)
        assert caught.value.code == "ACCESS_VIOLATION", caught.value.as_dict()
        assert "source path is not a project-relative file" in str(caught.value)
        assert api.open_attempts == [], api.open_attempts
        assert api.close_attempts == [], api.close_attempts
    finally:
        binding.close()
        assert api.live == set()


def test_fake_windows_multi_component_read_still_returns_bytes():
    api = _FakeWindowsReadAPI()
    binding = project_root.ProjectRootBinding(Path("project"), (1, 2), api=api, handles=["root"])
    try:
        data, identity = binding.read_file(("source_dir", "source.dat"), max_bytes=1024)
        assert data == api.data
        assert identity["size"] == len(api.data)
    finally:
        binding.close()
        assert api.live == set()
