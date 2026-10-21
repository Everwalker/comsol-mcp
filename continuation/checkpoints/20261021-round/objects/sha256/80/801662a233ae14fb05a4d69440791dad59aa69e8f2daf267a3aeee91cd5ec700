from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import windows_current_installed_profile_runtime_binding_r1 as binding


def _record(path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes((path.name + "\n").encode("utf-8"))
    raw = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _profile(root: Path) -> dict:
    rows = {name: _record(root / "app-profile" / filename) for name, filename in (
        ("wheel", "comsol_mcp-0.1.10-py3-none-any.whl"),
        ("result", "APPLICATION_BUNDLE_RESULT.json"),
        ("candidate", "ACTOR_CANDIDATE_app6c.json"),
        ("once", "UNIT_ONCE_GUARD.json"),
        ("review", "MAIN_SOURCE_REVIEW.json"),
    )}
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
        executable.write_text(version, encoding="ascii")
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
        lambda executable: Path(executable).read_text(encoding="ascii"),
    )


def _matrix(path: Path) -> Path:
    rows = []
    for index in range(60):
        rows.append({
            "id": f"T{index + 1:03}",
            "required": True,
            "targets": {
                "windows-x64/comsol6.3": {"status": "NOT_RUN", "evidence": []},
                "windows-x64/comsol6.4": {"status": "NOT_RUN", "evidence": []},
            },
        })
    path.write_text(json.dumps({"count": 60, "tests": rows}, sort_keys=True), encoding="utf-8")
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
    return {
        "repository_root": repo,
        "task_root": task,
        "task_root_proof": _root_proof(task),
        "target": target,
        "source_profile": "current175-r6",
        "current_application_profile": _profile(tmp_path),
        "current_target_inputs": _target_inputs(),
        "installed_identity": _installed_identity(tmp_path / "installed", target),
        "matrix_path": _matrix(tmp_path / "ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"),
    }


@pytest.mark.parametrize("target", sorted(binding.TARGET_BUILDS))
def test_current_profile_and_real_production_runtime_type_bind_independently_per_version(tmp_path, target, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("binding is prebirth only"))
    inputs = _inputs(tmp_path, target)
    result = binding.build_runtime_binding(**inputs)
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
        "Main_native_once": "NOT_CREATED",
        "original_Popen_or_Windows_handle": "NOT_BORN",
        "COMSOL_or_JVM": "NOT_RUN",
        "replay": False,
    }
    assert result["original_windows_matrix"]["required_rows"] == 60
    assert result["original_windows_matrix"]["target_status"] == "NOT_RUN"
    assert binding.verify_runtime_binding(result, **inputs) == result


@pytest.mark.parametrize("fault", [
    "wrong-target-build", "missing-classpath", "profile-once-drift",
    "current-input-target-drift", "matrix-row-promoted", "root-security-missing",
    "wrong-desktop-version", "wrong_server_version", "readme-drift", "missing-javac",
    "jdk-architecture-unknown", "caller-provenance-extra", "root-provider-drift",
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
        binding.build_runtime_binding(**inputs)


def test_source_producer_and_task_root_changes_refuse_reverification(tmp_path):
    inputs = _inputs(tmp_path, "windows-x64/comsol6.3")
    source_copy = tmp_path / "source-copy"
    original_root = inputs["repository_root"]
    for relative in binding.SOURCE_MEMBERS:
        destination = source_copy / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((original_root / relative).read_bytes())
    inputs["repository_root"] = source_copy
    result = binding.build_runtime_binding(**inputs)
    producer = source_copy / "tools/w26_target_inputs.py"
    producer.write_bytes(producer.read_bytes() + b"\n# controlled test mutation\n")
    with pytest.raises(RuntimeError, match="source|binding"):
        binding.verify_runtime_binding(result, **inputs)


def test_worker_and_core_server_resolve_the_same_owned_preferences_without_birth(tmp_path):
    from comsol_mcp._session_context import CanonicalSocket, OwnedServerProcessIdentity

    inputs = _inputs(tmp_path, "windows-x64/comsol6.4")
    result = binding.build_runtime_binding(**inputs)
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
        application_profile_descriptor={"schema": "fixture", "source_profile": "current175-r6"},
        target_inputs_record={"path": str(tmp_path / "TARGET_INPUTS.json")},
        target_project_path=tmp_path / "pyproject.toml",
        installed_identity=inputs["installed_identity"],
        matrix_path=inputs["matrix_path"],
    )
    assert bound["application_profile"] == profile
    assert len(calls) == 2 and calls[0][0] == "profile" and calls[1][0] == "targets"

