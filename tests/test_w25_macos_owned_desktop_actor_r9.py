from __future__ import annotations

import json
import ast
import importlib.util
import os
import subprocess
import sys
import threading
import textwrap
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

REPOSITORY = Path(os.environ.get("W25_R9_REPOSITORY_ROOT", Path(__file__).resolve().parents[1])).resolve()
SCRATCH_ROOT = Path(os.environ["W25_R9_SCRATCH_ROOT"]).resolve() if os.environ.get("W25_R9_SCRATCH_ROOT") else None
if SCRATCH_ROOT is None:
    from tools import w25_macos_owned_desktop_actor_r9 as actor
else:
    _ACTOR_PATH = SCRATCH_ROOT / "tools/w25_macos_owned_desktop_actor_r9.py"
    _ACTOR_SPEC = importlib.util.spec_from_file_location("w25_macos_owned_desktop_actor_r9_candidate", _ACTOR_PATH)
    assert _ACTOR_SPEC is not None and _ACTOR_SPEC.loader is not None
    actor = importlib.util.module_from_spec(_ACTOR_SPEC)
    sys.modules[_ACTOR_SPEC.name] = actor
    _ACTOR_SPEC.loader.exec_module(actor)
    actor.REPOSITORY = REPOSITORY
    actor.STATE = REPOSITORY / "docs/full_project_continuation_v3/state"
from comsol_mcp._desktop_macos import MacOSFrameworkGateway, TaskOwnedMacOSWindowObserver
from comsol_mcp._desktop_service import DesktopOperationError, WindowIdentity


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="task-owned process/session observation is macOS-specific")

OWNER_PID = "owner_pid"
WINDOW_ID = "window_id"


def test_generated_plan_binds_shared_bounded_owned_window_transition_contract():
    ownership, stop_conditions = actor._bounded_owned_window_plan_contract()
    collector = ownership["owned_window_collector"]
    transition = ownership["owned_window_transition"]
    assert collector == {
        "api": "shared internal TaskOwnedMacOSWindowObserver._collect_owned_window_observations",
        "max_owned_rows_per_inventory": 16,
        "exact_original_popen_and_full_process_tree_revalidated_before_and_after_each_inventory": True,
        "foreign_quartz_fields": "read OwnerPID only, then discard",
    }
    assert transition["maximum_samples"] == 60
    assert transition["sample_interval_seconds"] == 1.0
    assert transition["fixed_shared_deadline_seconds"] == 60
    assert transition["deadline_is_not_reset_between_readiness_and_transition"] is True
    assert transition["global_max_total_refresh_or_reselection_actions"] == 2
    assert "do not inspect AX nodes or select a target while ambiguous" in transition["zero_or_multiple_owned_rows"]
    assert "old native ID is absent" in transition["one_unique_new_native_id"]
    assert "same owner PID, birth, UID, session, version, and process-tree identity" in transition["one_unique_new_native_id"]
    assert transition["initial_ambiguity_without_approved_trigger"] == "terminal without AX observation or target selection"
    assert any("remains unresolved at the fixed shared deadline" in item for item in stop_conditions)
    assert not any(item in {"process or window identity ambiguity", "more than one visible owned window"} for item in stop_conditions)

    macos_source = (SCRATCH_ROOT / "overlay/repository/comsol_mcp/_desktop_macos.py"
                    if SCRATCH_ROOT is not None else REPOSITORY / "comsol_mcp/_desktop_macos.py")
    tree = ast.parse(macos_source.read_text(encoding="utf-8"), filename=str(macos_source))
    assignments = {
        target.id: node.value.value
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant)
    }
    assert assignments["MAX_TASK_OWNED_QUARTZ_WINDOWS_PER_INVENTORY"] == collector["max_owned_rows_per_inventory"]


def test_r3_main_once_binding_distinguishes_total_active_and_cleanup_budgets(tmp_path):
    assert actor.BOUNDED_RUNTIME_SECONDS == 600
    assert actor.ACTIVE_RUNTIME_SECONDS == 540
    assert actor.CLEANUP_SECONDS == 60
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"command": {"argv_sha256": "a" * 64},
                                    "runner_interpreter_required": str(Path(sys.executable).resolve())}),
                         encoding="utf-8")
    candidate = {
        "activity_kind": "W25_MACOS_OWNED_DESKTOP_METADATA",
        "candidate_sha256": "b" * 64,
        "root_identity": {"device": 1, "inode": 2},
        "plan": {"path": str(plan_path), "sha256": "c" * 64},
        "source": {"sha256": "d" * 64}, "resume_sha256": "e" * 64,
    }
    candidate_path = tmp_path / "ACTOR_CANDIDATE_macos1.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    (tmp_path / "MAIN_SOURCE_REVIEW.json").write_text("{}", encoding="utf-8")
    binding = actor.build_expected_binding(
        {"path": str(candidate_path), "sha256": "b" * 64},
        {"path": str(tmp_path / "W25_LANE_CHECK_CURRENT.json"), "sha256": "f" * 64},
    )
    assert binding["max_runtime_seconds"] == 600
    assert binding["max_active_runtime_seconds"] == 540
    assert binding["cleanup_reserve_seconds"] == 60


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


def _restrict_psutil_to_direct_fixture_roots(monkeypatch, *root_processes, capture_actor_owned=False):
    """Keep synthetic Popen tests scoped to exact fixture roots, without host-wide PID enumeration."""
    import os
    import psutil

    pytest_pid = os.getpid()
    root_pids = {process.pid for process in root_processes}

    def exact_direct_root_children(owner, recursive=False):
        if owner.ppid() != pytest_pid:
            raise AssertionError("fixture attempted to enumerate a process outside its direct Popen roots")
        return []

    def exact_fixture_process_iter(attrs=None):
        return [SimpleNamespace(info={"pid": pid}) for pid in sorted(root_pids) if psutil.pid_exists(pid)]

    monkeypatch.setattr(psutil.Process, "children", exact_direct_root_children)
    monkeypatch.setattr(psutil, "process_iter", exact_fixture_process_iter)
    if capture_actor_owned:
        original_popen = actor.W25OriginalProcessOwner.popen

        def capture_owned_root(owner, *args, **kwargs):
            process = original_popen(owner, *args, **kwargs)
            if type(process) is subprocess.Popen:
                root_pids.add(process.pid)
            return process

        monkeypatch.setattr(actor.W25OriginalProcessOwner, "popen", capture_owned_root)


def _finish_original_root(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _observer(process, monkeypatch, snapshots, *, on_list=None):
    import psutil

    # These legacy observer fixtures use a bare /bin/sleep root with no child
    # process. Keep the test local to its owned Popen PID instead of asking
    # psutil to enumerate every process on the host.
    monkeypatch.setattr(psutil.Process, "children", lambda self, recursive=False: [])
    rows = actor._snapshot_owned_processes(process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0")
    observer = TaskOwnedMacOSWindowObserver(rows, original_root=process)
    quartz = _Quartz(snapshots, on_list=on_list)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    return observer, rows, quartz


def _spawn_owned_sleep_root(child_count=1, *, monkeypatch=None):
    """A real Popen root owns sleep children; the root itself always TERM+waits them before exit."""
    source = textwrap.dedent(r"""
        import json, signal, subprocess, sys
        children = []
        def reap_children():
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                child.wait()
        def stop(_signum=None, _frame=None):
            reap_children()
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, stop)
        children = [subprocess.Popen(["/bin/sleep", "120"]) for _ in range(int(sys.argv[1]))]
        print(json.dumps([child.pid for child in children]), flush=True)
        for command in sys.stdin:
            if command.strip() == "exit_children":
                reap_children()
                print("CHILDREN_REAPED", flush=True)
            if command.strip() == "stop":
                stop()
    """)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", source, str(child_count)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True, bufsize=1,
    )
    try:
        child_pids = json.loads(process.stdout.readline())
        assert len(child_pids) == child_count
        if monkeypatch is not None:
            import psutil

            def exact_fixture_children(owner, recursive=False):
                if owner.pid != process.pid:
                    return []
                rows = []
                for pid in child_pids:
                    try:
                        child = psutil.Process(pid)
                        if child.ppid() == process.pid:
                            rows.append(child)
                    except psutil.NoSuchProcess:
                        continue
                return rows

            # Avoid psutil's system-wide PID listing in the test sandbox; the
            # fixture reads only its exact retained root and announced children.
            monkeypatch.setattr(psutil.Process, "children", exact_fixture_children)
        return process, child_pids
    except BaseException:
        if process.poll() is None:
            process.terminate()  # The retained root's signal handler TERM+waits every owned child.
            process.wait(timeout=5)
        raise


def _stop_owned_sleep_root(process):
    if process.poll() is None:
        try:
            assert process.stdin is not None
            process.stdin.write("stop\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            if process.poll() is None:
                process.terminate()  # Its handler TERM+waits every child before exiting.
        process.wait(timeout=5)


def _synthetic_no_such_error(message, pid):
    try:
        import psutil
        raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", message, safe_retry=True) from psutil.NoSuchProcess(pid)
    except DesktopOperationError as exc:
        return exc


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
            assert set(receipt) == {"schema", "candidate_sha256", "binding_sha256", "receipt",
                                    "trigger_kind", "trigger_error", "privacy_scope"}
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


def test_multiple_owned_windows_to_one_new_id_waits_records_and_proves_old_id_absent(tmp_path, monkeypatch):
    process = _spawn_original_root()
    clock = _Clock()
    try:
        foreign = _foreign(process.pid + 10005)
        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[_owned(process.pid, 42)],
             [_owned(process.pid, 42), _owned(process.pid, 43), foreign],
             [_owned(process.pid, 43)]])
        original = observer.enumerate_windows()[0].identity
        last_ax_error = {"kind": "AX_ERROR", "ax_error_code": -25204,
                         "ax_error_name": "cannot_complete", "attribute": "AXWindows",
                         "scope": "application_window_map", "readiness_transient": True}
        wrapped = DesktopOperationError(
            "WINDOW_IDENTITY_REVALIDATION_FAILED", "Quartz enumeration refused an ambiguous identity",
            diagnostics={"scope": "readiness_revalidation", "last_ax_error": last_ax_error,
                         "revalidation_error_type": "DesktopOperationError"},
        )
        wrapped.__cause__ = DesktopOperationError(
            "WINDOW_IDENTITY_UNKNOWN", "task-owned COMSOL process tree has multiple visible windows",
        )
        recovered = actor._bounded_owned_window_transition(
            tmp_path, observer, process, original,
            candidate_sha256=actor._sha(b"candidate-multiple-new"),
            binding_sha256=actor._sha(b"binding-multiple-new"), action_ordinal=1,
            readiness_deadline=60, deadline=600, trigger_kind="AXWINDOWS_TRANSIENT",
            trigger_error=wrapped, attempts=[], attempt_start_index=0, receipt_ordinal_start=0,
            permission_reader=_allow_accessibility, clock=clock.now, sleeper=clock.sleep,
        )
        assert recovered["kind"] == "WINDOW_RESELECTION"
        assert recovered["identity"].native_window_id == "CGWindow:43"
        assert recovered["sample_count"] == 2
        assert [row["status"] for row in recovered["receipts"]] == [
            "MULTIPLE_OWNED_WINDOWS_WAITING", "ONE_NEW_NATIVE_ID_SAME_OWNER",
        ]
        assert clock.value == 1.0
        assert quartz.calls == 3
        assert foreign.reads == [OWNER_PID]
        receipts = [json.loads(Path(item["receipt"]["path"]).read_text()) for item in recovered["receipts"]]
        assert [len(row["receipt"]["observed_owned_windows"]) for row in receipts] == [2, 1]
        assert receipts[0]["trigger_error"]["direct_cause"]["text"] == "task-owned COMSOL process tree has multiple visible windows"
        reselection = json.loads(Path(recovered["reselection_record"]["path"]).read_text())
        assert reselection["old_native_id_absent_in_unique_inventory"] is True
        assert reselection["identity"]["native_window_id"] == "CGWindow:43"
    finally:
        _finish_original_root(process)


def test_multiple_owned_windows_to_same_id_refreshes_only_after_ambiguity_clears(tmp_path, monkeypatch):
    process = _spawn_original_root()
    clock = _Clock()
    try:
        foreign = _foreign(process.pid + 10007)
        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[_owned(process.pid, 42)],
             [_owned(process.pid, 42), _owned(process.pid, 43), foreign],
             [_owned(process.pid, 42)]])
        original = observer.enumerate_windows()[0].identity
        diagnostics = {"scope": "readiness_revalidation", "last_ax_error": {
            "kind": "AX_ERROR", "ax_error_code": -25204,
            "ax_error_name": "cannot_complete", "attribute": "AXWindows",
            "scope": "application_window_map", "readiness_transient": True}}
        error = DesktopOperationError("WINDOW_IDENTITY_CHANGED", "transient AXWindows identity mismatch",
                                      diagnostics=diagnostics)
        refreshed = actor._bounded_owned_window_transition(
            tmp_path, observer, process, original,
            candidate_sha256=actor._sha(b"candidate-multiple-same"), binding_sha256=actor._sha(b"binding-multiple-same"),
            action_ordinal=1, readiness_deadline=60, deadline=600, trigger_kind="AXWINDOWS_TRANSIENT",
            trigger_error=error, attempts=[], attempt_start_index=0, receipt_ordinal_start=0,
            permission_reader=_allow_accessibility, clock=clock.now, sleeper=clock.sleep,
        )
        assert refreshed["kind"] == "AXREF_REFRESH"
        assert refreshed["identity"] == original
        assert refreshed["sample_count"] == 2
        assert [row["status"] for row in refreshed["receipts"]] == [
            "MULTIPLE_OWNED_WINDOWS_WAITING", "SAME_NATIVE_ID_SAME_OWNER",
        ]
        assert clock.value == 1.0
        assert quartz.calls == 3
        assert foreign.reads == [OWNER_PID]
        record = json.loads(Path(refreshed["refresh_record"]["path"]).read_text())
        assert record["same_quartz_identity"] is True
        assert record["owned_window_count"] == 1
        assert len(record["transition_sample_receipts"]) == 2
        assert not list(tmp_path.glob("OWNED_WINDOW_RESELECTION_*.json"))
    finally:
        _finish_original_root(process)


def test_persistent_multiple_owned_windows_timeout_after_sixty_one_second_spaced_samples(tmp_path, monkeypatch):
    process = _spawn_original_root()
    clock = _Clock()
    try:
        multi_rows = [_owned(process.pid, 43), _owned(process.pid, 44)]
        snapshots = [[_owned(process.pid, 42)]] + [multi_rows for _ in range(60)]
        observer, _frozen, quartz = _observer(process, monkeypatch, snapshots)
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate-persistent-multiple"),
                binding_sha256=actor._sha(b"binding-persistent-multiple"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=clock.now, sleeper=clock.sleep,
            )
        assert caught.value.code == "PREAX_RESELECTION_READINESS_TIMEOUT"
        assert caught.value.diagnostics["sample_count"] == 60
        assert clock.value == 59.0
        assert quartz.calls == 61
        receipt_paths = sorted(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))
        assert len(receipt_paths) == 60
        assert all(json.loads(path.read_text())["receipt"]["status"] == "MULTIPLE_OWNED_WINDOWS_WAITING"
                   for path in receipt_paths)
    finally:
        _finish_original_root(process)


def test_bounded_transition_stops_before_quartz_when_accessibility_permission_is_revoked(tmp_path, monkeypatch):
    process = _spawn_original_root()
    try:
        observer, _frozen, quartz = _observer(process, monkeypatch,
            [[_owned(process.pid, 42)], [_owned(process.pid, 43)]])
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate-revoked"), binding_sha256=actor._sha(b"binding-revoked"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=lambda: {"permission_status": "BLOCKED_PERMISSION",
                                           "accessibility_trusted": False,
                                           "metadata_status": "AVAILABLE"},
                clock=lambda: 0.0, sleeper=lambda _seconds: None,
            )
        assert caught.value.code == "PREAX_ACCESSIBILITY_PERMISSION_UNAVAILABLE"
        assert quartz.calls == 1
        assert not list(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))
    finally:
        _finish_original_root(process)


def test_bounded_transition_rejects_a_window_owner_pid_outside_the_frozen_tree(tmp_path, monkeypatch):
    # This production collector fixture runs a Python root plus /bin/sleep child;
    # the COMSOL bundle/version fields are test metadata only.
    process, child_pids = _spawn_owned_sleep_root(monkeypatch=monkeypatch)
    try:
        rows = actor._snapshot_owned_processes(process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0")
        observer = TaskOwnedMacOSWindowObserver(rows, original_root=process)
        _child = child_pids[0]
        quartz = _Quartz([[_owned(process.pid, 42)], [_owned(_child, 43)]])
        monkeypatch.setitem(sys.modules, "Quartz", quartz)
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate-owner-change"),
                binding_sha256=actor._sha(b"binding-owner-change"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=lambda: 0.0, sleeper=lambda _seconds: None,
            )
        assert caught.value.code == "PREAX_OWNED_WINDOW_OWNER_CHANGED"
        assert quartz.calls == 2
        assert not list(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))
    finally:
        _stop_owned_sleep_root(process)


def test_bounded_transition_rejects_original_root_termination_during_inventory(tmp_path, monkeypatch):
    process = _spawn_original_root()
    try:
        def terminate_on_transition(number):
            if number == 2 and process.poll() is None:
                process.terminate()

        observer, _frozen, quartz = _observer(
            process, monkeypatch, [[_owned(process.pid, 42)], [_owned(process.pid, 43)]],
            on_list=terminate_on_transition,
        )
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate-root-termination"),
                binding_sha256=actor._sha(b"binding-root-termination"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=lambda: 0.0, sleeper=lambda _seconds: None,
            )
        assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
        assert quartz.calls == 2
        assert not list(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))
    finally:
        _finish_original_root(process)


@pytest.mark.parametrize("snapshot, message", [
    ([42, 42], "ambiguous"),
    ([*range(1, 18)], "row cap"),
])
def test_bounded_transition_refuses_duplicate_or_overflow_from_shared_collector(tmp_path, monkeypatch,
                                                                                snapshot, message):
    process = _spawn_original_root()
    try:
        initial = [_owned(process.pid, 41)]
        transition_rows = [_owned(process.pid, index) for index in snapshot]
        observer, _frozen, quartz = _observer(process, monkeypatch, [initial, transition_rows])
        original = observer.enumerate_windows()[0].identity
        attempts = _preax_attempts()
        with pytest.raises(DesktopOperationError, match=message) as caught:
            actor._reselect_after_exact_preax_identity_loss(
                tmp_path, observer, process, original,
                candidate_sha256=actor._sha(b"candidate-malformed-inventory"),
                binding_sha256=actor._sha(b"binding-malformed-inventory"),
                reselection_ordinal=1, readiness_deadline=60, deadline=600,
                trigger_error=DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", actor.PREAX_IDENTITY_LOSS_LITERAL),
                attempts=attempts, attempt_start_index=0, receipt_ordinal_start=0,
                permission_reader=_allow_accessibility, clock=lambda: 0.0, sleeper=lambda _seconds: None,
            )
        assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
        assert quartz.calls == 2
        assert not list(tmp_path.glob("OWNED_WINDOW_PREAX_REVALIDATION_*.json"))
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
    ("old_id_still_present", "SAME_NATIVE_ID_SAME_OWNER"),
    ("owner_context", "NEW_NATIVE_WINDOW_OWNER_CONTEXT_CHANGED_TERMINAL"),
    ("multiple", "MULTIPLE_OWNED_WINDOWS_WAITING"),
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


def test_transient_axwindows_gate_accepts_only_exact_public_multiple_window_refusal():
    last_ax_error = {"kind": "AX_ERROR", "ax_error_code": -25204,
                     "ax_error_name": "cannot_complete", "attribute": "AXWindows",
                     "scope": "application_window_map", "readiness_transient": True}
    diagnostics = {"scope": "readiness_revalidation", "last_ax_error": last_ax_error,
                   "revalidation_error_type": "DesktopOperationError"}
    direct = DesktopOperationError("WINDOW_IDENTITY_CHANGED", "transient mismatch", diagnostics=diagnostics)
    assert actor._is_transient_owned_window_change(direct)
    exact_multiple = DesktopOperationError("WINDOW_IDENTITY_REVALIDATION_FAILED", "ambiguous identity", diagnostics=diagnostics)
    exact_multiple.__cause__ = DesktopOperationError(
        "WINDOW_IDENTITY_UNKNOWN", "task-owned COMSOL process tree has multiple visible windows",
    )
    assert actor._is_transient_owned_window_change(exact_multiple)
    changed_tree = DesktopOperationError("WINDOW_IDENTITY_REVALIDATION_FAILED", "changed process", diagnostics=diagnostics)
    changed_tree.__cause__ = DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "process tree changed")
    assert not actor._is_transient_owned_window_change(changed_tree)
    wrong_attribute = dict(diagnostics, last_ax_error={**last_ax_error, "attribute": "AXSize"})
    other_scope = DesktopOperationError("WINDOW_IDENTITY_REVALIDATION_FAILED", "not an AXWindows error",
                                        diagnostics=wrong_attribute)
    other_scope.__cause__ = exact_multiple.__cause__
    assert not actor._is_transient_owned_window_change(other_scope)


@pytest.mark.parametrize("attribute", ["AXPosition", "AXSize"])
def test_initial_window_association_invalid_axref_has_one_narrow_actor_recovery_gate(attribute):
    diagnostics = {"kind": "AX_ERROR", "ax_error_code": -25202,
                   "ax_error_name": "invalid_ui_element", "attribute": attribute,
                   "scope": "window_association", "readiness_transient": False}
    error = DesktopOperationError("AX_ATTRIBUTE_UNAVAILABLE", "invalid UI reference", diagnostics=diagnostics)
    attempts = [{"attempt": 1, "status": "FAIL_TERMINAL", "diagnostics": diagnostics}]
    assert actor._is_initial_window_association_invalid_axref(error, attempts, 0)


@pytest.mark.parametrize("code,attribute,scope,transient,error_code", [
    (-25204, "AXPosition", "window_association", True, "AX_ATTRIBUTE_TRANSIENT"),
    (-25202, "AXTitle", "window_association", False, "AX_ATTRIBUTE_UNAVAILABLE"),
    (-25202, "AXPosition", "owned_window_tree", False, "AX_ATTRIBUTE_UNAVAILABLE"),
    (-25202, "AXPosition", "window_association", True, "AX_ATTRIBUTE_TRANSIENT"),
])
def test_stale_axref_recovery_rejects_other_errors_and_scopes(code, attribute, scope, transient, error_code):
    name = { -25202: "invalid_ui_element", -25204: "cannot_complete"}[code]
    diagnostics = {"kind": "AX_ERROR", "ax_error_code": code, "ax_error_name": name,
                   "attribute": attribute, "scope": scope, "readiness_transient": transient}
    error = DesktopOperationError(error_code, "scoped AX error", diagnostics=diagnostics)
    attempts = [{"attempt": 1, "status": "FAIL_TERMINAL", "diagnostics": diagnostics}]
    assert not actor._is_initial_window_association_invalid_axref(error, attempts, 0)


def test_same_window_axref_refresh_requires_exact_live_tree_and_unique_same_quartz_identity(tmp_path, monkeypatch):
    process = _spawn_original_root()
    try:
        owned_initial = _owned(process.pid, 42)
        foreign_initial = _foreign(process.pid + 10000)
        owned_refresh = _owned(process.pid, 42)
        foreign_refresh = _foreign(process.pid + 10001)
        observer, _frozen, quartz = _observer(
            process, monkeypatch,
            [[owned_initial, foreign_initial], [owned_refresh, foreign_refresh]],
        )
        identity = observer.enumerate_windows()[0].identity
        diagnostics = {"kind": "AX_ERROR", "ax_error_code": -25202,
                       "ax_error_name": "invalid_ui_element", "attribute": "AXPosition",
                       "scope": "window_association", "readiness_transient": False}
        error = DesktopOperationError("AX_ATTRIBUTE_UNAVAILABLE", "invalid UI reference", diagnostics=diagnostics)
        attempts = [{"attempt": 1, "status": "FAIL_TERMINAL", "diagnostics": diagnostics}]
        refreshed = actor._refresh_same_owned_window_after_stale_axref(
            tmp_path, observer, process, identity,
            candidate_sha256=actor._sha(b"candidate"), binding_sha256=actor._sha(b"binding"),
            refresh_ordinal=1, readiness_deadline=60, deadline=600,
            trigger_error=error, attempts=attempts, attempt_start_index=0,
            permission_reader=_allow_accessibility, clock=lambda: 1.0,
        )
        assert refreshed["identity"] == identity
        assert refreshed["rows"] == sorted(observer._owned_processes.values(), key=lambda row: row.process_id)
        assert quartz.calls == 2  # initial selection and one full refresh revalidation
        assert foreign_initial.reads == foreign_refresh.reads == [OWNER_PID]
        assert owned_initial.reads == owned_refresh.reads == [OWNER_PID, WINDOW_ID]
        record = json.loads(Path(refreshed["refresh_record"]["path"]).read_text())
        assert record["trigger_ax_error"] == diagnostics
        assert record["same_quartz_identity"] is True
        assert record["full_process_tree_equal_before_and_after_each_inventory"] is True
        assert record["same_original_root_proof"]["same_original_popen_object"] is True
        assert record["old_adapter_and_ax_enumeration_discarded"] is True
        assert record["next_observation_constructs_fresh_adapter_then_axapplication_and_axwindows"] is True
        assert set(_mapping_keys(record)).isdisjoint({"window_name", "owner_name", "title", "bounds"})
    finally:
        _finish_original_root(process)


def test_stale_axref_transition_reselects_unique_new_quartz_identity_after_old_id_is_absent(tmp_path, monkeypatch):
    process = _spawn_original_root()
    try:
        observer, _frozen, quartz = _observer(
            process, monkeypatch,
            [[_owned(process.pid, 42)], [_owned(process.pid, 43)]],
        )
        identity = observer.enumerate_windows()[0].identity
        diagnostics = {"kind": "AX_ERROR", "ax_error_code": -25202,
                       "ax_error_name": "invalid_ui_element", "attribute": "AXSize",
                       "scope": "window_association", "readiness_transient": False}
        error = DesktopOperationError("AX_ATTRIBUTE_UNAVAILABLE", "invalid UI reference", diagnostics=diagnostics)
        attempts = [{"attempt": 1, "status": "FAIL_TERMINAL", "diagnostics": diagnostics}]
        transitioned = actor._refresh_same_owned_window_after_stale_axref(
            tmp_path, observer, process, identity,
            candidate_sha256=actor._sha(b"candidate-new-id"), binding_sha256=actor._sha(b"binding-new-id"),
            refresh_ordinal=1, readiness_deadline=60, deadline=600,
            trigger_error=error, attempts=attempts, attempt_start_index=0,
            permission_reader=_allow_accessibility, clock=lambda: 1.0,
        )
        assert transitioned["kind"] == "WINDOW_RESELECTION"
        assert transitioned["identity"].native_window_id == "CGWindow:43"
        assert quartz.calls == 2
        record = json.loads(Path(transitioned["reselection_record"]["path"]).read_text())
        assert record["old_native_id_absent_in_unique_inventory"] is True
        assert record["trigger_kind"] == "AXREF"
    finally:
        _finish_original_root(process)


def test_same_window_axref_refresh_uses_the_existing_two_total_reselection_cap(tmp_path):
    diagnostics = {"kind": "AX_ERROR", "ax_error_code": -25202,
                   "ax_error_name": "invalid_ui_element", "attribute": "AXPosition",
                   "scope": "window_association", "readiness_transient": False}
    error = DesktopOperationError("AX_ATTRIBUTE_UNAVAILABLE", "invalid UI reference", diagnostics=diagnostics)
    attempts = [{"attempt": 1, "status": "FAIL_TERMINAL", "diagnostics": diagnostics}]
    with pytest.raises(DesktopOperationError) as caught:
        actor._refresh_same_owned_window_after_stale_axref(
            tmp_path, object(), object(), _identity(os.getpid()),
            candidate_sha256=actor._sha(b"candidate"), binding_sha256=actor._sha(b"binding"),
            refresh_ordinal=3, readiness_deadline=60, deadline=600,
            trigger_error=error, attempts=attempts, attempt_start_index=0,
            permission_reader=_allow_accessibility,
        )
    assert caught.value.code == "AXREF_REFRESH_CAP_EXCEEDED"


def test_actor_fresh_adapter_recreates_axapplication_and_axwindows_after_invalid_ref(monkeypatch):
    from comsol_mcp import _desktop_macos as desktop_macos

    identity = _identity(os.getpid(), "CGWindow:42")
    counters = {"apps": 0, "windows": 0, "position": 0}

    class _Point:
        x, y = 10.0, 20.0

    class _Size:
        width, height = 300.0, 200.0

    class _Window:
        def __init__(self, generation):
            self.generation = generation

    class _Application:
        def __init__(self, generation):
            self.generation = generation

    class _Accessibility:
        kAXValueCGPointType = "point"
        kAXValueCGSizeType = "size"

        @staticmethod
        def AXIsProcessTrusted():
            return True

        @staticmethod
        def AXUIElementCreateApplication(_pid):
            counters["apps"] += 1
            return _Application(counters["apps"])

        @staticmethod
        def AXUIElementSetMessagingTimeout(_element, _seconds):
            return 0

        @staticmethod
        def AXUIElementCopyAttributeValue(element, name, _out):
            if isinstance(element, _Application) and name == "AXWindows":
                counters["windows"] += 1
                return 0, [_Window(element.generation)]
            if name == "AXPosition":
                counters["position"] += 1
                if counters["position"] == 1:
                    return -25202, None
                return 0, "point-value"
            if name == "AXSize":
                return 0, "size-value"
            if name == "AXTitle":
                return 0, "Fixture"
            raise AssertionError(f"unexpected AX access: {name}")

        @staticmethod
        def AXValueGetValue(_value, kind, _out):
            return (True, _Point() if kind == "point" else _Size())

    class _QuartzForTarget:
        kCGWindowListOptionIncludingWindow = 4
        kCGWindowNumber = "window_id"
        kCGWindowOwnerPID = "owner_pid"
        kCGWindowBounds = "bounds"
        kCGWindowName = "window_name"

        @staticmethod
        def CGPreflightScreenCaptureAccess():
            return True

        @staticmethod
        def CGWindowListCopyWindowInfo(_option, _window_id):
            return [{"window_id": 42, "owner_pid": identity.process_id,
                     "bounds": {"X": 10.0, "Y": 20.0, "Width": 300.0, "Height": 200.0},
                     "window_name": "Fixture"}]

    monkeypatch.setattr(desktop_macos, "MacOSFrameworkGateway",
                        lambda: MacOSFrameworkGateway(framework_loader=lambda: (_Accessibility, _QuartzForTarget)))
    first = actor._fresh_owned_accessibility_observer(object())
    assert first.gateway._loader is not None
    with pytest.raises(DesktopOperationError) as caught:
        first.gateway.target_window(identity)
    assert caught.value.code == "AX_ATTRIBUTE_UNAVAILABLE", (caught.value.code, caught.value.diagnostics, repr(caught.value.__cause__))
    assert caught.value.diagnostics["ax_error_code"] == -25202
    second = actor._fresh_owned_accessibility_observer(object())
    fresh_window = second.gateway.target_window(identity)
    assert first is not second
    assert first.gateway is not second.gateway
    assert isinstance(fresh_window, _Window)
    assert fresh_window.generation == 2
    assert counters == {"apps": 2, "windows": 2, "position": 2}


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


def test_execute_popen_no_window_observation_stops_at_total_deadline_minus_reserve(tmp_path, monkeypatch):
    """Exercise the real actor Popen/observation loop with a harmless sleeper only."""
    _restrict_psutil_to_direct_fixture_roots(monkeypatch, capture_actor_owned=True)
    class _Clock:
        def __init__(self):
            self.value = 100.0

        def monotonic(self):
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    clock = _Clock()
    monkeypatch.setattr(actor, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    root = tmp_path / "timer-fixture"
    root.mkdir(mode=0o700)
    for name in ("prefs", "configuration", "recovery", "tmp", "home", "logs", "evidence"):
        (root / name).mkdir(mode=0o700)

    launcher = Path("/bin/sleep")
    plan_path = root / "PLAN.json"
    plan = {"runner_interpreter_required": str(Path(sys.executable).resolve())}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    source_path = root / "SOURCE.json"
    source_path.write_text(json.dumps({str(launcher): actor.file_record(launcher)}), encoding="utf-8")
    candidate = {
        "native_Popen": 0, "r9_profile": actor.CURRENT_R9,
        "plan": {"path": str(plan_path), "sha256": actor._sha(plan_path.read_bytes())},
        "source": {"path": str(source_path), "sha256": actor._sha(source_path.read_bytes())},
        "installed_root": str(root / "synthetic-install"),
    }
    candidate_path = root / "ACTOR_CANDIDATE_macos1.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    (root / "MAIN_SOURCE_REVIEW.json").write_text(
        json.dumps({"status": "SYNTHETIC_TEST_ONLY_NO_MAIN_AUTHORITY"}), encoding="utf-8",
    )
    (root / "W25_LANE_CHECK_CURRENT.json").write_text(
        json.dumps({"status": "SYNTHETIC_TEST_ONLY_NO_NATIVE_LANE"}), encoding="utf-8",
    )
    argv = [str(launcher), "120"]
    binding = {"argv_sha256": actor._sha(actor._json_bytes(argv)), "nonce": "fixture-only"}
    lane = {"status": "SYNTHETIC_TEST_ONLY"}

    monkeypatch.setattr(actor, "verify_authority", lambda *_args: (candidate, binding, lane))
    monkeypatch.setattr(actor, "_activate_current_desktop_overlay", lambda *_args: None)
    monkeypatch.setattr(actor, "_installed_caller_status", lambda *_args: {"status": "FIXTURE_ONLY"})
    monkeypatch.setattr(actor, "_exact_argv", lambda _root: argv)
    monkeypatch.setattr(actor, "LAUNCHER", launcher)
    monkeypatch.setattr(
        "comsol_mcp._desktop_macos.MacOSDesktopAdapter._os_build",
        staticmethod(lambda: "synthetic-test-build"),
    )
    observations = []

    def no_owned_window(_root, process, rows, **_kwargs):
        assert type(process) is subprocess.Popen and process.poll() is None
        observations.append(process.pid)
        return {"recovery_receipt": None, "observer": object(), "windows": [], "rows": list(rows)}

    monkeypatch.setattr(actor, "_enumerate_owned_windows_with_startup_recovery", no_owned_window)
    result = actor.execute(root, root / "MAIN_ONCE_BINDING.json", "fixture-binding-sha256")

    assert result["budget"]["total_budget_seconds"] == 600
    assert result["budget"]["active_observation_budget_seconds"] == 540
    assert result["budget"]["cleanup_reserve_seconds"] == 60
    assert result["budget"]["active_deadline_monotonic"] == 640.0
    assert result["budget"]["total_deadline_monotonic"] == 700.0
    assert len(observations) == 540
    assert result["failure"].startswith("TimeoutError: no owned visible Desktop window appeared before the active cutoff")
    assert result["cleanup"]["owned_group_absent"] is True
    assert result["cleanup"]["cleanup_deadline_monotonic"] == 700.0
    assert result["cleanup"]["budget_exceeded"] is False
    assert result["popen_returncode"] is not None
    assert result["owned_window"] is None
    assert result["owned_ax_tree"] is None
    assert result["control_actions"] == "NOT_RUN"


@pytest.mark.parametrize("terminal_overrun_seconds", [0.0, 1.25])
@pytest.mark.parametrize("metadata_gaps", [[], [{"kind": "ATTRIBUTE_GAP", "path": "synthetic"}]])
def test_terminal_cleanup_at_or_after_inclusive_total_deadline_is_unknown_not_accepted(
        tmp_path, monkeypatch, terminal_overrun_seconds, metadata_gaps):
    """A clean resource teardown at the 600s boundary cannot earn PASS/PARTIAL."""
    _restrict_psutil_to_direct_fixture_roots(monkeypatch, capture_actor_owned=True)
    class _Clock:
        def __init__(self):
            self.value = 100.0
            self.monotonic_calls = 0

        def monotonic(self):
            self.monotonic_calls += 1
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    clock = _Clock()
    monkeypatch.setattr(actor, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    root = tmp_path / "late-terminal-fixture"
    root.mkdir(mode=0o700)
    for name in ("prefs", "configuration", "recovery", "tmp", "home", "logs", "evidence"):
        (root / name).mkdir(mode=0o700)

    launcher = Path("/bin/sleep")
    plan_path = root / "PLAN.json"
    plan_path.write_text(json.dumps({"runner_interpreter_required": str(Path(sys.executable).resolve())}), encoding="utf-8")
    source_path = root / "SOURCE.json"
    source_path.write_text(json.dumps({str(launcher): actor.file_record(launcher)}), encoding="utf-8")
    candidate = {
        "native_Popen": 0, "r9_profile": actor.CURRENT_R9,
        "plan": {"path": str(plan_path), "sha256": actor._sha(plan_path.read_bytes())},
        "source": {"path": str(source_path), "sha256": actor._sha(source_path.read_bytes())},
        "installed_root": str(root / "synthetic-install"),
    }
    (root / "ACTOR_CANDIDATE_macos1.json").write_text(json.dumps(candidate), encoding="utf-8")
    (root / "MAIN_SOURCE_REVIEW.json").write_text(
        json.dumps({"status": "SYNTHETIC_TEST_ONLY_NO_MAIN_AUTHORITY"}), encoding="utf-8",
    )
    (root / "W25_LANE_CHECK_CURRENT.json").write_text(
        json.dumps({"status": "SYNTHETIC_TEST_ONLY_NO_NATIVE_LANE"}), encoding="utf-8",
    )
    argv = [str(launcher), "120"]
    binding = {"argv_sha256": actor._sha(actor._json_bytes(argv)), "nonce": "fixture-only"}
    lane = {"status": "SYNTHETIC_TEST_ONLY"}
    monkeypatch.setattr(actor, "verify_authority", lambda *_args: (candidate, binding, lane))
    monkeypatch.setattr(actor, "_activate_current_desktop_overlay", lambda *_args: None)
    monkeypatch.setattr(actor, "_installed_caller_status", lambda *_args: {"status": "FIXTURE_ONLY"})
    monkeypatch.setattr(actor, "_exact_argv", lambda _root: argv)
    monkeypatch.setattr(actor, "LAUNCHER", launcher)
    monkeypatch.setattr(
        "comsol_mcp._desktop_macos.MacOSDesktopAdapter._os_build",
        staticmethod(lambda: "synthetic-test-build"),
    )

    def one_owned_window(_root, process, rows, **_kwargs):
        from comsol_mcp._desktop_service import WindowIdentity
        assert type(process) is subprocess.Popen and process.poll() is None
        row = next(item for item in rows if item.process_id == process.pid)
        identity = WindowIdentity(
            "macos", "CGWindow:4242", process.pid, row.process_birth,
            f"uid:{row.real_uid}", f"process-session:{row.session_id}", "6.4.0",
        )
        return {"recovery_receipt": None, "observer": object(),
                "windows": [SimpleNamespace(identity=identity)], "rows": list(rows)}

    monkeypatch.setattr(actor, "_enumerate_owned_windows_with_startup_recovery", one_owned_window)
    monkeypatch.setattr(actor, "_fresh_owned_accessibility_observer", lambda _observer: object())
    monkeypatch.setattr(actor, "_inspect_owned_window_with_readiness",
                        lambda *_args, **_kwargs: {"status": "SYNTHETIC_VALID_AX_METADATA"})
    monkeypatch.setattr(actor, "_owned_ax_metadata_gaps", lambda _tree: list(metadata_gaps))

    original_cleanup = actor.W25OriginalProcessOwner.cleanup_or_hold

    def return_at_deadline(owner, rows, *, total_deadline_monotonic):
        cleanup = original_cleanup(owner, rows, total_deadline_monotonic=total_deadline_monotonic)
        assert cleanup["owned_group_absent"] is True
        assert owner.hold_entered is False
        # Model a slow final group inventory/terminal-evidence write after
        # otherwise-normal cleanup. The cleanup facts remain valid; its return
        # time is the inclusive deadline or a small overrun.
        clock.value = total_deadline_monotonic + terminal_overrun_seconds
        cleanup["cleanup_finished_monotonic"] = clock.value
        cleanup["budget_exceeded"] = True
        cleanup["budget_overrun_seconds"] = terminal_overrun_seconds
        clock.calls_at_cleanup_return = clock.monotonic_calls
        return cleanup

    monkeypatch.setattr(actor.W25OriginalProcessOwner, "cleanup_or_hold", return_at_deadline)
    result = actor.execute(root, root / "MAIN_ONCE_BINDING.json", "fixture-binding-sha256")

    assert result["owned_ax_tree"] == {"status": "SYNTHETIC_VALID_AX_METADATA"}
    assert result["cleanup"]["owned_group_absent"] is True
    assert result["terminal_resources"]["owner_hold_entered"] is False
    assert result["terminal_resources"]["owner_released"] is True
    assert result["terminal_resources"]["owned_group_absent"] is True
    assert result["budget"]["terminal_snapshot_monotonic"] == (
        result["budget"]["total_deadline_monotonic"] + terminal_overrun_seconds)
    assert result["budget"]["terminal_boundary_policy"].endswith(">= total_deadline_monotonic")
    assert clock.monotonic_calls == clock.calls_at_cleanup_return + 1
    assert result["budget"]["budget_exceeded"] is True
    assert result["budget"]["budget_overrun_seconds"] == terminal_overrun_seconds
    assert result["cleanup"]["cleanup_finished_monotonic"] == result["budget"]["terminal_snapshot_monotonic"]
    assert result["cleanup"]["budget_exceeded"] is True
    assert result["status"] == "UNKNOWN_BUDGET_EXCEEDED"
    assert "reached or exceeded" in result["failure"]
    assert (root / "FIRST_FAILURE.json").is_file()
    assert not (root / "APPLICATION_DESKTOP_OBSERVATION_RESULT_R1.json").exists()
    assert result["selector_profile"] == "UNAVAILABLE"
    assert result["model_binding"] == "UNVERIFIED"


def test_cleanup_uses_only_remaining_total_reserve_and_revalidates_before_kill(monkeypatch):
    class _Clock:
        def __init__(self):
            self.value = 595.0

        def monotonic(self):
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    clock = _Clock()
    monkeypatch.setattr(actor, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    script = (
        "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('READY', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True, close_fds=True)
    _restrict_psutil_to_direct_fixture_roots(monkeypatch, process)
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == b"READY"
        rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        result = actor._terminate_owned(process, rows, total_deadline_monotonic=600.0)
        assert clock.value == 600.0
        assert result["cleanup_deadline_monotonic"] == 600.0
        assert result["cleanup_reserve_seconds"] == 60
        assert result["force_stopped_known_owned_pids"] == []
        assert result["owned_group_absent"] is False
        assert result["budget_exceeded"] is True
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def test_cleanup_preserves_thirty_second_term_then_revalidates_exact_popen_before_kill(monkeypatch):
    import comsol_mcp._desktop_macos as desktop_macos

    class _Clock:
        def __init__(self):
            self.value = 540.0

        def monotonic(self):
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    clock = _Clock()
    monkeypatch.setattr(actor, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    script = (
        "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('READY', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True, close_fds=True)
    _restrict_psutil_to_direct_fixture_roots(monkeypatch, process)
    events = []
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == b"READY"
        rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        original_digest = desktop_macos.macos_process_command_sha256

        def record_identity_revalidation(executable, argv):
            events.append(("IDENTITY_REVALIDATION", clock.value))
            return original_digest(executable, argv)

        monkeypatch.setattr(desktop_macos, "macos_process_command_sha256", record_identity_revalidation)
        original_term, original_kill = process.terminate, process.kill

        def record_term():
            events.append(("TERM", clock.value))
            return original_term()

        def record_kill():
            events.append(("KILL", clock.value))
            return original_kill()

        process.terminate = record_term
        process.kill = record_kill
        result = actor._terminate_owned(process, rows, total_deadline_monotonic=600.0)
        term_index = next(index for index, row in enumerate(events) if row[0] == "TERM")
        kill_index = next(index for index, row in enumerate(events) if row[0] == "KILL")
        assert events[term_index][1] == 540.0
        assert events[kill_index][1] == 570.0
        assert any(row[0] == "IDENTITY_REVALIDATION" for row in events[term_index + 1:kill_index])
        assert result["force_stopped_known_owned_pids"] == [process.pid]
        assert result["owned_group_absent"] is True
        assert result["cleanup_deadline_monotonic"] == 600.0
        assert result["budget_exceeded"] is False
    finally:
        if process.poll() is None:
            original_kill = getattr(process, "kill")
            original_kill()
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


def test_unknown_owner_hold_records_budget_overrun_and_keeps_original_popen(tmp_path, monkeypatch):
    class _Clock:
        def monotonic(self):
            return 700.0

    monkeypatch.setattr(actor, "time", SimpleNamespace(monotonic=_Clock().monotonic))
    root = tmp_path / "unknown-owner"
    (root / "logs").mkdir(parents=True, mode=0o700)
    (root / "evidence").mkdir(mode=0o700)
    owner = actor.W25OriginalProcessOwner(
        root, root / "logs/stdout", root / "logs/stderr", total_deadline_monotonic=600.0,
    )
    process = None
    thread = None
    try:
        owner.open_logs()
        process = owner.popen(["/bin/sleep", "90"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=str(root), env={"PATH": "/bin"}, start_new_session=True,
                              close_fds=True, bufsize=0)
        _restrict_psutil_to_direct_fixture_roots(monkeypatch, process)
        owner.rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        thread = threading.Thread(target=lambda: owner.passive_hold("synthetic UNKNOWN owner"))
        thread.start()
        receipt_path = root / "evidence/W25_ORIGINAL_OWNER_HOLD.json"
        receipt = None
        for _ in range(100):
            if receipt_path.is_file():
                try:
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    break
                except json.JSONDecodeError:
                    pass  # The exclusive file is visible before its fsync completes.
            threading.Event().wait(0.01)
        assert isinstance(receipt, dict)
        assert receipt["budget_exceeded_at_hold_start"] is True
        assert receipt["total_deadline_monotonic"] == 600.0
        assert receipt["signals_during_unknown_hold"] == 0
        assert process.poll() is None
        assert actor._RETAINED_OWNERS[str(root)] is owner
        process.terminate()  # Test harness releases its own harmless sleeper.
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert owner.released is True
        assert owner.cleanup["budget_exceeded"] is True
        assert owner.cleanup["budget_overrun_seconds"] == 100.0
        assert owner.cleanup["signals_during_unknown_hold"] == 0
    finally:
        if process is not None and process.poll() is None:
            process.kill()
        if process is not None:
            process.wait(timeout=5)
        if thread is not None:
            thread.join(timeout=5)
        for drainer in owner.drainers.values():
            drainer.join(timeout=5)


def test_startup_child_exit_recovery_rebuilds_observer_from_same_live_popen_and_persists_raw_cause(
        tmp_path, monkeypatch):
    # The bundle/version strings below are fixture metadata only. This test runs
    # Python and /bin/sleep; it never starts or claims to identify COMSOL.
    process, child_pids = _spawn_owned_sleep_root(monkeypatch=monkeypatch)
    try:
        child_pid = child_pids[0]
        frozen_rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        assert {row.process_id for row in frozen_rows} == {process.pid, child_pid}
        quartz = _Quartz([])
        monkeypatch.setitem(sys.modules, "Quartz", quartz)

        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("exit_children\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "CHILDREN_REAPED"
        deadline = actor.time.monotonic() + 300
        inventory = actor._enumerate_owned_windows_with_startup_recovery(
            tmp_path, process, frozen_rows, ordinal=1, deadline=deadline,
            candidate_sha256=actor._sha(b"startup-candidate"),
            binding_sha256=actor._sha(b"startup-binding"), before_first_window=True,
        )
        assert inventory["observer"] is None
        assert inventory["windows"] is None
        assert quartz.calls == 0
        assert process.poll() is None
        assert [row.process_id for row in inventory["rows"]] == [process.pid]
        assert inventory["recovery_receipt"]["path"].endswith("STARTUP_CHILD_EXIT_RECOVERY_01.json")
        proof = json.loads(Path(inventory["recovery_receipt"]["path"]).read_text(encoding="utf-8"))
        assert proof["status"] == "PASS_SINGLE_CAPTURED_NONROOT_CHILD_EXIT_BEFORE_FIRST_WINDOW"
        assert proof["failure_cause_proof"]["wrapper"]["text"] == "task-owned process identity is unavailable"
        assert proof["failure_cause_proof"]["cause"]["pid"] == child_pid
        assert proof["failure_cause_proof"]["cause"]["type"] == "psutil.NoSuchProcess"
        assert proof["independent_absence_proof"]["status"] == "ABSENT_CONFIRMED"
        assert proof["snapshot_subset_proof"]["root_row_equal"] is True
        assert proof["snapshot_subset_proof"]["survivor_rows_equal"] is True
        assert proof["snapshot_subset_proof"]["added_process_ids"] == []
        assert proof["snapshot_subset_proof"]["missing_process_ids"] == [child_pid]
        assert proof["budget"]["fixed_total_launch_deadline_monotonic"] == deadline
        assert proof["budget"]["recovery_sleep_cap_seconds"] == 1.0

        fresh_quartz = _Quartz([[_owned(process.pid, 42)]])
        monkeypatch.setitem(sys.modules, "Quartz", fresh_quartz)
        second_inventory = actor._enumerate_owned_windows_with_startup_recovery(
            tmp_path, process, inventory["rows"], ordinal=2, deadline=deadline,
            candidate_sha256=actor._sha(b"startup-candidate"),
            binding_sha256=actor._sha(b"startup-binding"), before_first_window=True,
        )
        assert second_inventory["recovery_receipt"] is None
        assert second_inventory["observer"]._original_root is process
        assert len(second_inventory["windows"]) == 1
        assert second_inventory["windows"][0].identity.process_id == process.pid
        assert fresh_quartz.calls == 1
    finally:
        _stop_owned_sleep_root(process)


def test_startup_child_exit_accepts_second_exact_production_literal_only_with_direct_nosuch_cause(
        tmp_path, monkeypatch):
    process, child_pids = _spawn_owned_sleep_root(monkeypatch=monkeypatch)
    try:
        child_pid = child_pids[0]
        frozen_rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("exit_children\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "CHILDREN_REAPED"
        error = _synthetic_no_such_error(
            "task-owned process identity could not be revalidated", child_pid,
        )
        recovered = actor._recover_startup_owned_child_exit(
            tmp_path, process, frozen_rows, error, ordinal=1, deadline=actor.time.monotonic() + 300,
            candidate_sha256=actor._sha(b"candidate"), binding_sha256=actor._sha(b"binding"),
            before_first_window=True,
        )
        assert recovered is not None
        proof = json.loads(Path(recovered["receipt"]["path"]).read_text(encoding="utf-8"))
        assert proof["failure_cause_proof"]["wrapper"]["text"] == "task-owned process identity could not be revalidated"
        assert proof["failure_cause_proof"]["cause"]["pid"] == child_pid
    finally:
        _stop_owned_sleep_root(process)


@pytest.mark.parametrize("case", ["wrong_code", "wrong_message", "wrong_pid", "root_pid", "access_denied", "nested_cause"])
def test_startup_child_exit_classifier_rejects_unknown_or_ambiguous_cause(case, monkeypatch):
    import psutil
    process, child_pids = _spawn_owned_sleep_root(monkeypatch=monkeypatch)
    try:
        child_pid = child_pids[0]
        rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        if case == "wrong_code":
            exc = _synthetic_no_such_error("task-owned process identity is unavailable", child_pid)
            exc.code = "WINDOW_METADATA_UNAVAILABLE"
        elif case == "wrong_message":
            exc = _synthetic_no_such_error("a different startup error", child_pid)
        elif case == "wrong_pid":
            exc = _synthetic_no_such_error("task-owned process identity is unavailable", child_pid + 100000)
        elif case == "root_pid":
            exc = _synthetic_no_such_error("task-owned process identity is unavailable", process.pid)
        elif case == "access_denied":
            try:
                raise DesktopOperationError(
                    "WINDOW_IDENTITY_UNKNOWN", "task-owned process identity is unavailable", safe_retry=True,
                ) from psutil.AccessDenied(child_pid)
            except DesktopOperationError as caught:
                exc = caught
        else:
            try:
                try:
                    raise DesktopOperationError(
                        "WINDOW_IDENTITY_UNKNOWN", "intermediate wrapper", safe_retry=True,
                    ) from psutil.NoSuchProcess(child_pid)
                except DesktopOperationError as inner:
                    raise DesktopOperationError(
                        "WINDOW_IDENTITY_UNKNOWN", "task-owned process identity is unavailable", safe_retry=True,
                    ) from inner
            except DesktopOperationError as caught:
                exc = caught
        assert actor._startup_owned_child_exit_cause(exc, rows, process.pid) is None
    finally:
        _stop_owned_sleep_root(process)


def test_startup_child_exit_refuses_after_window_or_above_fixed_recovery_cap(tmp_path, monkeypatch):
    process, child_pids = _spawn_owned_sleep_root(monkeypatch=monkeypatch)
    try:
        rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        monkeypatch.setitem(sys.modules, "Quartz", _Quartz([]))
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("exit_children\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "CHILDREN_REAPED"
        with pytest.raises(DesktopOperationError) as caught_after_window:
            actor._enumerate_owned_windows_with_startup_recovery(
                tmp_path, process, rows, ordinal=1, deadline=actor.time.monotonic() + 300,
                candidate_sha256=actor._sha(b"candidate"), binding_sha256=actor._sha(b"binding"),
                before_first_window=False,
            )
        assert caught_after_window.value.__cause__.pid == child_pids[0]
        with pytest.raises(DesktopOperationError) as caught_over_cap:
            actor._enumerate_owned_windows_with_startup_recovery(
                tmp_path, process, rows, ordinal=9, deadline=actor.time.monotonic() + 300,
                candidate_sha256=actor._sha(b"candidate"), binding_sha256=actor._sha(b"binding"),
                before_first_window=True,
            )
        assert caught_over_cap.value.__cause__.pid == child_pids[0]
        assert not list(tmp_path.glob("STARTUP_CHILD_EXIT_RECOVERY_*.json"))
    finally:
        _stop_owned_sleep_root(process)


def test_startup_child_exit_refuses_multiple_missing_children(tmp_path, monkeypatch):
    process, child_pids = _spawn_owned_sleep_root(child_count=2, monkeypatch=monkeypatch)
    try:
        frozen_rows = actor._snapshot_owned_processes(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        monkeypatch.setitem(sys.modules, "Quartz", _Quartz([]))
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("exit_children\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "CHILDREN_REAPED"
        with pytest.raises(DesktopOperationError) as caught:
            actor._enumerate_owned_windows_with_startup_recovery(
                tmp_path, process, frozen_rows, ordinal=1,
                deadline=actor.time.monotonic() + 300, candidate_sha256=actor._sha(b"candidate"),
                binding_sha256=actor._sha(b"binding"), before_first_window=True,
            )
        assert caught.value.__cause__.pid in child_pids
        assert not (tmp_path / "STARTUP_CHILD_EXIT_RECOVERY_01.json").exists()

    finally:
        _stop_owned_sleep_root(process)


@pytest.mark.parametrize("drift", ["added_pid", "reused_pid", "changed_root"])
def test_startup_child_exit_refuses_added_reused_or_changed_fresh_rows(tmp_path, monkeypatch, drift):
    process, child_pids = _spawn_owned_sleep_root(monkeypatch=monkeypatch)
    try:
        child_pid = child_pids[0]
        original_snapshot = actor._snapshot_owned_processes
        frozen_rows = original_snapshot(
            process, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0",
        )
        monkeypatch.setitem(sys.modules, "Quartz", _Quartz([]))
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write("exit_children\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "CHILDREN_REAPED"

        def drifted_snapshot(root_process, *, bundle_identifier, comsol_version):
            rows = original_snapshot(
                root_process, bundle_identifier=bundle_identifier, comsol_version=comsol_version,
            )
            if drift == "added_pid":
                new_row = replace(
                    frozen_rows[0], process_id=process.pid + 100000,
                    parent_pid=process.pid, process_group_id=process.pid, session_id=process.pid,
                )
                return [*rows, new_row]
            if drift == "reused_pid":
                changed_child = replace(frozen_rows[1], process_birth="1799999999.000001")
                return [*rows, changed_child]
            return [replace(rows[0], process_birth="1799999999.000001")]

        monkeypatch.setattr(actor, "_snapshot_owned_processes", drifted_snapshot)
        with pytest.raises(DesktopOperationError) as caught:
            actor._enumerate_owned_windows_with_startup_recovery(
                tmp_path, process, frozen_rows, ordinal=1,
                deadline=actor.time.monotonic() + 300, candidate_sha256=actor._sha(b"candidate"),
                binding_sha256=actor._sha(b"binding"), before_first_window=True,
            )
        assert caught.value.__cause__.pid == child_pid
        assert not (tmp_path / "STARTUP_CHILD_EXIT_RECOVERY_01.json").exists()
    finally:
        _stop_owned_sleep_root(process)
