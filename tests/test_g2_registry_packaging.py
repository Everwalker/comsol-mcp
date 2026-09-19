"""Packaging and import isolation checks for the G2 action catalog."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from comsol_mcp import _g2_registry as registry


def _workspace_catalog() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "comsol_mcp_design_v1" / "02_ACTION_CATALOG.json"


def _package_catalog() -> Path:
    return Path(registry.__file__).resolve().parent / "data" / "g2" / "02_ACTION_CATALOG.json"


def test_packaged_catalog_matches_reviewed_source_and_is_authoritative():
    workspace = _workspace_catalog()
    package = _package_catalog()

    assert package.is_file()
    assert package.read_bytes() == workspace.read_bytes()
    assert registry.CATALOG_PATH == package
    assert registry.registry_manifest()["catalog_sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()


def test_catalog_selector_rejects_drift_and_prefers_package(monkeypatch, tmp_path):
    package = tmp_path / "package" / "02_ACTION_CATALOG.json"
    workspace = tmp_path / "workspace" / "02_ACTION_CATALOG.json"
    package.parent.mkdir()
    workspace.parent.mkdir()
    package.write_bytes(b"package-catalog")
    workspace.write_bytes(b"workspace-catalog")
    monkeypatch.setattr(registry, "_PACKAGE_CATALOG_PATH", package)
    monkeypatch.setattr(registry, "_WORKSPACE_CATALOG_PATH", workspace)

    with pytest.raises(RuntimeError, match="differs from the reviewed design source"):
        registry._select_catalog_path()

    workspace.write_bytes(package.read_bytes())
    assert registry._select_catalog_path() == package


def test_catalog_selector_has_only_legacy_workspace_fallback(monkeypatch, tmp_path):
    package = tmp_path / "package" / "02_ACTION_CATALOG.json"
    workspace = tmp_path / "workspace" / "02_ACTION_CATALOG.json"
    workspace.parent.mkdir()
    workspace.write_bytes(b"workspace-catalog")
    monkeypatch.setattr(registry, "_PACKAGE_CATALOG_PATH", package)
    monkeypatch.setattr(registry, "_WORKSPACE_CATALOG_PATH", workspace)

    assert registry._select_catalog_path() == workspace


def test_pyproject_declares_catalog_as_package_data():
    project = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'comsol_mcp = ["phase1_java/*.java", "worker_java/*.java", "data/g2/*.json"]' in project


def test_registry_imports_from_isolated_package_tree_without_checkout(tmp_path):
    package_source = Path(registry.__file__).resolve().parent
    isolated_site = tmp_path / "site-packages"
    shutil.copytree(package_source, isolated_site / "comsol_mcp")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(isolated_site)
    code = (
        "import hashlib, json; "
        "from comsol_mcp import _g2_registry as r; "
        "print(json.dumps({'catalog': str(r.CATALOG_PATH), 'entries': len(r.ENTRIES), "
        "'sha256': hashlib.sha256(r.CATALOG_PATH.read_bytes()).hexdigest()}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["entries"] == len(registry.ENTRIES)
    assert observed["catalog"].endswith("comsol_mcp/data/g2/02_ACTION_CATALOG.json")
    assert observed["sha256"] == hashlib.sha256(_package_catalog().read_bytes()).hexdigest()
