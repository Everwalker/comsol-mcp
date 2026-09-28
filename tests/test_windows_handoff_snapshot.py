from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_handoff_snapshot", ROOT / "tools/windows_handoff_snapshot.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def add_file(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


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


def init_repo(root: Path, files: dict[str, bytes]) -> str:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "snapshot-test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Snapshot Test"], cwd=root, check=True)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    subprocess.run(["git", "add", "--all"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def write_source_manifest(root: Path, manifest_path: Path, names: list[str]) -> Path:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for name in names:
        entries.append({"path": name, "sha256": module.sha256(root / name)})
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "source_head": module.git_head(root), "files": entries}, indent=2),
        encoding="utf-8",
    )
    return manifest_path


@pytest.mark.parametrize("name", [
    "../escape", "/absolute", "", ".", r"..\escape", r"\\server\share", "C:/absolute",
    "safe:stream", "safe//file", "CON/file", "safe/trailing. ", "src/a?.py", "src/a*.py",
    "src/a|b.py", "src/a<b.py", "src/a>b.py", 'src/a"b.py', "src/a\x01b.py",
])
def test_safe_member_path_rejects_windows_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        module.safe_member_path(name)


@pytest.mark.parametrize("control", [chr(value) for value in range(0x20)])
def test_safe_member_path_rejects_all_ascii_control_characters(control: str) -> None:
    with pytest.raises(ValueError, match="unsafe archive member"):
        module.safe_member_path(f"src/a{control}b.py")


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
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "elsewhere"
        tar.addfile(info)
    with pytest.raises(ValueError, match="non-regular"):
        module.extract_verified(archive, target)
    assert not target.exists()


@pytest.mark.parametrize("name", ["src/a?.py", "src/a\x01b.py"])
def test_extract_rejects_windows_invalid_name_before_creating_destination(tmp_path: Path, name: str) -> None:
    archive, target = tmp_path / "invalid-name.tar.gz", tmp_path / "target"
    with tarfile.open(archive, "w:gz") as tar:
        add_file(tar, name, b"invalid Windows member")
    with pytest.raises(ValueError, match="unsafe archive member"):
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


def test_default_snapshot_contains_tracked_source_only_and_roundtrips(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(
        root,
        {
            "src/keep.py": b"value = 1\n",
            "src/secret_credentials.py": b"TRACKED_SECRET_SENTINEL\n",
            "docs/full_project_execution/evidence/raw.py": b"RAW_EVIDENCE_SENTINEL\n",
            "runtime/session.json": b"RUNTIME_SENTINEL\n",
            ".pytest_cache/cache.py": b"CACHE_SENTINEL\n",
            "assets/model.mph": b"NATIVE_MODEL_SENTINEL\n",
        },
    )
    (root / "src/keep.py").write_bytes(b"value = 2\n")
    (root / "src/untracked.py").write_bytes(b"UNTRACKED_SOURCE_SENTINEL\n")
    (root / ".env").write_text("UNTRACKED_SECRET_SENTINEL\n", encoding="utf-8")
    (root / "docs/full_project_execution/evidence/raw.py").write_bytes(b"CHANGED_RAW_EVIDENCE_SENTINEL\n")

    archive = tmp_path / "default.tar.gz"
    result = module.create_snapshot(root, archive, "default-safe")
    assert [entry["path"] for entry in result["files"]] == ["src/keep.py"]
    with tarfile.open(archive, "r:gz") as tar:
        assert set(tar.getnames()) == {"src/keep.py", *module.METADATA_FILES}
        patch = tar.extractfile("WORKTREE.patch").read()
        assert b"value = 2" in patch
        for sentinel in (
            b"TRACKED_SECRET_SENTINEL", b"RAW_EVIDENCE_SENTINEL", b"CHANGED_RAW_EVIDENCE_SENTINEL",
            b"RUNTIME_SENTINEL", b"CACHE_SENTINEL", b"NATIVE_MODEL_SENTINEL",
            b"UNTRACKED_SOURCE_SENTINEL", b"UNTRACKED_SECRET_SENTINEL",
        ):
            assert sentinel not in patch
            assert all(sentinel not in tar.extractfile(name).read() for name in tar.getnames())
        assert json.loads(tar.extractfile("NEW_FILES.json").read()) == []
    destination = tmp_path / "extracted"
    assert module.extract_verified(archive, destination) == {"snapshot_id": "default-safe", "verified_files": 1}
    assert (destination / "src/keep.py").read_bytes() == b"value = 2\n"


def test_hash_bound_manifest_includes_only_reviewed_untracked_source(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(
        root,
        {
            "src/keep.py": b"value = 1\n",
            "src/unselected_secret.txt": b"UNSELECTED_SECRET_SENTINEL\n",
            "evidence/raw.txt": b"UNSELECTED_RAW_SENTINEL\n",
        },
    )
    (root / "src/keep.py").write_bytes(b"value = 3\n")
    (root / "evidence/raw.txt").write_bytes(b"CHANGED_RAW_SENTINEL\n")
    (root / "src/new_required.py").write_bytes(b"reviewed untracked source\n")
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", ["src/keep.py", "src/new_required.py"])

    archive = tmp_path / "reviewed.tar.gz"
    result = module.create_snapshot(root, archive, "reviewed-only", manifest)
    assert result["selection_mode"] == "hash_bound_source_manifest"
    assert result["source_manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    with tarfile.open(archive, "r:gz") as tar:
        assert set(tar.getnames()) == {"src/keep.py", "src/new_required.py", *module.METADATA_FILES}
        patch = tar.extractfile("WORKTREE.patch").read()
        assert b"value = 3" in patch
        assert b"new_required.py" not in patch
        assert b"UNSELECTED_SECRET_SENTINEL" not in patch
        assert b"UNSELECTED_RAW_SENTINEL" not in patch
        assert b"CHANGED_RAW_SENTINEL" not in patch
        assert json.loads(tar.extractfile("NEW_FILES.json").read()) == ["src/new_required.py"]
        assert tar.extractfile("src/new_required.py").read() == b"reviewed untracked source\n"
    destination = tmp_path / "reviewed-extracted"
    assert module.extract_verified(archive, destination) == {"snapshot_id": "reviewed-only", "verified_files": 2}
    assert (destination / "src/new_required.py").read_bytes() == b"reviewed untracked source\n"


def test_hash_bound_manifest_rejects_source_drift_before_archive_creation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"before\n"})
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", ["src/keep.py"])
    (root / "src/keep.py").write_bytes(b"after\n")
    archive = tmp_path / "drift.tar.gz"
    with pytest.raises(ValueError, match="hash drift"):
        module.create_snapshot(root, archive, "drift", manifest)
    assert not archive.exists()


def test_manifest_expected_file_hash_survives_later_source_selection_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"reviewed\n"})
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", ["src/keep.py"])
    original = module._refuse_selected_renames

    def mutate_after_manifest_validation(repository: Path, paths: list[Path]) -> None:
        original(repository, paths)
        (repository / "src/keep.py").write_bytes(b"changed after review\n")

    monkeypatch.setattr(module, "_refuse_selected_renames", mutate_after_manifest_validation)
    archive = tmp_path / "raced-source.tar.gz"
    with pytest.raises(ValueError, match="source manifest hash drift after source selection"):
        module.create_snapshot(root, archive, "raced-source", manifest)
    assert not archive.exists()


def test_manifest_expected_head_survives_later_source_selection_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"reviewed\n"})
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", ["src/keep.py"])
    original = module._refuse_selected_renames

    def advance_head_after_manifest_validation(repository: Path, paths: list[Path]) -> None:
        original(repository, paths)
        subprocess.run(["git", "commit", "--allow-empty", "-qm", "change head during packaging"], cwd=repository, check=True)

    monkeypatch.setattr(module, "_refuse_selected_renames", advance_head_after_manifest_validation)
    archive = tmp_path / "raced-head.tar.gz"
    with pytest.raises(ValueError, match="Git HEAD changed after source selection"):
        module.create_snapshot(root, archive, "raced-head", manifest)
    assert not archive.exists()


def test_source_manifest_digest_is_rechecked_before_archive_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"reviewed\n"})
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", ["src/keep.py"])
    original = module._verify_created_archive

    def mutate_manifest_before_preflight(
        archive_path: Path,
        metadata: dict,
        patch: bytes,
        new_files: list[str],
        repository: Path,
        manifest_path: Path | None,
    ) -> None:
        assert manifest_path is not None
        manifest_path.write_text("{}\n", encoding="utf-8")
        original(archive_path, metadata, patch, new_files, repository, manifest_path)

    monkeypatch.setattr(module, "_verify_created_archive", mutate_manifest_before_preflight)
    archive = tmp_path / "raced-manifest.tar.gz"
    with pytest.raises(ValueError, match="source manifest changed"):
        module.create_snapshot(root, archive, "raced-manifest", manifest)
    assert not archive.exists()


def test_hash_bound_manifest_rejects_stale_head(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"before\n"})
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", ["src/keep.py"])
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "advance head"], cwd=root, check=True)
    with pytest.raises(ValueError, match="source_head"):
        module.create_snapshot(root, tmp_path / "stale.tar.gz", "stale", manifest)


@pytest.mark.parametrize("schema_version", [True, 1.0, 2])
def test_source_manifest_rejects_noninteger_or_unsupported_schema(tmp_path: Path, schema_version) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"ok\n"})
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": schema_version,
            "source_head": module.git_head(root),
            "files": [{"path": "src/keep.py", "sha256": module.sha256(root / "src/keep.py")}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema_version"):
        module.create_snapshot(root, tmp_path / "bad-schema.tar.gz", "bad-schema", manifest)


def test_source_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"ok\n"})
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        '{"schema_version":1,"schema_version":1,"source_head":"' + module.git_head(root) + '","files":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        module.create_snapshot(root, tmp_path / "duplicate-json.tar.gz", "duplicate-json", manifest)


@pytest.mark.parametrize("name", [".envrc", ".npmrc", "src/api_token.txt"])
def test_source_manifest_rejects_secret_like_filenames(tmp_path: Path, name: str) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"ok\n"})
    source = root / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"secret-like fixture\n")
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", [name])
    with pytest.raises(ValueError, match="secret-like filename"):
        module.create_snapshot(root, tmp_path / "secret-like.tar.gz", "secret-like", manifest)


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("../outside.py", "unsafe archive member"),
        ("/outside.py", "unsafe archive member"),
        ("missing.py", "missing or unreadable"),
        ("folder", "not a regular file"),
        ("CON.py", "unsafe archive member"),
        ("src/a?.py", "unsafe archive member"),
        ("src/a|b.py", "unsafe archive member"),
        ("src/a\x01b.py", "unsafe archive member"),
        ("WORKTREE.patch", "excluded path"),
    ],
)
def test_source_manifest_rejects_invalid_or_unusable_paths(tmp_path: Path, path: str, reason: str) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"ok\n"})
    (root / "folder").mkdir()
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": 1,
            "source_head": module.git_head(root),
            "files": [{"path": path, "sha256": hashlib.sha256(b"").hexdigest()}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=reason):
        module.create_snapshot(root, tmp_path / "invalid.tar.gz", "invalid", manifest)


def test_source_manifest_rejects_symlink_component(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"ok\n"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external.py").write_bytes(b"outside\n")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": 1,
            "source_head": module.git_head(root),
            "files": [{"path": "linked/external.py", "sha256": module.sha256(outside / "external.py")}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="symlink"):
        module.create_snapshot(root, tmp_path / "symlink.tar.gz", "symlink", manifest)


def test_source_manifest_rejects_duplicate_casefolded_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/Case.py": b"one\n"})
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": 1,
            "source_head": module.git_head(root),
            "files": [
                {"path": "src/Case.py", "sha256": module.sha256(root / "src/Case.py")},
                {"path": "src/case.py", "sha256": module.sha256(root / "src/Case.py")},
            ],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate or duplicate-case"):
        module.create_snapshot(root, tmp_path / "duplicate.tar.gz", "duplicate", manifest)


def test_default_snapshot_fails_closed_for_selected_deleted_source(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"ok\n"})
    (root / "src/keep.py").unlink()
    with pytest.raises(ValueError, match="missing or unreadable"):
        module.create_snapshot(root, tmp_path / "deleted.tar.gz", "deleted")
    assert not (tmp_path / "deleted.tar.gz").exists()


def test_create_rejects_windows_invalid_tracked_name_before_archive_publish(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/a?.py": b"invalid Windows filename\n"})
    archive = tmp_path / "invalid-name.tar.gz"
    with pytest.raises(ValueError, match="unsafe archive member"):
        module.create_snapshot(root, archive, "invalid-name")
    assert not archive.exists()


def test_snapshot_refuses_selected_rename(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/old.py": b"same source\n"})
    (root / "src/old.py").rename(root / "src/new.py")
    manifest = write_source_manifest(root, tmp_path / "source-manifest.json", ["src/new.py"])
    with pytest.raises(ValueError, match="participates in a Git rename"):
        module.create_snapshot(root, tmp_path / "rename.tar.gz", "rename", manifest)


def test_create_refuses_existing_archive_without_modifying_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    init_repo(root, {"src/keep.py": b"ok\n"})
    archive = tmp_path / "existing.tar.gz"
    archive.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        module.create_snapshot(root, archive, "existing")
    assert archive.read_bytes() == b"original"
