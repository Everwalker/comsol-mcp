from __future__ import annotations

import ast
import json
import shutil
import uuid
from pathlib import Path

import pytest

from tools import w25_current_r6_offline_install_actor as actor
from tools import w26_application_lifecycle_r6_actor as lifecycle_r6
from tools import w26_runtime_actor as runtime
from tools import w26_sealed_data as data
from tools import w26_sealed_data_r6 as data_r6
from tools.w26_prepare_current_inputs import _helpers


def _row(label, *, offline=False, argv=None):
    return {"label": label, "request_argv": argv or ["/python", "-c", label],
            "cap": 600 if offline else 180, "max_calls": 3,
            "cwd": "/private/tmp/cvw26-example", "offline_listener_object": offline}


def test_r6_install_plan_is_exactly_one_install_plus_three_readbacks():
    rows = [_row("old-package-rollback", offline=True),
            _row("current-offline-install", offline=True),
            _row("current-package_fingerprint"),
            _row("current-distribution_fingerprint"),
            _row("current-caller-desktop-metadata-status", argv=["/python", "-I", "-c", "old status"]),
            _row("data-restore", offline=True)]
    selected = actor._selected_inventory(rows)
    assert [row["label"] for row in selected] == list(actor.CAPTURE_LABELS)
    assert [row["max_calls"] for row in selected] == [1, 1, 1, 1]
    assert selected[0]["offline_listener_object"] is True
    assert selected[-1]["request_argv"][-1] == actor.CALLER_STATUS_CODE
    assert sum(row["max_calls"] for row in selected) == 4


@pytest.mark.parametrize("damage", ["missing", "duplicate"])
def test_r6_install_plan_refuses_missing_or_ambiguous_capture_rows(damage):
    rows = [_row(label, offline=label == "current-offline-install") for label in actor.CAPTURE_LABELS]
    if damage == "missing":
        rows = rows[:-1]
    else:
        rows.append(dict(rows[0]))
    with pytest.raises(RuntimeError, match="missing or ambiguous"):
        actor._selected_inventory(rows)


def test_r6_caller_status_is_nonprompt_and_uses_r6_source_hashes():
    code = actor.CALLER_STATUS_CODE
    assert "MacOSAccessibilityObserver().status()" in code
    assert "AXIsProcessTrustedWithOptions" not in code
    assert "CGRequestScreenCaptureAccess" not in code
    assert "enumerate_windows(" not in code and "inspect_accessibility(" not in code
    assert "capture(" not in code and "window_tree_pixels_enumerated':False" in code
    lock = json.loads(actor.APP_LOCK.read_text())
    expected = {
        module: lock["members"][module.replace(".", "/") + ".py"]["sha256"]
        for module in actor.CALLER_SOURCE_HASHES
    }
    assert actor.CALLER_SOURCE_HASHES == expected


def test_r6_lifecycle_candidate_and_reader_are_additive_and_self_pinned():
    assert lifecycle_r6.SEALED_R6_HELPER.name == "w26_sealed_data_r6.py"
    assert "ACTOR_CANDIDATE_app6c.json" not in Path(lifecycle_r6.__file__).read_text()
    assert "CURRENT_APP_ROOT" not in Path(actor.__file__).read_text()
    source = lifecycle_r6._records()
    assert str(Path(lifecycle_r6.__file__).resolve()) in source
    assert str(lifecycle_r6.SEALED_R6_HELPER) in source
    assert data.CURRENT_APPLICATION_R6_LOCK.name == "w26_application_sources_175_r6.lock.json"


def test_r6_four_capture_plan_retains_venv_and_excludes_lifecycle_mutations():
    scope = runtime.activity_scope({"activity_kind": runtime.APP_LIFECYCLE})
    sandbox = actor._sandbox_profile(Path("/private/tmp/cvw26-example")).decode()
    assert scope["budget_seconds"] == 3600 and scope["cleanup_reserve_seconds"] == 600
    assert sum(row["max_calls"] for row in actor._selected_inventory([_row(label, offline=label == "current-offline-install",
                          argv=["/python", "-I", "-c", "code"] if label == "current-caller-desktop-metadata-status" else None)
                          for label in actor.CAPTURE_LABELS])) == 4
    assert actor._sandbox_profile(Path("/private/tmp/cvw26-example")) == actor._sandbox_profile(Path("/private/tmp/cvw26-example"))
    assert '(deny network*)' in sandbox and '(deny file-write*)' in sandbox
    assert str(Path("/private/tmp/cvw26-example/current-venv")) in sandbox
    assert str(Path("/private/tmp/cvw26-example/transition-work")) not in sandbox
    assert str(Path("/private/tmp/cvw26-example/uninstall-evidence")) not in sandbox
    assert all(term in actor._expected_plan({"prepared": {"path": "/private/tmp/cvw26-example/LIFECYCLE_INPUT_life.json"}},
        {"inventory": [_row(label, offline=label == "current-offline-install",
          argv=["/python", "-I", "-c", "code"] if label == "current-caller-desktop-metadata-status" else None) for label in actor.CAPTURE_LABELS]})["forbidden"]
        for term in ("upgrade", "rollback", "uninstall", "data transition", "COMSOL", "JVM", "GUI"))


def test_r6_actor_has_no_direct_subprocess_popen_framework():
    source = Path(actor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct_popen_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                          isinstance(node.func, ast.Attribute) and node.func.attr == "Popen" and
                          isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"]
    assert direct_popen_calls == []
    assert "lifecycle.LifecycleLaunch(" in source
    assert "release.offline_install(" in source
    assert "runtime.original.run" not in source


def test_r6_profile_derives_candidate_filename_from_app_once_binding():
    # _producer_records operates on authoritative app records; the full producer
    # verifier is tested with the shared current_product fixture in the paired
    # installed-profile test module.
    helper = _helpers()
    root = Path("/private/tmp") / ("cvw26-application-" + uuid.uuid4().hex[:8])
    root.mkdir(mode=0o700)
    helper.publish(root / "FIRST_RECORD.json", {"root_identity": {"device": root.stat().st_dev, "inode": root.stat().st_ino}})
    h = actor._helpers()
    candidate_name = "ACTOR_CANDIDATE_app6e.json"
    candidate = h.publish(root / candidate_name, {"status": "READY_APPLICATION_BUNDLE_MAIN_REVIEW_NOT_EXECUTION_APPROVED",
        "native_Popen": 0, "root_identity": {"device": root.stat().st_dev, "inode": root.stat().st_ino}})
    review = h.publish(root / "MAIN_SOURCE_REVIEW.json", {"status": "PASS_W26_APPLICATION_BUNDLE_SOURCE_FOR_EXACT_CANDIDATE",
        "candidate_sha256": candidate["sha256"]})
    binding = h.publish(root / "MAIN_ONCE_BINDING.json", {"status": "MAIN_APPROVED_ONCE_W26_APPLICATION_BUNDLE",
        "root": str(root), "root_identity": {"device": root.stat().st_dev, "inode": root.stat().st_ino},
        "candidate_filename": candidate_name, "candidate_sha256": candidate["sha256"], "nonce": "0123456789abcdef",
        "review_sha256": review["sha256"]})
    h.publish(root / "APPLICATION_BUNDLE_RESULT.json", {})
    once = h.publish(root / "UNIT_ONCE_GUARD.json", {"activity_kind": "CURRENT_APPLICATION_BUNDLE",
        "nonce": "0123456789abcdef", "binding_first_record": binding})
    try:
        records = data_r6._producer_records(root, data)
        assert records["candidate"][1] == candidate
        assert Path(records["candidate"][1]["path"]).name == candidate_name
        assert records["once"][1] == once

        value = json.loads((root / "MAIN_ONCE_BINDING.json").read_text())
        value["candidate_filename"] = "../ACTOR_CANDIDATE_app6e.json"
        (root / "MAIN_ONCE_BINDING.json").write_text(json.dumps(value, sort_keys=True))
        new_binding = h.raw_file(root / "MAIN_ONCE_BINDING.json")[1]
        once_value = json.loads((root / "UNIT_ONCE_GUARD.json").read_text())
        once_value["binding_first_record"] = new_binding
        (root / "UNIT_ONCE_GUARD.json").write_text(json.dumps(once_value, sort_keys=True))
        with pytest.raises(RuntimeError, match="candidate filename"):
            data_r6._producer_records(root, data)
    finally:
        shutil.rmtree(root)
