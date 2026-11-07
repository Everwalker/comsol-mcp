from __future__ import annotations

import plistlib
import struct
from pathlib import Path

import pytest

from comsol_mcp import _runtime_installation as runtime


def _write_macho(path: Path, cpu_type: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xcf\xfa\xed\xfe" + struct.pack("<I", cpu_type) + b"\0" * 56)
    path.chmod(0o755)


def _write_pe(path: Path, machine: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = bytearray(128)
    image[:2] = b"MZ"
    image[0x3C:0x40] = struct.pack("<I", 64)
    image[64:70] = b"PE\0\0" + struct.pack("<H", machine)
    path.write_bytes(image)


def _make_macos_install(root: Path, *, version: str = "6.4.0.293", cpu_type: int = 0x0100000C) -> Path:
    root.mkdir(parents=True)
    (root / "readme.txt").write_text(f"COMSOL {version} README\n", encoding="utf-8")
    _write_macho(root / "bin" / "macarm64" / "comsollauncher", cpu_type)
    app = root / "COMSOL Multiphysics.app" / "Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleShortVersionString": "6.4.0",
        "LSArchitectures": ["arm64", "x86_64"],
    }))
    java_home = root / "java" / "macarm64" / "jre" / "Contents" / "Home"
    _write_macho(java_home / "bin" / "java", cpu_type)
    (java_home / "release").write_text('JAVA_VERSION="21.0.7"\nOS_ARCH="aarch64"\n', encoding="utf-8")
    return root


def _make_windows_install(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "readme.txt").write_text("COMSOL 6.3.0.282 README\n", encoding="utf-8")
    _write_pe(root / "bin" / "win64" / "comsolmphserver.exe", 0x8664)
    java_home = root / "java" / "win64" / "jre"
    _write_pe(java_home / "bin" / "java.exe", 0x8664)
    (java_home / "release").write_text('JAVA_VERSION="17.0.12"\nOS_ARCH="amd64"\n', encoding="utf-8")
    return root


def test_macos_discovery_uses_readme_bundle_and_real_headers(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    result = runtime.discover_installations([str(tmp_path)], system="Darwin")

    assert result["status"] == "OBSERVED"
    assert len(result["installations"]) == 1
    row = result["installations"][0]
    assert row["version"] == {
        "status": "OBSERVED",
        "value": "6.4.0",
        "sources": [
            {"value": "6.4.0", "path": str(install / "readme.txt")},
            {"value": "6.4.0", "path": str(install / "COMSOL Multiphysics.app/Contents/Info.plist")},
        ],
    }
    assert row["build"] == {"status": "OBSERVED", "value": 293, "source": str(install / "readme.txt")}
    assert row["architecture"]["values"] == ["arm64"]
    assert row["bundled_java"][0]["version"] == {"status": "OBSERVED", "value": "21.0.7"}
    assert row["bundled_java"][0]["architecture"]["values"] == ["arm64"]
    assert row["comsol_started"] is False
    assert row["license_checked_out"] is False


def test_version_conflict_is_not_resolved_by_install_folder_name(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL 6.4" / "Multiphysics", version="6.4.1.99")
    bundle = install / "COMSOL Multiphysics.app" / "Contents" / "Info.plist"
    bundle.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "6.4.0"}))

    row = runtime.inspect_installation(runtime.runtime_id_for_root(install), system="Darwin")["installation"]
    assert row["version"]["status"] == "CONFLICT"
    assert row["version"]["value"] is None


def test_windows_architecture_is_read_from_pe_headers(tmp_path: Path) -> None:
    install = _make_windows_install(tmp_path / "COMSOL63" / "Multiphysics")
    result = runtime.discover_installations([str(install)], system="Windows")

    assert result["status"] == "OBSERVED"
    row = result["installations"][0]
    assert row["version"]["value"] == "6.3.0"
    assert row["build"]["value"] == 282
    assert row["architecture"]["values"] == ["x86_64"]
    assert row["bundled_java"][0]["architecture"]["values"] == ["x86_64"]


def test_discovery_keeps_inaccessible_roots_as_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "absent"
    result = runtime.discover_installations([str(missing)], system="Darwin")
    assert result["status"] == "UNKNOWN"
    assert result["installations"] == []
    assert result["errors"][0]["status"] == "UNKNOWN"


@pytest.mark.parametrize("runtime_id", ["", "comsol-install:", "comsol-install:relative", "other:/Applications/COMSOL64"])
def test_runtime_id_requires_canonical_existing_absolute_path(runtime_id: str) -> None:
    with pytest.raises(runtime.RuntimeInstallationError):
        runtime.inspect_installation(runtime_id, system="Darwin")


def test_compatibility_is_unknown_when_required_metadata_is_unknown(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics", version="6.4.1.293")
    (install / "COMSOL Multiphysics.app" / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "6.4.0"})
    )
    result = runtime.compatibility_report(
        [runtime.runtime_id_for_root(install)], {"minimum_version": "6.4.0"}, system="Darwin"
    )
    assert result["status"] == "UNVERIFIED"
    assert result["runtimes"][0]["checks"][0]["status"] == "UNKNOWN"
    assert result["render_tested"] is False
    assert result["license_checked_out"] is False


def test_unknown_compatibility_requirement_is_not_silently_accepted(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    result = runtime.compatibility_report(
        [runtime.runtime_id_for_root(install)], {"gpu_driver": "known"}, system="Darwin"
    )
    assert result["status"] == "UNVERIFIED"
    assert result["runtimes"][0]["checks"] == [{
        "name": "gpu_driver",
        "status": "UNVERIFIED",
        "reason": "requirement field is outside the supported static compatibility contract",
    }]


def test_compatibility_treats_trailing_zero_precision_as_equivalent_and_keeps_build_separate(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    result = runtime.compatibility_report(
        [runtime.runtime_id_for_root(install)],
        {"minimum_version": "6.4", "maximum_version": "6.4.0.0", "minimum_build": 294},
        system="Darwin",
    )
    checks = {row["name"]: row for row in result["runtimes"][0]["checks"]}
    assert checks["minimum_version"]["status"] == "PASS"
    assert checks["maximum_version"]["status"] == "PASS"
    assert checks["minimum_build"]["status"] == "INCOMPATIBLE"
    assert checks["minimum_build"]["actual"] == 293


def test_invalid_bundled_java_version_stays_unknown_instead_of_raising(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    release = install / "java/macarm64/jre/Contents/Home/release"
    release.write_text('JAVA_VERSION="unknown"\nOS_ARCH="aarch64"\n', encoding="utf-8")
    result = runtime.compatibility_report(
        [runtime.runtime_id_for_root(install)], {"minimum_java_version": "21.0"}, system="Darwin"
    )
    assert result["status"] == "UNVERIFIED"
    assert result["runtimes"][0]["checks"][0]["status"] == "UNKNOWN"


def test_g3_static_runtime_routes_are_registered_and_do_not_start_comsol(tmp_path: Path) -> None:
    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._g3_ops import IMPLEMENTED_OPERATIONS
    from comsol_mcp._g2_registry import is_implemented

    daemon = ControlDaemon(tmp_path)
    try:
        for operation in (
            "runtime.discover", "runtime.inspect", "runtime.doctor", "runtime.compatibility_report",
        ):
            assert operation in IMPLEMENTED_OPERATIONS
            assert is_implemented(operation)
        install = _make_macos_install(tmp_path / "fixtures" / "COMSOL64" / "Multiphysics")
        result = daemon.dispatch({
            "operation": "runtime.discover",
            "arguments": {"roots": [str(install)]},
        })
        assert result["success"] is True
        assert result["data"]["installations"][0]["root"] == str(install)
        assert result["data"]["comsol_started"] is False
        assert result["data"]["license_checked_out"] is False
        inspect_result = daemon.dispatch({
            "operation": "runtime.inspect",
            "arguments": {"runtime_id": runtime.runtime_id_for_root(install)},
        })
        assert inspect_result["success"] is True
        assert inspect_result["data"]["installation"]["version"]["value"] == "6.4.0"
    finally:
        daemon.close()


def test_runtime_static_read_rejects_model_revision_envelope(tmp_path: Path) -> None:
    from comsol_mcp._control_daemon import ControlDaemon

    daemon = ControlDaemon(tmp_path)
    try:
        result = daemon.dispatch({
            "operation": "runtime.discover",
            "arguments": {"roots": []},
            "execution": {"expected_revision": 0},
        })
        assert result["success"] is False
        assert result["error"]["code"] == "INVALID_REQUEST"
    finally:
        daemon.close()


def test_doctor_passes_a_fully_configured_static_installation(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    jdk = tmp_path / "jdk-17"
    _write_macho(jdk / "bin" / "java", 0x0100000C)
    _write_macho(jdk / "bin" / "javac", 0x0100000C)
    (jdk / "release").write_text('JAVA_VERSION="17.0.12"\nOS_ARCH="aarch64"\n', encoding="utf-8")
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["installation", "version", "build", "launcher", "architecture", "java", "directories", "configuration"],
        system="Darwin",
        environ={"COMSOL_ROOT": str(install), "COMSOL_JAVA_HOME": str(jdk)},
    )
    assert result["status"] == "PASS_STATIC"
    config = [row for row in result["installations"][0]["checks"] if row["name"] == "configuration"][0]
    assert config["status"] == "PASS"
    assert config["comsol_root_resolves"] is True
    assert config["configured_java"] == {"java_executable": True, "javac_executable": True, "version": "17.0.12"}
    assert config["values_redacted"] is True


def test_doctor_names_the_broken_configuration_with_an_actionable_reason(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    jre = tmp_path / "jre-only"
    _write_macho(jre / "bin" / "java", 0x0100000C)
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["configuration"],
        system="Darwin",
        environ={"COMSOL_ROOT": str(tmp_path / "no-such-comsol"), "COMSOL_JAVA_HOME": str(jre)},
    )
    assert result["status"] == "UNKNOWN"
    row = result["installations"][0]["checks"][0]
    assert row["status"] == "INVALID"
    assert row["comsol_root_resolves"] is False
    assert row["configured_java"]["java_executable"] is True
    assert row["configured_java"]["javac_executable"] is False
    joined = " ".join(row["reasons"])
    assert "COMSOL_ROOT" in joined
    assert "COMSOL_JAVA_HOME" in joined
    assert "javac" in joined


def test_doctor_flags_configured_root_that_is_not_a_comsol_installation(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    jdk = tmp_path / "jdk-17"
    _write_macho(jdk / "bin" / "java", 0x0100000C)
    _write_macho(jdk / "bin" / "javac", 0x0100000C)
    (jdk / "release").write_text('JAVA_VERSION="17.0.12"\nOS_ARCH="aarch64"\n', encoding="utf-8")
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["installation", "configuration"],
        system="Darwin",
        environ={"COMSOL_ROOT": str(scratch), "COMSOL_JAVA_HOME": str(jdk)},
    )
    row = [row for row in result["installations"][0]["checks"] if row["name"] == "configuration"][0]
    assert row["status"] == "INVALID"
    assert row["comsol_root_resolves"] is True
    assert row["comsol_root_is_installation"] is False
    assert "COMSOL_ROOT" in " ".join(row["reasons"])
    assert result["status"] == "UNKNOWN"


def test_doctor_treats_non_executable_jdk_binaries_as_unusable_on_macos(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    jdk = tmp_path / "jdk-nonexecutable"
    _write_macho(jdk / "bin" / "java", 0x0100000C)
    _write_macho(jdk / "bin" / "javac", 0x0100000C)
    (jdk / "bin" / "java").chmod(0o644)
    (jdk / "bin" / "javac").chmod(0o644)
    (jdk / "release").write_text('JAVA_VERSION="17.0.12"\nOS_ARCH="aarch64"\n', encoding="utf-8")
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["configuration"],
        system="Darwin",
        environ={"COMSOL_JAVA_HOME": str(jdk)},
    )
    row = result["installations"][0]["checks"][0]
    assert row["status"] == "INVALID"
    assert row["configured_java"]["java_executable"] is False
    assert row["configured_java"]["javac_executable"] is False
    joined = " ".join(row["reasons"])
    assert "COMSOL_JAVA_HOME" in joined
    assert "java" in joined


def test_doctor_windows_configuration_observes_executable_suffixes(tmp_path: Path) -> None:
    install = _make_windows_install(tmp_path / "COMSOL63" / "Multiphysics")
    jre = tmp_path / "jre-only"
    _write_pe(jre / "bin" / "java.exe", 0x8664)
    (jre / "bin" / "java.exe").chmod(0o644)
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["configuration"],
        system="Windows",
        environ={"COMSOL_ROOT": str(install), "JAVA_HOME": str(jre)},
    )
    row = result["installations"][0]["checks"][0]
    assert row["status"] == "INVALID"
    assert row["configured_java"]["java_executable"] is True
    assert "JAVA_HOME" in row["reasons"][0]
    assert "javac.exe" in row["reasons"][0]


def test_doctor_keeps_discovery_diagnostics_when_nothing_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "absent-root"
    monkeypatch.setattr(runtime, "_default_roots", lambda target, environ: [missing])
    result = runtime.doctor_installation(None, ["installation"], system="Darwin", environ={})
    assert result["status"] == "UNKNOWN"
    assert result["roots_examined"] == []
    assert result["errors"] == [{"path": str(missing), "status": "UNKNOWN", "reason": "installation root is missing or inaccessible"}]
    assert "roots_examined" in result["checks"][0]["reason"]


def test_doctor_keeps_configuration_verdict_when_nothing_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(runtime, "_default_roots", lambda target, environ: [tmp_path / "empty-scan"])
    result = runtime.doctor_installation(
        None,
        ["installation", "configuration"],
        system="Darwin",
        environ={"COMSOL_ROOT": str(scratch)},
    )
    assert result["status"] == "UNKNOWN"
    rows = {row["name"]: row for row in result["checks"]}
    assert rows["installation"]["status"] == "UNKNOWN"
    assert "roots_examined" in rows["installation"]["reason"]
    config = rows["configuration"]
    assert config["status"] == "INVALID"
    assert config["comsol_root_resolves"] is True
    assert config["comsol_root_is_installation"] is False
    assert "COMSOL_ROOT" in " ".join(config["reasons"])


def test_doctor_marks_root_only_configuration_invalid_for_the_backend(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["installation", "configuration"],
        system="Darwin",
        environ={"COMSOL_ROOT": str(install)},
    )
    row = [row for row in result["installations"][0]["checks"] if row["name"] == "configuration"][0]
    assert row["status"] == "INVALID"
    assert row["comsol_root_set"] is True
    assert row["java_home_set"] is False
    assert "COMSOL_JAVA_HOME" in " ".join(row["reasons"])
    assert result["status"] == "UNKNOWN"


def test_doctor_marks_java_only_configuration_invalid_for_the_backend(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    jdk = tmp_path / "jdk-11"
    _write_macho(jdk / "bin" / "java", 0x0100000C)
    _write_macho(jdk / "bin" / "javac", 0x0100000C)
    (jdk / "release").write_text('JAVA_VERSION="11.0.28"\nOS_ARCH="aarch64"\n', encoding="utf-8")
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["installation", "configuration"],
        system="Darwin",
        environ={"COMSOL_JAVA_HOME": str(jdk)},
    )
    row = [row for row in result["installations"][0]["checks"] if row["name"] == "configuration"][0]
    assert row["status"] == "INVALID"
    assert row["comsol_root_set"] is False
    assert row["configured_java"] == {"java_executable": True, "javac_executable": True, "version": "11.0.28"}
    assert "COMSOL_ROOT" in " ".join(row["reasons"])
    assert result["status"] == "UNKNOWN"


def test_doctor_keeps_not_configured_when_neither_variable_is_set(tmp_path: Path) -> None:
    install = _make_macos_install(tmp_path / "COMSOL64" / "Multiphysics")
    result = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["installation", "configuration"],
        system="Darwin",
        environ={},
    )
    row = [row for row in result["installations"][0]["checks"] if row["name"] == "configuration"][0]
    assert row["status"] == "NOT_CONFIGURED"
    assert row["comsol_root_set"] is False
    assert row["java_home_set"] is False
    assert result["status"] == "UNKNOWN"
    install_only = runtime.doctor_installation(
        runtime.runtime_id_for_root(install),
        ["installation", "directories"],
        system="Darwin",
        environ={},
    )
    assert install_only["status"] == "PASS_STATIC"
