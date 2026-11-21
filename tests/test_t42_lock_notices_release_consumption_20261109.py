"""Release/bundle consumption and re-audit controls for the source-lock route.

No module-level import of tools.source_lock_notices: on a baseline without the
candidate module these tests fail on the missing release keyword (meaningful
RED) instead of a collection import error. Synthetic fixtures; never a final,
native, installed or legally-certified bundle.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from tools import full_project_release as release
from tests._t42_lock_notices_support import build_fixture
from tests.test_full_project_release import make_build_evidence


@pytest.fixture
def prepared(tmp_path):
    fixture, paths = build_fixture(tmp_path)
    return fixture, paths, tmp_path


def make_manifest(fixture, paths, tmp_path, metadata_name="metadata", **kwargs):
    metadata = tmp_path / metadata_name
    return release.create_bundle_manifest(fixture.wheelhouse, "macos_arm64", paths["requirements"],
                                          paths["lock"], metadata,
                                          **kwargs), metadata


def packaging_args(fixture, paths, tmp_path):
    app = fixture.wheelhouse / "comsol_mcp-0.1.10-py3-none-any.whl"
    build = tmp_path / "build"
    make_build_evidence(build, app, version="0.1.10")
    tool = tmp_path / "full_project_release.py"
    tool.write_text("# synthetic tool\n")
    operations = tmp_path / "RELEASE_OPERATIONS.md"
    operations.write_text("synthetic operations\n")
    return (fixture.wheelhouse, paths["requirements"], paths["lock"], tmp_path / "metadata",
            operations, "macos_arm64", tmp_path / "offline.zip", tmp_path / "evidence",
            build, tool)


def test_generic_unknown_versions_consumed_by_release_manifest(prepared):
    fixture, paths, tmp_path = prepared
    manifest, metadata = make_manifest(fixture, paths, tmp_path, primary_notice_receipt=paths["receipt"])
    by_name = {pathlib.PurePosixPath(row["path"]).parts[1]: row for row in manifest["primary_notices"]}
    assert set(by_name) == {"synth-alpha", "synth-beta"}
    for name, row in by_name.items():
        wheel_row = next(w for w in manifest["wheels"] if w["name"].lower().replace("_", "-") == name)
        assert row["provenance"]["version"] == wheel_row["version"] != "12.2.2"
        assert row["provenance"]["wheel_sha256"] == wheel_row["sha256"]
        assert row["bytes"] == len((metadata / row["path"]).read_bytes())
    assert manifest["primary_notice_route"]["kind"] == "SOURCE_LOCK_BOUND_PRIMARY_NOTICES"
    assert manifest["primary_notice_route"]["source_lock_sha256"] != ""
    licenses = json.loads((metadata / "LICENSES.json").read_text())
    covered = [row["name"] for row in licenses["components"] if row.get("primary_source_notices")]
    assert sorted(covered) == ["synth-alpha", "synth-beta"]
    assert not any(row.get("primary_source_notices") for row in licenses["components"]
                   if row["name"] == "synth-epsilon")


def test_wrong_wheel_hash_in_receipt_blocked_before_manifest(prepared):
    fixture, paths, tmp_path = prepared
    row = next(row for row in fixture.receipt_components if row["name"] == "synth-alpha")
    row["wheels"]["macos_arm64"]["sha256"] = "0" * 64
    fixture.save_receipt()
    with pytest.raises(release.ReleaseError, match="source-lock primary notice verification failed"):
        make_manifest(fixture, paths, tmp_path, primary_notice_receipt=paths["receipt"])
    assert not (tmp_path / "metadata" / "PACKAGE_MANIFEST.json").exists()


def test_notice_package_absent_from_wheelhouse_rejected(prepared):
    fixture, paths, tmp_path = prepared
    (fixture.wheelhouse / "synth_beta-2.0.0-py3-none-any.whl").unlink()
    with pytest.raises(release.ReleaseError, match="no wheel in target wheelhouse|absent from target wheelhouse"):
        make_manifest(fixture, paths, tmp_path, primary_notice_receipt=paths["receipt"])


def test_dual_route_inputs_rejected(prepared):
    fixture, paths, tmp_path = prepared
    with pytest.raises(release.ReleaseError, match="exactly one input route"):
        make_manifest(fixture, paths, tmp_path, primary_notice_manifest=paths["receipt"],
                      primary_notice_receipt=paths["receipt"])


def test_package_reaudit_reuses_full_source_lock_verification(prepared):
    """Post-manifest drift in source material must be refused by the packaging re-audit."""
    fixture, paths, tmp_path = prepared
    make_manifest(fixture, paths, tmp_path, primary_notice_receipt=paths["receipt"])
    # swap the saved sdist AFTER first validation; the old manifest looks intact
    fixture.sdist_path("synth-beta").write_bytes(b"drifted bytes after first validation")
    with pytest.raises(release.ReleaseError, match="source-lock primary notice verification failed"):
        release.package_wheelhouse_bundle(*packaging_args(fixture, paths, tmp_path),
                                          primary_notice_receipt=paths["receipt"])
    assert not (tmp_path / "offline.zip").exists()


def test_package_reaudit_refuses_receipt_scope_change(prepared):
    fixture, paths, tmp_path = prepared
    make_manifest(fixture, paths, tmp_path, primary_notice_receipt=paths["receipt"])
    fixture.receipt_components = [row for row in fixture.receipt_components if row["name"] == "synth-alpha"]
    fixture.save_receipt()
    with pytest.raises(release.ReleaseError, match="primary notice (provenance|input route) changed"):
        release.package_wheelhouse_bundle(*packaging_args(fixture, paths, tmp_path),
                                          primary_notice_receipt=paths["receipt"])
    assert not (tmp_path / "offline.zip").exists()


def test_notice_payload_swap_after_manifest_blocks_packaging(prepared):
    fixture, paths, tmp_path = prepared
    manifest, metadata = make_manifest(fixture, paths, tmp_path, primary_notice_receipt=paths["receipt"])
    (metadata / manifest["primary_notices"][0]["path"]).write_bytes(b"wrong payload")
    with pytest.raises(release.ReleaseError, match="primary notice payload changed since manifest"):
        release.package_wheelhouse_bundle(*packaging_args(fixture, paths, tmp_path),
                                          primary_notice_receipt=paths["receipt"])
    assert not (tmp_path / "offline.zip").exists()


def test_full_packaging_binds_notice_members(prepared):
    """Candidate bundle only: synthetic inputs, PACKAGED_NATIVE_UNVERIFIED at best."""
    import zipfile
    fixture, paths, tmp_path = prepared
    manifest, _metadata = make_manifest(fixture, paths, tmp_path, primary_notice_receipt=paths["receipt"])
    bundle = release.package_wheelhouse_bundle(*packaging_args(fixture, paths, tmp_path),
                                              primary_notice_receipt=paths["receipt"])
    assert bundle["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    with zipfile.ZipFile(tmp_path / "offline.zip") as archive:
        for row in manifest["primary_notices"]:
            payload = archive.read(row["path"])
            assert len(payload) == row["bytes"]
            assert hashlib.sha256(payload).hexdigest() == row["sha256"]
        members = set(archive.namelist())
    assert {"notices/synth-alpha/License.txt", "notices/synth-beta/COPYING"} <= members
