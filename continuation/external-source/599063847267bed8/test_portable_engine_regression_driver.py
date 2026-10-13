"""Offline guardrails for the portable real-engine regression driver."""
import asyncio
from pathlib import Path
import ast
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "portable_engine_regression.py"


def _driver_module():
    import pytest

    pytest.importorskip("mcp")
    from tools import portable_engine_regression

    return portable_engine_regression


def _text() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_driver_is_syntax_valid_and_has_separate_fixture_and_reopen_phases():
    tree = ast.parse(_text(), filename=str(SOURCE))
    assert tree.body
    source = _text()
    for marker in (
        "prepare-fixture",
        "production-chain",
        'sub.add_parser("reopen"',
        "BoundPhase1Poc",
        'host.call("server_connect"',
        'host.call("model_load"',
        'host.call("run_study"',
        'host.call("save_model"',
    ):
        assert marker in source


def test_driver_uses_portable_java_worker_path_resolution():
    source = _text()
    assert "JavaWorkerPaths" in source
    assert 'paths.executable("java")' in source
    assert 'paths.executable("javac")' in source
    assert "paths.classpath_separator" in source
    assert "os.pathsep" not in source
    assert "comsol_mcp.phase1_java" in source


def test_driver_does_not_probe_or_manage_process_lifecycles():
    source = _text()
    # The outer driver may create its own control daemon, but it must not
    # signal COMSOL or use an unbounded process-tree kill/restart path.
    assert "subprocess.Popen(command" in source
    for forbidden in ("os.kill", "taskkill", "lsof", "CREATE_KILL_ON_JOB_CLOSE"):
        assert forbidden not in source
    assert "server_lifecycle" in source
    assert "never start/stop/replace COMSOL" in source


def test_driver_requires_new_private_home_for_each_managed_worker_phase():
    source = _text()
    assert 'label="fresh private control home"' in source
    assert 'label="fresh reopen private control home"' in source
    assert "_new_directory" in source
    assert "compiled_marker" in source
    assert "worker_source_sha256" in source


def _reopen_with_fake_host(driver, monkeypatch, tmp_path, *, configured):
    saved_model = tmp_path / "saved.mph"
    saved_model.write_bytes(b"saved fixture")
    calls = []

    class StopAtConnect(RuntimeError):
        pass

    class FakeHost:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, operation, arguments=None):
            calls.append((operation, arguments or {}))
            if operation == "configure_single_main_workflow":
                return {"success": configured, "error": {"code": "CONFIG_FAIL"} if not configured else None}
            if operation == "server_connect":
                raise StopAtConnect("sentinel: stop before engine calls")
            raise AssertionError(f"unexpected engine call: {operation}")

    class FakeControl:
        def safe_identity(self):
            return {"pid": 1234, "home": "REDACTED_CONTROL_HOME"}

        def cleanup(self, worker_release, run_dir):
            return {"status": "BLOCKED", "reason": "fake control"}

    monkeypatch.setattr(driver, "_ProductionHost", lambda *args: FakeHost())
    monkeypatch.setattr(driver, "_launch_owned_control", lambda *args: FakeControl())
    monkeypatch.setattr(driver, "_artifact_path", lambda path, label: Path(path))

    def new_directory(path, *, label):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(driver, "_new_artifact_directory", new_directory)
    monkeypatch.setattr(driver, "_new_directory", new_directory)
    args = SimpleNamespace(
        run_dir=tmp_path / "run",
        private_home=tmp_path / "home",
        host="127.0.0.1",
        port=22036,
    )
    _, result = asyncio.run(driver._reopen(args, saved_model))
    return calls, result, StopAtConnect


def test_reopen_stops_before_connect_when_workflow_configuration_fails(monkeypatch, tmp_path):
    driver = _driver_module()
    calls, result, _ = _reopen_with_fake_host(driver, monkeypatch, tmp_path, configured=False)
    assert [operation for operation, _ in calls] == ["configure_single_main_workflow"]
    assert result["status"] == "FAIL"
    assert "before engine access" in result["reason"]


def test_reopen_configures_fixed_recipe_before_connect(monkeypatch, tmp_path):
    driver = _driver_module()
    calls, result, stop_at_connect = _reopen_with_fake_host(driver, monkeypatch, tmp_path, configured=True)
    assert [operation for operation, _ in calls] == ["configure_single_main_workflow", "server_connect"]
    config = calls[0][1]
    assert config["current_main_model_path"] == str(tmp_path / "saved.mph")
    assert config["snapshot_dir"] == str(tmp_path / "run" / "snapshots")
    assert config["snapshot_prefix"] == "portable-reopen"
    assert config["model_dimension"] == 2
    assert result["status"] == "FAIL"
    assert "StopAtConnect" in result["error"]


def test_portable_workflow_configuration_keeps_fixture_dimension_and_paths():
    driver = _driver_module()
    model = Path("fixture.mph")
    snapshots = Path("reopen-snapshots")
    config = driver._portable_workflow_configuration(model, snapshots, snapshot_prefix="portable-reopen")
    assert config["current_main_model_path"] == str(model)
    assert config["snapshot_dir"] == str(snapshots)
    assert config["snapshot_prefix"] == "portable-reopen"
    assert config["model_dimension"] == 2


def test_driver_marks_worker_lock_contention_blocked():
    source = _text()
    assert 'code == "ENGINE_BUSY"' in source
    assert '"status": "BLOCKED"' in source


def test_driver_releases_only_its_authenticated_worker_before_reopen():
    source = _text()
    assert "_owned_worker_identity" in source
    assert "PersistentJavaWorker" in source
    assert ".client().disconnect(" in source
    assert "private Worker only" in source
    assert "queued or running requests" in source
    assert "NOT_CHECKED_WINDOWS" in source
    assert "control active_jobs=[] was not freshly confirmed" in source


def test_driver_prestarts_matching_control_and_checks_stdio_teardown_identity():
    source = _text()
    for marker in (
        "_runtime_environment",
        "_launch_owned_control",
        "comsol_mcp._control_daemon",
        '"COMSOL_SERVER_MCP_HOME": str(private_home.resolve())',
        "stdio_teardown_control_alive",
        "stdio_teardown_worker_alive",
        "driver_control_released",
        '"worker_connected": False',
    ):
        assert marker in source


def test_driver_uses_execution_contract_to_reject_private_artifacts():
    source = _text()
    assert "canonical_project_path" in source
    assert "_artifact_path" in source
    assert "_new_artifact_directory" in source
    assert 'label="fixture model"' in source
    assert 'label="saved model"' in source


def test_driver_preserves_complete_w02_build_line():
    source = _text()
    assert 'r"(?m)^W02_BUILD\\s+(.+?)\\s*$"' in source
    assert '"server_build_line"' in source


def test_driver_rejects_private_model_and_result_paths_before_engine_calls():
    import pytest

    driver = _driver_module()
    for relative in (".phase1-private/model.mph", ".phase2-private/result.mph", "control-private/run.mph"):
        with pytest.raises(driver.DriverError, match="approved project artifact"):
            driver._artifact_path(driver.ROOT / relative, label="artifact")


def test_driver_verifies_same_control_and_worker_after_stdio_teardown(monkeypatch, tmp_path):
    driver = _driver_module()

    class Control:
        pid = 4321

        def health(self):
            return {"success": True, "data": {"control_pid": self.pid}}

        def safe_identity(self):
            return {"pid": self.pid, "home": "REDACTED_CONTROL_HOME"}

    monkeypatch.setattr(
        driver,
        "_owned_worker_identity",
        lambda private_home, expected: ({}, {"pid": expected["pid"], "command_match": "NOT_CHECKED_WINDOWS"}),
    )
    result = driver._verify_stdio_teardown(Control(), tmp_path, {"pid": 99})
    assert result["control_alive"] is True
    assert result["worker_alive"] is True
    assert result["evidence"]["control_health"]["control_pid"] == 4321


def test_owned_control_cleanup_requires_worker_disconnect_and_idle(monkeypatch, tmp_path):
    driver = _driver_module()

    class Process:
        pid = 5678

        def __init__(self):
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    process = Process()
    endpoint = {"pid": process.pid, "port": 4321, "token": "secret", "process_start_epoch_ms": 123}
    control = driver._OwnedControlDaemon(process, tmp_path, endpoint, "unit", platform_name="posix")
    blocked = control.cleanup({"status": "BLOCKED"}, tmp_path)
    assert blocked["status"] == "BLOCKED"
    assert process.terminated is False

    monkeypatch.setattr(driver, "process_identity", lambda pid: {"alive": not process.terminated, "start_epoch_ms": 123})
    monkeypatch.setattr(
        driver,
        "_control_request",
        lambda endpoint, payload, timeout: {
            "success": True,
            "data": {"active_jobs": [], "worker": {"connected": False, "server": "", "queued_or_running": 0}},
        },
    )
    passed = control.cleanup({"status": "PASS"}, tmp_path)
    assert passed["status"] == "PASS"
    assert process.terminated is True


def _windows_redirector_fixture(driver, tmp_path):
    birth = "2026-09-19T04:57:48.6336100Z"
    base = r"C:\Users\Everwalker\AppData\Local\Programs\Python\Python312-comsol-mcp\python.exe"
    launcher = r"C:\Users\Everwalker\Desktop\comsol-mcp-project\venv\Scripts\python.exe"
    home = tmp_path / "control-private"
    home_text = str(home).replace("/", "\\")
    snapshots = {
        34332: {
            "pid": 34332,
            "parent_pid": 777,
            "creation_utc": birth,
            "executable_path": launcher,
            "command_line": f'"{launcher}" -m comsol_mcp._control_daemon --home "{home_text}"',
        },
        28912: {
            "pid": 28912,
            "parent_pid": 34332,
            "creation_utc": birth,
            "executable_path": base,
            "command_line": f'"{base}" -m comsol_mcp._control_daemon --home "{home_text}"',
        },
    }
    endpoint = {
        "pid": 28912,
        "port": 4321,
        "process_start_epoch_ms": driver._windows_birth_epoch_ms(birth),
    }
    return home, base, snapshots, endpoint


def test_windows_venv_redirector_accepts_distinct_direct_child(monkeypatch, tmp_path):
    driver = _driver_module()
    home, base, snapshots, endpoint = _windows_redirector_fixture(driver, tmp_path)

    class Process:
        pid = 34332

        def poll(self):
            return None

    monkeypatch.setattr(driver, "_windows_process_snapshot", lambda pid: snapshots[pid])
    identity = driver._validate_windows_control_identity(Process(), endpoint, home, base)
    assert identity["launcher_pid"] == 34332
    assert identity["daemon_pid"] == 28912
    assert identity["daemon_parent_pid"] == 34332
    assert identity["windows_redirector"] is True
    assert identity["popen_handle_held"] is True


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("wrong_parent", "direct child"),
        ("wrong_birth", "birth"),
        ("daemon_before_launcher", "precedes"),
        ("wrong_executable", "base executable"),
    ],
)
def test_windows_redirector_rejects_wrong_parent_birth_or_executable(monkeypatch, tmp_path, change, message):
    driver = _driver_module()
    home, base, snapshots, endpoint = _windows_redirector_fixture(driver, tmp_path)
    if change == "wrong_parent":
        snapshots[28912]["parent_pid"] = 99999
    elif change == "wrong_birth":
        endpoint["process_start_epoch_ms"] += 1
    elif change == "daemon_before_launcher":
        daemon_birth = "2026-09-19T04:57:47.6336100Z"
        snapshots[28912]["creation_utc"] = daemon_birth
        endpoint["process_start_epoch_ms"] = driver._windows_birth_epoch_ms(daemon_birth)
    else:
        snapshots[28912]["executable_path"] = r"C:\Python\other.exe"

    class Process:
        pid = 34332

        def poll(self):
            return None

    monkeypatch.setattr(driver, "_windows_process_snapshot", lambda pid: snapshots[pid])
    with pytest.raises(driver.DriverError, match=message):
        driver._validate_windows_control_identity(Process(), endpoint, home, base)


def test_windows_cim_snapshot_rejects_a_record_for_a_different_pid(monkeypatch):
    driver = _driver_module()

    class Completed:
        returncode = 0
        stdout = '{"pid": 100, "parent_pid": 1, "creation_utc": "2026-09-19T00:00:00.0000000Z", "executable_path": "C:\\\\Python\\\\python.exe", "command_line": ""}'

    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: Completed())
    with pytest.raises(driver.DriverError, match="different PID"):
        driver._windows_process_snapshot(99)


def test_selected_python_base_query_uses_requested_python(monkeypatch, tmp_path):
    driver = _driver_module()
    selected = tmp_path / "venv" / "Scripts" / "python.exe"
    calls = []

    class Completed:
        returncode = 0
        stdout = r"C:\Python312-comsol-mcp\python.exe" + "\n"

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    result = driver._selected_python_base_executable(selected, {"PYTHONPATH": "x"})
    assert result == r"C:\Python312-comsol-mcp\python.exe"
    assert calls[0][0][0] == str(selected.resolve())
    assert "sys._base_executable" in calls[0][0][2]
    assert calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"


def test_windows_cleanup_revalidates_identity_and_terminates_only_launcher(monkeypatch, tmp_path):
    driver = _driver_module()
    home, base, snapshots, endpoint = _windows_redirector_fixture(driver, tmp_path)
    process_state = {"terminated": False}

    class Process:
        pid = 34332

        def poll(self):
            return 0 if process_state["terminated"] else None

        def terminate(self):
            process_state["terminated"] = True

        def wait(self, timeout=None):
            return 0

    def snapshot(pid):
        if process_state["terminated"]:
            return None
        return snapshots[pid]

    monkeypatch.setattr(driver, "_windows_process_snapshot", snapshot)
    monkeypatch.setattr(
        driver,
        "_control_request",
        lambda endpoint, payload, timeout: {
            "success": True,
            "data": {"active_jobs": [], "worker": {"connected": False, "server": "", "queued_or_running": 0}},
        },
    )
    identity = driver._validate_windows_control_identity(Process(), endpoint, home, base)
    control = driver._OwnedControlDaemon(
        Process(), home, endpoint, "redirector", identity=identity, base_executable=base, platform_name="nt"
    )
    result = control.cleanup({"status": "PASS"}, tmp_path)
    assert result["status"] == "PASS"
    assert result["before"]["daemon_pid"] == 28912
    assert result["after"] == {"launcher_alive": False, "daemon_alive": False}
    assert process_state["terminated"] is True


@pytest.mark.parametrize("missing", ["base_executable", "endpoint_birth"])
def test_windows_cleanup_missing_identity_is_blocked_without_terminate(monkeypatch, tmp_path, missing):
    driver = _driver_module()
    home, base, snapshots, endpoint = _windows_redirector_fixture(driver, tmp_path)
    process_state = {"terminated": False}

    class Process:
        pid = 34332

        def poll(self):
            return 0 if process_state["terminated"] else None

        def terminate(self):
            process_state["terminated"] = True

        def wait(self, timeout=None):
            return 0

    if missing == "base_executable":
        base_for_control = None
    else:
        base_for_control = base
        endpoint.pop("process_start_epoch_ms")

    monkeypatch.setattr(driver, "_windows_process_snapshot", lambda pid: snapshots[pid])
    monkeypatch.setattr(
        driver,
        "_control_request",
        lambda endpoint, payload, timeout: {
            "success": True,
            "data": {"active_jobs": [], "worker": {"connected": False, "server": "", "queued_or_running": 0}},
        },
    )
    control = driver._OwnedControlDaemon(
        Process(),
        home,
        endpoint,
        f"missing-{missing}",
        base_executable=base_for_control,
        platform_name="nt",
    )
    result = control.cleanup({"status": "PASS"}, tmp_path)
    assert result["status"] == "BLOCKED"
    assert process_state["terminated"] is False
