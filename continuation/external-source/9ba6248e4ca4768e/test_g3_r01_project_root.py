"""Tests for R01: Project Root vs Install Root Decoupling."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._java_worker import JavaWorkerError, JavaWorkerPaths
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._control_daemon import ControlDaemon


class _DummyStore:
    pass


def test_managed_backend_accepts_explicit_project_root(tmp_path: Path) -> None:
    home = tmp_path / "control_home"
    project = tmp_path / "user_project"
    project.mkdir()
    backend = ManagedBackend(home, _DummyStore(), project_root=project)
    assert backend.project_root == project.resolve()
    assert backend.private_control_root == home
    assert backend.package_resource_root.is_dir()


def test_managed_backend_uses_env_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "control_home"
    project = tmp_path / "env_project"
    project.mkdir()
    monkeypatch.setenv("COMSOL_PROJECT_ROOT", str(project))
    backend = ManagedBackend(home, _DummyStore())
    assert backend.project_root == project.resolve()


def test_managed_backend_rejects_site_packages_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "control_home"
    monkeypatch.delenv("COMSOL_PROJECT_ROOT", raising=False)
    # Mock __file__ to simulate running from site-packages
    fake_file = tmp_path / "site-packages" / "comsol_mcp" / "_managed_backend.py"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("# dummy")
    monkeypatch.setattr("comsol_mcp._managed_backend.__file__", str(fake_file))

    with pytest.raises(ExecutionContractError) as exc_info:
        ManagedBackend(home, _DummyStore())
    assert exc_info.value.code == "RUNTIME_CONFIGURATION_REQUIRED"


def test_control_daemon_passes_project_root(tmp_path: Path) -> None:
    home = tmp_path / "daemon_home"
    project = tmp_path / "user_project"
    project.mkdir()
    daemon = ControlDaemon(home, project_root=project)
    try:
        assert daemon.backend.project_root == project.resolve()
    finally:
        daemon.closed.set()


def test_java_worker_paths_resolved_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "comsol_root"
    jdk = tmp_path / "jdk_home"
    project = tmp_path / "custom_project"
    root.mkdir()
    jdk.mkdir()
    project.mkdir()

    paths = JavaWorkerPaths(root, jdk, project_root=project)
    assert paths.resolved_project_root == project.resolve()

    monkeypatch.setenv("COMSOL_PROJECT_ROOT", str(project))
    paths_env = JavaWorkerPaths(root, jdk)
    assert paths_env.resolved_project_root == project.resolve()
