"""Software controls for the ordinary release CLI; fixtures are not native evidence."""
import json
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import universal_release as tool
from tests.test_full_project_release import make_build_evidence, make_derived_crypto_inputs, make_wheel


ROOT = Path(__file__).resolve().parents[1]


def project():
    return {"name": "comsol-mcp", "version": "1.0", "requires-python": ">=3.10",
            "dependencies": ["dep>=1"], "optional-dependencies": {"desktop": []}}


def wheel_and_lock(tmp_path, *, tag="py3-none-any", requirement="dep>=1", bad_hash=False):
    house = tmp_path / "inputs"
    wheel = house / f"dep-1.0-{tag}.whl"
    digest = make_wheel(wheel, name="dep", tag=tag)
    lock = tmp_path / "requirements.lock"
    lock.write_text("dep==1.0 --hash=sha256:" + ("0" * 64 if bad_hash else digest) + "\n")
    p = project()
    p["dependencies"] = [requirement]
    return p, house, lock


def test_current_metadata_has_actual_platform_desktop_dependencies_and_valid_generated_lock():
    p = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    tool.validate_lock(p, lock)
    win = tool.active_requirements(p["optional-dependencies"]["desktop"], tool.environment("win_amd64"))
    arm = tool.active_requirements(p["optional-dependencies"]["desktop"], tool.environment("macos_arm64"))
    assert {r[0] for r in win} == {"pywin32"}
    assert next(iter(win))[1] == "==312"
    assert {"pyobjc-framework-coremedia", "pyobjc-framework-screencapturekit"} <= {r[0] for r in arm}


@pytest.mark.parametrize("name", ["pywin32", "pyobjc-framework-coremedia", "pyobjc-framework-screencapturekit"])
@pytest.mark.parametrize("change", ["omission", "wrong-version"])
def test_lock_project_metadata_omission_or_mismatch_is_rejected(name, change):
    p = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    root = next(r for r in lock["package"] if r["name"] == "comsol-mcp")
    rows = root["metadata"]["requires-dist"]
    if change == "omission":
        root["metadata"]["requires-dist"] = [r for r in rows if r["name"] != name]
    else:
        next(r for r in rows if r["name"] == name)["specifier"] = "==0.1"
    with pytest.raises(tool.release.ReleaseError, match="metadata"):
        tool.validate_lock(p, lock)


@pytest.mark.parametrize("change", ["content", "membership"])
def test_dynamic_source_snapshot_detects_drift(tmp_path, change):
    source = tmp_path / "source"
    (source / "comsol_mcp").mkdir(parents=True)
    (source / "comsol_mcp/main.py").write_text("VALUE = 1\n")
    for name in tool.release.SOURCE_ROOT_FILES:
        (source / name).write_text("fixture\n")
    stage = tmp_path / "snapshot"
    snapshot = tool.release.source_snapshot(source, stage)
    tool.verify_snapshot(stage, snapshot)
    (stage / "comsol_mcp" / ("main.py" if change == "content" else "new.py")).write_text("changed\n")
    with pytest.raises(tool.release.ReleaseError, match="content drift"):
        tool.verify_snapshot(stage, snapshot)


@pytest.mark.parametrize("case", ["tag", "hash", "graph"])
def test_target_wheel_hash_and_graph_mismatch_are_rejected(tmp_path, case):
    p, house, lock = wheel_and_lock(tmp_path, tag="cp312-cp312-win_amd64" if case == "tag" else "py3-none-any",
                                  requirement="dep>=2" if case == "graph" else "dep>=1", bad_hash=case == "hash")
    with pytest.raises(tool.release.ReleaseError, match={"tag": "tag mismatch", "hash": "hash mismatch", "graph": "graph version mismatch"}[case]):
        tool.audit_inputs(p, lock, house, "macos_arm64")


def test_unreachable_wheel_cannot_be_smuggled_into_closure(tmp_path):
    p, house, lock = wheel_and_lock(tmp_path)
    p["dependencies"] = []
    with pytest.raises(tool.release.ReleaseError, match="unreachable"):
        tool.audit_inputs(p, lock, house, "macos_arm64")


def test_missing_input_retains_incomplete_target_and_never_packages(tmp_path):
    p, house, lock = wheel_and_lock(tmp_path)
    lock.write_text(lock.read_text() + "child==1.0 --hash=sha256:" + "a" * 64 + "\n")
    row = tool.build_target(p, "macos_arm64", house, lock, lock, tmp_path / "unused.whl",
                            tmp_path / "target", tmp_path / "build", tmp_path / "ops")
    assert row["status"] == "MISSING_INPUT" and row["missing"] == ["child"]
    assert row["target_install"] == "NOT_RUN" and not (tmp_path / "target/offline.zip").exists()


def test_missing_parent_retains_its_present_transitive_inputs_without_certifying_graph(tmp_path):
    p, house, lock = wheel_and_lock(tmp_path)
    p["dependencies"] = ["parent>=1"]
    lock.write_text(lock.read_text() + "parent==1.0 --hash=sha256:" + "a" * 64 + "\n")
    row = tool.build_target(p, "macos_arm64", house, lock, lock, tmp_path / "unused.whl",
                            tmp_path / "target", tmp_path / "build", tmp_path / "ops")
    assert row["status"] == "MISSING_INPUT" and row["missing"] == ["parent"]
    assert row["graph"]["unresolved_reachability"] == ["dep"]
    assert not (tmp_path / "target/offline.zip").exists()


def test_normal_exact_lock_and_existing_manifest_package_apis(tmp_path):
    p, house, exported = wheel_and_lock(tmp_path)
    app = tmp_path / "comsol_mcp-1.0-py3-none-any.whl"
    make_wheel(app, name="comsol-mcp")
    evidence = tmp_path / "build"
    make_build_evidence(evidence, app, version="1.0")
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n")
    ops = tmp_path / "operations.md"
    ops.write_text("Synthetic software packaging test.\n")
    out = tmp_path / "target"
    result = tool.build_target(p, "macos_arm64", house, exported, lock, app, out, evidence, ops)
    pins = tool.release.parse_hash_requirements(out / "requirements.lock", "macos_arm64")
    assert set(pins) == {"comsol-mcp", "dep"}
    assert pins["comsol-mcp"]["hashes"] == {tool.release.sha256_file(app)}
    assert result["native_engine"] == "NOT_RUN" and result["target_install"] == "NOT_RUN"
    with zipfile.ZipFile(out / "offline.zip") as archive:
        manifest = json.loads(archive.read("OFFLINE_BUNDLE_MANIFEST.json"))
        assert manifest["native_execution_verified"] is False
        assert {"SBOM.cdx.json", "LICENSES.json", "locks/requirements.lock"} <= set(archive.namelist())


def test_existing_output_is_never_reused_or_overwritten(tmp_path):
    out = tmp_path / "occupied"
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("do not overwrite")
    args = SimpleNamespace(source=ROOT, lock=ROOT / "uv.lock", output=out)
    with pytest.raises(tool.release.ReleaseError, match="no overwrite"):
        tool.run(args)
    assert sentinel.read_text() == "do not overwrite" and list(out.iterdir()) == [sentinel]


def test_source_wheel_package_member_drift_is_rejected(tmp_path):
    p = project()
    p["dependencies"] = []
    p["optional-dependencies"] = {}
    wheel = tmp_path / "comsol_mcp-1.0-py3-none-any.whl"
    make_wheel(wheel, name="comsol-mcp")
    snapshot = {"files": [{"path": "comsol_mcp/__init__.py", "sha256": "0" * 64}]}
    with pytest.raises(tool.release.ReleaseError, match="source/wheel content mismatch"):
        tool.validate_wheel(p, wheel, snapshot)


def test_403_derived_target_records_actual_native_version_and_legacy_license_alias(tmp_path):
    _house, wheel, exported, lock, provenance, _sha = make_derived_crypto_inputs(tmp_path)
    payload = json.loads(provenance.read_text())
    payload["native_component"].update(version="4.0.3",
        source_url="https://github.com/openssl/openssl/releases/download/openssl-4.0.3/openssl-4.0.3.tar.gz",
        source_sha256="325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9")
    provenance.write_text(json.dumps(payload))
    app = tmp_path / "comsol_mcp-1.0-py3-none-any.whl"
    make_wheel(app, name="comsol-mcp")
    evidence = tmp_path / "build"
    make_build_evidence(evidence, app, version="1.0")
    ops = tmp_path / "operations.md"
    ops.write_text("Synthetic software packaging test.\n")
    p = project()
    p["dependencies"] = ["cryptography>=3.4"]
    house = tmp_path / "available"
    house.mkdir()
    out = tmp_path / "target"
    row = tool.build_target(p, "macos_x86_64", house, exported, lock, app, out, evidence, ops,
                            wheel, provenance, tmp_path / "OpenSSL-4.0.2-LICENSE.txt")
    assert row["derived_input"]["native_component"]["version"] == "4.0.3"
    assert row["native_engine"] == "NOT_RUN"
    with zipfile.ZipFile(out / "offline.zip") as z:
        sbom = json.loads(z.read("SBOM.cdx.json"))
        assert next(c for c in sbom["components"] if c["name"] == "OpenSSL")["version"] == "4.0.3"
        assert z.read("licenses/openssl-4.0.2-LICENSE.txt") == (tmp_path / "OpenSSL-4.0.2-LICENSE.txt").read_bytes()
