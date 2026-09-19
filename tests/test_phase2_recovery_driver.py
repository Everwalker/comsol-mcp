"""Guardrails for the real recovery acceptance driver.

Live T027/T028/T035 evidence is intentionally not simulated in pytest.  These
tests keep its production transport and fault-target preconditions intact.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from comsol_mcp._operation_store import OperationStore


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("phase2_recovery_mcp", ROOT / "tools" / "phase2_recovery_mcp.py")
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def test_driver_uses_stdio_client_sessions_and_never_imports_control_client():
    source = (ROOT / "tools" / "phase2_recovery_mcp.py").read_text(encoding="utf-8")
    assert "stdio_client" in source
    assert "ClientSession" in source
    assert "_control_client" not in source
    assert 'call("run_study"' in source
    assert "same_key_retry" in source
    assert 'call("model_inspect"' in source
    assert "control_plane_samples" in source
    assert "ExecutionDeadlineExceeded" in source


def test_control_home_matches_production_private_layout(tmp_path: Path):
    assert driver._control_home(tmp_path) == tmp_path.resolve() / "control-private"


def test_model_ref_accepts_saved_execution_envelope(tmp_path: Path):
    ref = {"schema_version": 1, "session_id": "s", "server_instance_id": "srv", "model_tag": "m", "generation": 2}
    path = tmp_path / "ref.json"
    path.write_text(__import__("json").dumps({"execution": {"model_ref": ref}}), encoding="utf-8")
    assert driver._model_ref(path) == ref


def test_model_ref_rejects_non_identity_input(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no model_ref"):
        driver._model_ref(path)


def test_execution_identity_requires_a_bound_ref_and_integer_revision():
    ref = {"session_id": "s", "model_tag": "m"}
    assert driver._execution_identity({"execution": {"model_ref": ref, "revision": 3}}) == (ref, 3)
    assert driver._execution_identity({"execution": {"model_ref": ref, "revision": True}}) == (ref, None)
    assert driver._execution_identity({"execution": {"model_ref": "m", "revision": 3}}) == (None, 3)


def test_evidence_redaction_removes_private_paths_and_tokens():
    value = driver._redact({
        "token": "secret", "argument": "/repo/.phase1-private/prefs",
        "private_credential_path_denied": True, "credential_content_not_logged": False,
        "authorization_attempt_count": 3,
    })
    assert value == {
        "token": "REDACTED", "argument": "REDACTED_PATH",
        "private_credential_path_denied": True, "credential_content_not_logged": False,
        "authorization_attempt_count": 3,
    }


def test_verified_pid_rejects_registered_comsol_pid_before_ps():
    with pytest.raises(RuntimeError, match="protected"):
        driver._verified_pid({"pid": 84749}, "daemon", Path("/private/control"), 84749)


def test_windows_verified_pid_requires_endpoint_birth_and_exact_private_command(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(driver, "_is_windows", lambda: True)
    home = Path(r"C:\Users\Test User\控制\worker")
    endpoint = {"pid": 28912, "process_start_epoch_ms": driver._windows_birth_epoch_ms("2026-09-19T04:57:48.6336100Z")}
    snapshot = {
        "pid": 28912,
        "parent_pid": 34332,
        "creation_utc": "2026-09-19T04:57:48.6336100Z",
        "executable_path": r"C:\Program Files\Eclipse Adoptium\jdk-11\bin\java.exe",
        "command_line": r'"C:\Program Files\Eclipse Adoptium\jdk-11\bin\java.exe" -Dcs.prefsdir="C:\Users\Test User\控制\prefs" -cp "C:\Users\Test User\控制\classes;C:\Program Files\COMSOL\plugins\*" comsol_mcp.worker_java.PersistentComsolWorker --port 0 --endpoint-file "C:\Users\Test User\控制\worker\worker_endpoint.json" --server-lock-root "C:\Users\Test User\控制\locks" --generation 4',
    }
    monkeypatch.setattr(driver, "_windows_process_snapshot", lambda pid: snapshot if pid == 28912 else None)
    identity = driver._verified_pid(endpoint, "comsol_mcp.worker_java.PersistentComsolWorker", home, 26076)
    assert identity["pid"] == 28912
    assert identity["process_start_epoch_ms"] == endpoint["process_start_epoch_ms"]

    endpoint["process_start_epoch_ms"] += 1
    with pytest.raises(RuntimeError, match="creation identity"):
        driver._verified_pid(endpoint, "comsol_mcp.worker_java.PersistentComsolWorker", home, 26076)

    endpoint["process_start_epoch_ms"] = driver._windows_birth_epoch_ms("2026-09-19T04:57:48.6336100Z")
    with pytest.raises(RuntimeError, match="command/home"):
        driver._verified_pid(endpoint, "comsol_mcp.worker_java.PersistentComsolWorker", home / "wrong", 26076)


def test_windows_command_identity_requires_one_module_invocation_and_one_home():
    home = Path(r"C:\Users\Test User\控制\control-private")
    command = r'"C:\Python\python.exe" -m comsol_mcp._control_daemon --home "C:\Users\Test User\控制\control-private"'
    assert driver._windows_command_matches(command, "comsol_mcp._control_daemon", home)
    assert not driver._windows_command_matches(command + " --home other", "comsol_mcp._control_daemon", home)
    assert not driver._windows_command_matches(command.replace(" -m comsol_mcp._control_daemon", " comsol_mcp._control_daemon"), "comsol_mcp._control_daemon", home)


def test_windows_external_control_argv_is_a_list_with_unicode_home():
    args = type("Args", (), {"python": "/tmp/Test User/venv/Scripts/python.exe"})()
    home = Path(r"C:\Users\Test User\控制\control-private")
    assert driver._windows_external_control_command(args, home) == [
        str(Path(args.python).resolve()),
        "-m", "comsol_mcp._control_daemon", "--home", str(home),
    ]


def test_windows_external_control_pair_checks_redirector_child_and_selected_python(monkeypatch):
    monkeypatch.setattr(driver, "_is_windows", lambda: True)
    home = Path(r"C:\Users\Test User\控制\control-private")
    endpoint = {
        "pid": 28912,
        "process_start_epoch_ms": driver._windows_birth_epoch_ms("2026-09-19T04:57:48.6336100Z"),
    }
    snapshots = {
        34332: {
            "pid": 34332, "parent_pid": 7000,
            "creation_utc": "2026-09-19T04:57:47.6336100Z",
            "executable_path": r"C:\Users\Test User\venv\Scripts\python.exe",
            "command_line": r'"C:\Users\Test User\venv\Scripts\python.exe" -m comsol_mcp._control_daemon --home "C:\Users\Test User\控制\control-private"',
        },
        28912: {
            "pid": 28912, "parent_pid": 34332,
            "creation_utc": "2026-09-19T04:57:48.6336100Z",
            "executable_path": r"C:\Python312-comsol-mcp\python.exe",
            "command_line": r'"C:\Python312-comsol-mcp\python.exe" -m comsol_mcp._control_daemon --home "C:\Users\Test User\控制\control-private"',
        },
    }
    monkeypatch.setattr(driver, "_windows_process_snapshot", lambda pid: snapshots.get(pid))

    class FakeProcess:
        pid = 34332
        def poll(self):
            return None

    identity = driver._validate_windows_external_control_pair(
        FakeProcess(), endpoint, home, r"C:\Python312-comsol-mcp\python.exe", 26076,
    )
    assert identity["windows_redirector"] is True
    assert identity["daemon_pid"] == 28912
    assert identity["popen_handle_held"] is True

    snapshots[28912]["parent_pid"] = 99999
    with pytest.raises(RuntimeError, match="direct child"):
        driver._validate_windows_external_control_pair(
            FakeProcess(), endpoint, home, r"C:\Python312-comsol-mcp\python.exe", 26076,
        )


def test_windows_external_preflight_fails_closed_without_existing_endpoint(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(driver, "_is_windows", lambda: True)
    args = type("Args", (), {"private_home": tmp_path, "comsol_pid": 26076})()
    with pytest.raises(RuntimeError, match="prestarted external control daemon"):
        driver._require_windows_external_control(args)


def test_windows_external_preflight_requires_healthy_verified_endpoint(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(driver, "_is_windows", lambda: True)
    control = tmp_path / "control-private"
    control.mkdir()
    endpoint = {"pid": 28912, "port": 49152, "token": "private"}
    target = {
        "pid": 28912,
        "process_start_epoch_ms": 123,
        "executable_path": r"C:\Python312-comsol-mcp\python.exe",
    }
    monkeypatch.setattr(driver, "_read_json", lambda path: endpoint)
    monkeypatch.setattr(driver, "_verified_pid", lambda *args: target)
    monkeypatch.setattr(driver, "_selected_python_base_executable", lambda *args: target["executable_path"])
    monkeypatch.setattr(driver, "_control_request", lambda *args: {"success": True})
    args = type("Args", (), {
        "private_home": tmp_path,
        "comsol_pid": 26076,
        "python": r"C:\Users\Test User\venv\Scripts\python.exe",
        "comsol_root": r"C:\Program Files\COMSOL\COMSOL64\Multiphysics",
        "jdk11": r"C:\Users\Test User\jdk11",
        "prefs": tmp_path / "prefs",
    })()
    result = driver._require_windows_external_control(args)
    assert result["daemon_pid"] == 28912
    assert result["health_success"] is True
    assert result["base_executable_match"] is True


def test_windows_external_preflight_rejects_verified_endpoint_from_other_python(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(driver, "_is_windows", lambda: True)
    control = tmp_path / "control-private"
    control.mkdir()
    birth = "2026-09-19T04:57:48.6336100Z"
    endpoint = {
        "pid": 28912,
        "port": 49152,
        "token": "private",
        "process_start_epoch_ms": driver._windows_birth_epoch_ms(birth),
    }
    (control / "control.json").write_text(json.dumps(endpoint), encoding="utf-8")
    snapshot = {
        "pid": 28912,
        "parent_pid": 34332,
        "creation_utc": birth,
        "executable_path": r"C:\OtherPython\python.exe",
        "command_line": f'"C:\\OtherPython\\python.exe" -m comsol_mcp._control_daemon --home "{control}"',
    }
    monkeypatch.setattr(driver, "_windows_process_snapshot", lambda pid: snapshot if pid == 28912 else None)
    selected_base = r"C:\Python312-comsol-mcp\python.exe"
    monkeypatch.setattr(driver, "_selected_python_base_executable", lambda *args: selected_base)
    monkeypatch.setattr(driver, "_control_request", lambda *args: pytest.fail("health must not run after identity rejection"))
    args = type("Args", (), {
        "private_home": tmp_path,
        "comsol_pid": 26076,
        "python": r"C:\Users\Test User\venv\Scripts\python.exe",
        "comsol_root": r"C:\Program Files\COMSOL\COMSOL64\Multiphysics",
        "jdk11": r"C:\Users\Test User\jdk11",
        "prefs": tmp_path / "prefs",
    })()
    with pytest.raises(RuntimeError, match="prestarted external control daemon"):
        driver._require_windows_external_control(args)


@pytest.mark.parametrize(
    ("state", "scope"),
    [
        ("RUNNING", "active_job_recovery"),
        ("SUCCEEDED", "terminal_job_only"),
        ("FAILED", "terminal_job_only"),
        ("UNKNOWN", "unknown_or_lost_job"),
        ("LOST", "unknown_or_lost_job"),
        (None, "job_state_unavailable"),
    ],
)
def test_t028_recovery_scope_preserves_exact_fault_state(state, scope):
    assert driver._t028_recovery_scope(state) == scope


def test_runtime_preflight_requires_matching_java_environment_and_redacts_command():
    args = type("Args", (), {"python": "/Users/Test User/venv/bin/python", "jdk11": "/Library/JDK 11"})()
    env = {
        "COMSOL_ROOT": "/Applications/COMSOL64/Multiphysics",
        "COMSOL_JAVA_HOME": "/Library/JDK 11",
        "JAVA_HOME": "/Library/JDK 11",
        "COMSOL_PREFS_DIR": "/private/prefs",
        "COMSOL_SERVER_MCP_HOME": "/private/control-private",
        "PYTHONPATH": "/repo",
    }
    result = driver._runtime_preflight(args, env)
    assert result["java_home_match"] is True
    assert result["required_environment_keys_present"] is True
    assert result["command_shape"] == ["<selected-python>", "-m", "comsol_mcp.mcp_server"]
    assert "/Users/Test User" not in json.dumps(result)


def test_windows_termination_never_falls_back_to_os_kill(monkeypatch):
    calls = []

    class FakeHandle:
        def __init__(self, target):
            calls.append(("open", target["pid"]))

        def terminate_and_wait(self):
            calls.append(("terminate",))
            return 0

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(driver, "_is_windows", lambda: True)
    monkeypatch.setattr(driver, "_WindowsProcessHandle", FakeHandle)
    monkeypatch.setattr(driver.os, "kill", lambda *_: (_ for _ in ()).throw(AssertionError("Windows must not use os.kill")))
    driver._terminate_verified({"pid": 28912})
    assert calls == [("open", 28912), ("terminate",), ("close",)]


def test_posix_termination_keeps_existing_verified_signal_path(monkeypatch):
    calls = []
    monkeypatch.setattr(driver, "_is_windows", lambda: False)
    monkeypatch.setattr(driver.os, "kill", lambda pid, signal: calls.append((pid, signal)))
    monkeypatch.setattr(driver, "_wait_dead", lambda pid: True)
    driver._terminate_verified({"pid": 1234})
    assert calls == [(1234, driver.signal.SIGTERM)]


def test_job_row_reads_result_from_operations_join_in_real_sqlite_fixture(tmp_path: Path):
    control_home = tmp_path / "control-private"
    store = OperationStore(control_home / "operations.sqlite3")
    record, _ = store.begin(request_id="r", idempotency_key="k", request_hash="h", operation="run_study")
    store.update_job(record["job_id"], "RUNNING", {"phase": "solve"})
    store.finish(record["operation_id"], status="SUCCEEDED", result={"success": True, "data": {"value": 2}})
    store.close()

    row = driver._job_row(control_home, record["job_id"])
    assert row["job_id"] == record["job_id"]
    assert row["operation_id"] == record["operation_id"]
    assert json.loads(row["result"]) == {"success": True, "data": {"value": 2}}
    assert row["operation_count"] == 1


def test_worker_request_requires_pending_solver_run_not_preflight_or_completed_call():
    observed = {
        "status": {"data": {"status": "RUNNING"}},
        "log": {"data": {"events": [
            {"event": "worker_request", "metadata": {
                "phase": "submitted", "request_id": "snapshot", "kind": "model_snapshot",
                "metadata": {"method": "snapshot"},
            }},
            {"event": "worker_request", "metadata": {
                "phase": "submitted", "request_id": "label", "kind": "call",
                "metadata": {"method": "label"},
            }},
            {"event": "worker_request", "metadata": {
                "phase": "submitted", "request_id": "run-complete", "kind": "call",
                "metadata": {"method": "run"},
            }},
            {"event": "worker_request", "metadata": {
                "phase": "observed", "request_id": "run-complete", "kind": "call",
                "metadata": {"method": "run"},
            }},
        ]}},
    }
    assert driver._status(observed["status"]) == "RUNNING"
    assert driver._worker_request_submitted(observed) is False
    observed["log"]["data"]["events"].append({"event": "worker_request", "metadata": {
        "phase": "submitted", "request_id": "run-pending", "kind": "call", "metadata": {"method": "run"},
    }})
    assert driver._worker_request_submitted(observed) is True
    observed["log"]["data"]["events"].append({"event": "worker_request", "metadata": {
        "phase": "observed", "request_id": "run-pending", "kind": "call", "metadata": {"method": "run"},
    }})
    assert driver._worker_request_submitted(observed) is False


def test_p95_uses_the_95th_percentile_rank_and_empty_samples_are_unknown():
    assert driver._p95([]) is None
    assert driver._p95([0.8] * 19 + [1.2]) == 0.8
    assert driver._p95([0.4] * 20) == 0.4


def test_inspect_submission_detection_only_matches_worker_submit_events():
    observed = {"log": {"data": {"events": [
        {"event": "worker_request", "metadata": {"phase": "observed"}},
        {"event": "other", "metadata": {"phase": "submitted"}},
    ]}}}
    assert driver._has_worker_submission(observed) is False
    observed["log"]["data"]["events"].append(
        {"event": "worker_request", "metadata": {"phase": "submitted"}})
    assert driver._has_worker_submission(observed) is True
