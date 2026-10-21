from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import subprocess

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp import _runtime_installation
from tools import windows_current_installed_profile_runtime_binding_r4 as binding


def _installed_fixture(tmp_path: Path, target: str) -> tuple[dict, dict]:
    full_version = binding.TARGET_BUILDS[target]
    catalog_version = binding.TARGET_CATALOG_VERSIONS[target]
    build_number = binding.TARGET_BUILD_NUMBERS[target]
    root = tmp_path / ("COMSOL-" + full_version)
    root.mkdir()
    readme = root / "readme.txt"
    readme.write_text(
        f"COMSOL {full_version} README\nfor COMSOL {catalog_version.rsplit('.', 1)[0]}.\n",
        encoding="utf-8",
    )
    desktop = root / "bin" / "win64" / "comsol.exe"
    server = root / "bin" / "win64" / "comsolmphserver.exe"
    for executable in (desktop, server):
        executable.parent.mkdir(parents=True, exist_ok=True)
        image = bytearray(128)
        image[:2] = b"MZ"
        image[0x3C:0x40] = struct.pack("<I", 64)
        image[64:70] = b"PE\0\0" + struct.pack("<H", 0x8664)
        executable.write_bytes(image)
    manifest = root / "bin" / "comsolclientpath.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("apiplugins/client.jar\n", encoding="utf-8")
    jar = root / "apiplugins" / "client.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"fixture-client-jar")
    jdk = tmp_path / "jdk"
    (jdk / "bin").mkdir(parents=True)
    for name in ("java", "javac", "java.exe", "javac.exe"):
        (jdk / "bin" / name).write_bytes((name + " fixture; never launched\n").encode("ascii"))
    (jdk / "release").write_text('JAVA_VERSION="11.0.24"\nOS_ARCH="amd64"\n', encoding="ascii")

    runtime_id = _runtime_installation.runtime_id_for_root(root)
    installation = {
        "runtime_id": runtime_id,
        "root": str(root.resolve()),
        "metadata_only": True,
        "comsol_started": False,
        "license_checked_out": False,
        "version": {"status": "OBSERVED", "value": catalog_version},
        "build": {"status": "OBSERVED", "value": build_number},
        "bundled_java": [],
    }

    def inspect(_runtime_id: str, *, system: str | None = None) -> dict:
        assert _runtime_id == runtime_id
        return {"installation": installation}

    executable_record = lambda path: {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    identity = {
        "schema": "windows-current-comsol-install-identity/2",
        "status": "READONLY_INSTALLED_IDENTITY_VERIFIED",
        "target": target,
        "catalog_version": catalog_version,
        "full_product_version": full_version,
        "build_number": build_number,
        "installation_root": str(root.resolve()),
        "desktop_executable": executable_record(desktop),
        "server_executable": executable_record(server),
        "jdk_home": str(jdk.resolve()),
    }
    return {"root": root, "jdk": jdk, "runtime_id": runtime_id,
            "installation": installation, "inspect": inspect, "identity": identity}


def _candidate_for_session_config(target: str, installed: dict, tmp_path: Path) -> dict:
    catalog = binding.TARGET_CATALOG_VERSIONS[target]
    full = binding.TARGET_BUILDS[target]
    build = binding.TARGET_BUILD_NUMBERS[target]
    installation_root = installed["installation_root"]
    runtime_id = installed["runtime_id"]
    target_runtime_root = str(tmp_path / "target-runtime")
    source_profile_proof = {"source_profile": binding._SOURCE_PROFILE}
    java_path = installed["java_executable"]["path"]
    classpath = [row["path"] for row in installed["classpath"]]
    runtime = {
        "runtime_id": runtime_id,
        "comsol_version": catalog,
        "catalog_version": catalog,
        "full_product_version": full,
        "build_number": build,
        "installation_root": installation_root,
        "java_executable": java_path,
        "classpath": classpath,
        "preferences_dir": str(Path(target_runtime_root) / "preferences"),
        "session_state_root": str(Path(target_runtime_root) / "session-state"),
    }
    digest_input = {
        "target": target,
        "runtime_id": runtime_id,
        "comsol_version": catalog,
        "version_identity": {
            "catalog_version": catalog,
            "full_product_version": full,
            "build_number": build,
        },
        "application_profile_sha256": "a" * 64,
        "target_inputs_sha256": "b" * 64,
        "source_profile_proof": source_profile_proof,
        "installed_identity": installed,
        "root_proof": {},
        "source_manifest": {},
        "matrix_sha256": "c" * 64,
        "target_runtime_root": target_runtime_root,
    }
    digest = binding._sha(binding.canonical_bytes(digest_input))
    return {
        "schema": binding.SCHEMA,
        "status": "SOFTWARE_BINDING_ONLY_PENDING_MAIN_REVIEW_NO_NATIVE_AUTHORITY",
        "target": target,
        "runtime_id": runtime_id,
        "binding_id": "windows-current-profile-binding:" + digest,
        "binding_digest": digest,
        "source_profile_proof": source_profile_proof,
        "application_artifact_profile": {"source_profile": binding._SOURCE_PROFILE},
        "installed_python_application": {"status": "NOT_CHECKED"},
        "application_profile_sha256": "a" * 64,
        "current_target_inputs_sha256": "b" * 64,
        "installed_identity": installed,
        "task_root_proof": {},
        "source_manifest": {},
        "original_windows_matrix": {"sha256": "c" * 64},
        "target_runtime_root": target_runtime_root,
        "runtime": runtime,
    }


@pytest.mark.parametrize("target", sorted(binding.TARGET_BUILDS))
def test_production_resolver_and_session_config_share_catalog_version_domain(
        tmp_path, monkeypatch, target):
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("version-domain controls must remain prebirth"),
    )
    fixture = _installed_fixture(tmp_path, target)
    monkeypatch.setattr(binding, "inspect_installation", fixture["inspect"])
    monkeypatch.setattr(
        binding, "_windows_product_version", lambda _path: binding.TARGET_BUILDS[target],
    )
    installed = binding._validate_installed_identity(target, fixture["identity"])
    assert installed["comsol_version"] == binding.TARGET_CATALOG_VERSIONS[target]
    assert installed["catalog_version"] == binding.TARGET_CATALOG_VERSIONS[target]
    assert installed["full_product_version"] == binding.TARGET_BUILDS[target]
    assert installed["build_number"] == binding.TARGET_BUILD_NUMBERS[target]

    monkeypatch.setattr(_runtime_installation, "inspect_installation", fixture["inspect"])
    monkeypatch.setenv("COMSOL_JAVA_HOME", str(fixture["jdk"]))
    monkeypatch.setenv("JAVA_HOME", str(fixture["jdk"]))
    control_home = tmp_path / "control-home"
    control_home.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()
    daemon = object.__new__(ControlDaemon)
    daemon.home = control_home
    daemon.session_runtime_resolver = None
    resolved = daemon._resolve_session_runtime(
        fixture["runtime_id"], "project-a", "session-a", project_root,
    )

    binding_candidate = _candidate_for_session_config(target, installed, tmp_path)
    configured = binding.make_session_runtime_config(binding_candidate)
    expected_catalog = binding.TARGET_CATALOG_VERSIONS[target]
    assert resolved.comsol_version == expected_catalog
    assert configured.comsol_version == expected_catalog
    assert resolved.comsol_version == configured.comsol_version
    assert resolved.comsol_version != binding.TARGET_BUILDS[target]
    assert resolved.runtime_id == configured.runtime_id == fixture["runtime_id"]


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("catalog_version", "6.4.0.293"),
        ("full_product_version", "6.4.0"),
        ("build_number", True),
    ],
)
def test_installed_identity_rejects_catalog_full_build_domain_mixups(tmp_path, field, bad_value):
    target = "windows-x64/comsol6.4"
    fixture = _installed_fixture(tmp_path, target)
    fixture["identity"][field] = bad_value
    with pytest.raises(RuntimeError, match="exact Windows target/build"):
        binding._validate_installed_identity(target, fixture["identity"])


@pytest.mark.parametrize(
    "section,field,bad_value",
    [
        ("runtime", "comsol_version", "6.4.0.293"),
        ("runtime", "catalog_version", "6.4.0.293"),
        ("runtime", "full_product_version", "6.4.0"),
        ("runtime", "build_number", True),
        ("installed_identity", "comsol_version", "6.4.0.293"),
        ("installed_identity", "catalog_version", "6.4.0.293"),
        ("installed_identity", "full_product_version", "6.4.0"),
        ("installed_identity", "build_number", True),
    ],
)
def test_session_config_refuses_mixed_version_domains_even_with_candidate_digest(
        tmp_path, monkeypatch, section, field, bad_value):
    target = "windows-x64/comsol6.4"
    fixture = _installed_fixture(tmp_path, target)
    monkeypatch.setattr(binding, "inspect_installation", fixture["inspect"])
    monkeypatch.setattr(
        binding, "_windows_product_version", lambda _path: binding.TARGET_BUILDS[target],
    )
    installed = binding._validate_installed_identity(target, fixture["identity"])
    candidate = _candidate_for_session_config(target, installed, tmp_path)
    candidate[section][field] = bad_value
    with pytest.raises(RuntimeError, match="mix the catalog and full product/build domains"):
        binding.make_session_runtime_config(candidate)
