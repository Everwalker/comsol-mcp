"""T20 project-root digest: cross-project hard-link negative (fake engine only)."""
import hashlib
import os
from pathlib import Path

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import ProjectRootBinding, open_project_root_binding
from tests.test_t20_artifact_boundary import _call, _envelope, _make_project, _node_path


@pytest.mark.skipif(os.name == "nt", reason="POSIX os.read instrumentation")
def test_reload_control_digest_never_reads_cross_project_hardlink(tmp_path, monkeypatch):
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, "hardlink", monkeypatch)
    try:
        # Positive control uses the same daemon/action before the negative case.
        inside = root / "inputs" / "inside.dat"
        inside.write_bytes(b"authorized-project-data")
        interp.values["filename"] = str(inside)
        path = _node_path(("func", "interpA"))
        positive = _call(daemon, "function.data_reload", {"path": path},
                         _envelope(project, exe, "positive"))
        assert positive["success"] is True, positive
        assert positive["data"]["sha256"]["value"] == hashlib.sha256(inside.read_bytes()).hexdigest()
        assert interp.refreshes == 1

        outside = tmp_path / "other-project" / "other.dat"
        outside.parent.mkdir()
        outside.write_bytes(b"synthetic-other-project-data")
        alias = root / "inputs" / "alias.dat"
        os.link(outside, alias)
        outside_info = outside.stat()
        assert outside_info.st_nlink >= 2
        assert (alias.stat().st_dev, alias.stat().st_ino) == (outside_info.st_dev, outside_info.st_ino)
        interp.values["filename"] = str(alias)

        real_read = os.read
        observed_reads = []

        def tracked_read(fd, count):
            try:
                info = os.fstat(fd)
            except OSError:
                pass
            else:
                if (info.st_dev, info.st_ino) == (outside_info.st_dev, outside_info.st_ino):
                    observed_reads.append(count)
            return real_read(fd, count)

        monkeypatch.setattr(os, "read", tracked_read)
        negative = _call(daemon, "function.data_reload", {"path": path},
                         _envelope(project, positive["execution"], "hardlink"))
        digest = (negative.get("data") or {}).get("sha256") or {}
        assert observed_reads == [], f"control process read the other project's inode: {observed_reads}"
        assert digest.get("value") is None, negative
        assert digest.get("status") != "verified", negative
        # The fake refresh only records a call. Actual engine file access is a
        # separate unresolved boundary and is not claimed to be prevented.
        assert interp.refreshes == 2
    finally:
        daemon.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX os.read instrumentation")
def test_java_source_snapshot_refuses_cross_project_hardlink_before_read(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "other-project.java"
    outside.write_bytes(b"synthetic other project Java source")
    os.link(outside, root / "source.java")
    binding = open_project_root_binding(root)
    real_read = os.read
    reads = []

    def tracked_read(fd, count):
        reads.append(count)
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", tracked_read)
    try:
        with pytest.raises(ExecutionContractError) as error:
            binding.read_file(("source.java",), max_bytes=1024)
        assert error.value.code == "ACCESS_VIOLATION"
        assert reads == []
    finally:
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX os.read instrumentation")
@pytest.mark.parametrize("method", ["hash_file", "read_file"])
def test_posix_binding_withholds_when_link_count_changes_during_read(tmp_path, monkeypatch, method):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.dat"
    source.write_bytes(b"authorized original bytes")
    binding = open_project_root_binding(root)
    real_read = os.read
    added_link = []

    def read_and_link(fd, count):
        data = real_read(fd, count)
        if data and not added_link:
            os.link(source, root / "alias.dat")
            added_link.append(True)
        return data

    monkeypatch.setattr(os, "read", read_and_link)
    try:
        with pytest.raises(ExecutionContractError) as error:
            if method == "hash_file":
                binding.hash_file(("source.dat",))
            else:
                binding.read_file(("source.dat",), max_bytes=1024)
        assert error.value.code == "ARTIFACT_CHANGED"
        assert added_link == [True]
    finally:
        binding.close()


@pytest.mark.parametrize("method", ["hash_file", "read_file"])
def test_windows_binding_refuses_multiple_links_before_read(method):
    class FakeWindowsAPI:
        def __init__(self):
            self.events = []

        def open_source_for_snapshot(self, parent, name):
            self.events.append(("open", parent, name))
            return "file"

        def source_fingerprint(self, handle):
            assert handle == "file"
            return ((1, 2), 4, 3, 4, 2)

        def read_chunk(self, handle, count):
            self.events.append(("read", handle, count))
            return b"test"

        def close(self, handle):
            self.events.append(("close", handle))

    api = FakeWindowsAPI()
    binding = ProjectRootBinding(Path("project"), (1, 2), api=api, handles=["root"])
    try:
        with pytest.raises(ExecutionContractError) as error:
            if method == "hash_file":
                binding.hash_file(("source.dat",))
            else:
                binding.read_file(("source.dat",), max_bytes=1024)
        assert error.value.code == "ACCESS_VIOLATION"
        assert api.events == [("open", "root", "source.dat"), ("close", "file")]
    finally:
        binding.close()
    assert api.events[-1] == ("close", "root")
