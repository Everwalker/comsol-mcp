from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_handoff_snapshot", ROOT / "tools/windows_handoff_snapshot.py")
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


def add_file(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))


def write_snapshot(path: Path, files: dict[str, bytes], *, manifest_files: dict[str, bytes] | None = None) -> None:
    manifest_files = files if manifest_files is None else manifest_files
    manifest = {
        "snapshot_id": "s",
        "files": [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in manifest_files.items()
        ],
    }
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            add_file(tar, name, data)
        add_file(tar, "SNAPSHOT_MANIFEST.json", json.dumps(manifest).encode())
        add_file(tar, "WORKTREE.patch", b"")
        add_file(tar, "NEW_FILES.json", b"[]")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "", ".", r"..\escape", r"\\server\share", "C:/absolute", "safe:stream", "safe//file", "CON/file", "safe/trailing. "])
def test_safe_member_path_rejects_windows_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        module.safe_member_path(name)


def test_extract_refuses_existing_destination_before_archive_access(tmp_path: Path) -> None:
    archive, target = tmp_path / "x.tar.gz", tmp_path / "target"
    archive.write_bytes(b"not a tar archive")
    target.mkdir()
    with pytest.raises(FileExistsError):
        module.extract_verified(archive, target)


def test_git_paths_uses_nul_records_without_git_unicode_quoting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []

    def fake_check_output(command, **kwargs):
        calls.append((command, kwargs))
        return "unicodé.txt\0plain.txt\0".encode()

    monkeypatch.setattr(module.subprocess, "check_output", fake_check_output)
    assert module.git_paths(tmp_path, "--cached") == [Path("unicodé.txt"), Path("plain.txt")]
    assert calls[0][0][-1] == "-z"
    assert "text" not in calls[0][1]


def test_extract_refuses_symlink_member_without_creating_destination(tmp_path: Path) -> None:
    archive, target = tmp_path / "x.tar.gz", tmp_path / "target"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("link"); info.type = tarfile.SYMTYPE; info.linkname = "elsewhere"; tar.addfile(info)
    with pytest.raises(ValueError, match="non-regular"):
        module.extract_verified(archive, target)
    assert not target.exists()


def test_extract_preverifies_manifest_hashes_before_creating_destination(tmp_path: Path) -> None:
    archive, target = tmp_path / "x.tar.gz", tmp_path / "target"
    write_snapshot(archive, {"safe/file.txt": b"actual!!"}, manifest_files={"safe/file.txt": b"expected"})
    with pytest.raises(ValueError, match="hash verification failed"):
        module.extract_verified(archive, target)
    assert not target.exists()


def test_extract_rejects_unmanifested_member_without_creating_destination(tmp_path: Path) -> None:
    archive, target = tmp_path / "x.tar.gz", tmp_path / "target"
    write_snapshot(archive, {"safe/file.txt": b"expected", "unlisted.txt": b"reject"}, manifest_files={"safe/file.txt": b"expected"})
    with pytest.raises(ValueError, match="exactly match"):
        module.extract_verified(archive, target)
    assert not target.exists()


def test_extract_rejects_duplicate_casefolded_windows_member(tmp_path: Path) -> None:
    archive, target = tmp_path / "x.tar.gz", tmp_path / "target"
    with tarfile.open(archive, "w:gz") as tar:
        add_file(tar, "safe/file.txt", b"one")
        add_file(tar, "SAFE/FILE.TXT", b"two")
        add_file(tar, "SNAPSHOT_MANIFEST.json", json.dumps({"snapshot_id": "s", "files": []}).encode())
        add_file(tar, "WORKTREE.patch", b"")
        add_file(tar, "NEW_FILES.json", b"[]")
    with pytest.raises(ValueError, match="duplicate Windows"):
        module.extract_verified(archive, target)
    assert not target.exists()


def test_extract_verifies_exact_manifest_and_hashes(tmp_path: Path) -> None:
    archive, target = tmp_path / "x.tar.gz", tmp_path / "target"
    content = b"expected"
    write_snapshot(archive, {"safe/file.txt": content})
    assert module.extract_verified(archive, target) == {"snapshot_id": "s", "verified_files": 1}
    assert (target / "safe/file.txt").read_bytes() == content
