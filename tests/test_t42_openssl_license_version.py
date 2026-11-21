"""T42: offline OpenSSL license names follow the attested linked version.

Synthetic wheels and source metadata only.  This checks output identity, not
whether an arbitrary license file came from an OpenSSL source distribution.
"""
import json
import zipfile

import pytest

from tests.test_full_project_release import (
    TOOL, make_build_evidence, make_derived_crypto_inputs, make_wheel, release,
)


@pytest.mark.parametrize("version", ["4.0.2", "4.0.3"])
def test_manifest_names_license_for_attested_openssl_version(tmp_path, version):
    wheelhouse, wheel, requirements, source_lock, provenance, _wheel_sha = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    if version == "4.0.3":
        payload = json.loads(provenance.read_text())
        payload["native_component"].update(
            version="4.0.3",
            source_url="https://github.com/openssl/openssl/releases/download/openssl-4.0.3/openssl-4.0.3.tar.gz",
            source_sha256="325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9",
        )
        provenance.write_text(json.dumps(payload))
    derived_lock = tmp_path / "derived.lock"
    release.compose_derived_wheel_lock(requirements, wheel, source_lock, provenance,
                                       license_path, "macos_x86_64", derived_lock)
    output = tmp_path / "metadata"
    manifest = release.create_bundle_manifest(wheelhouse, "macos_x86_64", derived_lock,
                                              source_lock, output, provenance, license_path)
    expected_path = f"licenses/openssl-{version}-LICENSE.txt"
    native = manifest["derived_wheel"]["native_component"]
    assert native["version"] == version
    assert native["license_file"]["path"] == expected_path
    assert native["license_file"]["sha256"] == release.sha256_file(license_path)
    licenses = json.loads((output / "LICENSES.json").read_text())
    listed = next(item for item in licenses["components"] if item["name"] == "OpenSSL")
    assert listed["version"] == version
    assert listed["license_files"][0]["path"] == expected_path


def test_bundle_license_member_matches_manifest_version_and_bytes(tmp_path):
    wheelhouse, wheel, requirements, source_lock, provenance, _wheel_sha = make_derived_crypto_inputs(tmp_path)
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    payload = json.loads(provenance.read_text())
    payload["native_component"].update(
        version="4.0.3",
        source_url="https://github.com/openssl/openssl/releases/download/openssl-4.0.3/openssl-4.0.3.tar.gz",
        source_sha256="325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9",
    )
    provenance.write_text(json.dumps(payload))
    app = wheelhouse / "comsol_mcp-0.1.9-py3-none-any.whl"
    make_wheel(app, name="comsol-mcp", version="0.1.9")
    derived_lock = tmp_path / "derived.lock"
    release.compose_derived_wheel_lock(requirements, wheel, source_lock, provenance,
                                       license_path, "macos_x86_64", derived_lock)
    final_lock = tmp_path / "final.lock"
    release.compose_install_lock(derived_lock, app, "macos_x86_64", final_lock)
    metadata = tmp_path / "metadata"
    manifest = release.create_bundle_manifest(wheelhouse, "macos_x86_64", final_lock,
                                              source_lock, metadata, provenance, license_path)
    expected = manifest["derived_wheel"]["native_component"]["license_file"]["path"]
    assert expected == "licenses/openssl-4.0.3-LICENSE.txt"
    build_evidence = tmp_path / "build-evidence"
    make_build_evidence(build_evidence, app)
    operations = tmp_path / "RELEASE_OPERATIONS.md"
    operations.write_text("Offline installation operations.\n")
    archive_path = tmp_path / "bundle.zip"
    release.package_wheelhouse_bundle(
        wheelhouse, final_lock, source_lock, metadata, operations, "macos_x86_64",
        archive_path, tmp_path / "evidence", build_evidence, TOOL, provenance, license_path,
    )
    with zipfile.ZipFile(archive_path) as archive:
        assert expected in archive.namelist()
        assert "licenses/openssl-4.0.2-LICENSE.txt" not in archive.namelist()
        assert archive.read(expected) == license_path.read_bytes()
        indexed = json.loads(archive.read("OFFLINE_BUNDLE_MANIFEST.json"))
        rows = {item["path"]: item for item in indexed["files_excluding_this_manifest"]}
        assert rows[expected]["sha256"] == release.sha256_file(license_path)
