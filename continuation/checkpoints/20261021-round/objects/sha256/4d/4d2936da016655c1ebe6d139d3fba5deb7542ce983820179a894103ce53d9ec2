from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid
from datetime import datetime, timezone

import pytest

REPOSITORY = Path(os.environ.get("W25_R9_REPOSITORY_ROOT", Path(__file__).resolve().parents[1])).resolve()
SCRATCH_ROOT = Path(os.environ["W25_R9_SCRATCH_ROOT"]).resolve() if os.environ.get("W25_R9_SCRATCH_ROOT") else None
APP_ROOT = Path(os.environ["W25_R9_APP_ROOT"]).resolve() if os.environ.get("W25_R9_APP_ROOT") else None
CONTROL_RESULT = Path(os.environ["W25_R9_CONTROL_RESULT_PATH"]) if os.environ.get("W25_R9_CONTROL_RESULT_PATH") else None

if SCRATCH_ROOT is None:
    from tools import w25_current_r9_offline_install_actor as installer
    from tools import w25_macos_owned_desktop_actor_r9 as gui
else:
    def _load(path: Path, name: str):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    installer = _load(SCRATCH_ROOT / "tools/w25_current_r9_offline_install_actor.py", "candidate_r9_install_actor_control")
    gui = _load(SCRATCH_ROOT / "tools/w25_macos_owned_desktop_actor_r9.py", "candidate_r9_gui_actor_control")
    installer.REPOSITORY = REPOSITORY
    installer.STATE = REPOSITORY / "docs/full_project_continuation_v3/state"
    installer.APP_LOCK = REPOSITORY / "tools/w26_application_sources_175_r9_prepared.lock.json"
    installer.RESUME = installer.STATE / "RESUME.json"
    gui.REPOSITORY = REPOSITORY
    gui.STATE = REPOSITORY / "docs/full_project_continuation_v3/state"
    gui.APP_REPORT = (APP_ROOT / "SOURCE_EXTERNAL_R9_READER_RESULT.json") if APP_ROOT is not None else Path(gui.APP_REPORT)
    gui.APP_ACTUAL_REVIEW = gui.STATE / "W26_R9_MAIN_ACTUAL_APP_ARTIFACTS_REVIEW_R1.json"
    gui.INSTALL_ACTUAL_REPORT = gui.STATE / "W25_CURRENT175_R9_OFFLINE_INSTALL_ACTUAL_REPORT_R1.json"
    gui.INSTALL_ACTUAL_REVIEW = gui.STATE / "W25_R9_INSTALL_R1_MAIN_ACTUAL_EVIDENCE_REVIEW_R1.json"
    gui.R7_GUI_R2_SOURCE_CANDIDATE = gui.STATE / "W25_CURRENT175_R7_GUI_SOURCE_CANDIDATE_R2.json"
    gui.R7_GUI_R2_ACTUAL_REPORT = gui.STATE / "W25_CURRENT175_R7_MACOS_GUI_R2_ACTUAL_REPORT_R1.json"
    gui.R7_GUI_R2_MAIN_ACTUAL_REVIEW = gui.STATE / "W25_R7_GUI_R2_MAIN_ACTUAL_FAILED_EVIDENCE_REVIEW_R1.json"
    gui.R9_LOCK = REPOSITORY / "tools/w26_application_sources_175_r9_prepared.lock.json"
    gui.INSTALL_ACTOR = REPOSITORY / "tools/w25_current_r9_offline_install_actor.py"
    gui.RESUME = gui.STATE / "RESUME.json"

pytestmark = pytest.mark.skipif(
    SCRATCH_ROOT is None or APP_ROOT is None or CONTROL_RESULT is None,
    reason="set W25_R9_SCRATCH_ROOT, W25_R9_APP_ROOT, and W25_R9_CONTROL_RESULT_PATH for the explicit one-run scratch harness",
)


def _deny_process_creation(event, args):
    if event in {"subprocess.Popen", "os.system", "os.exec", "os.fork", "os.forkpty", "os.posix_spawn", "os.spawn"}:
        raise AssertionError("source consumer attempted process creation: " + event)


def _record(path: Path) -> dict[str, object]:
    raw = Path(path).read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _immutable_input_snapshot() -> dict[str, dict[str, object]]:
    paths = {
        "app_result": APP_ROOT / "APPLICATION_BUNDLE_RESULT.json",
        "app_reader_report": APP_ROOT / "SOURCE_EXTERNAL_R9_READER_RESULT.json",
        "app_candidate": APP_ROOT / "ACTOR_CANDIDATE_r9fresh-f1d815b5.json",
        "app_source_review": APP_ROOT / "MAIN_SOURCE_REVIEW.json",
        "app_once": APP_ROOT / "UNIT_ONCE_GUARD.json",
        "app_wheel": APP_ROOT / "appwheel/comsol_mcp-0.1.10-py3-none-any.whl",
        "app_lock": REPOSITORY / "tools/w26_application_sources_175_r9_prepared.lock.json",
        "app_archive_review": REPOSITORY / "docs/full_project_continuation_v3/state/W26_R9_MAIN_ACTUAL_APP_ARTIFACTS_REVIEW_R1.json",
        "dependency_manifest": Path(installer.BASE_INPUT_MANIFEST),
        "runtime_input": Path(installer.DEPENDENCY_ROOT) / "CURRENT_RUNTIME_INPUT.json",
    }
    return {name: gui.file_record(path) for name, path in paths.items()}


def _append_control(name: str, value: dict[str, object]) -> None:
    if CONTROL_RESULT is None:
        return
    current = json.loads(CONTROL_RESULT.read_text()) if CONTROL_RESULT.exists() else {"schema": "w25-r9-real-source-consumer-controls/1", "cases": {}}
    current["cases"][name] = value
    CONTROL_RESULT.write_text(json.dumps(current, sort_keys=True, indent=2) + "\n")
    CONTROL_RESULT.chmod(0o600)


def test_real_prepare_then_verify_candidate_no_once_no_process_or_install():
    sys.addaudithook(_deny_process_creation)
    assert APP_ROOT is not None and APP_ROOT == Path(gui.APP_REPORT).parent
    root = Path("/private/tmp") / ("cvw26-r9install-" + uuid.uuid4().hex[:8])
    before_once = root / "UNIT_ONCE_GUARD.json"
    external_before = _immutable_input_snapshot()
    prepared_record = installer.prepare(root, current_application_root=APP_ROOT, tag="r9positive")
    candidate, candidate_record, plan, nested_candidate = installer._verify_candidate(root)
    assert candidate_record == prepared_record
    assert candidate["native_Popen"] == 0
    assert candidate["application"]["current_desktop_overlay"] == candidate["current_desktop_overlay"]
    assert candidate["current_desktop_overlay"]["path"] == str(root / "CURRENT_DESKTOP_OVERLAY.json")
    assert candidate["application"]["source_member_count"] == 175
    assert candidate["application"]["wheelhouse_count"] == 42
    assert len(plan["inventory"]) == 4
    assert not before_once.exists()
    assert all(not (root / name).exists() and not (root / name).is_symlink() for name in
               ("current-venv", "current-install-evidence", "logs", "tmp", "evidence", "outside", "empty-home", "import-home"))
    assert not (root / "PREPARATION_FAILURE.json").exists()
    external_after = _immutable_input_snapshot()
    assert external_after == external_before
    _append_control("prepare_then_verify_candidate", {"status": "PASS", "root": str(root),
        "root_identity": candidate["root_identity"], "candidate": candidate_record,
        "overlay_manifest": candidate["current_desktop_overlay"], "inventory_rows": len(plan["inventory"]),
        "native_Popen": 0, "once_created": False, "install_performed": False,
        "immutable_inputs_before": external_before, "immutable_inputs_after": external_after})


def test_real_gui_prepare_then_verify_authority_normalizes_app_external_records(tmp_path):
    assert APP_ROOT is not None and INSTALL_ROOT is not None
    root = Path("/private/tmp") / ("cvw25-macos-" + uuid.uuid4().hex[:8])
    external_before = _immutable_input_snapshot()
    prepared = gui.prepare(root, INSTALL_ROOT)
    candidate_path = root / "ACTOR_CANDIDATE_macos1.json"
    candidate_record = gui.file_record(candidate_path)
    candidate = json.loads(candidate_path.read_text())
    plan = json.loads(Path(candidate["plan"]["path"]).read_text())
    source = json.loads(Path(candidate["source"]["path"]).read_text())
    preparation = json.loads(Path(candidate["preparation_evidence"]["path"]).read_text())
    imported = preparation["current_source_records"]
    for key in ("app_source_review", "app_result", "app_candidate", "app_wheel"):
        original = imported[key]
        normalized = source[original["path"]]
        assert "device" in normalized and "dev" not in normalized
        assert normalized == gui.file_record(Path(original["path"]))
    assert prepared["native_Popen"] == 0 and candidate["native_Popen"] == 0
    assert candidate["main_source_review"] == "NOT_CREATED" and candidate["main_once_binding"] == "NOT_CREATED"
    assert plan["budgets"]["launch_deadline_seconds"] == 600
    assert plan["budgets"]["cleanup_reserve_seconds"] == 60
    assert plan["budgets"]["max_ax_window_reselections"] == 2
    assert plan["budgets"]["max_ax_depth"] == 2 and plan["budgets"]["max_ax_nodes"] == 64

    review_value = {"schema": "w25-macos-owned-desktop-source-review/1",
        "status": "PASS_W25_MACOS_OWNED_DESKTOP_SOURCE_FOR_EXACT_CANDIDATE",
        "candidate_sha256": candidate_record["sha256"], "plan_sha256": candidate["plan"]["sha256"],
        "source_sha256": candidate["source"]["sha256"], "test_only_synthetic": True}
    review_record = gui._write_new(root / "MAIN_SOURCE_REVIEW.json", review_value)
    checked = datetime.now(timezone.utc).isoformat()
    lane_value = {"schema": "w25-t044-macos-lane-check/1", "status": "CLEAR_FOR_W25_MACOS_GUI_METADATA",
        "activity_kind": candidate["activity_kind"], "candidate_sha256": candidate_record["sha256"],
        "root": str(root), "root_identity": candidate["root_identity"], "plan_sha256": candidate["plan"]["sha256"],
        "source_sha256": candidate["source"]["sha256"], "resume_sha256": candidate["resume_sha256"],
        "review_sha256": review_record["sha256"], "checked_utc": checked,
        "matching_comsol_processes": [], "protected_t044_pids_present": [], "listener_49435_present": False,
        "test_only_synthetic": True}
    lane_record = gui._write_new(root / "W25_LANE_CHECK_CURRENT.json", lane_value)
    binding_value = gui.build_expected_binding(candidate_record, lane_record)
    binding_value["nonce"] = "test-only-synthetic-binding-nonce-" + uuid.uuid4().hex
    binding_value["test_only_synthetic"] = True
    gui._write_new(root / "MAIN_ONCE_BINDING.json", binding_value)
    assert not (root / "UNIT_ONCE_GUARD.json").exists()

    sys.addaudithook(_deny_process_creation)
    verified_candidate, verified_binding, verified_lane = gui.verify_authority(
        root, root / "MAIN_ONCE_BINDING.json", gui.file_record(root / "MAIN_ONCE_BINDING.json")["sha256"])
    assert verified_candidate["root_identity"] == candidate["root_identity"]
    assert verified_binding["nonce"] == binding_value["nonce"]
    assert verified_lane["test_only_synthetic"] is True
    assert not (root / "UNIT_ONCE_GUARD.json").exists()
    after = _immutable_input_snapshot()
    assert after == external_before
    auth_files = [root / name for name in ("MAIN_SOURCE_REVIEW.json", "W25_LANE_CHECK_CURRENT.json", "MAIN_ONCE_BINDING.json")]
    auth_records = {path.name: gui.file_record(path) for path in auth_files}
    for path in auth_files:
        path.unlink()
    assert not (root / "UNIT_ONCE_GUARD.json").exists()
    _append_control("real_prepare_then_verify_authority", {"status": "PASS", "root": str(root),
        "candidate": candidate_record, "candidate_plan_sha256": candidate["plan"]["sha256"],
        "candidate_source_sha256": candidate["source"]["sha256"], "producer_external_records_normalized": 4,
        "synthetic_review_lane_binding_only": True, "synthetic_auth_files_removed_after_verification": True,
        "synthetic_auth_records": auth_records, "native_Popen": 0, "UNIT_ONCE_GUARD_created": False,
        "GUI": "NOT_RUN", "COMSOL": "NOT_RUN", "JVM": "NOT_RUN", "window_inventory": "NOT_RUN",
        "lane_preparation_status": preparation["lane_preparation"]["status"],
        "install_once_sha256": gui.file_record(INSTALL_ROOT / "UNIT_ONCE_GUARD.json")["sha256"],
        "app_wheel_sha256": external_before["app_wheel"]["sha256"]})

def test_real_gui_app4cab_r9_input_consumer():
    sys.addaudithook(_deny_process_creation)
    assert APP_ROOT is not None and APP_ROOT == Path(gui.APP_REPORT).parent
    external_before = _immutable_input_snapshot()
    result_record = _record(APP_ROOT / "APPLICATION_BUNDLE_RESULT.json")
    wheel_record = _record(APP_ROOT / "appwheel/comsol_mcp-0.1.10-py3-none-any.whl")
    reader = gui._verify_r9_inputs(APP_ROOT)
    assert reader["app_result"]["sha256"] == result_record["sha256"]
    assert reader["app_wheel"]["sha256"] == wheel_record["sha256"]
    assert reader["r9_lock"]["sha256"] == "a524e533f01ed5e5ea0f06cfa5caf49ab0ddf50474258b672265c13f62f987a2"
    assert reader["r9_profile"]["source_profile"] == "current175-r9"
    assert reader["app_reader_report"]["sha256"] == "129789858d6573f74b10ba2c08b54200c0408d9df79f32aa840bc59ca38e2b33"
    assert reader["app_actual_review"]["sha256"] == "a82c5ad48149086ee91df405934c9910c45e2c913fdfee7be71fdb9926f77d69"
    external_after = _immutable_input_snapshot()
    assert external_after == external_before
    _append_control("gui_input_consumer", {"status": "PASS", "app_root": str(APP_ROOT),
        "app_reader_report": reader["app_reader_report"], "app_result": reader["app_result"],
        "app_wheel": reader["app_wheel"], "r9_lock": reader["r9_lock"],
        "app_actual_review": reader["app_actual_review"], "source_profile": reader["r9_profile"]["source_profile"],
        "immutable_inputs_before": external_before, "immutable_inputs_after": external_after})


INSTALL_ROOT = Path(os.environ["W25_R9_INSTALL_ROOT"]) if os.environ.get("W25_R9_INSTALL_ROOT") else None

def test_r9_overlay_external_record_normalizes_dev_and_rejects_inode_or_hash_mismatch(tmp_path):
    path = tmp_path / "overlay.json"
    path.write_text("{}\n")
    actual = gui.file_record(path)
    external = dict(actual)
    external["dev"] = external.pop("device")
    assert gui._capture_external_record(external) == actual
    wrong_inode = dict(external, inode=external["inode"] + 1)
    with pytest.raises(RuntimeError, match="external first-record mismatch"):
        gui._capture_external_record(wrong_inode)
    wrong_hash = dict(external, sha256="0" * 64)
    with pytest.raises(RuntimeError, match="external first-record mismatch"):
        gui._capture_external_record(wrong_hash)

def test_retained_r9_install_reader_and_app_reader_pass_read_only():
    sys.addaudithook(_deny_process_creation)
    assert INSTALL_ROOT is not None
    root_inputs = [INSTALL_ROOT / "W25_R9_INSTALL_CANDIDATE.json", INSTALL_ROOT / "CURRENT_R9_OFFLINE_INSTALL_RESULT.json",
                   INSTALL_ROOT / "MAIN_ONCE_BINDING.json", INSTALL_ROOT / "UNIT_ONCE_GUARD.json",
                   REPOSITORY / "docs/full_project_continuation_v3/state/W25_CURRENT175_R9_OFFLINE_INSTALL_ACTUAL_REPORT_R1.json"]
    before = {str(p): gui.file_record(p) for p in root_inputs}
    install = gui._verify_current_r9_install(INSTALL_ROOT)
    assert install["result"]["sha256"] == "fdd15a491768209ed87e8231ae2efe0195c3bbe9a413cddd606a5ebda53cbf89"
    assert install["venv"] == str(INSTALL_ROOT / "current-venv")
    assert len(install["package_records"]) == 172
    review = json.loads(Path(install["actual_review"]["path"]).read_text())
    assert review["status"] == "PASS_SCOPED_W25_R9_INSTALL_R1_MAIN_ACTUAL_REVIEW"
    app = gui._verify_r9_inputs(APP_ROOT)
    assert app["r9_profile"]["source_profile"] == "current175-r9"
    assert {str(p): gui.file_record(p) for p in root_inputs} == before


