from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import pathlib
import struct
import sys
import types
import zipfile

import pytest


TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "full_project_release.py"
spec = importlib.util.spec_from_file_location("full_project_release", TOOL)
release = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = release
spec.loader.exec_module(release)


def make_wheel(path: pathlib.Path, *, name: str = "demo-pkg", version: str = "1.0",
               tag: str = "py3-none-any", extra: tuple[str, bytes] | None = None) -> str:
    dist = name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    members = {
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            "License: MIT\nClassifier: License :: OSI Approved :: MIT License\n\n"
        ).encode(),
        f"{info}/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n".encode(),
        f"{info}/licenses/LICENSE.txt": b"MIT license text\n",
        f"{dist}/__init__.py": b"VALUE = 1\n",
    }
    if extra:
        members[extra[0]] = extra[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name_, content in members.items():
            archive.writestr(name_, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_build_evidence(path: pathlib.Path, wheel: pathlib.Path, *, version: str = "0.1.9") -> None:
    path.mkdir(parents=True)
    source_rows = [{"path": "comsol_mcp/__init__.py", "bytes": 1,
                    "sha256": hashlib.sha256(b"x").hexdigest()}]
    source_hash = hashlib.sha256(json.dumps(source_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (path / "source-snapshot-manifest.json").write_text(json.dumps({
        "schema": release.SCHEMA, "kind": "IMMUTABLE_RUNTIME_SOURCE_SNAPSHOT",
        "file_count": 1, "manifest_sha256": source_hash, "files": source_rows,
    }), encoding="utf-8")
    (path / "wheel-build-receipt.json").write_text(json.dumps({
        "schema": release.SCHEMA, "status": "WHEEL_BUILT_NATIVE_UNVERIFIED",
        "wheel": wheel.name, "sha256": release.sha256_file(wheel),
        "source_manifest_sha256": source_hash, "source_file_count": 1,
        "wheel_metadata": {"name": "comsol-mcp", "version": version},
    }), encoding="utf-8")


def make_appledouble(peer: pathlib.Path, *, zero_length: bool = False) -> pathlib.Path:
    # Descriptor shape from a read-only actual exFAT sample: Finder Info at
    # 50/3760 and resource fork at3810. Also exercise legal zero-length EOF.
    resource_size = 0 if zero_length else 286
    sidecar = peer.with_name("._" + peer.name)
    sidecar.write_bytes(struct.pack(">II16sHIIIIII", 0x00051607, 0x00020000,
                                   b"Mac OS X        ", 2, 9, 50, 3760,
                                   2, 3810, resource_size) + bytes(3760 + resource_size))
    return sidecar


@pytest.mark.parametrize("zero_length", [False, True])
def test_flat_wheel_inventory_records_valid_metadata_and_keeps_general_audit_strict(tmp_path, zero_length):
    house = tmp_path / "wheelhouse"
    wheel = house / "demo_pkg-1.0-py3-none-any.whl"
    make_wheel(wheel)
    sidecar = make_appledouble(wheel, zero_length=zero_length)
    wheels, metadata = release.flat_wheel_inventory(house)
    assert wheels == [wheel]
    assert metadata[0]["path"] == sidecar.name
    assert metadata[0]["sha256"] == release.sha256_file(sidecar)
    assert metadata[0]["peer_sha256"] == release.sha256_file(wheel)
    assert metadata[0]["header"]["entry_count"] == 2
    if zero_length:
        assert metadata[0]["entries"][1] == {"id": 2, "offset": sidecar.stat().st_size, "length": 0}
    assert release.audit_wheelhouse(house, wheels)["status"] == "PASS"
    assert any(r["kind"] == "APPLEDOUBLE_SIDECAR" for r in release.audit_tree(house)["findings"])


@pytest.mark.parametrize("defect", ["magic", "version", "header", "table", "offset-header", "offset-past", "length-past",
                                   "data-fork", "zero-id", "duplicate", "overlap", "orphan", "sidecar-link", "peer-link", "peer-size", "directory", "unexpected"])
def test_flat_wheel_inventory_rejects_invalid_metadata_and_nonflat_inputs(tmp_path, monkeypatch, defect):
    house = tmp_path / "wheelhouse"
    wheel = house / "demo_pkg-1.0-py3-none-any.whl"
    make_wheel(wheel)
    sidecar = make_appledouble(wheel)
    data = bytearray(sidecar.read_bytes())
    if defect in {"magic", "version"}:
        struct.pack_into(">I", data, 0 if defect == "magic" else 4, 0)
    elif defect == "header": data = data[:25]
    elif defect == "table": data = data[:37]
    elif defect in {"offset-header", "offset-past", "length-past"}:
        struct.pack_into(">I", data, 34 if defect == "length-past" else 30,
                         1 if defect == "offset-header" else len(data) + 1)
    elif defect in {"data-fork", "zero-id"}: struct.pack_into(">I", data, 26, 1 if defect == "data-fork" else 0)
    elif defect in {"duplicate", "overlap"}:
        data = bytearray(struct.pack(">II16sHIIIIII", 0x00051607, 0x00020000, b"\0"*16, 2,
                                    9, 50, 4, 9 if defect == "duplicate" else 2, 52, 2) + b"four")
    sidecar.write_bytes(data)
    if defect == "orphan": wheel.unlink()
    elif defect == "sidecar-link":
        other = tmp_path / "metadata"; other.write_bytes(data); sidecar.unlink(); sidecar.symlink_to(other)
    elif defect == "peer-link":
        other = tmp_path / "wheel"; other.write_bytes(wheel.read_bytes()); wheel.unlink(); wheel.symlink_to(other)
    elif defect == "peer-size":
        monkeypatch.setattr(release, "MAX_FILE_BYTES", 128 * 1024)
        with wheel.open("r+b") as f: f.truncate(128 * 1024 + 1)
    elif defect == "directory": (house / "nested").mkdir()
    elif defect == "unexpected": (house / "other.txt").write_text("not wheel metadata")
    with pytest.raises(release.ReleaseError): release.flat_wheel_inventory(house)


def test_valid_sidecar_does_not_hide_corrupt_real_wheel(tmp_path):
    house = tmp_path / "wheelhouse"; house.mkdir()
    wheel = house / "demo_pkg-1.0-py3-none-any.whl"; wheel.write_bytes(b"not a ZIP")
    make_appledouble(wheel)
    lock = tmp_path / "requirements.lock"; lock.write_text(f"demo-pkg==1.0 --hash=sha256:{release.sha256_file(wheel)}\n")
    with pytest.raises(release.ReleaseError, match="INVALID_ZIP"):
        release.create_bundle_manifest(house, "win_amd64", lock, None, tmp_path / "metadata")


def test_wheelhouse_exception_does_not_allow_sidecars_inside_real_wheel(tmp_path):
    house = tmp_path / "wheelhouse"; wheel = house / "demo_pkg-1.0-py3-none-any.whl"
    sha = make_wheel(wheel, extra=("demo_pkg/._hidden.txt", b"metadata"))
    make_appledouble(wheel)
    lock = tmp_path / "requirements.lock"; lock.write_text(f"demo-pkg==1.0 --hash=sha256:{sha}\n")
    with pytest.raises(release.ReleaseError, match="APPLEDOUBLE_SIDECAR"):
        release.create_bundle_manifest(house, "win_amd64", lock, None, tmp_path / "metadata")


@pytest.mark.parametrize("sidecar_change", ["removed", "added", "changed"])
def test_offline_bundle_replays_portable_identity_without_sidecar_equality(tmp_path, sidecar_change):
    house = tmp_path / "wheelhouse"; wheel = house / "comsol_mcp-0.1.9-py3-none-any.whl"
    sha = make_wheel(wheel, name="comsol-mcp", version="0.1.9")
    lock = tmp_path / "requirements.lock"; lock.write_text(f"comsol-mcp==0.1.9 --hash=sha256:{sha}\n")
    source_lock = tmp_path / "uv.lock"; source_lock.write_text("version = 1\n")
    if sidecar_change != "added": make_appledouble(wheel)
    metadata = tmp_path / "metadata"; release.create_bundle_manifest(house, "win_amd64", lock, source_lock, metadata)
    if sidecar_change == "removed": wheel.with_name("._" + wheel.name).unlink()
    else: make_appledouble(wheel, zero_length=True)
    build = tmp_path / "build"; make_build_evidence(build, wheel)
    tool = tmp_path / "full_project_release.py"; tool.write_text("# fixture tool\n")
    operations = tmp_path / "RELEASE_OPERATIONS.md"; operations.write_text("offline operations\n")
    archive = tmp_path / "bundle.zip"
    result = release.package_wheelhouse_bundle(house, lock, source_lock, metadata, operations, "win_amd64", archive, tmp_path / "evidence", build, tool)
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    with zipfile.ZipFile(archive) as z:
        assert not any(pathlib.PurePosixPath(n).name.startswith("._") for n in z.namelist())
        assert z.read("wheelhouse/" + wheel.name) == wheel.read_bytes()
    # Portable replay still rejects real wheel mutation instead of relaxing identity.
    wheel.write_bytes(wheel.read_bytes() + b"changed")
    with pytest.raises(release.ReleaseError, match="SHA256"):
        release.package_wheelhouse_bundle(house, lock, source_lock, metadata, operations, "win_amd64", tmp_path / "changed.zip", tmp_path / "changed-evidence", build, tool)


@pytest.mark.parametrize("invalid", [False, True])
def test_build_output_uses_validated_metadata_inventory_software_mock(tmp_path, monkeypatch, invalid):
    """Mocked builder transport only; no pip/build/installation executed."""
    out = tmp_path / "out"
    def snapshot(source, stage):
        stage.mkdir(); return {"files": [], "file_count": 0, "manifest_sha256": release.hashlib.sha256(b"[]").hexdigest()}
    def run(argv, **kwargs):
        if "wheel" in argv:
            wheel = out / "comsol_mcp-0.1.9-py3-none-any.whl"; make_wheel(wheel, name="comsol-mcp", version="0.1.9")
            sidecar = make_appledouble(wheel)
            if invalid: sidecar.write_bytes(b"invalid metadata")
        return types.SimpleNamespace(returncode=0, stdout="fixture builder", stderr="")
    monkeypatch.setattr(release, "source_snapshot", snapshot); monkeypatch.setattr(release.subprocess, "run", run)
    if invalid:
        with pytest.raises(release.ReleaseError, match="AppleDouble"):
            release.build_wheel(tmp_path, out, pathlib.Path(sys.executable), tmp_path / "evidence")
    else:
        result = release.build_wheel(tmp_path, out, pathlib.Path(sys.executable), tmp_path / "evidence")
        assert len(result["wheelhouse_metadata_sidecars"]) == 1 and result["audit"]["file_count"] == 1


def make_derived_crypto_inputs(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, str]:
    wheelhouse = tmp_path / "wheelhouse"
    wheel = wheelhouse / "cryptography-50.0.1-cp312-abi3-macosx_12_0_x86_64.whl"
    wheel_sha = make_wheel(wheel, name="cryptography", version="50.0.1",
                           tag="cp312-abi3-macosx_12_0_x86_64")
    source_sha = "5dd9bda1c12b4162f6ff568eeb5e0ff956c28d14406e875cfe8a63a2d414ff20"
    source_url = "https://files.pythonhosted.org/packages/bb/ad/5d6702db60b1e40b41ef513b6967ff5848f307d50f8449baf1634f5908f1/cryptography-50.0.1.tar.gz"
    source_lock = tmp_path / "uv.lock"
    source_lock.write_text(
        "version = 1\n\n[[package]]\nname = \"cryptography\"\nversion = \"50.0.1\"\n"
        f"sdist = {{ url = \"{source_url}\", hash = \"sha256:{source_sha}\" }}\n",
        encoding="utf-8",
    )
    license_path = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    license_path.write_text("Apache License\nVersion 2.0\n", encoding="utf-8")
    provenance_path = tmp_path / "derived-wheel-provenance.json"
    provenance = {
        "schema": "comsol-mcp-derived-wheel/1", "status": "BUILD_CANDIDATE", "target": "macos_x86_64",
        "package": {"name": "cryptography", "version": "50.0.1"},
        "wheel": {"filename": wheel.name, "bytes": wheel.stat().st_size, "sha256": wheel_sha,
                  "tag": "cp312-abi3-macosx_12_0_x86_64"},
        "source": {"url": source_url, "sha256": source_sha},
        "source_lock": {"filename": source_lock.name, "sha256": release.sha256_file(source_lock),
                        "sdist_sha256": source_sha},
        "native_component": {
            "name": "OpenSSL", "version": "4.0.2", "source_url": release.OPENSSL_402_SOURCE_URL,
            "source_sha256": release.OPENSSL_402_SOURCE_SHA256, "linkage": "static", "license": "Apache-2.0",
            "license_file": {"filename": license_path.name, "bytes": license_path.stat().st_size,
                             "sha256": release.sha256_file(license_path)},
        },
        "native_audit": {"architecture": "x86_64", "minimum_os": "12.0",
                          "dynamic_libraries": ["/usr/lib/libiconv.2.dylib", "/usr/lib/libSystem.B.dylib"],
                          "openssl_dynamic_dependency": False, "temporary_dylib_dependencies": []},
        "uv_lock_modified": False, "upstream_source_patched": False,
    }
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    requirements = tmp_path / "requirements.download.lock"
    requirements.write_text(f"cryptography==50.0.1 --hash=sha256:{source_sha}\n", encoding="utf-8")
    return wheelhouse, wheel, requirements, source_lock, provenance_path, wheel_sha


def test_wheel_target_tags_reject_wrong_platform_and_python() -> None:
    assert release.wheel_target_check("demo_pkg-1.0-py3-none-any.whl", "win_amd64")
    assert release.wheel_target_check("demo_pkg-1.0-cp312-cp312-win_amd64.whl", "win_amd64")
    assert not release.wheel_target_check("demo_pkg-1.0-cp312-cp312-win_amd64.whl", "macos_arm64")
    assert release.wheel_target_check("demo_pkg-1.0-cp312-cp312-macosx_11_0_arm64.whl", "macos_arm64")
    assert not release.wheel_target_check("demo_pkg-1.0-cp312-cp312-macosx_11_0_arm64.whl", "macos_x86_64")
    assert not release.wheel_target_check("demo_pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl", "macos_arm64")
    assert release.wheel_target_check("demo_pkg-1.0-cp312-cp312-macosx_10_15_x86_64.whl", "macos_x86_64")
    assert not release.wheel_target_check("demo_pkg-1.0-cp312-cp312-macosx_13_0_x86_64.whl", "macos_x86_64")


def test_requirement_lock_requires_exact_pin_and_sha256(tmp_path: pathlib.Path) -> None:
    lock = tmp_path / "requirements.lock"
    digest = "a" * 64
    lock.write_text(f"demo_pkg==1.0 \\\n  --hash=sha256:{digest}\n", encoding="utf-8")
    parsed = release.parse_hash_requirements(lock)
    assert parsed["demo-pkg"]["version"] == "1.0"
    assert digest in parsed["demo-pkg"]["hashes"]
    lock.write_text("demo_pkg>=1.0\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="exact name==version"):
        release.parse_hash_requirements(lock)


def test_target_marker_filter_keeps_windows_only_dependency_on_windows(tmp_path: pathlib.Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "pywin32==312 ; sys_platform == 'win32' \\\n  --hash=sha256:" + "b" * 64 + "\ncommon-pkg==1.2 --hash=sha256:" + "c" * 64 + "\n",
        encoding="utf-8",
    )
    assert "pywin32" not in release.parse_hash_requirements(lock, "macos_arm64")
    assert "pywin32" in release.parse_hash_requirements(lock, "win_amd64")
    assert "common-pkg" in release.parse_hash_requirements(lock, "macos_x86_64")


def test_target_download_lock_resolves_markers_before_cross_platform_pip(tmp_path: pathlib.Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        f"pywin32==312 ; sys_platform == 'win32' --hash=sha256:{'a' * 64}\n"
        f"darwin-only==1.0 ; sys_platform == 'darwin' --hash=sha256:{'b' * 64}\n"
        f"common-pkg==1.2 --hash=sha256:{'c' * 64}\n",
        encoding="utf-8",
    )
    target_lock = tmp_path / "download" / "windows.lock"
    receipt = release.write_target_download_lock(lock, "win_amd64", target_lock)
    content = target_lock.read_text(encoding="utf-8")
    assert receipt["active_package_count"] == 2
    assert "pywin32==312" in content
    assert "common-pkg==1.2" in content
    assert "darwin-only" not in content
    assert "; sys_platform" not in content
    resolved = release.parse_hash_requirements(target_lock)
    active = release.parse_hash_requirements(lock, "win_amd64")
    assert {k: (v["version"], v["hashes"]) for k, v in resolved.items()} == {
        k: (v["version"], v["hashes"]) for k, v in active.items()
    }
    with pytest.raises(release.ReleaseError, match="refusing to overwrite"):
        release.write_target_download_lock(lock, "win_amd64", target_lock)


def test_bundle_manifest_matches_lock_hash_tags_sbom_and_licenses(tmp_path: pathlib.Path) -> None:
    bundle = tmp_path / "wheelhouse"
    wheel = bundle / "demo_pkg-1.0-py3-none-any.whl"
    digest = make_wheel(wheel)
    requirements = tmp_path / "requirements.lock"
    requirements.write_text(f"demo_pkg==1.0 \\\n  --hash=sha256:{digest}\n", encoding="utf-8")
    out = tmp_path / "manifest"
    result = release.create_bundle_manifest(bundle, "win_amd64", requirements, None, out)
    assert result["status"] == "ARTIFACTS_HASHED_NATIVE_UNVERIFIED"
    assert result["target_execution_verified"] is False
    licenses = json.loads((out / "LICENSES.json").read_text())
    assert licenses["components"][0]["license"] == "MIT"
    assert licenses["components"][0]["license_files"][0]["sha256"]
    sbom = json.loads((out / "SBOM.cdx.json").read_text())
    assert sbom["components"][0]["hashes"][0]["content"] == digest


def test_derived_wheel_lock_binds_uv_sdist_and_adds_static_openssl_to_sbom(tmp_path: pathlib.Path) -> None:
    wheelhouse, wheel, requirements, source_lock, provenance, wheel_sha = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    original_source_lock = source_lock.read_bytes()
    overlay = tmp_path / "requirements-derived.lock"
    receipt = release.compose_derived_wheel_lock(requirements, wheel, source_lock, provenance,
                                                 openssl_license, "macos_x86_64", overlay)
    parsed = release.parse_hash_requirements(overlay, "macos_x86_64")
    assert wheel_sha in parsed["cryptography"]["hashes"]
    assert source_lock.read_bytes() == original_source_lock
    assert receipt["status"] == "DERIVED_TARGET_LOCK_HASHED"
    metadata = tmp_path / "metadata"
    manifest = release.create_bundle_manifest(wheelhouse, "macos_x86_64", overlay, source_lock,
                                              metadata, provenance, openssl_license)
    assert manifest["derived_wheel"]["wheel"]["sha256"] == wheel_sha
    sbom = json.loads((metadata / "SBOM.cdx.json").read_text(encoding="utf-8"))
    openssl = next(row for row in sbom["components"] if row["name"] == "OpenSSL")
    assert openssl["version"] == "4.0.2"
    assert openssl["properties"][1]["value"] == "static"
    licenses = json.loads((metadata / "LICENSES.json").read_text(encoding="utf-8"))
    openssl_license_record = next(row for row in licenses["components"] if row["name"] == "OpenSSL")
    assert openssl_license_record["license"] == "Apache-2.0"
    assert openssl_license_record["license_files"][0]["sha256"] == release.sha256_file(openssl_license)


def test_derived_wheel_provenance_rejects_temp_dylib_dependency_and_lock_drift(tmp_path: pathlib.Path) -> None:
    wheelhouse, wheel, requirements, source_lock, provenance, _wheel_sha = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    payload["native_audit"]["dynamic_libraries"].append("/private/tmp/build/libcrypto.dylib")
    bad_provenance = tmp_path / "bad-provenance.json"
    bad_provenance.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="native audit"):
        release.verify_derived_wheel(bad_provenance, wheel, source_lock, openssl_license, "macos_x86_64")
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    payload["source_lock"]["sha256"] = "0" * 64
    bad_provenance.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="not bound to the selected uv.lock"):
        release.verify_derived_wheel(bad_provenance, wheel, source_lock, openssl_license, "macos_x86_64")


def test_derived_wheel_provenance_and_openssl_license_are_bundled(tmp_path: pathlib.Path) -> None:
    wheelhouse, crypto_wheel, requirements, source_lock, provenance, _crypto_sha = make_derived_crypto_inputs(tmp_path)
    openssl_license = tmp_path / "OpenSSL-4.0.2-LICENSE.txt"
    app_wheel = wheelhouse / "comsol_mcp-0.1.9-py3-none-any.whl"
    app_sha = make_wheel(app_wheel, name="comsol-mcp", version="0.1.9")
    derived_lock = tmp_path / "requirements-derived.lock"
    release.compose_derived_wheel_lock(requirements, crypto_wheel, source_lock, provenance,
                                       openssl_license, "macos_x86_64", derived_lock)
    final_lock = tmp_path / "requirements.lock"
    release.compose_install_lock(derived_lock, app_wheel, "macos_x86_64", final_lock)
    assert app_sha in release.parse_hash_requirements(final_lock, "macos_x86_64")["comsol-mcp"]["hashes"]
    metadata = tmp_path / "metadata"
    release.create_bundle_manifest(wheelhouse, "macos_x86_64", final_lock, source_lock,
                                   metadata, provenance, openssl_license)
    build_evidence = tmp_path / "build-evidence"
    make_build_evidence(build_evidence, app_wheel)
    operations = tmp_path / "RELEASE_OPERATIONS.md"
    operations.write_text("Offline installation operations.\n", encoding="utf-8")
    archive_path = tmp_path / "derived-macos_x86_64.zip"
    result = release.package_wheelhouse_bundle(
        wheelhouse, final_lock, source_lock, metadata, operations, "macos_x86_64",
        archive_path, tmp_path / "evidence", build_evidence, TOOL, provenance, openssl_license,
    )
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    with zipfile.ZipFile(archive_path) as archive:
        assert "evidence/cryptography-x86-derived-provenance.json" in archive.namelist()
        assert "licenses/openssl-4.0.2-LICENSE.txt" in archive.namelist()
        assert "wheelhouse/cryptography-50.0.1-cp312-abi3-macosx_12_0_x86_64.whl" in archive.namelist()
        bundle_manifest = json.loads(archive.read("OFFLINE_BUNDLE_MANIFEST.json"))
        listed = {row["path"]: row for row in bundle_manifest["files_excluding_this_manifest"]}
        assert listed["evidence/cryptography-x86-derived-provenance.json"]["sha256"] == release.sha256_file(provenance)
        assert listed["licenses/openssl-4.0.2-LICENSE.txt"]["sha256"] == release.sha256_file(openssl_license)


def test_compose_lock_pins_application_wheel_hash(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "uv-export.txt"
    base.write_text("anyio==4.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    wheel = tmp_path / "comsol_mcp-0.1.9-py3-none-any.whl"
    make_wheel(wheel, name="comsol-mcp", version="0.1.9")
    composed = tmp_path / "requirements.lock"
    result = release.compose_install_lock(base, wheel, "macos_arm64", composed)
    parsed = release.parse_hash_requirements(composed, "macos_arm64")
    assert parsed["comsol-mcp"]["version"] == "0.1.9"
    assert release.sha256_file(wheel) in parsed["comsol-mcp"]["hashes"]
    assert result["active_package_count"] == 2


def test_bundle_manifest_rejects_wrong_hash_missing_wheel_or_extra_file(tmp_path: pathlib.Path) -> None:
    bundle = tmp_path / "wheelhouse"
    wheel = bundle / "demo_pkg-1.0-py3-none-any.whl"
    make_wheel(wheel)
    requirements = tmp_path / "requirements.lock"
    requirements.write_text(f"demo_pkg==1.0 --hash=sha256:{'0' * 64}\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="SHA256"):
        release.create_bundle_manifest(bundle, "win_amd64", requirements, None, tmp_path / "out1")
    requirements.write_text(f"demo_pkg==1.0 --hash=sha256:{release.sha256_file(wheel)}\nother==1 --hash=sha256:{'a' * 64}\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="no wheel"):
        release.create_bundle_manifest(bundle, "win_amd64", requirements, None, tmp_path / "out2")
    (bundle / "unexpected.txt").write_text("not an installable wheel", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="only flat .whl"):
        release.create_bundle_manifest(bundle, "win_amd64", requirements, None, tmp_path / "out3")


def test_bundle_manifest_rejects_renamed_wheel_tag(tmp_path: pathlib.Path) -> None:
    bundle = tmp_path / "wheelhouse"
    wheel = bundle / "demo_pkg-1.0-cp312-cp312-win_amd64.whl"
    digest = make_wheel(wheel)
    requirements = tmp_path / "requirements.lock"
    requirements.write_text(f"demo_pkg==1.0 --hash=sha256:{digest}\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="disagrees with internal WHEEL"):
        release.create_bundle_manifest(bundle, "win_amd64", requirements, None, tmp_path / "out")


def test_recursive_scan_finds_private_content_inside_wheel(tmp_path: pathlib.Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    make_wheel(wheelhouse / "demo_pkg-1.0-py3-none-any.whl", extra=("demo_pkg/data/model.mph", b"private model"))
    result = release.audit_tree(wheelhouse)
    assert result["status"] == "FAIL"
    assert any(row["kind"] == "PROHIBITED_VENDOR_OR_PRIVATE_BINARY" for row in result["findings"])


def test_recursive_scan_rejects_appledouble_and_symlink(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "._fake.whl").write_bytes(b"x")
    (root / "real.txt").write_text("ok", encoding="utf-8")
    (root / "link.txt").symlink_to(root / "real.txt")
    result = release.audit_tree(root)
    kinds = {row["kind"] for row in result["findings"]}
    assert result["status"] == "FAIL"
    assert "APPLEDOUBLE_SIDECAR" in kinds
    assert "SYMLINK" in kinds


def test_source_bundle_requires_explicit_safe_files_and_reports_hashes(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "repository"
    (source / "docs").mkdir(parents=True)
    (source / "README.md").write_text("public source\n", encoding="utf-8")
    manifest = tmp_path / "include.json"
    manifest.write_text(json.dumps({"schema": "comsol-mcp-full-release-source-input/1",
                                    "files": [{"path": "README.md", "reason": "public user guide"}]}), encoding="utf-8")
    archive = tmp_path / "release.zip"
    result = release.source_bundle(source, manifest, archive, tmp_path / "evidence")
    assert result["status"] == "SOURCE_ARCHIVE_HASHED_NATIVE_UNVERIFIED"
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {"README.md", "RELEASE_INPUT_MANIFEST.json"}
        receipt = json.loads(zipped.read("RELEASE_INPUT_MANIFEST.json"))
        assert receipt["git_history_included"] is False
        assert receipt["included_files"][0]["sha256"] == hashlib.sha256(b"public source\n").hexdigest()
    manifest.write_text(json.dumps({"schema": "comsol-mcp-full-release-source-input/1",
                                    "files": [".git/objects/private.pack"]}), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="PRIVATE_OR_GIT_STATE"):
        release.source_bundle(source, manifest, tmp_path / "blocked.zip", tmp_path / "blocked-evidence")


def test_source_bundle_rechecks_packaged_member_hashes_after_source_mutation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    source_file = source / "README.md"
    source_file.write_text("reviewed bytes\n", encoding="utf-8")
    manifest = tmp_path / "include.json"
    manifest.write_text(json.dumps({
        "schema": "comsol-mcp-full-release-source-input/1",
        "files": [{"path": "README.md", "reason": "public user guide"}],
    }), encoding="utf-8")
    archive = tmp_path / "release.zip"
    evidence = tmp_path / "evidence"
    original_write = release.zipfile.ZipFile.write

    def mutate_before_archive_read(
        archive_file: zipfile.ZipFile, filename: str | pathlib.Path, arcname: str | None = None,
        compress_type: int | None = None, compresslevel: int | None = None,
    ) -> None:
        if arcname == "README.md":
            source_file.write_text("modified bytes\n", encoding="utf-8")
        original_write(archive_file, filename, arcname, compress_type, compresslevel)

    monkeypatch.setattr(release.zipfile.ZipFile, "write", mutate_before_archive_read)
    with pytest.raises(release.ReleaseError, match="SHA-256 differs from input manifest"):
        release.source_bundle(source, manifest, archive, evidence)
    assert archive.is_file()
    assert not (evidence / "source-bundle-receipt.json").exists()


def test_completed_source_bundle_is_immutable_after_live_source_changes(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    source_file = source / "README.md"
    source_file.write_bytes(b"frozen at review time\n")
    manifest = tmp_path / "include.json"
    manifest.write_text(json.dumps({
        "schema": "comsol-mcp-full-release-source-input/1",
        "files": [{"path": "README.md", "reason": "reviewed source snapshot"}],
    }), encoding="utf-8")
    archive = tmp_path / "frozen-candidate.zip"
    receipt = release.source_bundle(source, manifest, archive, tmp_path / "evidence")
    frozen_sha = receipt["sha256"]
    frozen_bytes = archive.read_bytes()

    source_file.write_bytes(b"later source-tree change\n")

    assert hashlib.sha256(archive.read_bytes()).hexdigest() == frozen_sha
    assert archive.read_bytes() == frozen_bytes
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("README.md") == b"frozen at review time\n"
        embedded = json.loads(zipped.read("RELEASE_INPUT_MANIFEST.json"))
    assert embedded["included_files"][0]["sha256"] == hashlib.sha256(b"frozen at review time\n").hexdigest()


def test_verify_source_bundle_checks_hashes_and_extracts_to_new_root(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    (source / "README.md").write_bytes(b"verified restore source\n")
    manifest = tmp_path / "include.json"
    manifest.write_text(json.dumps({
        "schema": "comsol-mcp-full-release-source-input/1",
        "files": [{"path": "README.md", "reason": "recovery source"}],
    }), encoding="utf-8")
    archive = tmp_path / "source.zip"
    release.source_bundle(source, manifest, archive, tmp_path / "evidence")

    extracted = tmp_path / "clean-restore"
    result = release.verify_source_bundle(archive, extracted)

    assert result["status"] == "PASS_SOURCE_ARCHIVE_HASHED_AND_EXTRACTED"
    assert result["included_file_count"] == 1
    assert result["extraction"]["status"] == "PASS"
    assert (extracted / "README.md").read_bytes() == b"verified restore source\n"
    assert (extracted / "RELEASE_INPUT_MANIFEST.json").is_file()
    with pytest.raises(release.ReleaseError, match="already exists"):
        release.verify_source_bundle(archive, extracted)


def test_source_bundle_preserves_local_and_public_candidate_classification(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "local.json").write_text("{}\n", encoding="utf-8")
    (source / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = tmp_path / "include.json"
    manifest.write_text(json.dumps({
        "schema": "comsol-mcp-full-release-source-input/1",
        "files": [
            {"path": "local.json", "reason": "historical local receipt",
             "distribution_class": "LOCAL_RECOVERY_ONLY",
             "public_source_review_flags": ["contains local execution context"]},
            {"path": "runtime.py", "reason": "authored package source",
             "distribution_class": "PUBLIC_SOURCE_CANDIDATE", "public_source_review_flags": []},
        ],
    }), encoding="utf-8")
    archive = tmp_path / "source.zip"
    release.source_bundle(source, manifest, archive, tmp_path / "evidence")

    verified = release.verify_source_bundle(archive)

    assert verified["distribution_scope_counts"] == {
        "LOCAL_RECOVERY_ONLY": {"file_count": 1, "bytes": 3},
        "PUBLIC_SOURCE_CANDIDATE": {"file_count": 1, "bytes": 10},
    }
    with zipfile.ZipFile(archive) as zipped:
        receipt = json.loads(zipped.read("RELEASE_INPUT_MANIFEST.json"))
    assert receipt["archive_scope"] == "LOCAL_RECOVERY_ARCHIVE"
    assert receipt["public_source_release_authorized"] is False
    by_path = {row["path"]: row for row in receipt["included_files"]}
    assert by_path["local.json"]["distribution_class"] == "LOCAL_RECOVERY_ONLY"
    assert by_path["local.json"]["public_source_review_flags"] == ["contains local execution context"]
    assert by_path["runtime.py"]["distribution_class"] == "PUBLIC_SOURCE_CANDIDATE"


def test_source_bundle_rejects_unknown_distribution_class(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("snapshot\n", encoding="utf-8")
    manifest = tmp_path / "include.json"
    manifest.write_text(json.dumps({
        "schema": "comsol-mcp-full-release-source-input/1",
        "files": [{"path": "README.md", "distribution_class": "PUBLIC"}],
    }), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="unknown distribution_class"):
        release.source_bundle(source, manifest, tmp_path / "source.zip", tmp_path / "evidence")


def test_relative_archive_path_rejects_traversal_and_windows_drive() -> None:
    for value in ("../escape", "/absolute", "C:\\secret\\file", "safe/../../escape", "safe//path", "safe/./path"):
        with pytest.raises(release.ReleaseError):
            release.safe_relative(value)


@pytest.mark.parametrize("lifecycle_helper", [False, True, "missing"])
def test_offline_bundle_contains_auditable_hashed_inputs_and_source_lock(tmp_path: pathlib.Path, lifecycle_helper) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheel = wheelhouse / "comsol_mcp-0.1.9-py3-none-any.whl"
    digest = make_wheel(wheel, name="comsol-mcp", version="0.1.9")
    requirements = tmp_path / "requirements.lock"
    requirements.write_text(f"comsol-mcp==0.1.9 --hash=sha256:{digest}\n", encoding="utf-8")
    source_lock = tmp_path / "uv.lock"
    source_lock.write_text("version = 1\n", encoding="utf-8")
    metadata = tmp_path / "metadata"
    release.create_bundle_manifest(wheelhouse, "win_amd64", requirements, source_lock, metadata)
    build_evidence = tmp_path / "build-evidence"
    make_build_evidence(build_evidence, wheel)
    tool = tmp_path / "full_project_release.py"
    tool.write_text("# release tool fixture\n" + ("# --with-data-lifecycle\n" if lifecycle_helper else ""), encoding="utf-8")
    if lifecycle_helper is True:
        tool.with_name("release_transition_lifecycle.py").write_text("# lifecycle helper fixture\n")
    operations = tmp_path / "RELEASE_OPERATIONS.md"
    operations.write_text("Offline installation operations.\n", encoding="utf-8")
    archive_path = tmp_path / "comsol-mcp-win_amd64.zip"
    if lifecycle_helper == "missing":
        with pytest.raises(release.ReleaseError, match="lifecycle helper is missing"):
            release.package_wheelhouse_bundle(wheelhouse, requirements, source_lock, metadata, operations,
                "win_amd64", archive_path, tmp_path / "evidence", build_evidence, tool)
        return
    result = release.package_wheelhouse_bundle(
        wheelhouse, requirements, source_lock, metadata, operations,
        "win_amd64", archive_path, tmp_path / "evidence", build_evidence, tool,
    )
    assert result["status"] == "OFFLINE_BUNDLE_PACKAGED_NATIVE_UNVERIFIED"
    assert result["native_execution_verified"] is False
    with zipfile.ZipFile(archive_path) as archive:
        members = set(archive.namelist())
        assert "README_OFFLINE_INSTALL.txt" in members
        assert "locks/uv.lock" in members
        assert "evidence/wheel-build-receipt.json" in members
        assert "evidence/source-snapshot-manifest.json" in members
        assert ("tools/release_transition_lifecycle.py" in members) is (lifecycle_helper is True)
        bundle_manifest = json.loads(archive.read("OFFLINE_BUNDLE_MANIFEST.json"))
        listed = {row["path"]: row for row in bundle_manifest["files_excluding_this_manifest"]}
        assert listed["README_OFFLINE_INSTALL.txt"]["sha256"] == hashlib.sha256(archive.read("README_OFFLINE_INSTALL.txt")).hexdigest()
        assert listed["locks/uv.lock"]["sha256"] == hashlib.sha256(source_lock.read_bytes()).hexdigest()
        assert set(listed) | {"OFFLINE_BUNDLE_MANIFEST.json"} == members


def test_offline_bundle_rejects_missing_source_lock_binding(tmp_path: pathlib.Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheel = wheelhouse / "comsol_mcp-0.1.9-py3-none-any.whl"
    digest = make_wheel(wheel, name="comsol-mcp", version="0.1.9")
    requirements = tmp_path / "requirements.lock"
    requirements.write_text(f"comsol-mcp==0.1.9 --hash=sha256:{digest}\n", encoding="utf-8")
    source_lock = tmp_path / "uv.lock"
    source_lock.write_text("version = 1\n", encoding="utf-8")
    metadata = tmp_path / "metadata"
    release.create_bundle_manifest(wheelhouse, "win_amd64", requirements, None, metadata)
    build_evidence = tmp_path / "build-evidence"
    make_build_evidence(build_evidence, wheel)
    operations = tmp_path / "RELEASE_OPERATIONS.md"
    operations.write_text("operations\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="source lock hash"):
        release.package_wheelhouse_bundle(wheelhouse, requirements, source_lock, metadata, operations,
                                          "win_amd64", tmp_path / "bundle.zip", tmp_path / "evidence",
                                          build_evidence, TOOL)


def test_wheel_target_check_requires_real_platform_and_abi_tags() -> None:
    assert not release.wheel_target_check("demo_pkg-1.0-cp311-cp311-win_amd64.whl", "win_amd64")
    assert not release.wheel_target_check("demo_pkg-1.0-py3-cp312-win_amd64.whl", "win_amd64")
    assert release.wheel_target_check("demo_pkg-1.0-cp311-abi3-win_amd64.whl", "win_amd64")
    assert not release.wheel_target_check("demo_pkg-1.0-cp313-abi3-win_amd64.whl", "win_amd64")
    assert not release.wheel_target_check("demo_pkg-1.0-cp312-cp312-win_amd64.whl", "macos_arm64")
    assert not release.wheel_target_check("demo_pkg-1.0-cp312-cp312-macosx_11_0_arm64_extra.whl", "macos_arm64")


def test_runtime_source_snapshot_contains_only_hashed_allowlisted_files(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "source"
    package = source / "comsol_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION = 'candidate'\n", encoding="utf-8")
    (source / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
    (source / "README.md").write_text("public runtime readme\n", encoding="utf-8")
    (source / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (source / "ignored.txt").write_text("excluded by the runtime source allowlist\n", encoding="utf-8")
    stage = tmp_path / "stage"
    result = release.source_snapshot(source, stage)
    assert result["file_count"] == 4
    assert {row["path"] for row in result["files"]} == {
        "comsol_mcp/__init__.py", "pyproject.toml", "README.md", "LICENSE"
    }
    for row in result["files"]:
        copied = stage / row["path"]
        assert copied.stat().st_size == row["bytes"]
        assert release.sha256_file(copied) == row["sha256"]


def test_archive_scan_rejects_traversal_directory_entries(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "inputs"
    root.mkdir()
    with zipfile.ZipFile(root / "unsafe.zip", "w") as archive:
        archive.writestr("../", "")
        archive.writestr("safe.txt", "content")
    report = release.audit_tree(root)
    assert report["status"] == "FAIL"
    assert any(row["kind"] == "INVALID_ZIP" for row in report["findings"])


def test_scanner_allows_public_ca_bundle_and_jpype_bootstrap_jar_only(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "wheelhouse"
    ca = b"-----BEGIN CERTIFICATE-----\npublic certificate fixture\n-----END CERTIFICATE-----\n"
    make_wheel(root / "certifi-1.0-py3-none-any.whl", name="certifi", extra=("certifi/cacert.pem", ca))
    jar_bytes = io.BytesIO()
    with zipfile.ZipFile(jar_bytes, "w") as jar:
        jar.writestr("org/jpype/Bootstrap.class", b"class file fixture")
    make_wheel(root / "JPype1-1.7.1-py3-none-any.whl", name="JPype1", version="1.7.1",
               extra=("org.jpype.jar", jar_bytes.getvalue()))
    report = release.audit_tree(root)
    assert report["status"] == "PASS", report["findings"]

    make_wheel(root / "other_dependency-1.0-py3-none-any.whl", name="other-dependency",
               extra=("unapproved.jar", b"jar") )
    report = release.audit_tree(root)
    assert report["status"] == "FAIL"
    assert any(row["kind"] == "PROHIBITED_VENDOR_OR_PRIVATE_BINARY" for row in report["findings"])


def test_scanner_allows_only_the_pinned_pywin32_public_help_member(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "wheelhouse"
    help_path = root / "pywin32-312-cp312-cp312-win_amd64.whl"
    make_wheel(help_path, name="pywin32", version="312", tag="cp312-cp312-win_amd64",
               extra=("PyWin32.chm", b"public PyWin32 help"))
    report = release.audit_tree(root)
    assert report["status"] == "PASS", report["findings"]
    metadata = release.wheel_metadata(help_path)
    assert metadata["approved_public_docs"] == [{
        "path": "PyWin32.chm", "bytes": len(b"public PyWin32 help"),
        "sha256": hashlib.sha256(b"public PyWin32 help").hexdigest(),
    }]

    other = root / "other-1.0-py3-none-any.whl"
    make_wheel(other, name="other", version="1.0", extra=("docs/help.chm", b"unexpected documentation"))
    report = release.audit_tree(root)
    assert report["status"] == "FAIL"
    assert any(row["kind"] == "PROHIBITED_VENDOR_OR_PRIVATE_BINARY" and "help.chm" in row["path"]
               for row in report["findings"])


def test_scanner_detects_pem_private_key_block_but_not_parser_header_constant(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    (root / "parser.py").write_bytes(b'HEADER = "-----BEGIN OPENSSH PRIVATE KEY-----"\n')
    (root / "private.pem").write_bytes(
        b"-----BEGIN OPENSSH PRIVATE KEY-----\n" + b"A" * 160 +
        b"\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    report = release.audit_tree(root)
    assert report["status"] == "FAIL"
    assert any(row["path"] == "private.pem" and row["kind"] == "SECRET_PATTERN" for row in report["findings"])


def test_doctor_uses_venv_path_when_python_is_a_symlink(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv = tmp_path / "clean-venv"
    scripts = venv / "bin"
    scripts.mkdir(parents=True)
    base_python = tmp_path / "base-python"
    base_python.write_text("interpreter placeholder\n", encoding="utf-8")
    venv_python = scripts / "python"
    venv_python.symlink_to(base_python)
    launcher = scripts / "comsol-mcp"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    identity = {
        "python": "3.12.0", "executable": str(venv_python), "prefix": str(venv),
        "version": "0.1.9", "module": str(venv / "lib/python3.12/site-packages/comsol_mcp"),
        "entry_points": [{"name": "comsol-mcp", "value": "comsol_mcp.mcp_server:main"}],
        "entry_point_path": str(launcher), "entry_point_exists": True, "package_file_count": 4,
        "java_sources": ["Worker.java"], "action_catalogs": ["data/02_ACTION_CATALOG.json"],
        "schemas": ["data/schema.json"],
    }
    calls: list[dict[str, object]] = []

    def fake_run(argv: list[str], *, cwd: pathlib.Path | None = None, **kwargs: object) -> dict[str, object]:
        calls.append({"argv": argv, "cwd": cwd})
        stdout = json.dumps(identity) if "-c" in argv else "No broken requirements found.\n"
        return {"argv": argv, "cwd": str(cwd), "exit_code": 0, "stdout": stdout, "stderr": "", "duration_seconds": 0.0}

    monkeypatch.setattr(release, "run_record", fake_run)
    result = release.doctor(venv_python)
    assert result["status"] == "PASS"
    assert result["installed_under_prefix"] is True
    assert calls[0]["cwd"] == venv
    assert calls[1]["cwd"] == venv
    assert "pathlib.Path(sys.executable).parent" in calls[0]["argv"][3]


def _make_marked_install(root: pathlib.Path) -> pathlib.Path:
    root.mkdir()
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    binary_dir = root / ("Scripts" if os.name == "nt" else "bin")
    binary_dir.mkdir()
    python = binary_dir / ("python.exe" if os.name == "nt" else "python")
    python.write_text("isolated interpreter fixture\n", encoding="utf-8")
    release._write_install_marker(root, state="INITIALIZING")
    release._record_install_inventory(python)
    return python


def test_uninstall_default_writes_exact_plan_without_deleting_the_venv(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "owned-venv"
    _make_marked_install(root)
    monkeypatch.setattr(release, "_process_guard", lambda _root: {
        "status": "CLEAR", "reason": "test inventory clear", "pids": [],
    })

    result = release.uninstall_venv(root, tmp_path / "uninstall-evidence")

    assert result["status"] == "UNINSTALL_PLAN_READY"
    assert result["confirmed"] is False
    assert result["deletion_scope"]["exact_directory"] == str(root.resolve())
    assert result["deletion_scope"]["outside_paths_touched"] is False
    assert (tmp_path / "uninstall-evidence" / "uninstall-plan.json").is_file()
    assert root.is_dir()


def test_uninstall_confirm_removes_only_marked_venv_and_keeps_receipts_outside(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "owned-venv"
    _make_marked_install(root)
    monkeypatch.setattr(release, "_process_guard", lambda _root: {
        "status": "CLEAR", "reason": "test inventory clear", "pids": [],
    })
    evidence = tmp_path / "uninstall-evidence"

    result = release.uninstall_venv(root, evidence, confirm=True)

    assert result["status"] == "PASS_UNINSTALLED"
    assert not root.exists()
    assert (evidence / "uninstall-plan.json").is_file()
    final = json.loads((evidence / "uninstall-result.json").read_text(encoding="utf-8"))
    assert final["status"] == "PASS_UNINSTALLED"
    assert final["target_root_exists_after_attempt"] is False


def test_real_temporary_venv_marker_inventory_and_cleanup(tmp_path: pathlib.Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "real-temporary-venv"
    venv_python = release.create_venv(pathlib.Path(sys.executable), root)
    marker = release._load_install_marker(root)
    assert marker["state"] == "INITIALIZING"
    assert release._content_guard(root, marker)["status"] == "CLEAR"
    release._mark_install_ready(venv_python)
    monkeypatch.setattr(release, "_process_guard", lambda _root: {
        "status": "CLEAR", "reason": "test process inventory clear", "pids": [],
    })

    result = release.uninstall_venv(root, tmp_path / "real-venv-uninstall-evidence", confirm=True)

    assert result["status"] == "PASS_UNINSTALLED"
    assert not root.exists()


@pytest.mark.parametrize("guard_status", ["BUSY", "UNKNOWN"])
def test_uninstall_refuses_busy_or_unknown_process_inventory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, guard_status: str
) -> None:
    root = tmp_path / "owned-venv"
    _make_marked_install(root)
    monkeypatch.setattr(release, "_process_guard", lambda _root: {
        "status": guard_status, "reason": "not safe to remove", "pids": [4321] if guard_status == "BUSY" else [],
    })

    result = release.uninstall_venv(root, tmp_path / "uninstall-evidence", confirm=True)

    assert result["status"] == f"BLOCKED_{guard_status}"
    assert root.exists()
    assert not (tmp_path / "uninstall-evidence" / "uninstall-result.json").exists()


def test_uninstall_rejects_wrong_project_or_canonical_path_marker(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "owned-venv"
    _make_marked_install(root)
    marker_path = root / release.INSTALL_MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["install_root"] = str(tmp_path / "other-venv")
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(release.ReleaseError, match="canonical path"):
        release.uninstall_venv(root, tmp_path / "uninstall-evidence", confirm=True)
    assert root.exists()


def test_uninstall_rejects_unmarked_or_malformed_install(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "unmarked-venv"
    root.mkdir()
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="marker"):
        release.uninstall_venv(root, tmp_path / "uninstall-evidence", confirm=True)
    assert root.exists()

    malformed = tmp_path / "malformed-venv"
    _make_marked_install(malformed)
    (malformed / release.INSTALL_MARKER_NAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="malformed"):
        release.uninstall_venv(malformed, tmp_path / "malformed-evidence", confirm=True)
    assert malformed.exists()


def test_uninstall_requires_a_content_ownership_baseline(tmp_path: pathlib.Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "baseline-missing-venv"
    root.mkdir()
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    binary_dir = root / ("Scripts" if os.name == "nt" else "bin")
    binary_dir.mkdir()
    (binary_dir / ("python.exe" if os.name == "nt" else "python")).write_text("fixture\n", encoding="utf-8")
    release._write_install_marker(root, state="INITIALIZING")
    monkeypatch.setattr(release, "_process_guard", lambda _root: {
        "status": "CLEAR", "reason": "test inventory clear", "pids": [],
    })

    result = release.uninstall_venv(root, tmp_path / "uninstall-evidence", confirm=True)

    assert result["status"] == "BLOCKED_UNKNOWN"
    assert root.exists()


def test_uninstall_refuses_added_model_or_unowned_user_file(tmp_path: pathlib.Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "owned-venv"
    _make_marked_install(root)
    (root / "user-model.mph").write_bytes(b"protected model fixture")
    monkeypatch.setattr(release, "_process_guard", lambda _root: {
        "status": "CLEAR", "reason": "test inventory clear", "pids": [],
    })

    model_result = release.uninstall_venv(root, tmp_path / "model-evidence", confirm=True)
    assert model_result["status"] == "BLOCKED_PROTECTED_ARTIFACT"
    assert root.exists()

    # A non-COMSOL addition is still outside the recorded install ownership.
    second_root = tmp_path / "owned-venv-2"
    _make_marked_install(second_root)
    (second_root / "notes.txt").write_text("user data", encoding="utf-8")
    data_result = release.uninstall_venv(second_root, tmp_path / "data-evidence", confirm=True)
    assert data_result["status"] == "BLOCKED_CONTENT_DRIFT"
    assert second_root.exists()

    third_root = tmp_path / "owned-venv-3"
    _make_marked_install(third_root)
    generated_cache = third_root / "lib" / "__pycache__"
    generated_cache.mkdir(parents=True)
    (generated_cache / "user-model.mph").write_bytes(b"protected model hidden in cache dir")
    protected_result = release.uninstall_venv(third_root, tmp_path / "cache-model-evidence", confirm=True)
    assert protected_result["status"] == "BLOCKED_PROTECTED_ARTIFACT"
    assert third_root.exists()

    text_model_root = tmp_path / "owned-venv-4"
    _make_marked_install(text_model_root)
    (text_model_root / "project.mphtxt").write_text("protected model source", encoding="utf-8")
    text_result = release.uninstall_venv(text_model_root, tmp_path / "text-model-evidence", confirm=True)
    assert text_result["status"] == "BLOCKED_PROTECTED_ARTIFACT"
    assert text_model_root.exists()


def test_uninstall_content_inventory_fails_closed_on_unreadable_subtree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "owned-venv"
    _make_marked_install(root)
    marker = release._load_install_marker(root)

    def denied_walk(_root: pathlib.Path, *, followlinks: bool, onerror: object):
        assert followlinks is False
        assert callable(onerror)
        onerror(PermissionError("simulated denied subtree"))
        yield str(root), [], []

    monkeypatch.setattr(release.os, "walk", denied_walk)
    result = release._content_guard(root, marker)
    assert result["status"] == "UNKNOWN"
    with pytest.raises(PermissionError, match="simulated denied subtree"):
        release._validate_removal_tree(root)


def test_uninstall_inventory_counts_nested_same_name_marker_as_unowned_content(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "owned-venv"
    _make_marked_install(root)
    nested_marker = root / "site-packages" / release.INSTALL_MARKER_NAME
    nested_marker.parent.mkdir(parents=True)
    nested_marker.write_text("user data", encoding="utf-8")
    monkeypatch.setattr(release, "_process_guard", lambda _root: {
        "status": "CLEAR", "reason": "test inventory clear", "pids": [],
    })

    result = release.uninstall_venv(root, tmp_path / "nested-marker-evidence", confirm=True)

    assert result["status"] == "BLOCKED_CONTENT_DRIFT"
    assert root.exists()


@pytest.mark.parametrize("unsafe_root_kind", ["filesystem", "home", "source", "source_ancestor"])
def test_install_root_guard_rejects_protected_locations(unsafe_root_kind: str) -> None:
    source = pathlib.Path(release.__file__).resolve().parents[1]
    unsafe_roots = {
        "filesystem": pathlib.Path(pathlib.Path().anchor),
        "home": pathlib.Path.home(),
        "source": source,
        "source_ancestor": source.parent,
    }
    with pytest.raises(release.ReleaseError, match="refusing an install root"):
        release._canonical_install_root(unsafe_roots[unsafe_root_kind])


@pytest.mark.parametrize(
    ("ps_output", "expected"),
    [
        ("$SELF 1 /usr/bin/python3.12 /private/tmp/test-runner.py\n101 1 /usr/bin/python3 /tmp/other.py\n", "CLEAR"),
        ("$SELF 1 /usr/bin/python3.12 /private/tmp/test-runner.py\n101 1 $TARGET/bin/python -m comsol_mcp\n", "BUSY"),
        ("$SELF 1 /usr/bin/python3.12 /private/tmp/test-runner.py\n101 1 python -m comsol_mcp\n", "UNKNOWN"),
    ],
)
def test_process_guard_requires_attributable_python_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, ps_output: str, expected: str
) -> None:
    target = tmp_path / "owned-venv"

    def fake_run(*_args: object, **_kwargs: object) -> types.SimpleNamespace:
        output = ps_output.replace("$TARGET", str(target)).replace("$SELF", str(os.getpid()))
        return types.SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    result = release._process_guard(target)
    assert result["status"] == expected


def test_process_guard_inventory_error_is_unknown_and_never_clear(monkeypatch: pytest.MonkeyPatch,
                                                                  tmp_path: pathlib.Path) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=1, stdout="", stderr="permission denied")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    result = release._process_guard(tmp_path / "owned-venv")
    assert result["status"] == "UNKNOWN"


@pytest.mark.parametrize("stdout,stderr,returncode", [("", "", 0), ("malformed\n", "", 0),
                                                        ("", "permission denied", 0)])
def test_process_guard_empty_malformed_or_stderr_inventory_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, stdout: str, stderr: str, returncode: int
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    result = release._process_guard(tmp_path / "owned-venv")
    assert result["status"] == "UNKNOWN"


def test_process_guard_valid_rows_without_self_are_unknown(monkeypatch: pytest.MonkeyPatch,
                                                           tmp_path: pathlib.Path) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> types.SimpleNamespace:
        return types.SimpleNamespace(returncode=0, stdout="101 1 /usr/bin/python3 /tmp/other.py\n", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release._process_guard(tmp_path / "owned-venv")["status"] == "UNKNOWN"


def test_windows_process_guard_checks_every_process_before_classification(tmp_path: pathlib.Path) -> None:
    root = (tmp_path / "owned-venv").resolve()
    root_text = os.path.normcase(str(root)).replace("\\", "/")
    current_pid = 500
    self_row = {"ProcessId": current_pid, "ParentProcessId": 100, "Name": "python.exe",
                "ExecutablePath": "C:/Python/python.exe", "CommandLine": "C:/Python/python.exe release.py"}
    clear_row = {"ProcessId": 100, "ParentProcessId": 1, "Name": "explorer.exe",
                 "ExecutablePath": "C:/Windows/explorer.exe", "CommandLine": "C:/Windows/explorer.exe"}
    assert release._windows_process_rows_guard([self_row, clear_row], root_text, current_pid)["status"] == "CLEAR"

    unrelated_name_reference = dict(clear_row, CommandLine=f"C:/Windows/explorer.exe {root}")
    busy = release._windows_process_rows_guard([self_row, unrelated_name_reference], root_text, current_pid)
    assert busy["status"] == "BUSY"

    incomplete = dict(clear_row, CommandLine=None)
    unknown = release._windows_process_rows_guard([self_row, incomplete], root_text, current_pid)
    assert unknown["status"] == "UNKNOWN"
    malformed = dict(clear_row, ExecutablePath=17)
    assert release._windows_process_rows_guard([self_row, malformed], root_text, current_pid)["status"] == "UNKNOWN"

    parent_shell = dict(clear_row, Name="powershell.exe", CommandLine=f"powershell.exe -Command {root}")
    assert release._windows_process_rows_guard([self_row, parent_shell], root_text, current_pid)["status"] == "CLEAR"
    assert release._windows_process_rows_guard([], root_text, current_pid)["status"] == "UNKNOWN"
    assert release._windows_process_rows_guard([clear_row], root_text, current_pid)["status"] == "UNKNOWN"


def test_windows_process_guard_only_exempts_exact_kernel_identities_from_synthetic_inventory() -> None:
    # Keep the regression independent of an actual host process inventory. Two
    # exact kernel identities may be omitted; opaque service/user rows remain
    # UNKNOWN until their executable path and command line can be inspected.
    incomplete_rows = [
        {"ProcessId": 0, "ParentProcessId": 0, "Name": "System Idle Process",
         "ExecutablePath": None, "CommandLine": None},
        {"ProcessId": 4, "ParentProcessId": 0, "Name": "System",
         "ExecutablePath": None, "CommandLine": None},
        *[
            {"ProcessId": pid, "ParentProcessId": 100, "Name": f"OpaqueService{pid}",
             "ExecutablePath": None, "CommandLine": None}
            for pid in range(1000, 1017)
        ],
    ]
    current_pid = 500
    self_row = {"ProcessId": current_pid, "ParentProcessId": 100, "Name": "python.exe",
                "ExecutablePath": "C:/Python/python.exe", "CommandLine": "C:/Python/python.exe release.py"}
    root_text = "c:/users/example/comsol-venv"
    kernel_rows = [row for row in incomplete_rows if (
        (row["ProcessId"] == 0 and row["Name"] == "System Idle Process") or
        (row["ProcessId"] == 4 and row["Name"] == "System"))]
    opaque_rows = [row for row in incomplete_rows if row not in kernel_rows]

    assert len(incomplete_rows) == 19
    assert len(kernel_rows) == 2
    assert len(opaque_rows) == 17
    assert release._windows_process_rows_guard([self_row, *kernel_rows], root_text, current_pid)["status"] == "CLEAR"
    assert release._windows_process_rows_guard([self_row, *opaque_rows], root_text, current_pid)["status"] == "UNKNOWN"


def _make_campaign_state_fixture(root: pathlib.Path, *, active_jobs: list[object] | None = None) -> pathlib.Path:
    state = root / "repository" / "docs" / "full_project_execution" / "state"
    state.mkdir(parents=True)
    user_scope_decisions = state.parent / "USER_SCOPE_DECISIONS.json"
    user_scope_decisions.write_text(json.dumps({
        "schema_version": 1,
        "decisions": [{
            "decision": "USER_REQUESTED_SKIP",
            "targets": ["macos-arm64/comsol6.3", "macos-x86_64/comsol6.3"],
        }],
    }, indent=2) + "\n", encoding="utf-8")
    resume = {
        "status": "IN_PROGRESS", "phase": "P0", "task": "MODEL_ROUTES",
        "active_jobs": active_jobs or [], "main_context_id": None, "reviewer_context_id": None,
        "next_action": "Continue the recorded task after manual process and external job-store reconciliation.",
        "checkpoint": "/private/tmp/original-workpack/repository/docs/full_project_execution/state/RESUME.json",
    }
    (state / "RESUME.json").write_text(json.dumps(resume, indent=2) + "\n", encoding="utf-8")
    (state / "TASKS.json").write_text(json.dumps({"tasks": [{"id": "W23", "status": "IN_PROGRESS"}]}) + "\n",
                                                  encoding="utf-8")
    scope = root / "scope.json"
    scope.write_text(json.dumps({
        "schema": release.STATE_SNAPSHOT_SCOPE_SCHEMA,
        "files": [
            {"path": "repository/docs/full_project_execution/state/RESUME.json",
             "distribution_class": "LOCAL_RECOVERY_ONLY", "reason": "authoritative campaign resume state"},
            {"path": "repository/docs/full_project_execution/state/TASKS.json",
             "distribution_class": "LOCAL_RECOVERY_ONLY", "reason": "unfinished task ledger"},
            {"path": release.USER_SCOPE_DECISIONS_PATH,
             "distribution_class": "LOCAL_RECOVERY_ONLY", "reason": "explicit user scope overrides"},
        ],
        "external_pointers": [],
    }, indent=2) + "\n", encoding="utf-8")
    return scope


def test_campaign_state_snapshot_is_double_sampled_and_never_marks_final_acceptance(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source)
    bundle = tmp_path / "campaign-state.zip"
    receipt = release.campaign_state_bundle(source, scope, bundle, tmp_path / "capture-evidence")
    assert receipt["kind"] == release.STATE_SNAPSHOT_KIND
    assert receipt["status"] == "PASS_CAMPAIGN_STATE_ARCHIVE_HASHED"
    capture = json.loads((tmp_path / "capture-evidence" / "campaign-state-bundle-receipt.json").read_text())
    assert capture["payload_file_count"] == 3
    assert len(capture["manifest_capture_attempts"][-1]["passes"]) == 2
    assert capture["manifest_capture_attempts"][-1]["stable"] is True
    result = release.verify_campaign_state_bundle(bundle)
    assert result["snapshot_integrity"] == "PASS"
    assert result["resume_preparation"] == "PREPARED_READ_ONLY"
    assert result["final_delivery_acceptance"] == "INCOMPLETE"
    assert result["host_process_quiescence"] == "UNVERIFIED"
    assert result["external_job_store"] == "NOT_INCLUDED_AND_NOT_VERIFIED"
    assert result["job_reconnection_or_reassignment"] == "BLOCKED_PENDING_EXTERNAL_STORE_AND_LIVE_PROCESS_RECONCILIATION"
    assert result["unfinished_activity_recoverability"]["pending_work"] == ["W23", "W24", "independent final review"]
    assert result["job_relaunch_or_reassignment_permitted"] is False
    assert result["execution_performed"] is False


def test_campaign_state_scope_requires_explicit_user_scope_decisions(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source)
    scope_record = json.loads(scope.read_text(encoding="utf-8"))
    scope_record["files"] = [
        row for row in scope_record["files"]
        if row["path"] != release.USER_SCOPE_DECISIONS_PATH
    ]
    scope.write_text(json.dumps(scope_record), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="USER_SCOPE_DECISIONS.json"):
        release.campaign_state_bundle(source, scope, tmp_path / "missing-scope.zip",
                                      tmp_path / "missing-scope-evidence")


def test_campaign_state_scope_decisions_must_be_nonempty_json(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source)
    decision_path = source / release.USER_SCOPE_DECISIONS_PATH
    decision_path.write_text(json.dumps({"schema_version": 1, "decisions": []}), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="nonempty decisions list"):
        release.campaign_state_bundle(source, scope, tmp_path / "empty-decisions.zip",
                                      tmp_path / "empty-decisions-evidence")


def test_campaign_state_snapshot_retries_the_entire_group_after_drift(tmp_path: pathlib.Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source)
    resume_path = source / "repository/docs/full_project_execution/state/RESUME.json"
    original = release._stable_read_under
    changed = False

    def mutate_after_first_read(root: pathlib.Path, rel: str) -> tuple[bytes, dict[str, int]]:
        nonlocal changed
        data, identity = original(root, rel)
        if rel.endswith("/state/RESUME.json") and not changed:
            changed = True
            resume_path.write_text(resume_path.read_text(encoding="utf-8").replace("Continue", "Resume "), encoding="utf-8")
        return data, identity

    monkeypatch.setattr(release, "_stable_read_under", mutate_after_first_read)
    receipt = release.campaign_state_bundle(source, scope, tmp_path / "campaign-state.zip", tmp_path / "capture-evidence")
    attempts = receipt["manifest_capture_attempts"]
    assert attempts[0]["stable"] is False
    assert attempts[-1]["stable"] is True
    assert attempts[-1]["attempt"] == 2
    assert receipt["result"]["snapshot_integrity"] == "PASS"


def test_campaign_state_extract_and_path_map_are_detached_and_non_mutating(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source)
    resume_path = source / "repository/docs/full_project_execution/state/RESUME.json"
    original_bytes = resume_path.read_bytes()
    bundle = tmp_path / "campaign-state.zip"
    release.campaign_state_bundle(source, scope, bundle, tmp_path / "capture-evidence")
    extracted = tmp_path / "fresh-restore-check"
    overlay = tmp_path / "path-map.json"
    overlay.write_text(json.dumps({
        "schema": release.PATH_REMAP_SCHEMA,
        "mode": "READ_ONLY_DETACHED_OVERLAY",
        "mappings": [{"from_prefix": "/private/tmp/original-workpack", "to_prefix": str(extracted)}],
    }, indent=2) + "\n", encoding="utf-8")
    result = release.verify_campaign_state_bundle(bundle, extracted, overlay)
    assert result["extraction"]["status"] == "PASS_EXTRACTED_READ_ONLY"
    assert result["path_mapping"]["source_bytes_rewritten"] is False
    assert result["path_mapping"]["mapped_count"] == 1
    mapped = result["path_mapping"]["references"][0]
    assert mapped["mapping_status"] == "MAPPED_IN_DETACHED_OVERLAY"
    assert mapped["mapped_path"].startswith(str(extracted))
    archived_resume = extracted / "repository/docs/full_project_execution/state/RESUME.json"
    assert archived_resume.read_bytes() == original_bytes
    assert (extracted / "CAMPAIGN_STATE_SNAPSHOT_MANIFEST.json").is_file()
    assert (extracted / "PATH_REMAP_TEMPLATE.json").is_file()


def test_campaign_state_active_jobs_require_read_only_reconciliation(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source, active_jobs=[{"job_id": "unreconciled"}])
    bundle = tmp_path / "campaign-state.zip"
    release.campaign_state_bundle(source, scope, bundle, tmp_path / "capture-evidence")
    result = release.verify_campaign_state_bundle(bundle)
    assert result["resume_preparation"] == "RECONCILE_REQUIRED_READ_ONLY"
    assert result["recorded_active_jobs_count"] == 1
    assert result["host_process_quiescence"] == "UNVERIFIED"
    assert result["job_relaunch_or_reassignment_permitted"] is False


def test_campaign_state_verifier_rejects_payload_tampering_and_nonlocal_scope(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source)
    bundle = tmp_path / "campaign-state.zip"
    release.campaign_state_bundle(source, scope, bundle, tmp_path / "capture-evidence")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(bundle) as original, zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename.endswith("/state/TASKS.json"):
                data += b"\n"
            output.writestr(info.filename, data)
    with pytest.raises(release.ReleaseError, match="differs from its manifest"):
        release.verify_campaign_state_bundle(tampered)

    invalid_scope = json.loads(scope.read_text(encoding="utf-8"))
    invalid_scope["files"][0]["distribution_class"] = "PUBLIC_SOURCE_CANDIDATE"
    scope.write_text(json.dumps(invalid_scope), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="LOCAL_RECOVERY_ONLY"):
        release.campaign_state_bundle(source, scope, tmp_path / "invalid.zip", tmp_path / "invalid-evidence")


def test_campaign_state_verifier_rejects_manifest_without_user_scope_decisions(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "workpack"
    scope = _make_campaign_state_fixture(source)
    bundle = tmp_path / "campaign-state.zip"
    release.campaign_state_bundle(source, scope, bundle, tmp_path / "capture-evidence")
    tampered = tmp_path / "missing-user-scope.zip"
    with zipfile.ZipFile(bundle, "r") as original, zipfile.ZipFile(
        tampered, "w", compression=zipfile.ZIP_DEFLATED
    ) as output:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "CAMPAIGN_STATE_SNAPSHOT_MANIFEST.json":
                manifest = json.loads(data)
                manifest["included_files"] = [
                    row for row in manifest["included_files"]
                    if row["path"] != release.USER_SCOPE_DECISIONS_PATH
                ]
                data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
            output.writestr(info.filename, data)
    with pytest.raises(release.ReleaseError, match="USER_SCOPE_DECISIONS.json"):
        release.verify_campaign_state_bundle(tampered)


def _transition_fixture(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate_version: str = "0.2.0",
    upgrade_exit: int = 0,
    rollback_exit: int = 0,
    rollback_version: str | None = None,
    old_lock_has_version: bool = True,
    cleanup_mode: str = "success",
) -> tuple[dict[str, object], dict[str, str], list[str]]:
    old = {"comsol-mcp": "0.1.9", "base-dep": "1.0", "pip": "24.0"}
    candidate = {"comsol-mcp": candidate_version, "base-dep": "1.0", "candidate-dep": "2.0", "pip": "24.0"}
    state: dict[str, str] = {}
    events: list[str] = []
    old_lock = tmp_path / "old.lock"
    new_lock = tmp_path / "new.lock"
    old_line = "comsol-mcp==0.1.9" if old_lock_has_version else "base-dep==1.0"
    old_lock.write_text(f"{old_line} --hash=sha256:{'0' * 64}\n", encoding="utf-8")
    new_lock.write_text(f"comsol-mcp==0.2.0 --hash=sha256:{'1' * 64}\n", encoding="utf-8")
    old_wheelhouse = tmp_path / "old-wheelhouse"
    new_wheelhouse = tmp_path / "new-wheelhouse"
    old_wheelhouse.mkdir()
    new_wheelhouse.mkdir()
    args = types.SimpleNamespace(
        python=pathlib.Path(sys.executable), work_root=tmp_path / "transition-work",
        old_requirements=old_lock, old_wheelhouse=old_wheelhouse,
        new_requirements=new_lock, new_wheelhouse=new_wheelhouse,
    )

    def fake_offline_install(inner: types.SimpleNamespace, _script: pathlib.Path) -> dict[str, object]:
        events.append("initial-old-install")
        state.clear()
        state.update(old)
        return {"status": "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT"}

    def fake_run(argv: list[str], *, cwd: pathlib.Path | None = None) -> dict[str, object]:
        del cwd
        if "install" in argv:
            lock = pathlib.Path(argv[argv.index("--requirement") + 1])
            if "comsol-mcp==0.2.0" in lock.read_text(encoding="utf-8"):
                events.append("candidate-install")
                state.update(candidate)
                return {"argv": argv, "exit_code": upgrade_exit, "stdout": "", "stderr": "candidate install result",
                        "duration_seconds": 0.0}
            events.append("old-lock-rollback")
            state.update(old)
            if rollback_version is not None:
                state["comsol-mcp"] = rollback_version
            return {"argv": argv, "exit_code": rollback_exit, "stdout": "", "stderr": "rollback result",
                    "duration_seconds": 0.0}
        if "uninstall" in argv:
            names = argv[argv.index("--yes") + 1:]
            events.extend(f"uninstall:{name}" for name in names)
            if cleanup_mode != "return_nonzero":
                for name in names:
                    state.pop(name, None)
            if cleanup_mode == "raise_after_remove":
                raise OSError("simulated lost cleanup result")
            if cleanup_mode == "return_nonzero":
                return {"argv": argv, "exit_code": 1, "stdout": "", "stderr": "cleanup failure",
                        "duration_seconds": 0.0}
            return {"argv": argv, "exit_code": 0, "stdout": "", "stderr": "", "duration_seconds": 0.0}
        raise AssertionError(f"unexpected transition command: {argv}")

    def fake_package_fingerprint(_python: pathlib.Path) -> dict[str, object]:
        version = state.get("comsol-mcp")
        digest = hashlib.sha256(str(version).encode()).hexdigest()
        return {"digest": digest, "files": []}

    def fake_distribution_fingerprint(_python: pathlib.Path) -> dict[str, object]:
        rows = [{"name": name, "version": version} for name, version in sorted(state.items())]
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {"distributions": rows, "digest": digest}

    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setattr(release.shutil, "which", lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None)
    monkeypatch.setattr(release, "offline_install", fake_offline_install)
    monkeypatch.setattr(release, "run_record", fake_run)
    monkeypatch.setattr(release, "package_fingerprint", fake_package_fingerprint)
    monkeypatch.setattr(release, "distribution_fingerprint", fake_distribution_fingerprint)
    monkeypatch.setattr(release, "_installed_version", lambda _python: state.get("comsol-mcp"))
    result = release.transition_check(args, pathlib.Path(release.__file__))
    return result, state, events


def test_distribution_fingerprint_reads_complete_normalized_inventory() -> None:
    result = release.distribution_fingerprint(pathlib.Path(sys.executable))
    assert result["distributions"]
    assert result["digest"] == hashlib.sha256(
        json.dumps(result["distributions"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_transition_check_rolls_back_artifact_and_candidate_only_distributions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, events = _transition_fixture(tmp_path, monkeypatch)
    assert result["status"] == "PASS_ARTIFACT_ROLLBACK_MIGRATION_UNVERIFIED"
    assert result["artifact_version_transition_verified"] is True
    assert result["version_migration_verified"] is False
    assert result["rollback_identity_matches"] == {"package_fingerprint": True, "distribution_fingerprint": True}
    assert result["candidate_only_distribution_names_attempted"] == ["candidate-dep"]
    assert result["candidate_only_distribution_names_removed_confirmed"] == ["candidate-dep"]
    assert state == {"comsol-mcp": "0.1.9", "base-dep": "1.0", "pip": "24.0"}
    assert events == ["initial-old-install", "candidate-install", "old-lock-rollback", "uninstall:candidate-dep"]
    old_snapshot = pathlib.Path(result["input_locks"][0]["snapshot_path"])
    new_snapshot = pathlib.Path(result["input_locks"][1]["snapshot_path"])
    assert old_snapshot.read_text(encoding="utf-8").startswith("comsol-mcp==0.1.9")
    assert new_snapshot.read_text(encoding="utf-8").startswith("comsol-mcp==0.2.0")
    assert result["input_locks"][0]["sha256"] == hashlib.sha256(old_snapshot.read_bytes()).hexdigest()


def test_transition_check_attempts_and_verifies_rollback_after_partial_upgrade_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, events = _transition_fixture(tmp_path, monkeypatch, upgrade_exit=1)
    assert result["upgrade"]["exit_code"] == 1
    assert result["status"] == "FAIL_UPGRADE_ROLLED_BACK"
    assert events[2] == "old-lock-rollback"
    assert state == {"comsol-mcp": "0.1.9", "base-dep": "1.0", "pip": "24.0"}


def test_transition_check_rejects_candidate_version_mismatch_after_rollback(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, _events = _transition_fixture(tmp_path, monkeypatch, candidate_version="0.2.1")
    assert result["status"] == "FAIL_CANDIDATE_VERSION_ROLLED_BACK"
    assert result["artifact_version_transition_verified"] is False
    assert state["comsol-mcp"] == "0.1.9"


def test_transition_check_reports_rollback_state_mismatch_and_never_passes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, _events = _transition_fixture(tmp_path, monkeypatch, rollback_version="0.1.8")
    assert result["status"] == "FAIL_ROLLBACK_STATE_MISMATCH"
    assert result["rollback_identity_matches"]["package_fingerprint"] is False
    assert result["rollback_version_matches_lock"] is False
    assert state["comsol-mcp"] == "0.1.8"


def test_transition_check_does_not_call_old_lock_install_a_rollback_pass_if_it_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, events = _transition_fixture(tmp_path, monkeypatch, rollback_exit=1)
    assert result["status"] == "FAIL_ROLLBACK_COMMAND"
    assert "old-lock-rollback" in events
    assert result["rollback"]["exit_code"] == 1
    assert state["comsol-mcp"] == "0.1.9"


def test_transition_check_blocks_when_old_lock_does_not_pin_application_version(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, events = _transition_fixture(tmp_path, monkeypatch, old_lock_has_version=False)
    assert result["status"] == "BLOCKED_LOCK_VERSION_UNRESOLVED"
    assert result["expected_versions"]["old"] is None
    assert state == {}
    assert events == []


def test_transition_check_does_not_pass_when_candidate_cleanup_result_is_unknown(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, _events = _transition_fixture(tmp_path, monkeypatch, cleanup_mode="raise_after_remove")
    assert state == {"comsol-mcp": "0.1.9", "base-dep": "1.0", "pip": "24.0"}
    assert result["rollback_candidate_only_cleanup"]["attempted"] is True
    assert result["rollback_candidate_only_cleanup"]["exit_code"] is None
    assert result["candidate_only_distribution_names_attempted"] == ["candidate-dep"]
    assert result["candidate_only_distribution_names_removed_confirmed"] == ["candidate-dep"]
    assert result["status"] == "UNKNOWN_ROLLBACK_CLEANUP"


def test_transition_check_keeps_candidate_cleanup_failure_distinct_from_rollback_pass(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, state, _events = _transition_fixture(tmp_path, monkeypatch, cleanup_mode="return_nonzero")
    assert result["rollback_candidate_only_cleanup"]["exit_code"] == 1
    assert result["candidate_only_distribution_names_removed_confirmed"] == []
    assert "candidate-dep" in state
    assert result["status"] == "FAIL_ROLLBACK_CLEANUP"
