"""Tests for R02: Artifact Containment and Private Boundary Security."""
from __future__ import annotations

from pathlib import Path

import pytest

from comsol_mcp._artifact_store import ArtifactStore, artifact_read
from comsol_mcp._execution_contract import ExecutionContractError


class _Worker:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.paths = self
        self.resolved_project_root = project_root


def test_resolve_safe_path_rejects_private_directories(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    private_dir = tmp_path / ".phase1-private"
    private_dir.mkdir()
    sentinel = private_dir / "secret.txt"
    sentinel.write_text("SECRET")

    with pytest.raises(ExecutionContractError) as exc_info:
        store.resolve_safe_path(".phase1-private/secret.txt", allow_overwrite=True)
    assert exc_info.value.code == "ACCESS_VIOLATION"


def test_resolve_safe_path_rejects_git_and_env(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    for forbidden in (".git/config", ".env", ".credentials/token.json", "tokens/user_token"):
        with pytest.raises(ExecutionContractError) as exc_info:
            store.resolve_safe_path(forbidden, allow_overwrite=True)
        assert exc_info.value.code == "ACCESS_VIOLATION"


def test_artifact_read_blocks_synthetic_private_sentinel(tmp_path: Path) -> None:
    worker = _Worker(tmp_path)
    private_dir = tmp_path / ".g3-private"
    private_dir.mkdir()
    sentinel = private_dir / "sentinel.txt"
    sentinel.write_text("SYNTHETIC_SENTINEL")

    with pytest.raises(ExecutionContractError) as exc_info:
        artifact_read(worker, None, {"path": ".g3-private/sentinel.txt", "offset": 0, "length": 16})
    assert exc_info.value.code == "ACCESS_VIOLATION"


def test_artifact_registration_tracks_published_files(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    target = tmp_path / "valid.json"
    result = store.export_field_data(str(target), {"values": [1.0, 2.0], "status": {"ok": True}})
    assert ArtifactStore.is_registered_artifact(target)
    assert ArtifactStore.is_registered_artifact(result["file_path"])


def test_resolve_safe_path_rejects_control_prefs_runtime_db_and_source(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    # Control prefs
    for p in ("comsol_prefs/login.properties", "comsol.prefs", "comsol-server-home/port"):
        with pytest.raises(ExecutionContractError) as exc:
            store.resolve_safe_path(p, allow_overwrite=True)
        assert exc.value.code == "ACCESS_VIOLATION"

    # Runtime DB and control transactions
    for p in ("docs_index.sqlite3", "transactions.json", "data.db"):
        with pytest.raises(ExecutionContractError) as exc:
            store.resolve_safe_path(p, allow_overwrite=True)
        assert exc.value.code == "ACCESS_VIOLATION"

    # Installation source and python modules
    for p in ("comsol_mcp/__init__.py", "site-packages/pkg.py", "secret.py", "module.pyc"):
        with pytest.raises(ExecutionContractError) as exc:
            store.resolve_safe_path(p, allow_overwrite=True)
        assert exc.value.code == "ACCESS_VIOLATION"

