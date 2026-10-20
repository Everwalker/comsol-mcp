from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import w25_macos_owned_desktop_actor_r6 as actor
from comsol_mcp._desktop_service import DesktopOperationError, WindowIdentity


class _Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def _transient_ax_error(code=-25204):
    from comsol_mcp._desktop_macos import MacOSFrameworkGateway
    name = MacOSFrameworkGateway._ax_error_name(code)
    return DesktopOperationError(
        "AX_ATTRIBUTE_TRANSIENT", f"Accessibility attribute read failed ({name}, {code}): AXWindows in application_window_map",
        diagnostics={"kind": "AX_ERROR", "ax_error_code": code, "ax_error_name": name,
                     "attribute": "AXWindows", "scope": "application_window_map",
                     "readiness_transient": True},
    )


def _readiness_identity(birth="birth:123"):
    return WindowIdentity("macos", "CGWindow:7", 123, birth, "uid:501", "session:123", "6.4.0")


def test_original_owner_failure_record_keeps_structured_ax_cause(tmp_path):
    owner = actor.W25OriginalProcessOwner(tmp_path, tmp_path / "stdout", tmp_path / "stderr")
    error = _transient_ax_error(-25212)
    owner.fail(error, "task_owned_observation")
    assert owner.first_failure["phase"] == "task_owned_observation"
    assert owner.first_failure["diagnostics"]["ax_error_code"] == -25212
    assert owner.first_failure["diagnostics"]["attribute"] == "AXWindows"
    assert owner.first_failure["diagnostics"]["scope"] == "application_window_map"


def test_axwindows_readiness_retries_only_after_exact_owned_identity_revalidation():
    identity = _readiness_identity()
    clock = _Clock()

    class Observer:
        calls = 0
        def enumerate_windows(self):
            self.calls += 1
            return [SimpleNamespace(identity=identity)]

    class Adapter:
        calls = 0
        def inspect_accessibility(self, observed, **_limits):
            assert observed == identity
            self.calls += 1
            if self.calls < 3:
                raise _transient_ax_error(-25204 if self.calls == 1 else -25212)
            return {"nodes": [], "truncated": False}

    observer, adapter, attempts = Observer(), Adapter(), []
    result = actor._inspect_owned_window_with_readiness(observer, adapter, identity, deadline=60,
                                                         attempts=attempts, clock=clock.now, sleeper=clock.sleep)
    assert result["truncated"] is False
    assert adapter.calls == 3 and observer.calls == 2
    assert [row["status"] for row in attempts] == ["RETRY_AX_WINDOWS_READINESS", "RETRY_AX_WINDOWS_READINESS", "PASS"]
    assert [row["diagnostics"]["ax_error_code"] for row in attempts[:2]] == [-25204, -25212]


@pytest.mark.parametrize("code,name", [(-25205, "attribute_unsupported"), (-25211, "api_disabled")])
def test_terminal_ax_errors_are_not_retried(code, name):
    identity = _readiness_identity()

    class Observer:
        calls = 0
        def enumerate_windows(self):
            self.calls += 1
            return [SimpleNamespace(identity=identity)]

    class Adapter:
        calls = 0
        def inspect_accessibility(self, *_args, **_kwargs):
            self.calls += 1
            raise DesktopOperationError("AX_ATTRIBUTE_UNAVAILABLE", "terminal AX error",
                diagnostics={"kind": "AX_ERROR", "ax_error_code": code, "ax_error_name": name,
                             "attribute": "AXWindows", "scope": "application_window_map",
                             "readiness_transient": False})

    observer, adapter, attempts = Observer(), Adapter(), []
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(observer, adapter, identity, deadline=60, attempts=attempts)
    assert caught.value.diagnostics["ax_error_code"] == code
    assert adapter.calls == 1 and observer.calls == 0
    assert attempts[0]["status"] == "FAIL_TERMINAL"


def test_axwindows_readiness_stops_when_exact_owned_identity_changes():
    identity = _readiness_identity()
    clock = _Clock()

    class Observer:
        def enumerate_windows(self):
            return [SimpleNamespace(identity=_readiness_identity("birth:reused"))]

    class Adapter:
        calls = 0
        def inspect_accessibility(self, *_args, **_kwargs):
            self.calls += 1
            raise _transient_ax_error()

    adapter = Adapter()
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(Observer(), adapter, identity, deadline=60,
                                                   clock=clock.now, sleeper=clock.sleep)
    assert caught.value.code == "WINDOW_IDENTITY_CHANGED"
    assert caught.value.diagnostics["last_ax_error"]["ax_error_code"] == -25204
    assert adapter.calls == 1


def test_axwindows_readiness_timeout_retains_last_numeric_error_and_is_bounded(monkeypatch):
    identity = _readiness_identity()
    clock = _Clock()
    monkeypatch.setattr(actor, "AX_READINESS_SECONDS", 2)

    class Observer:
        calls = 0
        def enumerate_windows(self):
            self.calls += 1
            return [SimpleNamespace(identity=identity)]

    class Adapter:
        calls = 0
        def inspect_accessibility(self, *_args, **_kwargs):
            self.calls += 1
            raise _transient_ax_error(-25212)

    observer, adapter, attempts = Observer(), Adapter(), []
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(observer, adapter, identity, deadline=100,
                                                   attempts=attempts, clock=clock.now, sleeper=clock.sleep)
    assert caught.value.code == "AX_READINESS_TIMEOUT"
    assert caught.value.diagnostics["last_ax_error"]["ax_error_code"] == -25212
    assert caught.value.diagnostics["attempt_count"] == 3
    assert clock.value == 2 and adapter.calls == 3


def test_partial_owned_ax_tree_is_preserved_without_complete_profile_or_w25_pass():
    identity = _readiness_identity()
    tree = {"nodes": [{"path": [], "attributes": {"AXRole": "AXWindow"},
                       "attribute_observations": {"AXSubrole": {"status": "ATTRIBUTE_UNSUPPORTED",
                                                                   "ax_error_code": -25205}},
                       "children_observation": {"status": "ATTRIBUTE_UNSUPPORTED",
                                                "ax_error_code": -25205}}],
            "truncated": True, "scope": "TARGET_WINDOW_AX_METADATA_ONLY", "model_binding": "UNVERIFIED"}

    class Observer:
        def enumerate_windows(self):
            return [SimpleNamespace(identity=identity)]

    class Adapter:
        def inspect_accessibility(self, *_args, **_kwargs):
            return tree

    retained = actor._inspect_owned_window_with_readiness(Observer(), Adapter(), identity, deadline=60)
    gaps = actor._owned_ax_metadata_gaps(retained)
    status = actor._owned_observation_status(
        failure=None, tree=retained, metadata_gaps=gaps,
        cleanup={"owned_group_absent": True}, owner_hold_entered=False,
    )
    assert retained is tree and retained["truncated"] is True
    assert {row["kind"] for row in gaps} == {"TREE_TRUNCATED", "ATTRIBUTE_GAP", "CHILDREN_GAP"}
    assert status == "PARTIAL_SCOPED_TASK_OWNED_AX_METADATA_WITH_GAPS_NOT_PROFILE_CERTIFIED"
    assert status != "PASS_SCOPED_TASK_OWNED_AX_METADATA_ONLY"


@pytest.mark.parametrize("fault", ["failure", "no-cleanup", "owner-hold"])
def test_partial_tree_never_reports_even_scoped_observation_pass_when_terminal_state_unknown(fault):
    tree = {"nodes": [], "truncated": True, "scope": "TARGET_WINDOW_AX_METADATA_ONLY", "model_binding": "UNVERIFIED"}
    cleanup = {"owned_group_absent": True}
    failure = None
    hold = False
    if fault == "failure":failure = "AX failure"
    elif fault == "no-cleanup":cleanup = None
    else:hold = True
    status = actor._owned_observation_status(failure=failure, tree=tree, metadata_gaps=[{"kind":"TREE_TRUNCATED"}],
                                             cleanup=cleanup, owner_hold_entered=hold)
    assert status == "FAIL_PRESERVED_NATIVE_ATTEMPT"


def test_official_command_template_is_exact_blank_desktop_and_isolated(tmp_path):
    argv = actor._exact_argv(tmp_path)
    assert argv == [
        "/Applications/COMSOL64/Multiphysics/bin/comsol",
        "-prefsdir", str(tmp_path / "prefs"),
        "-configuration", str(tmp_path / "configuration"),
        "-recoverydir", str(tmp_path / "recovery"),
        "-tmpdir", str(tmp_path / "tmp"),
        "-np", "1",
    ]
    assert "-rdp" not in argv and "-client" not in argv
    assert not any(arg.endswith(".mph") for arg in argv)
    assert actor._sha(actor._json_bytes(argv)) == actor._sha(actor._json_bytes(actor._exact_argv(tmp_path)))


def test_private_root_requires_fresh_namespace_owner_and_mode(tmp_path):
    invalid = tmp_path / "cvw25-macos-1234abcd"
    invalid.mkdir(mode=0o700)
    with pytest.raises(RuntimeError, match="/private/tmp"):
        actor._secure_root(invalid)

    root = Path("/private/tmp") / f"cvw25-macos-{uuid.uuid4().hex[:8]}"
    root.mkdir(mode=0o700)
    try:
        os.chmod(root, 0o700)
        identity = actor._secure_root(root)
        assert identity["device"] == root.stat().st_dev
        assert identity["inode"] == root.stat().st_ino
        os.chmod(root, 0o755)
        with pytest.raises(RuntimeError, match="0700"):
            actor._secure_root(root)
    finally:
        os.chmod(root, 0o700)
        root.rmdir()


def test_retained_r6_install_root_requires_exact_private_namespace_and_mode():
    invalid = Path("/private/tmp") / f"cvw26-lifecycle-{uuid.uuid4().hex[:8]}"
    invalid.mkdir(mode=0o700)
    try:
        with pytest.raises(RuntimeError, match="cvw26-r6install"):
            actor._secure_installed_root(invalid)
    finally:
        invalid.rmdir()

    root = Path("/private/tmp") / f"cvw26-r6install-{uuid.uuid4().hex[:8]}"
    root.mkdir(mode=0o700)
    try:
        identity = actor._secure_installed_root(root)[1]
        assert identity == {"device": root.stat().st_dev, "inode": root.stat().st_ino}
        os.chmod(root, 0o755)
        with pytest.raises(RuntimeError, match="0700"):
            actor._secure_installed_root(root)
    finally:
        os.chmod(root, 0o700)
        root.rmdir()


def test_w26_first_records_normalize_without_weakening_identity_checks(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('{"status":"bound"}\n', encoding="utf-8")
    actual = actor.file_record(path)
    external = {"path": actual["path"], "dev": actual["device"], "inode": actual["inode"],
                "uid": actual["uid"], "mode": actual["mode"], "nlink": actual["nlink"],
                "bytes": actual["bytes"], "sha256": actual["sha256"]}
    assert actor._capture_external_record(external) == actual
    external["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="external first-record mismatch"):
        actor._capture_external_record(external)


def test_main_binding_pins_the_external_retained_r6_interpreter(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    installed_python = "/private/tmp/cvw26-r6install-afe658f0/current-venv/bin/python"
    plan = actor._write_new(root / "plan.json", {
        "runner_interpreter_required": installed_python,
        "command": {"argv_sha256": "a" * 64},
        "budgets": {"max_ax_readiness_seconds_total": actor.AX_READINESS_SECONDS,
                    "max_ax_window_reselections": actor.MAX_AX_WINDOW_RESELECTIONS},
    })
    candidate_record = actor._write_new(root / "ACTOR_CANDIDATE_macos1.json", {
        "activity_kind": "W25_MACOS_BLANK_DESKTOP_METADATA_OBSERVATION",
        "root_identity": {"device": 1, "inode": 2}, "plan": plan,
        "source": {"sha256": "b" * 64}, "resume_sha256": "c" * 64,
    })
    actor._write_new(root / "MAIN_SOURCE_REVIEW.json", {"status": "synthetic test review"})
    binding = actor.build_expected_binding(candidate_record, {
        "path": str(root / "W25_LANE_CHECK_CURRENT.json"), "sha256": "d" * 64,
    })
    plan_value = actor._read_json(root / "plan.json")
    assert binding["runner_python"] == installed_python
    assert binding["runner_python"] != str(root / "current-venv/bin/python")
    assert type(binding["max_ax_readiness_seconds_total"]) is int
    assert binding["max_ax_readiness_seconds_total"] == plan_value["budgets"]["max_ax_readiness_seconds_total"] == 60
    assert type(binding["max_ax_window_reselections"]) is int
    assert binding["max_ax_window_reselections"] == plan_value["budgets"]["max_ax_window_reselections"] == 2


def test_execute_without_exact_once_binding_refuses_before_any_popen(tmp_path, monkeypatch):
    root = tmp_path / "cvw25-macos-1234abcd"
    root.mkdir(mode=0o700)
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("COMSOL Popen must not occur in a missing-authority test")

    monkeypatch.setattr(actor.subprocess, "Popen", forbidden)
    with pytest.raises((FileNotFoundError, RuntimeError)):
        actor.execute(root, root / "MAIN_ONCE_BINDING.json", "0" * 64)
    assert not called


def test_log_drainer_never_writes_past_combined_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(actor, "MAX_OUTPUT_BYTES", 10)
    output = tmp_path / "bounded.log"
    output_stream = output.open("xb")
    total = [0]
    lock = actor.threading.Lock()
    exceeded = actor.threading.Event()
    errors = []
    worker = actor._start_bounded_drainer(io.BytesIO(b"0123456789abcdef"), output_stream, output,
                                          total=total, lock=lock, output_exceeded=exceeded,
                                          output_errors=errors)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert output.read_bytes() == b"0123456789"
    assert total == [10]
    assert exceeded.is_set()
    assert errors == []


@pytest.mark.skipif(sys.platform != "darwin", reason="process-group identity uses Darwin psutil/os APIs")
def test_owned_python_sleeper_is_revalidated_and_stopped_through_original_popen():
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        rows = actor._snapshot_owned_processes(child, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0")
        assert len(rows) == 1
        assert rows[0].process_id == child.pid
        assert rows[0].parent_pid == os.getpid()
        assert rows[0].process_group_id == child.pid
        assert rows[0].session_id == child.pid
        cleanup = actor._terminate_owned(child, rows, deadline_seconds=3)
        assert cleanup["original_popen_returncode"] is not None
        assert cleanup["owned_group_absent"] is True
        assert cleanup["remaining_owned_group_or_session_pids"] == []
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "darwin", reason="task-owned process groups are verified on Darwin")
@pytest.mark.parametrize("fault", ["identity-refresh", "hold-receipt-write", "keyboard-interrupt"])
def test_live_original_owner_retains_harmless_popen_through_unknown_fault(tmp_path, monkeypatch, fault):
    logs = tmp_path / "logs"
    evidence = tmp_path / "evidence"
    logs.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    owner = actor.W25OriginalProcessOwner(tmp_path, logs / "stdout", logs / "stderr")
    owner.open_logs()
    child = owner.popen([sys.executable, "-I", "-c", "import time; print('owned', flush=True); time.sleep(1.2)"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        cwd=str(tmp_path), env={"PATH": os.defpath}, start_new_session=True,
                        close_fds=True, bufsize=0)
    assert owner.process is child and type(child) is subprocess.Popen and child.poll() is None
    assert owner._original_popen is child
    assert owner.process_pid == child.pid and str(tmp_path) in actor._RETAINED_OWNERS

    def forbidden_signal(*_args, **_kwargs):
        pytest.fail("UNKNOWN hold may not signal an unidentified or uncertain child/group")

    monkeypatch.setattr(child, "terminate", forbidden_signal)
    monkeypatch.setattr(child, "kill", forbidden_signal)
    if fault == "identity-refresh":
        def failed_snapshot(*_args, **_kwargs):
            raise PermissionError("synthetic process identity refresh fault")
        monkeypatch.setattr(actor, "_snapshot_owned_processes", failed_snapshot)
        cleanup = owner.cleanup_or_hold([], deadline_seconds=1)
    else:
        if fault == "hold-receipt-write":
            monkeypatch.setattr(actor, "_write_new", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic evidence write fault")))
        interrupt = KeyboardInterrupt("synthetic interruption after original Popen birth")
        owner.passive_hold(interrupt)
        cleanup = owner.cleanup

    assert owner.process is child
    assert owner.hold_entered is True and owner.released is True
    assert child.returncode == 0
    assert cleanup["signals_during_unknown_hold"] == 0
    assert all(not thread.is_alive() for thread in owner.drainers.values())
    assert owner.first_failure is not None
    hold_path = evidence / "W25_ORIGINAL_OWNER_HOLD.json"
    if fault == "hold-receipt-write":
        assert not hold_path.exists()
        assert any(row["phase"] == "write_original_owner_hold_receipt" for row in owner.diagnostic_errors)
    else:
        hold_receipt = json.loads(hold_path.read_text())
        assert hold_receipt["status"] == "UNKNOWN_ORIGINAL_POPEN_RETAINED_PASSIVE_POLL"
        assert hold_receipt["popen_state"] == "ALIVE"
        assert hold_receipt["signals_during_unknown_hold"] == 0
    assert str(tmp_path) not in actor._RETAINED_OWNERS



@pytest.mark.parametrize("code", [-25204, -25212])
def test_only_transient_axwindows_identity_mismatch_may_trigger_owned_reselection(code):
    last = {"kind": "AX_ERROR", "ax_error_code": code, "attribute": "AXWindows",
            "scope": "application_window_map", "readiness_transient": True}
    permitted = DesktopOperationError("WINDOW_IDENTITY_CHANGED", "transient map mismatch",
        diagnostics={"scope": "readiness_revalidation", "last_ax_error": last})
    assert actor._is_transient_owned_window_change(permitted)
    for error in (
        DesktopOperationError("AX_ATTRIBUTE_UNAVAILABLE", "unsupported", diagnostics=last),
        DesktopOperationError("WINDOW_IDENTITY_CHANGED", "API disabled", diagnostics={
            "scope": "readiness_revalidation", "last_ax_error": {**last, "ax_error_code": -25211, "readiness_transient": False}}),
        DesktopOperationError("WINDOW_IDENTITY_CHANGED", "wrong scope", diagnostics={
            "scope": "other", "last_ax_error": last}),
    ):
        assert not actor._is_transient_owned_window_change(error)


def test_r6_install_root_is_exact_and_keeps_r5_namespace_rejected():
    old = Path("/private/tmp") / f"cvw26-r5install-{uuid.uuid4().hex[:8]}"
    old.mkdir(mode=0o700)
    try:
        with pytest.raises(RuntimeError, match="cvw26-r6install"):
            actor._secure_installed_root(old)
    finally:
        old.rmdir()
    fresh = Path("/private/tmp") / f"cvw26-r6install-{uuid.uuid4().hex[:8]}"
    fresh.mkdir(mode=0o700)
    try:
        assert actor._secure_installed_root(fresh)[1] == {"device": fresh.stat().st_dev, "inode": fresh.stat().st_ino}
    finally:
        fresh.rmdir()


@pytest.mark.skipif(sys.platform != "darwin", reason="original Popen/session proof uses Darwin process groups")
def test_reselected_quartz_identity_must_be_fresh_and_rooted_in_same_original_popen():
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(20)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        time.sleep(0.15)
        rows = actor._snapshot_owned_processes(child, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0")
        root_row = rows[0]
        identity = WindowIdentity("macos", "CGWindow:321", child.pid, root_row.process_birth,
                                  f"uid:{os.getuid()}", f"process-session:{child.pid}", "6.4.0")
        proof = actor._same_original_root_window_proof(identity, rows, child)
        assert proof["same_original_popen_object"] is True
        assert proof["root_live_before_after"] is True
        assert proof["window_owner_birth"] == root_row.process_birth
        changed_birth = WindowIdentity("macos", "CGWindow:322", child.pid, "1.000000",
                                       f"uid:{os.getuid()}", f"process-session:{child.pid}", "6.4.0")
        with pytest.raises(RuntimeError, match="original Popen process tree"):
            actor._same_original_root_window_proof(changed_birth, rows, child)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "darwin", reason="original Popen/session proof uses Darwin process groups")
def test_reselection_record_persists_new_native_id_before_any_ax_retry(tmp_path):
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(20)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    try:
        time.sleep(0.15)
        rows = actor._snapshot_owned_processes(child, bundle_identifier="com.comsol.comsol", comsol_version="6.4.0")
        row = rows[0]
        previous = WindowIdentity("macos", "CGWindow:320", child.pid, row.process_birth,
                                  f"uid:{os.getuid()}", f"process-session:{child.pid}", "6.4.0")
        current = WindowIdentity("macos", "CGWindow:321", child.pid, row.process_birth,
                                 f"uid:{os.getuid()}", f"process-session:{child.pid}", "6.4.0")
        record = actor._persist_owned_window_reselection(tmp_path, candidate_sha256="a" * 64,
            binding_sha256="b" * 64, ordinal=1, previous_identity=previous, identity=current,
            rows=rows, root_process=child)
        saved = json.loads(Path(record["path"]).read_text())
        assert saved["identity"] == actor._window_identity_value(current)
        assert saved["identity_fingerprint"] == current.fingerprint()
        assert saved["previous_identity_fingerprint"] == previous.fingerprint()
        assert saved["same_original_root_proof"]["original_popen_pid"] == child.pid
        assert saved["same_original_root_proof"]["same_original_popen_object"] is True
        assert not {"title", "bounds", "owner_name", "accessibility_tree", "pixels"} & set(saved)
        mismatched = WindowIdentity("macos", "CGWindow:322", child.pid, "1.000000",
                                   f"uid:{os.getuid()}", f"process-session:{child.pid}", "6.4.0")
        with pytest.raises(RuntimeError, match="original Popen process tree"):
            actor._persist_owned_window_reselection(tmp_path, candidate_sha256="a" * 64,
                binding_sha256="b" * 64, ordinal=2, previous_identity=current, identity=mismatched,
                rows=rows, root_process=child)
        assert not (tmp_path / "OWNED_WINDOW_RESELECTION_02.json").exists()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_r6_shared_readiness_deadline_bounds_identity_reselection_wait():
    identity = _readiness_identity()
    clock = _Clock()
    class Observer:
        def enumerate_windows(self):
            return [SimpleNamespace(identity=identity)]
    class Adapter:
        def inspect_accessibility(self, *_args, **_kwargs):
            raise _transient_ax_error(-25204)
    with pytest.raises(DesktopOperationError) as caught:
        actor._inspect_owned_window_with_readiness(Observer(), Adapter(), identity, deadline=100,
            readiness_deadline=1.5, clock=clock.now, sleeper=clock.sleep)
    assert caught.value.code == "AX_READINESS_TIMEOUT"
    assert clock.value == 1.5
    assert caught.value.diagnostics["last_ax_error"]["ax_error_code"] == -25204
