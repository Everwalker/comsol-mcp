from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct

import pytest

from tools import windows_current_installed_profile_runtime_binding_r3 as binding


def _record(path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes((path.name + "\n").encode("utf-8"))
    raw = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _profile(root: Path) -> dict:
    producer = root / "app-profile"
    rows = {
        "wheel": _record(producer / "appwheel" / "comsol_mcp-0.1.10-py3-none-any.whl"),
        "result": _record(producer / "APPLICATION_BUNDLE_RESULT.json"),
        "candidate": _record(producer / "ACTOR_CANDIDATE_app6c.json"),
        "once": _record(producer / "UNIT_ONCE_GUARD.json"),
        "review": _record(producer / "MAIN_SOURCE_REVIEW.json"),
    }
    rows["backup_source_sha256"] = "a" * 64
    return rows


def _target_inputs() -> dict:
    return {
        "schema": "W26_CURRENT_THREE_TARGET_INPUTS_V1",
        "status": "INPUTS_VERIFIED_NOT_INSTALLED_OR_RELEASED",
        "targets": {"win_amd64": {"rows": [{"name": f"dep-{index}"} for index in range(34)]}},
    }


def _installed_identity(root: Path, target: str) -> dict:
    version = binding.TARGET_BUILDS[target]
    install = root / ("comsol-" + version)
    major = ".".join(version.split(".")[:2])
    readme = install / "readme.txt"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(f"COMSOL {version} README\nfor COMSOL {major}.\n", encoding="utf-8")
    desktop_path = install / "bin" / "win64" / "comsol.exe"
    server_path = install / "bin" / "win64" / "comsolmphserver.exe"
    for executable in (desktop_path, server_path):
        executable.parent.mkdir(parents=True, exist_ok=True)
        image = bytearray(128)
        image[:2] = b"MZ"
        image[0x3C:0x40] = struct.pack("<I", 64)
        image[64:70] = b"PE\0\0" + struct.pack("<H", 0x8664)
        executable.write_bytes(image)
    manifest = install / "bin" / "comsolclientpath.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("apiplugins/client.jar\n", encoding="utf-8")
    _record(install / "apiplugins" / "client.jar")
    jdk = root / "jdk"
    for name in ("java.exe", "javac.exe"):
        path = jdk / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + "\n").encode("ascii"))
    release = jdk / "release"
    release.write_text('JAVA_VERSION="11.0.24"\nOS_ARCH="amd64"\n', encoding="ascii")
    return {
        "schema": "windows-current-comsol-install-identity/1",
        "status": "READONLY_INSTALLED_IDENTITY_VERIFIED",
        "target": target,
        "comsol_version": version,
        "installation_root": str(install.resolve()),
        "desktop_executable": _record(desktop_path),
        "server_executable": _record(server_path),
        "jdk_home": str(jdk.resolve()),
    }


@pytest.fixture(autouse=True)
def _software_product_version_resource(monkeypatch):
    monkeypatch.setattr(
        binding, "_windows_product_version",
        lambda executable: Path(executable).parents[2].name.removeprefix("comsol-"),
    )


def _matrix(path: Path) -> Path:
    repository_root = Path(__file__).resolve().parents[1]
    workspace_root = repository_root.parent
    original = workspace_root / "_state" / "windows-restored-host-current-20261014" / binding.ORIGINAL_MATRIX_NAME
    path.write_bytes(original.read_bytes())
    return path


def _root_proof(root: Path) -> dict:
    st = root.stat()
    return {
        "schema": "windows-same-handle-task-root-proof/1",
        "provider": "test-fixture-only-not-native-acceptance",
        "path": str(root.resolve()),
        "identity": {"device": int(st.st_dev), "inode": int(st.st_ino)},
        "security_descriptor_sha256": "b" * 64,
    }


def _inputs(tmp_path: Path, target: str) -> dict:
    repo = Path(__file__).resolve().parents[1]
    task = tmp_path / "fresh-task-root"
    task.mkdir()
    profile = _profile(tmp_path)
    descriptor = {
        "schema": "w26-current-installed-application/1",
        "source_profile": "current175-r6",
        "root": str((tmp_path / "app-profile").resolve()),
        "loader_source_closure": {"test-fixture-reader-seam": "NOT_NATIVE"},
    }
    target_record_path = tmp_path / "TARGET_INPUTS.json"
    target_record_path.write_text(json.dumps(_target_inputs(), sort_keys=True), encoding="utf-8")
    target_project_path = tmp_path / "pyproject.toml"
    target_project_path.write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return {
        "repository_root": repo,
        "task_root": task,
        "task_root_proof": _root_proof(task),
        "target": target,
        "application_profile_descriptor": descriptor,
        "target_inputs_record": {"path": str(target_record_path.resolve())},
        "target_project_path": target_project_path,
        "source_profile_proof": binding._build_w26_source_profile_proof(descriptor, profile),
        "current_application_profile": profile,
        "current_target_inputs": _target_inputs(),
        "installed_identity": _installed_identity(tmp_path / "installed", target),
        "matrix_path": _matrix(tmp_path / "ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"),
    }


def _binding_inputs(inputs: dict) -> dict:
    excluded = {
        "application_profile_descriptor",
        "target_inputs_record",
        "target_project_path",
    }
    return {key: value for key, value in inputs.items() if key not in excluded}


def _verification_inputs(inputs: dict) -> dict:
    excluded = {"source_profile_proof", "current_application_profile", "current_target_inputs"}
    return {key: value for key, value in inputs.items() if key not in excluded}


def _verify_with_fixture_w26_readers(binding_result: dict, inputs: dict, monkeypatch) -> dict:
    from tools import w26_sealed_data_r6, w26_target_inputs

    monkeypatch.setattr(
        w26_sealed_data_r6, "current_application_profile",
        lambda _descriptor: inputs["current_application_profile"],
    )
    monkeypatch.setattr(
        w26_target_inputs, "verify_targets",
        lambda _record, _project: inputs["current_target_inputs"],
    )
    return binding.verify_runtime_binding(binding_result, **_verification_inputs(inputs))


@pytest.mark.parametrize("target", sorted(binding.TARGET_BUILDS))
def test_current_profile_and_real_production_runtime_type_bind_independently_per_version(tmp_path, target, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("binding is prebirth only"))
    inputs = _inputs(tmp_path, target)
    result = binding._build_runtime_binding_from_verified_outputs(**_binding_inputs(inputs))
    runtime = binding.make_session_runtime_config(result)

    assert runtime.comsol_version == binding.TARGET_BUILDS[target]
    assert runtime.installation_root == Path(inputs["installed_identity"]["installation_root"])
    assert runtime.java_executable == Path(inputs["installed_identity"]["jdk_home"]) / "bin" / "java.exe"
    assert runtime.classpath == (
        Path(inputs["installed_identity"]["installation_root"]) / "apiplugins" / "client.jar",
    )
    assert result["gates"] == {
        "T037_task_specific_isolation": "UNVERIFIED",
        "JDK_target_compatibility": "UNVERIFIED",
        "W26_application_artifact_profile": "PASS_SOURCE_PROFILE_ONLY",
        "Windows_installed_python_venv": "NOT_CHECKED",
        "Windows_installed_python_RECORD": "NOT_CHECKED",
        "Main_native_once": "NOT_CREATED",
        "original_Popen_or_Windows_handle": "NOT_BORN",
        "COMSOL_or_JVM": "NOT_RUN",
        "replay": False,
    }
    assert result["original_windows_matrix"]["required_rows"] == 60
    assert result["original_windows_matrix"]["target_status"] == "NOT_RUN"
    assert _verify_with_fixture_w26_readers(result, inputs, monkeypatch) == result
    from comsol_mcp._runtime_installation import runtime_id_for_root
    assert result["runtime_id"] == runtime_id_for_root(inputs["installed_identity"]["installation_root"])
    assert runtime.runtime_id == result["runtime_id"]


@pytest.mark.parametrize("target", sorted(binding.TARGET_BUILDS))
def test_unmocked_production_runtime_id_inspection_binds_only_the_exact_install_root(tmp_path, target):
    from comsol_mcp import _runtime_installation as production_runtime

    inputs = _inputs(tmp_path, target)
    installation_root = Path(inputs["installed_identity"]["installation_root"])
    runtime_id = production_runtime.runtime_id_for_root(installation_root)
    inspected = production_runtime.inspect_installation(runtime_id, system="Windows")["installation"]

    assert inspected["runtime_id"] == runtime_id
    assert inspected["root"] == str(installation_root.resolve())
    assert inspected["metadata_only"] is True
    assert inspected["comsol_started"] is False
    assert inspected["license_checked_out"] is False
    expected_major, expected_build = binding.TARGET_BUILDS[target].rsplit(".", 1)
    assert inspected["version"]["value"] == expected_major
    assert inspected["build"]["value"] == int(expected_build)
    with pytest.raises(production_runtime.RuntimeInstallationError):
        production_runtime.inspect_installation("windows-current-" + target.rsplit("/", 1)[1], system="Windows")

    other_root = tmp_path / "other-install"
    other_root.mkdir()
    other_id = production_runtime.runtime_id_for_root(other_root)
    with pytest.raises(production_runtime.RuntimeInstallationError):
        production_runtime.inspect_installation(other_id, system="Windows")


def test_production_runtime_id_rejects_wrong_root_and_forged_binding_domain(tmp_path):
    inputs = _inputs(tmp_path, "windows-x64/comsol6.4")
    other = _installed_identity(tmp_path / "other-installed", "windows-x64/comsol6.4")
    inputs["installed_identity"]["installation_root"] = other["installation_root"]
    with pytest.raises(RuntimeError, match="path differs"):
        binding._build_runtime_binding_from_verified_outputs(**_binding_inputs(inputs))

    fresh_inputs = tmp_path / "fresh-inputs"
    fresh_inputs.mkdir()
    inputs = _inputs(fresh_inputs, "windows-x64/comsol6.4")
    result = binding._build_runtime_binding_from_verified_outputs(**_binding_inputs(inputs))
    forged = dict(result)
    forged["runtime_id"] = "windows-current-comsol6.4-" + "a" * 20
    with pytest.raises(RuntimeError, match="production installation-root identity"):
        binding.make_session_runtime_config(forged)


@pytest.mark.parametrize("fault", [
    "wrong-target-build", "missing-classpath", "profile-once-drift",
    "current-input-target-drift", "matrix-row-promoted", "root-security-missing",
    "wrong-desktop-version", "wrong_server_version", "readme-drift", "missing-javac",
    "jdk-architecture-unknown", "caller-provenance-extra", "root-provider-drift",
    "source-profile-label-drift", "source-profile-record-drift",
])
def test_binding_refuses_target_source_producer_matrix_or_root_drift(tmp_path, fault, monkeypatch):
    inputs = _inputs(tmp_path, "windows-x64/comsol6.4")
    if fault == "wrong-target-build":
        inputs["installed_identity"]["comsol_version"] = "6.3.0.290"
    elif fault == "missing-classpath":
        (Path(inputs["installed_identity"]["installation_root"]) / "bin" / "comsolclientpath.txt").write_text("", encoding="utf-8")
    elif fault == "profile-once-drift":
        inputs["current_application_profile"]["once"]["sha256"] = "0" * 64
    elif fault == "current-input-target-drift":
        inputs["current_target_inputs"]["targets"] = {}
    elif fault == "matrix-row-promoted":
        value = json.loads(inputs["matrix_path"].read_text())
        value["tests"][0]["targets"]["windows-x64/comsol6.4"]["status"] = "PASS"
        inputs["matrix_path"].write_text(json.dumps(value), encoding="utf-8")
    elif fault == "root-security-missing":
        inputs["task_root_proof"].pop("security_descriptor_sha256")
    elif fault == "root-provider-drift":
        inputs["task_root_proof"]["provider"] = "caller-supplied"
    elif fault == "source-profile-label-drift":
        inputs["source_profile_proof"]["source_profile"] = "current175-r7"
    elif fault == "source-profile-record-drift":
        inputs["source_profile_proof"]["producer_records"]["result"]["sha256"] = "0" * 64
    elif fault == "caller-provenance-extra":
        inputs["installed_identity"]["identity_provenance"] = {"sha256": "f" * 64}
    elif fault == "wrong-desktop-version":
        monkeypatch.setattr(binding, "_windows_product_version", lambda path: (
            "6.3.0.290" if Path(path).name == "comsol.exe" else binding.TARGET_BUILDS["windows-x64/comsol6.4"]
        ))
    elif fault == "wrong_server_version":
        monkeypatch.setattr(binding, "_windows_product_version", lambda path: (
            "6.3.0.290" if Path(path).name == "comsolmphserver.exe" else binding.TARGET_BUILDS["windows-x64/comsol6.4"]
        ))
    elif fault == "readme-drift":
        (Path(inputs["installed_identity"]["installation_root"]) / "readme.txt").write_text(
            "COMSOL 6.3.0.290 README\nfor COMSOL 6.3.\n", encoding="utf-8")
    elif fault == "missing-javac":
        (Path(inputs["installed_identity"]["jdk_home"]) / "bin" / "javac.exe").unlink()
    elif fault == "jdk-architecture-unknown":
        release = Path(inputs["installed_identity"]["jdk_home"]) / "release"
        release.write_text('JAVA_VERSION="11.0.24"\nOS_ARCH="arm64"\n', encoding="ascii")
    with pytest.raises(RuntimeError):
        binding._build_runtime_binding_from_verified_outputs(**_binding_inputs(inputs))


def test_source_producer_and_task_root_changes_refuse_reverification(tmp_path, monkeypatch):
    inputs = _inputs(tmp_path, "windows-x64/comsol6.3")
    source_copy = tmp_path / "source-copy"
    original_root = inputs["repository_root"]
    for relative in binding.SOURCE_MEMBERS:
        destination = source_copy / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((original_root / relative).read_bytes())
    inputs["repository_root"] = source_copy
    result = binding._build_runtime_binding_from_verified_outputs(**_binding_inputs(inputs))
    producer = source_copy / "tools/w26_target_inputs.py"
    producer.write_bytes(producer.read_bytes() + b"\n# controlled test mutation\n")
    with pytest.raises(RuntimeError, match="source|binding"):
        _verify_with_fixture_w26_readers(result, inputs, monkeypatch)


def test_worker_and_core_server_resolve_the_same_owned_preferences_without_birth(tmp_path):
    from comsol_mcp._session_context import CanonicalSocket, OwnedServerProcessIdentity

    inputs = _inputs(tmp_path, "windows-x64/comsol6.4")
    result = binding._build_runtime_binding_from_verified_outputs(**_binding_inputs(inputs))
    runtime = binding.make_session_runtime_config(result)
    project_id, session_id = "project-a", "session-a"
    digest = hashlib.sha256(f"{project_id}\0{session_id}".encode()).hexdigest()
    session = runtime.session_state_root / "sessions" / digest
    owned = session / "owned-server"
    preferences = owned / "preferences"
    private_binary = owned / "installation" / "bin" / "win64" / "comsolmphserver.exe"
    preferences.mkdir(parents=True)
    private_binary.parent.mkdir(parents=True)
    original = Path(runtime.installation_root) / "bin" / "win64" / "comsolmphserver.exe"
    private_binary.symlink_to(original)
    identity = OwnedServerProcessIdentity(
        pid=12345, birth="software-control-only",
        executable=str(original.resolve()),
        listener_sockets=(CanonicalSocket("127.0.0.1", 2036),),
        start_epoch_ms=123456,
    )
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    assert binding.assert_shared_owned_preferences(runtime, project_id, session_id, identity) == preferences.resolve()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert result["gates"]["original_Popen_or_Windows_handle"] == "NOT_BORN"


def test_current_w26_entrypoint_runs_the_existing_semantic_readers(tmp_path, monkeypatch):
    from tools import w26_sealed_data_r6, w26_target_inputs

    inputs = _inputs(tmp_path, "windows-x64/comsol6.3")
    profile = inputs["current_application_profile"]
    targets = inputs["current_target_inputs"]
    calls = []
    monkeypatch.setattr(
        w26_sealed_data_r6, "current_application_profile",
        lambda descriptor: calls.append(("profile", descriptor)) or profile,
    )
    monkeypatch.setattr(
        w26_target_inputs, "verify_targets",
        lambda record, project: calls.append(("targets", record, project)) or targets,
    )
    bound = binding.build_from_current_w26(
        repository_root=inputs["repository_root"],
        task_root=inputs["task_root"],
        task_root_proof=inputs["task_root_proof"],
        target=inputs["target"],
        application_profile_descriptor=inputs["application_profile_descriptor"],
        target_inputs_record=inputs["target_inputs_record"],
        target_project_path=inputs["target_project_path"],
        installed_identity=inputs["installed_identity"],
        matrix_path=inputs["matrix_path"],
    )
    assert bound["application_profile"] == profile
    assert bound["source_profile_proof"]["source_profile"] == "current175-r6"
    assert bound["application_artifact_profile"]["status"] == "PASS_W26_R6_BUILD_ARTIFACT_PROFILE_ONLY"
    assert bound["installed_python_application"]["status"] == "NOT_CHECKED"
    assert bound["installed_python_application"]["installed_distribution_RECORD"] == "NOT_CHECKED"
    assert len(calls) == 2 and calls[0][0] == "profile" and calls[1][0] == "targets"

