from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def test_production_startup_gate_checks_journal_under_daemon_lock(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    production_source = repository / "comsol_mcp/_control_daemon.py"
    source_text = production_source.read_text(encoding="utf-8")
    assert "from ._state_backup import assert_no_pending_restore" in source_text
    assert "assert_no_pending_restore(home)" in source_text
    isolated = tmp_path / "isolated-source"
    shutil.copytree(
        repository / "comsol_mcp",
        isolated / "comsol_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    integration_script = r'''
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from comsol_mcp import _control_daemon, _state_backup

home = Path(sys.argv[2])
mode = sys.argv[3]
home.mkdir(parents=True)
if mode == "pending":
    (home / _state_backup.ACTIVE_RESTORE_JOURNAL).write_text("incomplete journal")

events = []
probe_code = (
    "import sys; from pathlib import Path; "
    "from comsol_mcp._managed_backend import ProcessLock; "
    "path=Path(sys.argv[1])/'control.lock'; "
    "\ntry: lock=ProcessLock(path)"
    "\nexcept Exception: print('BUSY')"
    "\nelse: lock.close(); print('FREE')"
)
original_gate = _state_backup.assert_no_pending_restore
def observed_gate(control_home):
    probe = subprocess.run(
        [sys.executable, "-c", probe_code, str(control_home)],
        capture_output=True, text=True, check=False, timeout=15,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "BUSY":
        raise AssertionError("daemon control lock was not held during startup gate")
    events.append("gate")
    original_gate(control_home)
_state_backup.assert_no_pending_restore = observed_gate

class ReachedDaemon(Exception):
    pass
def daemon_constructor(_home):
    events.append("daemon")
    raise ReachedDaemon()
_control_daemon.ControlDaemon = daemon_constructor

database = home / _state_backup.DATABASE_NAME
if mode == "pending":
    try:
        _control_daemon.serve(home)
    except _state_backup.StateBackupError as error:
        assert error.code == "RESTORE_RECOVERY_REQUIRED", error.code
        assert events == ["gate"], events
        assert not database.exists(), "OperationStore/database setup ran before the gate"
        released = subprocess.run(
            [sys.executable, "-c", probe_code, str(home)],
            capture_output=True, text=True, check=False, timeout=15,
        )
        assert released.returncode == 0 and released.stdout.strip() == "FREE"
        print(json.dumps({"mode": mode, "events": events, "database_opened": False,
                          "lock_held_during_gate": True, "lock_released_after_rejection": True}))
    else:
        raise AssertionError("startup accepted an incomplete restore journal")
else:
    try:
        _control_daemon.serve(home)
    except ReachedDaemon:
        assert events == ["gate", "daemon"], events
        assert not database.exists(), "OperationStore/database setup ran before the test sentinel"
        print(json.dumps({"mode": mode, "events": events, "database_opened": False,
                          "lock_held_during_gate": True}))
    else:
        raise AssertionError("test daemon-constructor sentinel was not reached")
'''

    for mode in ("pending", "clear"):
        control_home = tmp_path / f"control-{mode}"
        result = subprocess.run(
            [sys.executable, "-c", integration_script, str(isolated), str(control_home), mode],
            cwd=isolated,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        observation = json.loads(result.stdout.strip().splitlines()[-1])
        assert observation["events"] == (["gate"] if mode == "pending" else ["gate", "daemon"])
        assert observation["database_opened"] is False
        assert observation["lock_held_during_gate"] is True
        if mode == "pending":
            assert observation["lock_released_after_rejection"] is True
