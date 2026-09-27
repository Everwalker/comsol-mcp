from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


RUNNER = Path(__file__).parents[1] / "docs/full_project_execution/w25/run_block_other_clients.py"
SPEC = importlib.util.spec_from_file_location("block_other_clients_runner", RUNNER)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_child_event_protocol_parses_only_explicit_event_lines() -> None:
    assert runner.encode_event_line("EVENT\tCONNECTED\trole=A\ttrial=one\n") == {
        "name": "CONNECTED",
        "detail": "role=A\ttrial=one",
    }
    assert runner.encode_event_line("ordinary startup output\n") is None
    assert runner.encode_event_line("EVENT\n") is None


def test_java_version_parser_and_bundled_runtime_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.java_major_version('openjdk version "21.0.7" 2025-04-15 LTS') == 21
    assert runner.java_major_version('java version "1.8.0_472"') == 8
    with pytest.raises(ValueError):
        runner.java_major_version("not Java version output")

    install = tmp_path / "comsol"
    java = install / "java/macarm64/jre/Contents/Home/bin/java"
    java.parent.mkdir(parents=True)
    java.touch()
    monkeypatch.setattr(runner.platform, "machine", lambda: "arm64")
    assert runner.default_java_home(install) == java.parent.parent


def test_client_command_uses_server_task_private_prefs_and_explicit_websocket(tmp_path: Path) -> None:
    experiment = runner.Experiment.__new__(runner.Experiment)
    experiment.java_exe = tmp_path / "java/bin/java"
    experiment.classes = tmp_path / "runtime/classes"
    experiment.install_root = tmp_path / "comsol"
    experiment.runtime = tmp_path / "runtime"
    experiment.server = SimpleNamespace(port=62829)

    command = experiment._client_command("observer", "observer")

    assert command[:2] == [
        str(experiment.java_exe),
        f"-Dcs.prefsdir={(experiment.runtime / 'prefs').resolve()}",
    ]
    assert command[2:4] == ["-cp", f"{experiment.classes}:{experiment.install_root}/plugins/*"]
    assert command[-4:] == ["observer", "127.0.0.1", "62829", "observer"]


def test_client_runtime_preflight_rejects_java_11_before_compiling(tmp_path: Path) -> None:
    install = tmp_path / "install"
    plugins = install / "plugins"
    plugins.mkdir(parents=True)
    java_home = tmp_path / "java"
    java = java_home / "bin/java"
    java.parent.mkdir(parents=True)
    java.touch()
    javac = tmp_path / "javac"
    javac.touch()
    classes = tmp_path / "classes"
    java_11 = SimpleNamespace(
        returncode=0,
        stdout="",
        stderr='openjdk version "11.0.31" 2024-10-15 LTS\n',
    )
    with patch.object(runner.subprocess, "run", return_value=java_11) as run:
        with pytest.raises(RuntimeError, match="Java 17 or newer"):
            runner.client_classpath_preflight(
                install_root=install, java_home=java_home, javac=javac, classes=classes,
            )
    assert run.call_count == 1
    assert not list(classes.glob("*.class"))


def test_release_boundary_uses_supervisor_clock_and_rejects_early_observation() -> None:
    assert runner.release_order_is_acceptable(
        child_result_received_ns=101,
        supervisor_command_sent_ns=100,
    )
    assert not runner.release_order_is_acceptable(
        child_result_received_ns=100,
        supervisor_command_sent_ns=100,
    )
    assert not runner.release_order_is_acceptable(
        child_result_received_ns=99,
        supervisor_command_sent_ns=100,
    )
    assert runner.recovery_within_budget(
        child_result_received_ns=5_000_000_100,
        supervisor_command_sent_ns=100,
    )
    assert not runner.recovery_within_budget(
        child_result_received_ns=5_000_000_101,
        supervisor_command_sent_ns=100,
    )


def _event(name: str, role: str, detail: str, received_ns: int) -> dict[str, object]:
    return {
        "name": name,
        "role": role,
        "detail": detail,
        "received_monotonic_ns": received_ns,
    }


def test_exact_pre_release_busy_refusal_requires_exact_api_error_and_context() -> None:
    acquired = _event("ACQUIRED", "A", "trial=explicit_release", 10)
    entered = _event("READ_CALL_ENTERED", "B", "label=explicit_release", 30)
    busy = _event(
        "READ_CALL_ERROR", "B",
        "label=explicit_release\terror_class=com.comsol.util.exceptions.FlException"
        "\tmessage=Server_is_in_use_by_another_client",
        40,
    )
    args = {
        "trial": "explicit_release",
        "acquired_event": acquired,
        "read_command_sent_ns": 20,
        "read_entered_event": entered,
        "read_result_event": busy,
        "control_boundary_ns": 50,
    }
    assert runner.exact_pre_control_busy_refusal(**args)

    invalid_events = (
        _event("READ_CALL_ERROR", "B", busy["detail"].replace("FlException", "IOException"), 40),
        _event("READ_CALL_ERROR", "B", busy["detail"].replace(
            "Server_is_in_use_by_another_client", "prefix_Server_is_in_use_by_another_client"), 40),
        _event("READ_CALL_ERROR", "B", busy["detail"].replace(
            "label=explicit_release", "label=another_trial"), 40),
        _event("READ_CALL_RETURNED", "B", "label=explicit_release\ttag_count=0", 40),
    )
    for invalid in invalid_events:
        assert not runner.exact_pre_control_busy_refusal(**{**args, "read_result_event": invalid})
    assert not runner.exact_pre_control_busy_refusal(**{
        **args, "read_result_event": {**busy, "received_monotonic_ns": 50},
    })
    assert not runner.exact_pre_control_busy_refusal(**{
        **args, "acquired_event": {**acquired, "received_monotonic_ns": 25},
    })
    assert not runner.exact_pre_control_busy_refusal(**{
        **args, "read_entered_event": {**entered, "detail": "label=other"},
    })


def test_finish_handshake_proves_fresh_read_precedes_owner_disconnect() -> None:
    trial = "explicit_release"
    valid = {
        "trial": trial,
        "release_command_sent_ns": 100,
        "release_returned_event": _event("RELEASE_RETURNED", "A", f"trial={trial}", 110),
        "owner_ready_event": _event("OWNER_READY_FOR_POST_RELEASE_READ", "A", f"trial={trial}", 120),
        "fresh_read_command_sent_ns": 130,
        "fresh_read_entered_event": _event(
            "READ_CALL_ENTERED", "B", f"label=post_release_{trial}", 140),
        "fresh_read_result_event": _event(
            "READ_CALL_RETURNED", "B", f"label=post_release_{trial}\ttag_count=0", 150),
        "finish_command_sent_ns": 160,
        "finish_accepted_event": _event("OWNER_FINISH_ACCEPTED", "A", f"trial={trial}", 170),
        "disconnect_requested_event": _event("DISCONNECT_REQUESTED", "A", f"trial={trial}", 180),
    }
    assert runner.post_release_handshake_order_is_acceptable(**valid)
    assert not runner.post_release_handshake_order_is_acceptable(**{
        **valid, "finish_command_sent_ns": 145,
    })
    assert not runner.post_release_handshake_order_is_acceptable(**{
        **valid, "fresh_read_command_sent_ns": 115,
    })
    assert not runner.post_release_handshake_order_is_acceptable(**{
        **valid, "owner_ready_event": _event("OWNER_READY_FOR_POST_RELEASE_READ", "A", "trial=other", 120),
    })


def test_supervisor_sends_finish_only_after_independent_read_result() -> None:
    trial = "explicit_release"
    sequence: list[str] = []

    class FakeObserver:
        pending_read_label = trial

        def wait_read_result(self, label: str, timeout_s: float) -> dict[str, object]:
            assert label == trial
            return _event("READ_CALL_RETURNED", "B", f"label={trial}\ttag_count=0", 190)

    class FakeOwner:
        process = SimpleNamespace(poll=lambda: None)
        acquired_event = _event("ACQUIRED", "A", f"trial={trial}", 160)

        def send(self, command: str, *, event_name: str) -> int:
            assert command == "FINISH"
            assert sequence == ["fresh_result_received"]
            sequence.append("finish_sent")
            return 220

        def wait_event(self, name: str, timeout_s: float, *, detail_prefix: str | None = None):
            events = {
                "OWNER_FINISH_ACCEPTED": _event(name, "A", f"trial={trial}", 230),
                "DISCONNECT_REQUESTED": _event(name, "A", f"trial={trial}", 240),
                "DISCONNECTED": _event(name, "A", f"trial={trial}", 250),
            }
            return events[name]

        def wait_exit(self, timeout_s: float) -> int:
            return 0

    experiment = runner.Experiment.__new__(runner.Experiment)
    observer = FakeObserver()
    owner = FakeOwner()
    experiment.owner = owner
    experiment.fresh_read_ok = False
    experiment._observer = lambda: observer
    experiment.stage_wait = lambda timeout_s, **kwargs: timeout_s
    experiment.journal = SimpleNamespace(write=lambda *args, **kwargs: None)

    def independent_read(label: str, *, timeout_s: float):
        assert label == f"post_release_{trial}"
        sequence.append("fresh_result_received")
        return {
            "command_sent_ns": 200,
            "entered": _event("READ_CALL_ENTERED", "B", f"label=post_release_{trial}", 205),
            "result": _event("READ_CALL_RETURNED", "B", f"label=post_release_{trial}\ttag_count=0", 210),
        }

    experiment._observer_read_any = independent_read
    proof = {
        "pre_release_result_event": None,
        "read_command_sent_ns": 170,
        "read_entered_event": _event("READ_CALL_ENTERED", "B", f"label={trial}", 180),
    }
    release_returned = _event("RELEASE_RETURNED", "A", f"trial={trial}", 150)
    owner_ready = _event("OWNER_READY_FOR_POST_RELEASE_READ", "A", f"trial={trial}", 160)

    outcome = experiment._finish_observer_after_release(
        trial, owner, 140, proof, release_returned, owner_ready,
    )

    assert sequence == ["fresh_result_received", "finish_sent"]
    assert outcome["verdict"] == "WAITED_COMPLETION"
    assert outcome["accepted_for_acceptance"] is True


def test_supervisor_keeps_busy_refusal_recovery_as_a_distinct_verdict() -> None:
    trial = "explicit_release"
    sequence: list[str] = []

    class FakeObserver:
        pending_read_label = None

        def wait_read_result(self, label: str, timeout_s: float):
            raise AssertionError("the exact pre-release error was already consumed")

    class FakeOwner:
        process = SimpleNamespace(poll=lambda: None)
        acquired_event = _event("ACQUIRED", "A", f"trial={trial}", 100)

        def send(self, command: str, *, event_name: str) -> int:
            assert command == "FINISH"
            assert sequence == ["fresh_result_received"]
            sequence.append("finish_sent")
            return 200

        def wait_event(self, name: str, timeout_s: float, *, detail_prefix: str | None = None):
            times = {
                "OWNER_FINISH_ACCEPTED": 210,
                "DISCONNECT_REQUESTED": 220,
                "DISCONNECTED": 230,
            }
            return _event(name, "A", f"trial={trial}", times[name])

        def wait_exit(self, timeout_s: float) -> int:
            return 0

    experiment = runner.Experiment.__new__(runner.Experiment)
    observer = FakeObserver()
    owner = FakeOwner()
    experiment.owner = owner
    experiment.fresh_read_ok = False
    experiment._observer = lambda: observer
    experiment.stage_wait = lambda timeout_s, **kwargs: timeout_s
    experiment.journal = SimpleNamespace(write=lambda *args, **kwargs: None)

    def independent_read(label: str, *, timeout_s: float):
        assert label == f"post_release_{trial}"
        sequence.append("fresh_result_received")
        return {
            "command_sent_ns": 180,
            "entered": _event("READ_CALL_ENTERED", "B", f"label=post_release_{trial}", 185),
            "result": _event("READ_CALL_RETURNED", "B", f"label=post_release_{trial}\ttag_count=0", 190),
        }

    experiment._observer_read_any = independent_read
    busy = _event(
        "READ_CALL_ERROR", "B",
        "label=explicit_release\terror_class=com.comsol.util.exceptions.FlException"
        "\tmessage=Server_is_in_use_by_another_client",
        140,
    )
    proof = {
        "pre_release_result_event": busy,
        "read_command_sent_ns": 120,
        "read_entered_event": _event("READ_CALL_ENTERED", "B", f"label={trial}", 130),
    }
    release_returned = _event("RELEASE_RETURNED", "A", f"trial={trial}", 160)
    owner_ready = _event("OWNER_READY_FOR_POST_RELEASE_READ", "A", f"trial={trial}", 170)

    outcome = experiment._finish_observer_after_release(
        trial, owner, 150, proof, release_returned, owner_ready,
    )

    assert sequence == ["fresh_result_received", "finish_sent"]
    assert outcome["blocking_verdict"] == "BUSY_REFUSAL"
    assert outcome["verdict"] == "BUSY_REFUSAL_RECOVERED"
    assert outcome["verdict"] != "WAITED_COMPLETION"
    assert outcome["accepted_for_acceptance"] is True


def _native_free_experiment(monkeypatch: pytest.MonkeyPatch):
    experiment = runner.Experiment.__new__(runner.Experiment)
    experiment.deadline = 123.0
    experiment.server = SimpleNamespace(
        prepare=lambda: None,
        start=lambda deadline: None,
    )
    experiment.compile_source = lambda: None
    experiment._observer = lambda: object()
    experiment._observer_read = lambda label: {"returned": {"detail": "tag_count=0"}}
    experiment.journal = SimpleNamespace(write=lambda *args, **kwargs: None)
    experiment.result = {"status": "RUNNING", "trials": []}
    monkeypatch.setattr(runner, "_foreign_comsol_processes", lambda: [])
    return experiment


def test_trial_failure_stops_later_trials_and_marks_them_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _native_free_experiment(monkeypatch)
    calls: list[str] = []

    def first_trial():
        calls.append("explicit_release")
        return {"trial": "explicit_release", "status": "UNCLASSIFIED_API_ERROR",
                "accepted_for_acceptance": False}

    experiment.run_explicit_release = first_trial
    experiment.run_finally_release = lambda: calls.append("exception_finally")
    experiment.run_disconnect_recovery = lambda: calls.append("owner_disconnect")

    result = experiment.run()

    assert calls == ["explicit_release"]
    assert [trial["status"] for trial in result["trials"]] == [
        "UNCLASSIFIED_API_ERROR", "NOT_RUN", "NOT_RUN",
    ]


def test_trial_timeout_records_unknown_and_marks_later_trials_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = _native_free_experiment(monkeypatch)

    def first_trial():
        raise TimeoutError("bounded read timed out")

    experiment.run_explicit_release = first_trial
    experiment.run_finally_release = lambda: pytest.fail("unexpected second trial")
    experiment.run_disconnect_recovery = lambda: pytest.fail("unexpected third trial")

    with pytest.raises(TimeoutError, match="bounded read timed out"):
        experiment.run()

    assert [trial["status"] for trial in experiment.result["trials"]] == [
        "UNKNOWN", "NOT_RUN", "NOT_RUN",
    ]


def test_owned_child_signal_requires_pid_start_and_executable_match(tmp_path: Path) -> None:
    java = tmp_path / "java"
    java.touch()
    expected = {"pid": 12, "executable_path": str(java), "start_identity": "Sat Sep 26 12:00:00 2026"}
    assert runner.validate_process_identity(expected, expected, java)
    assert not runner.validate_process_identity({**expected, "pid": 13}, expected, java)
    assert not runner.validate_process_identity({**expected, "start_identity": "different"}, expected, java)
    assert not runner.validate_process_identity({**expected, "executable_path": str(tmp_path / "other")}, expected, java)


def test_prelaunch_inventory_fails_closed_when_ps_is_denied_or_incomplete() -> None:
    for result in (
        SimpleNamespace(returncode=1, stdout="", stderr="ps: Operation not permitted"),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
    ):
        with patch.object(runner.subprocess, "run", return_value=result):
            with pytest.raises(runner.PrelaunchInventoryUnavailable):
                runner._foreign_comsol_processes()


def test_prelaunch_inventory_detects_comsol_java_server_and_worker() -> None:
    own_pid = os.getpid()
    ps = SimpleNamespace(
        returncode=0,
        stderr="",
        stdout=(
            f"{own_pid} /opt/python3.12 pytest test_block_other_clients_runner.py\n"
            "321 /usr/bin/java java -Dcs.root=/Applications/COMSOL64 "
            "com.comsol.util.application.ServerApplication -port 12345\n"
            "322 /usr/bin/java java comsol_mcp.worker_java.PersistentComsolWorker --port 0\n"
            "323 /usr/bin/python3 python3 /Volumes/SSD/Comsol-MCP/server.py\n"
        ),
    )
    with patch.object(runner.subprocess, "run", return_value=ps):
        rows = runner._foreign_comsol_processes()

    assert [row["pid"] for row in rows] == ["321", "322"]


def test_shutdown_requires_reaped_birth_and_a_clean_listener_probe() -> None:
    birth = {"pid": 123, "start_identity": "Sat Sep 26 12:00:00 2026", "executable_path": "/Applications/COMSOL64/bin/comsol"}
    clean_no_match = SimpleNamespace(returncode=1, stdout="", stderr="")
    assert runner._lsof_probe_complete(clean_no_match)
    assert runner._shutdown_status(
        owned_birth_reaped=True, birth_identity=birth, listener_probe=clean_no_match,
    ) == "STOPPED"
    assert runner._shutdown_status(
        owned_birth_reaped=False, birth_identity=birth, listener_probe=clean_no_match,
    ) == "STOP_UNVERIFIED"

    denied = SimpleNamespace(returncode=1, stdout="", stderr="lsof: Operation not permitted")
    assert not runner._lsof_probe_complete(denied)
    assert runner._shutdown_status(
        owned_birth_reaped=True, birth_identity=birth, listener_probe=denied,
    ) == "STOP_UNVERIFIED"

    listener = SimpleNamespace(
        returncode=0,
        stdout="COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
               "comsol 123 everwalker 10u IPv4 0x1 0t0 TCP 127.0.0.1:1234 (LISTEN)\n",
        stderr="",
    )
    assert runner._shutdown_status(
        owned_birth_reaped=True, birth_identity=birth, listener_probe=listener,
    ) == "STOP_UNVERIFIED"
