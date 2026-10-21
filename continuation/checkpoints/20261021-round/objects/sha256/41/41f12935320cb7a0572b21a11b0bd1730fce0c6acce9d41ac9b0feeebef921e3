from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from comsol_mcp._desktop_macos import (
    MacOSDesktopAdapter,
    TaskOwnedMacOSProcessIdentity,
    TaskOwnedMacOSWindowObserver,
    macos_process_command_sha256,
)
from comsol_mcp._desktop_service import DesktopOperationError

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="task-owned process/session observation is macOS-specific")

OWNER_PID = "owner_pid"
WINDOW_ID = "window_id"
WINDOW_NAME = "window_name"
OWNER_NAME = "owner_name"
BOUNDS = "bounds"
PROCESS_ID = 43210
EXECUTABLE = "/Applications/COMSOL64/Multiphysics/COMSOL Multiphysics.app/Contents/MacOS/ComsolLauncher"
ARGV = [EXECUTABLE, "-prefsdir", "/private/tmp/w25-owned/prefs", "-np", "1"]


class AccessTrapWindowRow:
    """Mapping double that makes forbidden foreign-window reads fail loudly."""

    def __init__(self, values, *, forbidden=()):
        self.values = dict(values)
        self.forbidden = set(forbidden)
        self.reads = []

    def get(self, key, default=None):
        self.reads.append(key)
        if key in self.forbidden:
            raise AssertionError(f"forbidden window property read: {key}")
        return self.values.get(key, default)


class FakeQuartz:
    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 2
    kCGNullWindowID = 0
    kCGWindowOwnerPID = OWNER_PID
    kCGWindowNumber = WINDOW_ID
    kCGWindowName = WINDOW_NAME
    kCGWindowOwnerName = OWNER_NAME
    kCGWindowBounds = BOUNDS

    def __init__(self, rows, *, on_list=None):
        self.rows = rows
        self.calls = []
        self.on_list = on_list

    def CGWindowListCopyWindowInfo(self, options, window_id):
        self.calls.append((options, window_id))
        if self.on_list is not None:
            self.on_list()
        return self.rows


class FakeProcess:
    def __init__(self, *, pid=PROCESS_ID, birth=1730000000.123456, uid=None,
                 ppid=None, executable=EXECUTABLE, argv=None):
        self.pid = pid
        self._birth = birth
        self._uid = os.getuid() if uid is None else uid
        self._ppid = os.getpid() if ppid is None else ppid
        self._executable = executable
        self._argv = list(ARGV if argv is None else argv)

    def create_time(self):
        return self._birth

    def uids(self):
        return SimpleNamespace(real=self._uid)

    def ppid(self):
        return self._ppid

    def exe(self):
        return self._executable

    def cmdline(self):
        return list(self._argv)


def _owned_identity(process: FakeProcess, *, command_sha256=None):
    return TaskOwnedMacOSProcessIdentity(
        process_id=process.pid,
        process_birth=f"{process._birth:.6f}",
        real_uid=process._uid,
        parent_pid=process._ppid,
        process_group_id=process.pid,
        session_id=process.pid,
        executable_path=process._executable,
        command_sha256=command_sha256 or macos_process_command_sha256(process._executable, process._argv),
        bundle_identifier="com.comsol.comsol",
        comsol_version="6.4.0",
    )


def _spawn_original_root():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _finish_original_root(original_root):
    if original_root.poll() is None:
        original_root.terminate()
    try:
        original_root.wait(timeout=5)
    except subprocess.TimeoutExpired:
        original_root.kill()
        original_root.wait(timeout=5)


def _observer(rows, process, *, original_root, command_sha256=None, expected_process=None,
              observed_group_delta=0, observed_session_delta=0, quartz_on_list=None):
    process.pid = original_root.pid
    if expected_process is not None:
        expected_process.pid = original_root.pid
    for row in rows:
        if row.values.get(OWNER_PID) == PROCESS_ID:
            row.values[OWNER_PID] = original_root.pid
    quartz = FakeQuartz(rows, on_list=quartz_on_list)
    psutil = SimpleNamespace(Process=lambda pid: process if pid == process.pid else (_ for _ in ()).throw(ProcessLookupError(pid)))
    observer = TaskOwnedMacOSWindowObserver(
        [_owned_identity(expected_process or process, command_sha256=command_sha256)],
        original_root=original_root,
    )
    patches = [
        patch.dict("sys.modules", {"Quartz": quartz, "psutil": psutil}),
        patch("comsol_mcp._desktop_macos.os.getpgid", return_value=original_root.pid + observed_group_delta),
        patch("comsol_mcp._desktop_macos.os.getsid", return_value=original_root.pid + observed_session_delta),
    ]
    for item in patches:
        item.start()
    return observer, quartz, patches, original_root


def _stop_patches(patches):
    for item in reversed(patches):
        item.stop()


def test_task_owned_observer_discards_foreign_rows_before_title_or_bounds_access():
    foreign = AccessTrapWindowRow(
        {OWNER_PID: 99881, WINDOW_ID: 9, WINDOW_NAME: "private foreign title", BOUNDS: {"X": 1}},
        forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS},
    )
    owned = AccessTrapWindowRow(
        {OWNER_PID: PROCESS_ID, WINDOW_ID: 42, WINDOW_NAME: "owned title", BOUNDS: {"X": 2}},
        forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS},
    )
    process = FakeProcess()
    original_root = _spawn_original_root()
    observer, quartz, patches, original_root = _observer([foreign, owned], process, original_root=original_root)
    try:
        adapter = MacOSDesktopAdapter(identity_observer=observer)
        rows = adapter.enumerate_windows()
        assert len(rows) == 1
        assert rows[0].identity.process_id == process.pid
        assert rows[0].identity.native_window_id == "CGWindow:42"
        assert rows[0].title == ""
        assert foreign.reads == [OWNER_PID]
        assert owned.reads == [OWNER_PID, WINDOW_ID]
        assert len(quartz.calls) == 1
    finally:
        _stop_patches(patches)
        _finish_original_root(original_root)


def test_accessibility_revalidation_uses_the_same_task_owned_provider_only():
    foreign = AccessTrapWindowRow(
        {OWNER_PID: 99881, WINDOW_ID: 9, WINDOW_NAME: "private foreign title", BOUNDS: {"X": 1}},
        forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS},
    )
    owned = AccessTrapWindowRow(
        {OWNER_PID: PROCESS_ID, WINDOW_ID: 42, WINDOW_NAME: "owned title", BOUNDS: {"X": 2}},
        forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS},
    )
    process = FakeProcess()
    original_root = _spawn_original_root()
    observer, quartz, patches, original_root = _observer([foreign, owned], process, original_root=original_root)
    seen = []
    try:
        adapter = MacOSDesktopAdapter(
            identity_observer=observer,
            gateway=SimpleNamespace(window_tree=lambda identity, **limits: seen.append((identity, limits)) or {
                "nodes": [{"path": [], "attributes": {"AXRole": "AXWindow"}}],
                "truncated": False,
                "scope": "TARGET_WINDOW_AX_METADATA_ONLY",
                "model_binding": "UNVERIFIED",
            }),
        )
        identity = adapter.enumerate_windows()[0].identity
        result = adapter.inspect_accessibility(identity, max_depth=2, max_nodes=64)
        assert result["native_evidence"] == "NOT_RUN"
        assert result["window_identity_fingerprint"] == identity.fingerprint()
        assert seen == [(identity, {"max_depth": 2, "max_nodes": 64})]
        assert len(quartz.calls) == 3  # initial identity plus pre/post AX revalidation
        assert foreign.reads == [OWNER_PID] * len(quartz.calls)
        assert all(key in (OWNER_PID, WINDOW_ID) for key in owned.reads)
        assert WINDOW_NAME not in foreign.reads and BOUNDS not in foreign.reads
    finally:
        _stop_patches(patches)
        _finish_original_root(original_root)


def test_pid_reuse_or_command_drift_rejects_before_any_quartz_inventory():
    row = AccessTrapWindowRow({OWNER_PID: PROCESS_ID, WINDOW_ID: 42}, forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS})
    expected = FakeProcess(birth=1730000000.123456)
    process = FakeProcess(birth=1730000001.000000)
    original_root = _spawn_original_root()
    observer, quartz, patches, original_root = _observer([row], process, original_root=original_root, expected_process=expected)
    try:
        with pytest.raises(DesktopOperationError) as caught:
            observer.enumerate_windows()
        assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
        assert quartz.calls == []
        assert row.reads == []
    finally:
        _stop_patches(patches)
        _finish_original_root(original_root)


def test_command_drift_rejects_before_any_quartz_inventory():
    row = AccessTrapWindowRow({OWNER_PID: PROCESS_ID, WINDOW_ID: 42}, forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS})
    process = FakeProcess()
    original_root = _spawn_original_root()
    observer, quartz, patches, original_root = _observer([row], process, original_root=original_root, command_sha256="a" * 64)
    try:
        with pytest.raises(DesktopOperationError) as caught:
            observer.enumerate_windows()
        assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
        assert "command" in str(caught.value)
        assert quartz.calls == []
        assert row.reads == []
    finally:
        _stop_patches(patches)
        _finish_original_root(original_root)


@pytest.mark.parametrize(
    "drift",
    [
        "uid",
        "parent",
        "group",
        "session",
    ],
)
def test_uid_parent_or_session_group_drift_rejects_before_quartz_inventory(
    drift,
):
    row = AccessTrapWindowRow({OWNER_PID: PROCESS_ID, WINDOW_ID: 42}, forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS})
    expected = FakeProcess()
    process = FakeProcess(
        uid=os.getuid() + 1 if drift == "uid" else None,
        ppid=os.getpid() + 1 if drift == "parent" else None,
    )
    original_root = _spawn_original_root()
    observer, quartz, patches, original_root = _observer(
        [row], process, original_root=original_root, expected_process=expected,
        observed_group_delta=1 if drift == "group" else 0,
        observed_session_delta=1 if drift == "session" else 0,
    )
    try:
        with pytest.raises(DesktopOperationError) as caught:
            observer.enumerate_windows()
        assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
        assert quartz.calls == []
        assert row.reads == []
    finally:
        _stop_patches(patches)
        _finish_original_root(original_root)


def test_multiple_owned_windows_fail_closed_without_inspecting_window_contents():
    rows = [
        AccessTrapWindowRow({OWNER_PID: PROCESS_ID, WINDOW_ID: 42}, forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS}),
        AccessTrapWindowRow({OWNER_PID: PROCESS_ID, WINDOW_ID: 43}, forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS}),
    ]
    process = FakeProcess()
    original_root = _spawn_original_root()
    observer, quartz, patches, original_root = _observer(rows, process, original_root=original_root)
    try:
        with pytest.raises(DesktopOperationError) as caught:
            observer.enumerate_windows()
        assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
        assert "multiple visible windows" in str(caught.value)
        assert len(quartz.calls) == 1
        assert all(row.reads == [OWNER_PID, WINDOW_ID] for row in rows)
    finally:
        _stop_patches(patches)
        _finish_original_root(original_root)


def test_command_digest_is_bound_to_executable_path_and_exact_argv():
    first = macos_process_command_sha256(EXECUTABLE, ARGV)
    assert len(first) == 64
    assert first != macos_process_command_sha256(EXECUTABLE, [*ARGV, "-open", "other.mph"])
    assert first != macos_process_command_sha256("/other/comsol", ARGV)
    assert first == hashlib.sha256(json.dumps([EXECUTABLE, ARGV], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(ValueError, match="absolute executable and string argv"):
        macos_process_command_sha256(EXECUTABLE, [])


def test_owned_snapshot_rejects_process_ancestry_that_does_not_reach_popen_root():
    original_root = _spawn_original_root()
    uid = os.getuid()
    digest = macos_process_command_sha256(EXECUTABLE, ARGV)

    def record(pid, parent):
        return TaskOwnedMacOSProcessIdentity(
            process_id=pid,
            process_birth=f"{1730000000 + pid / 1000000:.6f}",
            real_uid=uid,
            parent_pid=parent,
            process_group_id=original_root.pid,
            session_id=original_root.pid,
            executable_path=EXECUTABLE,
            command_sha256=digest,
            bundle_identifier="com.comsol.comsol",
            comsol_version="6.4.0",
        )

    root = record(original_root.pid, os.getpid())
    left = record(original_root.pid + 1, original_root.pid + 2)
    right = record(original_root.pid + 2, original_root.pid + 1)
    try:
        with pytest.raises(ValueError, match="cycle or an unrooted descendant"):
            TaskOwnedMacOSWindowObserver([root, left, right], original_root=original_root)
    finally:
        _finish_original_root(original_root)


def test_forged_supervisor_parent_and_removed_supervisor_argument_are_rejected():
    original_root = _spawn_original_root()
    process = FakeProcess(pid=original_root.pid, ppid=os.getpid() + 1)
    try:
        serialized_handle = SimpleNamespace(pid=original_root.pid, poll=lambda: None)
        with pytest.raises(ValueError, match="original Popen root handle"):
            TaskOwnedMacOSWindowObserver([_owned_identity(process)], original_root=serialized_handle)
        with pytest.raises(ValueError, match="original Popen root spawned by this supervisor"):
            TaskOwnedMacOSWindowObserver([_owned_identity(process)], original_root=original_root)
        with pytest.raises(TypeError):
            TaskOwnedMacOSWindowObserver([_owned_identity(process)], original_root=original_root,
                                         supervisor_pid=os.getpid())
    finally:
        _finish_original_root(original_root)


def test_owned_snapshot_root_pid_must_equal_original_popen_pid():
    original_root = _spawn_original_root()
    process = FakeProcess(pid=original_root.pid + 1, ppid=os.getpid())
    try:
        with pytest.raises(ValueError, match="match the original Popen root"):
            TaskOwnedMacOSWindowObserver([_owned_identity(process)], original_root=original_root)
    finally:
        _finish_original_root(original_root)


def test_terminal_original_popen_is_rejected_before_quartz_inventory():
    original_root = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    original_root.wait(timeout=5)
    process = FakeProcess(pid=original_root.pid, ppid=os.getpid())
    with pytest.raises(DesktopOperationError) as caught:
        TaskOwnedMacOSWindowObserver([_owned_identity(process)], original_root=original_root)
    assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"


def test_original_popen_exit_during_inventory_fails_closed_without_takeover():
    row = AccessTrapWindowRow({OWNER_PID: PROCESS_ID, WINDOW_ID: 42}, forbidden={WINDOW_NAME, OWNER_NAME, BOUNDS})
    process = FakeProcess()
    original_root = _spawn_original_root()

    def terminate_original():
        if original_root.poll() is None:
            original_root.terminate()
            original_root.wait(timeout=5)

    observer, quartz, patches, original_root = _observer(
        [row], process, original_root=original_root, quartz_on_list=terminate_original,
    )
    try:
        with pytest.raises(DesktopOperationError) as caught:
            observer.enumerate_windows()
        assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
        assert "original task-owned Popen root is terminal" in str(caught.value)
        assert observer._original_root is original_root
        assert original_root.poll() is not None
        assert len(quartz.calls) == 1
    finally:
        _stop_patches(patches)
        _finish_original_root(original_root)
