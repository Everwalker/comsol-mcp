"""Synthetic end-to-end source-notice routing through the universal release builder.

These are disposable wheel/lock fixtures. No COMSOL, installation, network,
real user data, native platform behavior or legal certification is involved.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile

import pytest

from tests._t42_lock_notices_support import APP_VERSION, build_fixture
from tests.test_full_project_release import make_build_evidence
from tools import universal_release as tool


@pytest.mark.parametrize("target", ["macos_arm64", "macos_x86_64", "win_amd64"])
def test_universal_target_packages_verified_lock_bound_notices(tmp_path, target):
    fixture, paths = build_fixture(tmp_path)
    # The synthetic wheels are py3-none-any; a lock-bound wheel may therefore
    # apply to all three targets, with each target claim explicit in the receipt.
    for row in fixture.receipt_components:
        claim = row["wheels"].get("macos_arm64")
        if claim:
            row["wheels"]["macos_x86_64"] = dict(claim)
            row["wheels"]["win_amd64"] = dict(claim)
    fixture.save_receipt()

    app = fixture.wheelhouse / f"comsol_mcp-{APP_VERSION}-py3-none-any.whl"
    dependency_house = tmp_path / "dependency-inputs"
    dependency_house.mkdir()
    for wheel in fixture.wheelhouse.glob("*.whl"):
        if wheel != app:
            shutil.copyfile(wheel, dependency_house / wheel.name)
    exported = tmp_path / "requirements-export.lock"
    exported.write_text("\n".join(
        line for line in paths["requirements"].read_text().splitlines()
        if not line.startswith("comsol-mcp==")
    ) + "\n")
    build_evidence = tmp_path / "build-evidence"
    make_build_evidence(build_evidence, app, version=APP_VERSION)
    operations = tmp_path / "operations.md"
    operations.write_text("Synthetic offline packaging fixture.\n")
    project = {
        "name": "comsol-mcp", "version": APP_VERSION, "requires-python": ">=3.10",
        "dependencies": ["synth-alpha==7.1.0", "synth-beta==2.0.0", "synth-epsilon==1.1.0"],
        "optional-dependencies": {"desktop": []},
    }

    out = tmp_path / "target"
    result = tool.build_target(
        project, target, dependency_house, exported, paths["lock"], app, out,
        build_evidence, operations, primary_notice_receipt=paths["receipt"],
    )
    assert result["status"] == "PACKAGED_NATIVE_UNVERIFIED"
    assert result["target_install"] == "NOT_RUN"
    assert result["native_engine"] == "NOT_RUN"
    with zipfile.ZipFile(out / "offline.zip") as archive:
        manifest = json.loads(archive.read("PACKAGE_MANIFEST.json"))
        licenses = json.loads(archive.read("LICENSES.json"))
        assert manifest["primary_notice_route"]["kind"] == "SOURCE_LOCK_BOUND_PRIMARY_NOTICES"
        assert manifest["primary_notice_route"]["target"] == target
        rows = manifest["primary_notices"]
        assert len(rows) == 2
        assert {row["provenance"]["name"] for row in rows} == {"synth-alpha", "synth-beta"}
        for row in rows:
            payload = archive.read(row["path"])
            assert len(payload) == row["bytes"]
            assert hashlib.sha256(payload).hexdigest() == row["sha256"]
        assert {row["name"] for row in licenses["components"] if row.get("primary_source_notices")} == {
            "synth-alpha", "synth-beta"
        }


def test_cli_parses_and_forwards_new_receipt_option(tmp_path, monkeypatch, capsys):
    # Tests the real command-line parser/dispatch but substitutes run() to
    # prevent actual uv export, building and any external process execution.
    house = tmp_path / "house"
    house.mkdir()
    receipt = tmp_path / "receipt.json"
    observed = {}

    def fake_run(args):
        observed["receipt"] = args.primary_notices_lock_receipt
        observed["wheelhouse"] = args.wheelhouse
        return {"targets": {}}

    monkeypatch.setattr(tool, "run", fake_run)
    args = [
        "--source", str(tmp_path), "--lock", str(tmp_path / "uv.lock"),
        "--builder-python", str(tmp_path / "python"), "--uv", str(tmp_path / "uv"),
        "--operations", str(tmp_path / "ops.md"), "--output", str(tmp_path / "output"),
        "--wheelhouse", f"macos_arm64={house}",
        "--primary-notices-lock-receipt", str(receipt),
    ]
    assert tool.main(args) == 0
    assert observed["receipt"] == receipt
    assert observed["wheelhouse"] == {"macos_arm64": house.resolve()}
    assert str(tmp_path / "output" / "RELEASE.json") in capsys.readouterr().out
