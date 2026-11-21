"""Control-side POSIX link behavior; no engine or real user files involved."""
import os

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._project_root import open_project_root_binding


pytestmark = pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="POSIX non-root permission and os.read instrumentation required",
)


def test_cross_project_hardlink_needs_no_source_parent_write(tmp_path):
    source_dir = tmp_path / "sibling"
    source_dir.mkdir()
    source = source_dir / "source.dat"
    source.write_bytes(b"synthetic sibling bytes")
    project = tmp_path / "project"
    project.mkdir()
    binding = open_project_root_binding(project)
    source_dir.chmod(0o555)
    try:
        assert not os.access(source_dir, os.W_OK)
        os.link(source, project / "alias.dat")
        assert source.stat().st_nlink == 2
        with pytest.raises(ExecutionContractError) as error:
            binding.hash_file(("alias.dat",))
        assert error.value.code == "ACCESS_VIOLATION"
    finally:
        source_dir.chmod(0o755)
        binding.close()


@pytest.mark.parametrize("method", ["hash_file", "read_file"])
def test_link_and_unlink_during_read_changes_ctime_and_is_withheld(tmp_path, monkeypatch, method):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "source.dat"
    source.write_bytes(b"synthetic project bytes")
    binding = open_project_root_binding(project)
    before = source.stat()
    real_read = os.read
    observed = []

    def read_with_transient_link(fd, count):
        data = real_read(fd, count)
        if data and not observed:
            alias = project / "transient.dat"
            os.link(source, alias)
            alias.unlink()
            observed.append(source.stat())
        return data

    monkeypatch.setattr(os, "read", read_with_transient_link)
    try:
        refusal = None
        try:
            if method == "hash_file":
                binding.hash_file(("source.dat",))
            else:
                binding.read_file(("source.dat",), max_bytes=1024)
        except ExecutionContractError as error:
            refusal = error
        assert observed and observed[0].st_nlink == before.st_nlink == 1
        if observed[0].st_ctime_ns == before.st_ctime_ns:
            pytest.skip("filesystem did not expose a ctime change for this transient link")
        assert refusal is not None and refusal.code == "ARTIFACT_CHANGED"
    finally:
        binding.close()
