from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools import w25_current_r5_offline_install_actor as actor


def _row(label, *, offline=False, argv=None):
    return {"label": label, "request_argv": argv or ["/python", "-c", label], "cap": 600 if offline else 180,
            "max_calls": 3, "cwd": "/private/tmp/cvw26-example", "offline_listener_object": offline}


def test_w25_install_plan_is_one_offline_install_and_three_readbacks_only():
    rows = [
        _row("old-package-rollback", offline=True),
        _row("current-offline-install", offline=True),
        _row("current-package_fingerprint"),
        _row("current-distribution_fingerprint"),
        _row("current-caller-desktop-metadata-status", argv=["/python", "-I", "-c", "old status"]),
        _row("data-restore", offline=True),
    ]
    selected = actor._selected_inventory(rows)
    assert [row["label"] for row in selected] == list(actor.CAPTURE_LABELS)
    assert [row["max_calls"] for row in selected] == [1, 1, 1, 1]
    assert selected[0]["offline_listener_object"] is True
    assert selected[-1]["request_argv"][-1] == actor.CALLER_STATUS_CODE
    assert all("rollback" not in row["label"] and "uninstall" not in row["label"] for row in selected)


@pytest.mark.parametrize("damage", ["missing", "duplicate"])
def test_w25_install_plan_refuses_missing_or_ambiguous_original_capture_rows(damage):
    rows = [_row(label, offline=label == "current-offline-install") for label in actor.CAPTURE_LABELS]
    if damage == "missing":
        rows = rows[:-1]
    else:
        rows.append(dict(rows[0]))
    with pytest.raises(RuntimeError, match="missing or ambiguous"):
        actor._selected_inventory(rows)


def test_caller_status_code_is_nonprompt_and_does_not_enumerate_windows_or_pixels():
    code = actor.CALLER_STATUS_CODE
    assert "MacOSAccessibilityObserver().status()" in code
    assert "AXIsProcessTrustedWithOptions" not in code
    assert "CGRequestScreenCaptureAccess" not in code
    assert "enumerate_windows(" not in code
    assert "inspect_accessibility(" not in code
    assert "capture(" not in code
    assert "window_tree_pixels_enumerated':False" in code
    assert len(actor.CALLER_SOURCE_HASHES) == 4


def test_install_outer_sandbox_denies_network_and_writes_only_fresh_install_paths(tmp_path):
    raw = actor._sandbox_profile(tmp_path).decode()
    assert raw.startswith("(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n")
    assert raw.index("(allow default)") < raw.index("(deny network*)") < raw.index("(deny file-write*)")
    assert raw.count("(deny network*)") == 1
    assert raw.count("(deny file-write*)") == 1
    assert str(tmp_path / "current-venv") in raw
    assert str(tmp_path / "current-install-evidence") in raw
    assert str(tmp_path / "transition-work") not in raw
    assert str(tmp_path / "uninstall-evidence") not in raw


def test_install_actor_does_not_create_its_own_process_framework():
    source = Path(actor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct_popen_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                          isinstance(node.func, ast.Attribute) and node.func.attr == "Popen" and
                          isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"]
    assert direct_popen_calls == []
    assert "lifecycle.LifecycleLaunch(" in source
    assert "release.offline_install(" in source
    assert "runtime.original.run" not in source

