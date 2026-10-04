"""Software/fixture evidence only: never installs a release or starts an engine."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import struct
import subprocess
import sys
import types
import zipfile

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = load("release_lifecycle_driver_tests", "tools/full_project_release.py")
helper = load("release_lifecycle_helper_tests", "tools/release_transition_lifecycle.py")
ENV = {"python_version": "3.12", "python_full_version": "3.12.13", "sys_platform": "darwin",
       "platform_machine": "arm64", "platform_system": "Darwin", "os_name": "posix",
       "implementation_name": "cpython", "implementation_version": "3.12.13", "platform_release": "test",
       "platform_version": "test", "platform_python_implementation": "CPython"}


def input_fixture(tmp_path, *, code=b"VALUE = 1\n", version="0.1.9", label="old"):
    wheelhouse = tmp_path / label
    wheelhouse.mkdir()
    wheel = wheelhouse / f"comsol_mcp-{version}-py3-none-any.whl"
    info = f"comsol_mcp-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("comsol_mcp/__init__.py", b"")
        archive.writestr("comsol_mcp/_operation_store.py", code)
        archive.writestr(info + "/METADATA", f"Metadata-Version: 2.1\nName: comsol-mcp\nVersion: {version}\n\n")
        archive.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    sha = release.sha256_file(wheel)
    lock = tmp_path / (label + ".lock")
    lock.write_text(f"comsol-mcp=={version} --hash=sha256:{sha}\n")
    return wheelhouse, lock, sha, helper.wheel_input(release, lock, wheelhouse, sha, ENV)


def test_real_old_wheel_metadata_source_payload_is_verified_without_install(tmp_path):
    wheel = ROOT / "evidence/w21_closure/delivery/comsol_mcp-0.1.9-py3-none-any.whl"
    # Tests bind the checked-in/local fixture bytes, not a package install.
    if not wheel.is_file():
        pytest.skip("historical local wheel unavailable")
    wheelhouse = tmp_path / "real-old"
    wheelhouse.mkdir()
    (wheelhouse / wheel.name).write_bytes(wheel.read_bytes())
    sha = "8511dbc550792fbe538646897c951649a49de668030e66bd9306868801b94e5d"
    lock = tmp_path / "old.lock"
    lock.write_text(f"comsol-mcp==0.1.9 --hash=sha256:{sha}\n")
    result = helper.wheel_input(release, lock, wheelhouse, sha, ENV)
    assert result["wheel_sha256"] == sha
    assert len(result["members"]) == 81
    assert "mcp<2,>=1.30" in result["requires_dist"]
    assert next(r for r in result["members"] if r["path"] == "comsol_mcp/_operation_store.py")["sha256"] == "e516d9c2c3fab348ac6769eeef995c968a6683e22279e9a645e3f2995c24ccef"


@pytest.mark.parametrize("defect", ["expected-hash", "lock-hash", "lock-version", "missing-wheel"])
def test_inputs_reject_unbound_wheel_and_metadata(tmp_path, defect):
    house, lock, sha, _ = input_fixture(tmp_path)
    if defect == "expected-hash":
        sha = "0" * 64
    elif defect == "lock-hash":
        lock.write_text("comsol-mcp==0.1.9 --hash=sha256:" + "0" * 64)
    elif defect == "lock-version":
        lock.write_text(f"comsol-mcp==9.0 --hash=sha256:{sha}")
    else:
        next(house.glob("*.whl")).unlink()
    with pytest.raises(release.ReleaseError):
        helper.wheel_input(release, lock, house, sha, ENV)


def test_applicable_lock_uses_actual_full_python_marker_not_target_dot_zero(tmp_path):
    house, lock, sha, _ = input_fixture(tmp_path)
    lock.write_text(f"comsol-mcp==0.1.9; python_full_version >= '3.12.13' --hash=sha256:{sha}\n"
                    f"comsol-mcp==0.0; python_full_version < '3.12.13' --hash=sha256:{'0'*64}\n")
    result = helper.wheel_input(release, lock, house, sha, ENV)
    assert result["version"] == "0.1.9"


@pytest.mark.parametrize("invalid", [False, True])
def test_lifecycle_input_records_only_validated_paired_wheel_metadata(tmp_path, invalid):
    house, lock, sha, _ = input_fixture(tmp_path)
    wheel = next(house.glob("*.whl"))
    sidecar = wheel.with_name("._" + wheel.name)
    sidecar.write_bytes(b"bad" if invalid else struct.pack(">II16sHIII", 0x00051607, 0x00020000, b"\0"*16, 1, 2, 38, 0))
    if invalid:
        with pytest.raises(release.ReleaseError, match="AppleDouble"):
            helper.wheel_input(release, lock, house, sha, ENV)
    else:
        result = helper.wheel_input(release, lock, house, sha, ENV)
        assert len(result["available_locked_wheels"]) == 1
        assert result["wheelhouse_metadata_sidecars"][0]["sha256"] == release.sha256_file(sidecar)
        assert result["wheelhouse_metadata_sidecars"][0]["peer_sha256"] == sha


@pytest.mark.parametrize("metadata_only", [False, True])
def test_lifecycle_preflight_rejects_same_wheel_or_metadata_only_repack(tmp_path, monkeypatch, metadata_only):
    old_house, old_lock, old_sha, _ = input_fixture(tmp_path)
    if metadata_only:
        new_house, new_lock, new_sha, _ = input_fixture(tmp_path, label="new", version="0.2.0")
    else:
        new_house, new_lock, new_sha = old_house, old_lock, old_sha
    args = types.SimpleNamespace(python=pathlib.Path(sys.executable), work_root=tmp_path / "work",
                                 old_wheelhouse=old_house, new_wheelhouse=new_house,
                                 old_wheel_sha256=old_sha, new_wheel_sha256=new_sha)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setattr(release, "run_record", lambda argv, **kw: {"argv": argv, "exit_code": 0, "stdout": json.dumps(ENV), "stderr": ""})
    with pytest.raises(release.ReleaseError, match="same application wheel|metadata-only"):
        helper.Lifecycle(args, release, evidence, old_lock, new_lock, "sandbox")


def installed_state(expected, extra=None):
    rows = [{"name": n, "version": p["version"]} for n, p in expected["applicable_pins"].items()]
    rows.extend({"name": n, "version": v} for n, v in (extra or {"pip": "25.0"}).items())
    files = [{**r, "path": r["path"].removeprefix("comsol_mcp/")} for r in expected["package_members"]]
    return {"installed_version": expected["version"], "capture_errors": {},
            "package_fingerprint": {"files": files, "digest": helper.digest(files)},
            "distribution_fingerprint": {"distributions": rows, "digest": helper.digest(rows)},
            "metadata_members": [next(r for r in expected["members"] if r["path"] == expected["metadata_path"])],
            "marker_environment": ENV}


@pytest.mark.parametrize("defect", [None, "source", "extra", "version", "metadata", "pip-check", "unknown", "bootstrap", "environment"])
def test_installed_identity_and_exact_lock_set_fail_closed(tmp_path, defect):
    _, _, _, expected = input_fixture(tmp_path)
    lifecycle = object.__new__(helper.Lifecycle)
    lifecycle.release, lifecycle.bootstrap, lifecycle.failures, lifecycle.identities = release, None, [], {}
    state = installed_state(expected)
    if defect == "source":
        state["package_fingerprint"]["files"][1] = {**state["package_fingerprint"]["files"][1], "sha256": "0" * 64}
    elif defect == "extra":
        state["distribution_fingerprint"]["distributions"].append({"name": "unlocked", "version": "1"})
    elif defect == "version":
        state["distribution_fingerprint"]["distributions"][0]["version"] = "9"
    elif defect == "metadata":
        state["metadata_members"] = []
    elif defect == "bootstrap":
        lifecycle.bootstrap = {"pip": "24.0"}
    elif defect == "environment":
        state["marker_environment"] = {**ENV, "python_full_version": "3.12.0"}
    lifecycle.process = lambda *args: {"exit_code": None if defect == "unknown" else 1 if defect == "pip-check" else 0}
    assert lifecycle.verify_installed("old", pathlib.Path(sys.executable), state, expected) is (defect is None)


@pytest.mark.parametrize("record", [{"exit_code": None, "stdout": "{}"}, {"exit_code": 0, "stdout": "not-json"}, {"exit_code": 0, "stdout": "{}"}])
def test_failed_identity_capture_is_retained_even_if_later_state_recovers(tmp_path, record):
    _, _, _, expected = input_fixture(tmp_path)
    lifecycle = object.__new__(helper.Lifecycle)
    lifecycle.old = lifecycle.new = expected
    lifecycle.failures = []
    lifecycle.process = lambda *args: record
    captured = lifecycle.capture(pathlib.Path(sys.executable))
    assert captured["capture_errors"] == {"lifecycle_capture": "UNKNOWN"}
    assert lifecycle.failures == ["installed-capture-failed-or-unknown"]


def run_fixture_child(package_root, code, args):
    # Explicit software fixture path; no pip/venv/release installation occurs.
    bootstrap = f"import sys; sys.path.insert(0,{str(package_root)!r});\n"
    return subprocess.run([sys.executable, "-I", "-B", "-c", bootstrap + code, *map(str, args)], capture_output=True, text=True, timeout=45)


def test_actual_old_current_code_profile_backup_restore_and_independent_reopen(tmp_path):
    old_wheel = ROOT / "evidence/w21_closure/delivery/comsol_mcp-0.1.9-py3-none-any.whl"
    if not old_wheel.is_file():
        pytest.skip("historical local wheel unavailable")
    old_root, new_root = tmp_path / "old-code", tmp_path / "candidate-code"
    for base in (old_root, new_root):
        pkg = base / "comsol_mcp"
        pkg.mkdir(parents=True)
        # Production backup/lock modules come from the existing source tree;
        # OperationStore resolves to this phase's exact fixture source first.
        (pkg / "__init__.py").write_text(f"__path__.append({str(ROOT / 'comsol_mcp')!r})\n")
    with zipfile.ZipFile(old_wheel) as wheel:
        old_bytes = wheel.read("comsol_mcp/_operation_store.py")
    (old_root / "comsol_mcp/_operation_store.py").write_bytes(old_bytes)
    (new_root / "comsol_mcp/_operation_store.py").write_bytes((ROOT / "comsol_mcp/_operation_store.py").read_bytes())
    home, seed = tmp_path / "synthetic-home", tmp_path / "seed.json"
    backup = tmp_path / "backup.sqlite3"
    manifest = pathlib.Path(str(backup) + ".manifest.json")
    stages = [
        (old_root, helper.DATA_SCRIPT, ["seed", home, seed]),
        (new_root, helper.BACKUP_SCRIPT, ["backup", "--home", home, "--destination", backup]),
        (new_root, helper.BACKUP_VERIFY_SCRIPT, [backup, manifest]),
        (new_root, helper.DATA_SCRIPT, ["candidate", home, seed]),
        (new_root, helper.FAILURE_SCRIPT, []),
        (new_root, helper.BACKUP_SCRIPT, ["restore", "--home", home, "--backup", backup, "--manifest", manifest, "--confirm"]),
        (old_root, helper.DATA_SCRIPT, ["restored", home, seed]),
    ]
    records = []
    for index, (root, code, args) in enumerate(stages):
        record = run_fixture_child(root, code, args)
        records.append(record)
        (tmp_path / f"actual-source-child-{index}.json").write_text(json.dumps({"exit_code": record.returncode, "stdout": record.stdout, "stderr": record.stderr}))
        assert record.returncode == (73 if index == 4 else 0), record.stderr
    seed_state = json.loads(records[0].stdout)
    restored = json.loads(records[-1].stdout)
    assert restored["old_data_verified"] is True
    assert restored["unknown_safe_retry_false_verified"] is True
    assert restored["before_storage"]["schema"] != seed_state["storage"]["schema"]
    assert restored["before_storage"]["sha256"] != seed_state["storage"]["sha256"]
    assert len(json.loads(records[3].stdout)["reconciled_job_ids"]) == 1
    assert release.sha256_file(backup) == json.loads(records[2].stdout)["sha256"]


@pytest.mark.parametrize("defect", ["seed-result", "terminal-status", "unknown-retry", "new-row"])
def test_actual_child_rejects_changed_old_data(tmp_path, defect):
    # Same fixed child profile, using current source only; this is a software
    # counterexample, not a second v1 migration suite or release transition.
    root = tmp_path / "code"
    pkg = root / "comsol_mcp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f"__path__.append({str(ROOT / 'comsol_mcp')!r})\n")
    home, seed = tmp_path / "home", tmp_path / "seed.json"
    initial = run_fixture_child(root, helper.DATA_SCRIPT, ["seed", home, seed])
    assert initial.returncode == 0, initial.stderr
    import sqlite3
    with sqlite3.connect(home / "operations.sqlite3") as db:
        if defect == "seed-result":
            db.execute("UPDATE operations SET result='{}'")
        elif defect == "terminal-status":
            db.execute("UPDATE operations SET status='RUNNING' WHERE status='SUCCEEDED'")
        elif defect == "unknown-retry":
            db.execute("UPDATE operations SET result=? WHERE status='UNKNOWN'", (json.dumps({"safe_retry": True}),))
        else:
            db.execute("INSERT INTO sessions VALUES('unexpected','{}')")
    rejected = run_fixture_child(root, helper.DATA_SCRIPT, ["candidate", home, seed])
    assert rejected.returncode != 0
    assert "original data changed" in rejected.stderr


@pytest.mark.parametrize("failure", [None, "candidate-data", "candidate-identity", "restore", "rollback", "unknown-capture", "inactive-first-pin", "cleanup-capture"])
def test_full_mode_ordering_and_unexpected_failures_always_rollback_software_mock(tmp_path, monkeypatch, failure):
    """Mocked install/transport explicitly proves orchestration only."""
    events = []
    state = {"phase": "old"}
    old_house, old_lock, old_sha, old = input_fixture(tmp_path)
    new_house, new_lock, new_sha, new = input_fixture(tmp_path, label="new", code=b"VALUE = 2\n")
    if failure == "inactive-first-pin":
        for lock in (old_lock, new_lock):
            lock.write_text("comsol-mcp==0.0; python_version < '3.0' --hash=sha256:" + "0" * 64 + "\n" + lock.read_text())
    work = tmp_path / "transition"
    args = types.SimpleNamespace(python=pathlib.Path(sys.executable), work_root=work,
        old_requirements=old_lock, new_requirements=new_lock, old_wheelhouse=old_house,
        new_wheelhouse=new_house, with_data_lifecycle=True, old_wheel_sha256=old_sha, new_wheel_sha256=new_sha)

    class FakeLifecycle:
        def __init__(self, *args):
            self.old, self.new, self.failures = old, new, []
            self.candidate_pass = self.restore_pass = False
            self.capture_count = 0
            (work / "transition-evidence/network-deny-transition.sb").write_text("fixture profile")

        def capture(self, py):
            self.capture_count += 1
            if failure == "cleanup-capture" and self.capture_count == 3:
                self.failures.append("installed-capture-failed-or-unknown")
                return {"capture_errors": {"fixture": "unknown"}, "installed_version": None}
            if failure == "unknown-capture" and state["phase"] == "candidate":
                return {"capture_errors": {"fixture": "unknown"}, "installed_version": None}
            output = installed_state(self.new if state["phase"] == "candidate" else self.old)
            if state["phase"] == "candidate":
                output["distribution_fingerprint"]["distributions"].append({"name": "candidate-only", "version": "1"})
            elif state.get("candidate_only"):
                output["distribution_fingerprint"]["distributions"].append({"name": "candidate-only", "version": "1"})
            rows = output["distribution_fingerprint"]["distributions"]
            output["distribution_fingerprint"]["digest"] = helper.digest(rows)
            return output

        def retain_record(self, name, record):
            events.append("raw-record:" + name)

        def verify_installed(self, name, py, observed, expected):
            events.append("verify-" + name)
            passed = not (name == "candidate" and failure in {"candidate-identity", "unknown-capture"})
            if not passed:
                self.failures.append(name)
            return passed

        def seed_old(self, py):
            events.append("old-real-code-seed-fixture-mock")

        def candidate_phase(self, py):
            events.extend(["verified-backup-before-candidate-open-fixture-mock", "candidate-reopen-fixture-mock"])
            if failure == "candidate-data":
                raise release.ReleaseError("simulated real-data validation failure")
            events.append("expected-controlled-failure-fixture-mock")
            self.candidate_pass = True

        def restore_candidate(self, py):
            events.append("production-restore-fixture-mock")
            if failure == "restore":
                raise release.ReleaseError("simulated restore failure")
            self.restore_pass = True

        def finish(self, py, package_pass, identity_pass):
            events.append("old-independent-reopen-fixture-mock")
            passed = package_pass and identity_pass and self.candidate_pass and self.restore_pass and not self.failures
            return {"status": "PASS_DISPOSABLE_CODE_DATA_LIFECYCLE" if passed else "FAIL_OR_UNKNOWN_DATA_LIFECYCLE", "release_acceptance": "NOT_RUN", "native": "NOT_RUN", "science": "NOT_RUN"}

    original_factory = importlib.util.module_from_spec
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: types.SimpleNamespace(Lifecycle=FakeLifecycle) if spec.name == "release_transition_lifecycle" else original_factory(spec))
    # The existing driver loader executes the module. Intercept only its helper
    # spec so this boundary remains plainly an orchestration fixture.
    original_spec = importlib.util.spec_from_file_location
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda name, path: types.SimpleNamespace(name=name, loader=types.SimpleNamespace(exec_module=lambda module: None)) if name == "release_transition_lifecycle" else original_spec(name, path))
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setattr(release.shutil, "which", lambda name: "/sandbox-fixture")
    monkeypatch.setattr(release, "offline_install", lambda *args: {"status": "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT"})

    def fake_run(argv, **kwargs):
        if "install" in argv:
            lock = pathlib.Path(argv[argv.index("--requirement") + 1])
            candidate = "candidate-requirements.lock" == lock.name
            events.append("candidate-install" if candidate else "old-package-rollback")
            state["phase"] = "candidate" if candidate else "old"
            state["candidate_only"] = not (not candidate and failure == "cleanup-capture")
            return {"argv": argv, "exit_code": 1 if not candidate and failure == "rollback" else 0, "stdout": "", "stderr": ""}
        assert "uninstall" in argv
        state["candidate_only"] = False
        events.append("candidate-only-cleanup")
        return {"argv": argv, "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(release, "run_record", fake_run)
    result = release.transition_check(args, pathlib.Path(release.__file__))
    assert "old-package-rollback" in events
    assert events.index("old-real-code-seed-fixture-mock") < events.index("candidate-install")
    assert events.index("old-package-rollback") < events.index("old-independent-reopen-fixture-mock")
    if failure is None:
        cleanup = result["rollback_candidate_only_cleanup"]
        assert cleanup["attempted"] is True
        assert cleanup["package_names"] == ["candidate-only"]
        assert result["candidate_only_distribution_names_attempted"] == ["candidate-only"]
        assert result["candidate_only_distribution_names_removed_confirmed"] == ["candidate-only"]
        assert events.count("candidate-only-cleanup") == 1
    if failure in (None, "inactive-first-pin"):
        assert result["status"] == "PASS_DISPOSABLE_CODE_DATA_LIFECYCLE_RELEASE_UNVERIFIED"
        assert result["version_migration_verified"] is False  # genuine source delta, same version label
        assert result["expected_versions"] == {"old": "0.1.9", "candidate": "0.1.9"}
        assert result["data_lifecycle"]["release_acceptance"] == "NOT_RUN"
        assert events.index("verified-backup-before-candidate-open-fixture-mock") < events.index("candidate-reopen-fixture-mock")
        assert events.index("production-restore-fixture-mock") < events.index("old-package-rollback")
    else:
        assert not result["status"].startswith("PASS")
        assert result["data_lifecycle"]["status"] == "FAIL_OR_UNKNOWN_DATA_LIFECYCLE"
        if failure == "candidate-data":
            assert "production-restore-fixture-mock" in events
