from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import windows_current_installed_profile_runtime_binding_r4 as binding


def test_r9_source_lock_binding_is_main_reviewed_preparation_only():
    readiness = binding.current_source_readiness(Path(__file__).resolve().parents[1])
    source_lock = readiness["source_lock"]

    assert readiness["status"] == "PASS_R9_SOURCE_PROFILE_PREPARATION_ONLY"
    assert readiness["source_profile"] == "current175-r9"
    assert source_lock["sha256"] == binding.R9_SOURCE_LOCK_SHA256
    assert source_lock["member_fingerprint_sha256"] == binding.R9_MEMBER_FINGERPRINT_SHA256
    assert source_lock["member_count"] == 175
    assert source_lock["main_review_sha256"] == binding.R9_SOURCE_PROFILE_REVIEW_SHA256
    assert source_lock["main_review_status"] == "PASS_SCOPED_EXACT_SOURCE_PROFILE_PREPARATION"
    assert source_lock["preparation_only"] is True
    assert readiness["current_app_build"] == "NOT_RUN"
    assert readiness["current_runtime_binding_created"] is False
    assert readiness["T037"] == "UNVERIFIED"


def test_r9_lock_sha_is_a_pinned_value_not_learned_from_the_current_file(monkeypatch):
    monkeypatch.setattr(binding, "R9_SOURCE_LOCK_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="exact Main-reviewed preparation"):
        binding._r9_source_lock_binding(Path(__file__).resolve().parents[1])


def test_current_binding_remains_not_ready_without_r9_reader_or_real_app_root():
    readiness = binding.current_application_readiness(Path(__file__).resolve().parents[1])

    assert readiness["status"] in {
        "NOT_READY_R9_SEMANTIC_READER_PENDING",
        "NOT_READY_CURRENT_R9_APP_BUILD_PENDING",
    }
    assert readiness["current_application_root"] is None
    assert readiness["actual_app_reader_result"] == "NOT_AVAILABLE"
    assert readiness["runtime_binding_created"] is False
    assert readiness["source_preparation"]["source_lock"]["preparation_only"] is True


def test_r7_label_cannot_be_relabelled_as_the_current_r9_producer(tmp_path):
    descriptor = {
        "schema": "w26-current-installed-application/1",
        "source_profile": "current175-r7",
        "root": str(tmp_path),
        "loader_source_closure": {},
    }
    with pytest.raises(RuntimeError, match="current175-r9 producer"):
        binding._build_w26_source_profile_proof(
            descriptor, {}, binding._r9_source_lock_binding(Path(__file__).resolve().parents[1]),
        )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("source_profile", "current175-r7"),
        ("source_lock_sha256", "0" * 64),
        ("member_fingerprint_sha256", "1" * 64),
        ("source_snapshot_fingerprint_sha256", "2" * 64),
        ("origin_snapshot_fingerprint_sha256", "3" * 64),
    ],
)
def test_reader_provenance_refuses_wrong_generation_lock_or_snapshot(tmp_path, field, bad_value):
    lock = binding._r9_source_lock_binding(Path(__file__).resolve().parents[1])
    provenance = {
        "schema": "w26-source-provenance-snapshot-binding/1",
        "source_profile": "current175-r9",
        "source_lock_path": lock["path"],
        "source_lock_sha256": binding.R9_SOURCE_LOCK_SHA256,
        "member_count": 175,
        "package_count": 172,
        "root_count": 3,
        "member_fingerprint_sha256": binding.R9_MEMBER_FINGERPRINT_SHA256,
        "source_snapshot_path": str(tmp_path / "source-input"),
        "source_snapshot_member_count": 175,
        "source_snapshot_fingerprint_sha256": binding.R9_MEMBER_FINGERPRINT_SHA256,
        "origin_snapshot_path": str(tmp_path / "source-origin"),
        "origin_snapshot_member_count": 175,
        "origin_snapshot_fingerprint_sha256": binding.R9_MEMBER_FINGERPRINT_SHA256,
        "source_snapshot_record_path": str(tmp_path / "APP_INPUT_BINDING_fixture.json"),
        "source_snapshot_record_sha256": "4" * 64,
    }
    provenance[field] = bad_value
    with pytest.raises(RuntimeError, match="R9 reader provenance differs"):
        binding._validate_r9_source_provenance(provenance, tmp_path, lock, {})


def test_r9_reader_route_refuses_before_target_or_runtime_binding_when_app_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("current-profile admission must not create a process"),
    )
    with pytest.raises(binding.CurrentProfileNotReady) as captured:
        binding.verify_current_w26_inputs(
            current_application_root=None,
            target_inputs_record={},
            target_project_path=tmp_path / "unused-pyproject.toml",
            repository_root=Path(__file__).resolve().parents[1],
        )
    assert captured.value.readiness["runtime_binding_created"] is False
    assert captured.value.readiness["source_preparation"]["status"] == "PASS_R9_SOURCE_PROFILE_PREPARATION_ONLY"


def test_projected_windows_routes_preserve_all_60_rows_for_both_targets_without_binding(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    workspace_root = repository_root.parent
    matrix_path = (
        workspace_root / "_state/windows-restored-host-current-20261014/ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"
    )
    before_sha = binding._file_record(matrix_path)["sha256"]
    projection = binding.project_windows_matrix_route(
        repository_root=repository_root,
        matrix_path=matrix_path,
    )

    assert projection["status"] == "NOT_READY_CURRENT_R9_APP_REQUIRED"
    assert projection["matrix"]["sha256"] == binding.ORIGINAL_MATRIX_SHA256
    assert projection["matrix"]["required_rows"] == 60
    original = json.loads(matrix_path.read_text(encoding="utf-8"))
    assert projection["schema"] == "cv3-windows-current-installed-profile-r9-route-preparation/1"
    assert projection["original_matrix"] == original
    assert projection["original_matrix"]["tests"] == original["tests"]
    original_ids = [row["id"] for row in original["tests"]]
    assert projection["projected_route_count"] == 120
    assert set(projection["target_routes"]) == {
        "windows-x64/comsol6.3", "windows-x64/comsol6.4",
    }
    for target, route in projection["target_routes"].items():
        assert route["test_ids"] == original_ids
        assert route["route_count"] == 60
        assert route["route_status"] == "NOT_RUN"
        assert route["evidence"] == [] and route["runtime_id"] is None
        assert route["binding_id"] is None and route["process_creation"] == "NOT_RUN"
        assert route["COMSOL_or_JVM"] == "NOT_RUN"
        assert projection["target_statuses"][target] == "NOT_RUN"
    assert "routes" not in projection
    assert projection["runtime_binding_created"] is False
    assert binding._file_record(matrix_path)["sha256"] == before_sha


def test_projected_windows_route_requires_canonical_frozen_matrix_path(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    matrix_path = (
        repository_root.parent
        / "_state/windows-restored-host-current-20261014/ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"
    )
    copied = tmp_path / "ORIGINAL_WINDOWS_REQUIRED_MATRIX.json"
    copied.write_bytes(matrix_path.read_bytes())
    with pytest.raises(RuntimeError, match="exact frozen V3 matrix path"):
        binding.project_windows_matrix_route(repository_root=repository_root, matrix_path=copied)


def test_r9_consumer_uses_unmocked_closed_loader_in_ordinary_tools_namespace(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    project_path = tmp_path / "pyproject.toml"
    project_path.write_text("[project]\nname='consumer-loader-test'\n", encoding="utf-8")
    source = r'''import json, sys
from pathlib import Path

repository_root = Path(sys.argv[1]).resolve(strict=True)
project_path = Path(sys.argv[2]).resolve(strict=True)
sys.path.insert(0, str(repository_root))

from tools import windows_current_installed_profile_runtime_binding_r4 as binding
from tools import w26_sealed_data_r9 as sealed_r9
import sys as _sys

tools_root = repository_root / "tools"
assert Path(binding.__file__).resolve(strict=True) == tools_root / "windows_current_installed_profile_runtime_binding_r4.py"
loader_sources = binding._current_r9_loader_sources(reader=sealed_r9, target_project_path=project_path)
closure = sealed_r9.BASE["current_application_loader_closure"](loader_sources)
expected_names = sealed_r9.BASE["CURRENT_APPLICATION_LOADER_NAMES"]
assert len(expected_names) == 28
assert set(closure["members"]) == expected_names
assert sealed_r9.BASE["verify_current_application_loader_closure"](closure) == tools_root

# Exercise the real loader with the ordinary consumer cached, without deleting
# or restoring any sys.modules entries.
consumer_name = "tools.windows_current_installed_profile_runtime_binding_r4"
consumer = _sys.modules[consumer_name]
app_module, actor_module = sealed_r9.BASE["_install_current_application_tools_namespace"](tools_root, closure)
assert _sys.modules[consumer_name] is consumer
assert Path(app_module.__file__).resolve(strict=True) == tools_root / "w26_application_bundle.py"
assert Path(actor_module.__file__).resolve(strict=True) == tools_root / "w26_runtime_actor.py"
assert {str(Path(item).resolve(strict=True)) for item in _sys.modules["tools"].__path__} == {str(tools_root)}

application = sys.modules["tools.w26_application_bundle"]
r5_lock_path = str(application.CURRENT175_R5_SOURCE_LOCK.resolve(strict=True))
missing_r5 = dict(loader_sources)
del missing_r5[r5_lock_path]
try:
    sealed_r9.BASE["current_application_loader_closure"](missing_r5)
except RuntimeError as exc:
    assert "membership differs" in str(exc)
else:
    raise AssertionError("missing R5 lock source was accepted")

wrong_r5 = dict(loader_sources)
wrong_r5[r5_lock_path] = dict(wrong_r5[r5_lock_path], sha256="0" * 64)
try:
    sealed_r9.BASE["current_application_loader_closure"](wrong_r5)
except RuntimeError as exc:
    assert "fingerprint differs" in str(exc)
else:
    raise AssertionError("wrong R5 lock fingerprint was accepted")

print(json.dumps({
    "status": "PASS_R9_UNMOCKED_LOADER_CLOSURE_AND_ORDINARY_NAMESPACE",
    "expected_member_count": len(expected_names),
    "observed_member_count": len(closure["members"]),
    "consumer_source_sha256": binding._file_record(Path(binding.__file__))['sha256'],
    "r9_reader_sha256": binding._file_record(Path(sealed_r9.__file__))['sha256'],
    "missing_and_wrong_r5_lock_refusals": "PASS",
    "cached_consumer_preserved": True,
    "tools_namespace_paths": sorted(str(Path(item).resolve(strict=True)) for item in _sys.modules["tools"].__path__),
}, sort_keys=True))
'''
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("PYTHONHOME", None)
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", source, str(repository_root), str(project_path)],
        cwd=repository_root,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS_R9_UNMOCKED_LOADER_CLOSURE_AND_ORDINARY_NAMESPACE"
    assert result["expected_member_count"] == result["observed_member_count"] == 28
    assert result["missing_and_wrong_r5_lock_refusals"] == "PASS"
    assert result["cached_consumer_preserved"] is True
    assert result["r9_reader_sha256"] == "b7bb254e6cef089e39ef9408c0a16371703b84ad4e706c74bad0ced418c0153c"
