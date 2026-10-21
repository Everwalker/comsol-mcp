from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import w25_macos_owned_desktop_actor_r8 as actor
from comsol_mcp._desktop_macos import TaskOwnedMacOSWindowObserver
from comsol_mcp._desktop_service import DesktopOperationError, WindowIdentity


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="task-owned process/session observation is macOS-specific")

OWNER_PID = "owner_pid"
WINDOW_ID = "window_id"


class _AccessTrapRow:
    def __init__(self, pid: int, window_id: int, *, forbidden=()):
        self.values = {OWNER_PID: pid, WINDOW_ID: window_id}
        self.forbidden = set(forbidden)
        self.reads: list[str] = []

    def get(self, key, default=None):
        self.reads.append(key)
        if key in self.forbidden:
            raise AssertionError(f"forbidden Quartz property read: {key}")
        return self.values.get(key, default)


class _Quartz:
    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 2
    kCGNullWindowID = 0
    kCGWindowOwnerPID = OWNER_PID
    kCGWindowNumber = WINDOW_ID

    def __init__(self, snapshots, *, on_list=None):
        self.snapshots = list(snapshots)
        self.calls = 0
        self.on_list = on_list

    def CGWindowListCopyWindowInfo(self, _options, _window_id):
        self.calls += 1
        if self.on_list is not None:
            self.on_list(self.calls)
        if not self.snapshots:
            return []
        return self.snapshots.pop(0)


class _Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def _allow_accessibility():
    return {"permission_status": "AVAILABLE", "accessibility_trusted": True, "metadata_status": "AVAILABLE"}


def _spawn_original_root():
    return subprocess.Popen(
        ["/bin/sleep", "90"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _finish_original_root(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _observer(process, monkeypatch, snapshots, *, on_list=None):
    rows = actor._snapshot_owned_processes(process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0")
    observer = TaskOwnedMacOSWindowObserver(rows, original_root=process)
    quartz = _Quartz(snapshots, on_list=on_list)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    return observer, rows, quartz


def _owned(pid: int, native_window_id: int):
    return _AccessTrapRow(pid, native_window_id)


def _foreign(pid: int):
    return _AccessTrapRow(pid, 901, forbidden={WINDOW_ID, "window_name", "owner_name", "bounds"})


def _preax_attempts(code: int = -25204, attribute: str = "AXWindows", scope: str = "application_window_map"):
    retry_status = "RETRY_AX_WINDOWS_READINESS" if attribute == "AXWindows" else "RETRY_AX_WINDOW_GEOMETRY_ASSOCIATION"
    return [
        {"attempt": 1, "status": retry_status,
         "diagnostics": {"kind": "AX_ERROR", "ax_error_code": code,
                         "ax_error_name": "cannot_complete" if code == -25204 else "no_value",
                         "attribute": attribute, "scope": scope, "readiness_transient": code in {-25204, -25212}}},
        {"attempt": 2, "status": "FAIL_TERMINAL", "diagnostics": {"error_code": "WINDOW_IDENTITY_UNKNOWN"}},
    ]


def _identity(pid: int, window_id: str = "CGWindow:42", *, birth="1790000000.123456", uid=None, sid=None):
    real_uid = os.getuid() if uid is None else uid
    session_id = pid if sid is None else sid
    return WindowIdentity("macos", window_id, pid, birth, f"uid:{real_uid}", f"process-session:{session_id}", "6.4.0")


def _mapping_keys(value):
    if isinstance(value, dict):
        yield from value
        for nested in value.values():
            yield from _mapping_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _mapping_keys(nested)


def test_preax_unknown_recovers_only_with_zero_then_one_new_same_owner_id_and_persists_both_protocols(tmp_path, monkeypatch):
    process = _spawn_original_root()
    clock = _Clock()
    try:
        calls = []
        foreign_initial = _foreign(process.pid + 10000)
        foreign_zero = _foreign(process.pid + 10001)
        foreign_recovered = _foreign(process.pid + 10002)
        initial_owned = _owned(process.pid, 42)
        recovered_owned = _owned(process.pid, 43)

        def on_list(number):
            calls.append(number)
            if number == 2:
                clock.value += 40

        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[initial_owned, foreign_initial], [foreign_zero],
             [recovered_owned, foreign_recovered]], on_list=on_list)
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        candidate_sha = actor._sha(b"R8-source-control-candidate")
        binding_sha = actor._sha(b"R8-source-control-binding")
        recovered = actor._reselect_after_exact_preax_identity_loss(
            tmp_path, observer, process, original, candidate_sha256=candidate_sha,
            binding_sha256=binding_sha, reselection_ordinal=1, readiness_deadline=60,
            deadline=600, trigger_error=DesktopOperationError(
                "WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
            attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
            permission_reader=_allow_accessibility, clock=clock.now, sleeper=clock.sleep,
        )
        assert recovered["identity"] == _identity(process.pid, "CGWindow:43", birth=original.process_birth)
        assert recovered["sample_count"] == 2
        assert clock.value == 41.0  # the shared 60-second readiness deadline was not reset
        assert [row["status"] for row in recovered["receipts"]] == ["ZERO_OWNED_WINDOWS", "ONE_NEW_NATIVE_ID_SAME_OWNER"]
        assert attempts[-2]["revalidation_status"] == "ZERO_OWNED_WINDOWS"
        assert attempts[-1]["revalidation_status"] == "ONE_NEW_NATIVE_ID_SAME_OWNER"
        assert quartz.calls == 3  # initial owned identity and two real observer revalidations
        assert foreign_initial.reads == foreign_zero.reads == foreign_recovered.reads == [OWNER_PID]
        assert initial_owned.reads == recovered_owned.reads == [OWNER_PID, WINDOW_ID]

        receipt0 = json.loads(Path(recovered["receipts"][0]["receipt"]["path"]).read_text())
        receipt1 = json.loads(Path(recovered["receipts"][1]["receipt"]["path"]).read_text())
        for receipt in (receipt0, receipt1):
            assert set(receipt) == {"schema", "candidate_sha256", "binding_sha256", "receipt", "privacy_scope"}
            assert set(_mapping_keys(receipt)).isdisjoint({"window_name", "owner_name", "title", "bounds"})
            assert receipt["receipt"]["original_popen_proof"]["process_id"] == process.pid
            assert receipt["receipt"]["window_owner_proof"]["process_birth"] == original.process_birth
        assert receipt0["receipt"]["observed_owned_windows"] == []
        assert receipt1["receipt"]["observed_owned_windows"][0]["native_window_id"] == "CGWindow:43"

        reselection = json.loads(Path(recovered["reselection_record"]["path"]).read_text())
        assert reselection["previous_identity"]["native_window_id"] == "CGWindow:42"
        assert reselection["identity"]["native_window_id"] == "CGWindow:43"
        assert reselection["same_original_root_proof"]["same_original_popen_object"] is True
        assert reselection["same_original_root_proof"]["window_owner_birth"] == original.process_birth
        assert all(row.reads == [OWNER_PID, WINDOW_ID] for row in [])
    finally:
        _finish_original_root(process)


def test_shared_deadline_expires_after_zero_owned_rows_without_resetting_or_reselecting(tmp_path, monkeypatch):
    process = _spawn_original_root()
    clock = _Clock()
    try:
        foreign_zero_1 = _foreign(process.pid + 10003)
        foreign_zero_2 = _foreign(process.pid + 10004)

        def on_list(number):
            if number == 2:
                clock.value += 40
            elif number == 3:
                clock.value += 20

        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[_owned(process.pid, 42)], [foreign_zero_1], [foreign_zero_2]], on_list=on_list)
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate"), binding_sha256=actor._sha(b"binding"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=clock.now, sleeper=clock.sleep,
            )
        assert caught.value.code == "PREAX_RESELECTION_READINESS_TIMEOUT"
        assert caught.value.diagnostics["sample_count"] == 2
        assert clock.value == 61
        assert quartz.calls == 3
        receipt_paths = sorted(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))
        assert len(receipt_paths) == 2
        assert all(json.loads(path.read_text())["receipt"]["status"] == "ZERO_OWNED_WINDOWS" for path in receipt_paths)
        assert foreign_zero_1.reads == foreign_zero_2.reads == [OWNER_PID]
    finally:
        _finish_original_root(process)


def test_new_owned_id_observed_after_shared_deadline_is_persisted_but_never_selected(tmp_path, monkeypatch):
    process = _spawn_original_root()
    clock = _Clock()
    try:
        foreign_zero = _foreign(process.pid + 10006)

        def on_list(number):
            if number == 2:
                clock.value += 40
            elif number == 3:
                clock.value += 20

        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[_owned(process.pid, 42)], [foreign_zero], [_owned(process.pid, 43)]], on_list=on_list)
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate-late"), binding_sha256=actor._sha(b"binding-late"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=clock.now, sleeper=clock.sleep,
            )
        assert caught.value.code == "PREAX_RESELECTION_READINESS_TIMEOUT"
        assert caught.value.diagnostics["revalidation_status"] == "ONE_NEW_NATIVE_ID_SAME_OWNER"
        assert caught.value.diagnostics["sample_count"] == 2
        assert clock.value == 61
        assert quartz.calls == 3
        assert foreign_zero.reads == [OWNER_PID]
        records = [json.loads(path.read_text())["receipt"] for path in sorted(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))]
        assert [row["status"] for row in records] == ["ZERO_OWNED_WINDOWS", "ONE_NEW_NATIVE_ID_SAME_OWNER"]
        assert records[1]["observed_owned_windows"][0]["native_window_id"] == "CGWindow:43"
        assert not list(tmp_path.glob("OWNED_WINDOW_RESELECTION_*.json"))
    finally:
        _finish_original_root(process)


def test_multiple_owned_windows_are_persisted_as_terminal_and_never_reselected(tmp_path, monkeypatch):
    process = _spawn_original_root()
    try:
        foreign = _foreign(process.pid + 10005)
        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[_owned(process.pid, 42)], [_owned(process.pid, 43), _owned(process.pid, 44), foreign]])
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate"), binding_sha256=actor._sha(b"binding"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=lambda: 0, sleeper=lambda _seconds: None,
            )
        assert caught.value.code == "PREAX_RESELECTION_REFUSED"
        assert caught.value.diagnostics["revalidation_status"] == "MULTIPLE_OWNED_WINDOWS_TERMINAL"
        assert quartz.calls == 2
        receipt = json.loads(next(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json")).read_text())
        assert receipt["receipt"]["observed_owned_windows"] == []
        assert receipt["receipt"]["status"] == "MULTIPLE_OWNED_WINDOWS_TERMINAL"
        assert foreign.reads == [OWNER_PID]
    finally:
        _finish_original_root(process)


def test_changed_fresh_process_tree_is_terminal_before_quartz_revalidation(tmp_path, monkeypatch):
    process = _spawn_original_root()
    try:
        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[_owned(process.pid, 42)], [_owned(process.pid, 43)]])
        original = observer.enumerate_windows()[0].identity
        real_snapshot = actor._snapshot_owned_processes
        calls = 0

        def drift_once(root_process, *, bundle_identifier, comsol_version):
            nonlocal calls
            calls += 1
            rows = real_snapshot(root_process, bundle_identifier=bundle_identifier, comsol_version=comsol_version)
            if calls == 1:
                rows = [replace(row, command_sha256="f" * 64) if row.process_id == process.pid else row for row in rows]
            return rows

        monkeypatch.setattr(actor, "_snapshot_owned_processes", drift_once)
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate-tree-drift"), binding_sha256=actor._sha(b"binding-tree-drift"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=lambda: 0, sleeper=lambda _seconds: None,
            )
        assert caught.value.code == "PREAX_OWNED_PROCESS_TREE_CHANGED"
        assert calls == 1
        assert quartz.calls == 1  # no fresh window inventory after the process proof failed
        assert not list(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))
    finally:
        _finish_original_root(process)


@pytest.mark.parametrize("damage, expected", [
    ("new_pid", "WINDOW_OWNER_PID_OR_BIRTH_CHANGED_TERMINAL"),
    ("reused_birth", "WINDOW_OWNER_PID_OR_BIRTH_CHANGED_TERMINAL"),
    ("old_tuple", "OLD_NATIVE_IDENTITY_TUPLE_CHANGED_TERMINAL"),
    ("old_id_still_present", "OLD_NATIVE_WINDOW_STILL_PRESENT_TERMINAL"),
    ("owner_context", "NEW_NATIVE_WINDOW_OWNER_CONTEXT_CHANGED_TERMINAL"),
    ("multiple", "MULTIPLE_OWNED_WINDOWS_TERMINAL"),
])
def test_preax_window_tuple_classifier_refuses_foreign_reuse_and_old_id_changes(damage, expected):
    previous = _identity(123, "CGWindow:42", birth="birth:1", uid=501, sid=123)
    previous_tuple = actor.OwnedWindowIdentityTuple.from_identity(previous)
    new = actor.OwnedWindowIdentityTuple("macos", "CGWindow:43", 123, "birth:1", "uid:501", "process-session:123", "6.4.0")
    if damage == "new_pid":
        new = actor.OwnedWindowIdentityTuple("macos", "CGWindow:43", 999, "birth:1", "uid:501", "process-session:123", "6.4.0")
    elif damage == "reused_birth":
        new = actor.OwnedWindowIdentityTuple("macos", "CGWindow:43", 123, "birth:reused", "uid:501", "process-session:123", "6.4.0")
    elif damage == "old_tuple":
        new = actor.OwnedWindowIdentityTuple("macos", "CGWindow:42", 123, "birth:1", "uid:501", "process-session:999", "6.4.0")
    elif damage == "old_id_still_present":
        new = previous_tuple
    elif damage == "owner_context":
        new = actor.OwnedWindowIdentityTuple("macos", "CGWindow:43", 123, "birth:1", "uid:502", "process-session:123", "6.4.0")
    elif damage == "multiple":
        assert actor._classify_preax_owned_windows(previous, (new, previous_tuple)) == expected
        return
    assert actor._classify_preax_owned_windows(previous, (new,)) == expected


def test_preax_gate_requires_exact_error_literal_and_last_windows_transient_only():
    attempts = _preax_attempts()
    exact = DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL)
    assert actor._is_exact_pre_ax_identity_loss_after_last_windows_retry(exact, attempts, 0)
    assert not actor._is_exact_pre_ax_identity_loss_after_last_windows_retry(
        DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "generic window unknown"), attempts, 0)
    assert not actor._is_exact_pre_ax_identity_loss_after_last_windows_retry(
        DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL,
                              diagnostics={"kind": "unexpected"}), attempts, 0)
    ax_size = _preax_attempts(attribute="AXSize", scope="window_association")
    assert not actor._is_exact_pre_ax_identity_loss_after_last_windows_retry(exact, ax_size, 0)
    wrong_code = _preax_attempts(code=-25205)
    assert not actor._is_exact_pre_ax_identity_loss_after_last_windows_retry(exact, wrong_code, 0)


def test_accessibility_tcc_status_is_nonprompt_and_terminal_when_not_available():
    with pytest.raises(DesktopOperationError) as caught:
        actor._preax_accessibility_status(lambda: {
            "permission_status": "BLOCKED_PERMISSION", "accessibility_trusted": False,
            "metadata_status": "AVAILABLE", "prompt_requested": False,
        })
    assert caught.value.code == "PREAX_ACCESSIBILITY_PERMISSION_UNAVAILABLE"
    assert caught.value.diagnostics == {"permission_status": {
        "permission_status": "BLOCKED_PERMISSION", "accessibility_trusted": False,
        "metadata_status": "AVAILABLE"}}
