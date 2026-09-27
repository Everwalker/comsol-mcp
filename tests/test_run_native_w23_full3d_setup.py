from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
import types
import importlib
from pathlib import Path
from typing import Any

import pytest

from tools.run_native_w23_full3d_setup import (
    EXPLICIT_SITE_PACKAGES,
    EXPECTED_PYTHON,
    PublicDispatchAdapter,
    CleanupRefused,
    CandidateError,
    REPO,
    _assert_no_editable_fallback,
    _audit_loaded_project_modules,
    _lsof_listeners,
    _bind_native_server_identity,
    _job_ledger_terminal,
    _process_identity,
    _start_control_daemon,
    _stop_owned_process,
    _resource_ownership_receipt,
    _source_inventory,
    build_disconnect_request,
    orchestrate_cleanup,
    validate_native_mode_configuration,
)


def _retirement(*, worker_id: str = "worker-1", epoch: int = 5,
                pid: int = 4101, birth: int = 1700000000000) -> dict[str, Any]:
    return {"success": True, "data": {
        "project_id": "project-1", "session_id": "session-1",
        "state": "DISCONNECTED", "client_state": "RETIRED",
        "server_stopped": False,
        "worker_retirement": {
            "status": "RETIRED", "worker_instance_id": worker_id,
            "worker_epoch": epoch,
            "process_identity": {"pid": pid, "start_epoch_ms": birth},
            "exact_popen_handle": True,
            "birth_identity_matched_before_close": True,
            "child_exit_confirmed": True, "child_reaped": True,
            "admission_fence": "RETIRED",
            "disconnect_rpc_dispatched": True, "worker_close_started": True,
        },
    }}


def _inspect(*, worker_id: str = "worker-1", epoch: int = 5,
             runtime_live: bool = False) -> dict[str, Any]:
    return {"success": True, "data": {
        "project_id": "project-1", "runtime_live": runtime_live,
        "worker_binding": None,
        "lifecycle": {"session_id": "session-1", "state": "DISCONNECTED",
                      "client_state": "RETIRED", "worker_instance_id": worker_id,
                      "worker_epoch": epoch},
    }}


def _stopped(pid: int = 4201, birth: int = 1700000000100) -> dict[str, Any]:
    return {"status": "STOPPED_AND_REAPED", "pid": pid,
            "start_epoch_ms": birth, "child_exit_confirmed": True,
            "child_reaped": True, "listener_absent": True}


def _cleanup(**overrides: Any) -> tuple[list[str], list[str]]:
    events: list[str] = []
    kwargs: dict[str, Any] = {
        "worker_state": "CONNECTED", "project_id": "project-1",
        "session_id": "session-1", "worker_instance_id": "worker-1",
        "connected_epoch": 4, "detached_epoch": 5,
        "all_project_jobs_terminal": True,
        "disconnect": lambda: (events.append("disconnect") or _retirement()),
        "inspect": lambda: (events.append("inspect") or _inspect()),
        "stop_server": lambda: (events.append("server") or _stopped()),
        "stop_control": lambda: (events.append("control") or _stopped(4301, 1700000000200)),
    }
    kwargs.update(overrides)
    return orchestrate_cleanup(**kwargs), events


def test_cleanup_retires_exact_worker_before_server_and_control() -> None:
    completed, events = _cleanup()
    assert events == ["disconnect", "inspect", "server", "control"]
    assert completed == [
        "exact_managed_worker_retired", "public_disconnected_inspect_verified",
        "exact_task_server_stopped_and_reaped", "exact_control_daemon_stopped_and_reaped",
    ]


def test_unknown_disconnect_preserves_server_and_control() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="disconnect outcome unknown"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: (_ for _ in ()).throw(TimeoutError("transport unknown")),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == []


@pytest.mark.parametrize("response", [
    _retirement(worker_id="different-worker"),
    _retirement(epoch=4),
    _retirement(pid=1),
    {**_retirement(), "data": {**_retirement()["data"],
        "worker_retirement": {**_retirement()["data"]["worker_retirement"],
                              "disconnect_rpc_dispatched": False}}},
    {**_retirement(), "data": {**_retirement()["data"],
        "worker_retirement": {**_retirement()["data"]["worker_retirement"],
                              "worker_close_started": False}}},
    {**_retirement(), "data": {**_retirement()["data"],
        "worker_retirement": {**_retirement()["data"]["worker_retirement"],
                              "child_reaped": False}}},
])
def test_retirement_identity_or_reap_gap_preserves_both_servers(response: dict[str, Any]) -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or response,
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == ["disconnect"]


def test_retired_inspect_mismatch_prevents_both_process_stops() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="session_inspect"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(runtime_live=True),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == ["disconnect", "inspect"]


def test_nonterminal_or_unknown_project_job_blocks_retirement() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="terminal ledger"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=False,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == []


def test_server_stop_failure_does_not_stop_control_daemon() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="server_stop"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or {"status": "STOP_UNVERIFIED"},
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == ["disconnect", "inspect", "server"]


def test_control_stop_failure_is_reported_after_exact_server_cleanup() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="control_stop"):
        orchestrate_cleanup(
            worker_state="CONNECTED", project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", connected_epoch=4, detached_epoch=5,
            all_project_jobs_terminal=True,
            disconnect=lambda: events.append("disconnect") or _retirement(),
            inspect=lambda: events.append("inspect") or _inspect(),
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or {"status": "STOP_UNVERIFIED"},
        )
    assert events == ["disconnect", "inspect", "server", "control"]


def test_never_dispatched_worker_stops_only_owned_processes() -> None:
    events: list[str] = []
    completed = orchestrate_cleanup(
        worker_state="NEVER_DISPATCHED", project_id=None, session_id=None,
        worker_instance_id=None, connected_epoch=None, detached_epoch=None,
        all_project_jobs_terminal=True, disconnect=None, inspect=None,
        stop_server=lambda: events.append("server") or {"status": "NOT_STARTED", "child_started": False},
        stop_control=lambda: events.append("control") or _stopped(),
    )
    assert events == ["server", "control"]
    assert completed == ["exact_task_server_stopped_and_reaped",
                         "exact_control_daemon_stopped_and_reaped"]


def test_unknown_connect_or_worker_state_never_cleans_up() -> None:
    events: list[str] = []
    with pytest.raises(CleanupRefused, match="UNKNOWN"):
        orchestrate_cleanup(
            worker_state="UNKNOWN", project_id=None, session_id=None,
            worker_instance_id=None, connected_epoch=None, detached_epoch=None,
            all_project_jobs_terminal=True, disconnect=None, inspect=None,
            stop_server=lambda: events.append("server") or _stopped(),
            stop_control=lambda: events.append("control") or _stopped(),
        )
    assert events == []


def test_disconnect_request_uses_public_scoped_retirement_envelope() -> None:
    request = build_disconnect_request(project_id="project-1", session_id="session-1",
        request_id="request-1", idempotency_key="idem-1")
    assert request["operation"] == "session.disconnect"
    assert request["arguments"] == {"retire_worker": True}
    assert request["execution"]["project_id"] == "project-1"
    assert request["execution"]["session_id"] == "session-1"
    assert request["execution"]["request_id"] == "request-1"
    assert request["execution"]["idempotency_key"] == "idem-1"


def test_disconnect_request_rejects_missing_session_identity() -> None:
    with pytest.raises(Exception, match="exact project/session"):
        build_disconnect_request(project_id="project-1", session_id=" ",
            request_id="request-1", idempotency_key="idem-1")


def _native_build_readback() -> dict[str, Any]:
    def properties(port_name: str) -> dict[str, Any]:
        return {"requested_properties": {
            key: {"has_property_exact": True, "string_readback": value}
            for key, value in {"PortType": "Numeric", "PortName": port_name,
                               "PortModeNumber": "1"}.items()}}
    return {
        "ports": [
            {"tag": "portIn3d", "properties": properties("1")},
            {"tag": "portOut3d", "properties": properties("2")},
        ],
        "study_steps": [
            {"tag": "bmaInput3d", "type": "BoundaryModeAnalysis", "port": "1",
             "modeFreq": "f0", "neigs": 2},
            {"tag": "bmaOutput3d", "type": "BoundaryModeAnalysis", "port": "2",
             "modeFreq": "f0", "neigs": 2},
            {"tag": "freq3d", "type": "Frequency", "plist": "f0"},
        ],
    }


def test_native_configuration_readback_keeps_port_number_separate_from_two_basis_ordinals() -> None:
    proof = validate_native_mode_configuration(_native_build_readback())
    assert proof == {
        "status": "NATIVE_CONFIGURATION_PROPERTIES_READ_BACK",
        "receiver_numeric_port": {"feature_tag": "portOut3d", "port_name": "2",
                                   "port_mode_number": 1},
        "bma_output_step": {"feature_tag": "bmaOutput3d", "port_name": "2",
                             "mode_frequency": "f0", "requested_eigensolutions": 2},
        "basis_ordinals": [1, 2],
        "basis_ordinal_is_not_port_mode_number": True,
        "producer_step_binding": "UNVERIFIED",
        "numeric_port_mode_field_mapping": "UNVERIFIED",
    }


@pytest.mark.parametrize("mutation", [
    "wrong_port_name", "wrong_port_type", "wrong_port_mode", "missing_port_property",
    "duplicate_output_port", "wrong_bma_port", "wrong_frequency", "wrong_neigs",
    "boolean_neigs", "duplicate_bma_step", "wrong_bma_type", "missing_neigs",
])
def test_native_configuration_readback_fails_closed_on_tampered_or_missing_identity(mutation: str) -> None:
    readback = _native_build_readback()
    if mutation == "wrong_port_name":
        readback["ports"][1]["properties"]["requested_properties"]["PortName"]["string_readback"] = "3"
    elif mutation == "wrong_port_type":
        readback["ports"][1]["properties"]["requested_properties"]["PortType"]["string_readback"] = "UserDefined"
    elif mutation == "wrong_port_mode":
        readback["ports"][1]["properties"]["requested_properties"]["PortModeNumber"]["string_readback"] = "2"
    elif mutation == "missing_port_property":
        readback["ports"][1]["properties"]["requested_properties"]["PortModeNumber"]["has_property_exact"] = False
    elif mutation == "duplicate_output_port":
        readback["ports"].append(readback["ports"][1])
    elif mutation == "wrong_bma_port":
        readback["study_steps"][1]["port"] = "1"
    elif mutation == "wrong_frequency":
        readback["study_steps"][1]["modeFreq"] = "f1"
    elif mutation == "wrong_neigs":
        readback["study_steps"][1]["neigs"] = 1
    elif mutation == "boolean_neigs":
        readback["study_steps"][1]["neigs"] = True
    elif mutation == "duplicate_bma_step":
        readback["study_steps"].append(dict(readback["study_steps"][1]))
    elif mutation == "wrong_bma_type":
        readback["study_steps"][1]["type"] = "Frequency"
    elif mutation == "missing_neigs":
        del readback["study_steps"][1]["neigs"]
    with pytest.raises(Exception):
        validate_native_mode_configuration(readback)


def test_solution_inventory_rechecks_bma_configuration_without_claiming_producer_lineage() -> None:
    readback = {"study_steps_in_configured_order": [
        {"tag": "bmaInput3d", "feature_type": "BoundaryModeAnalysis",
         "PortName": "1", "modeFreq": "f0", "neigs": 2},
        {"tag": "bmaOutput3d", "feature_type": "BoundaryModeAnalysis",
         "PortName": "2", "modeFreq": "f0", "neigs": 2},
        {"tag": "freq3d", "feature_type": "Frequency", "plist": "f0"},
    ]}
    proof = validate_native_mode_configuration(readback, inventory=True)
    assert proof["status"] == "NATIVE_CONFIGURATION_PROPERTIES_READ_BACK"
    assert proof["producer_step_binding"] == "UNVERIFIED"
    assert proof["numeric_port_mode_field_mapping"] == "UNVERIFIED"


class _FakeOwnedProcess:
    def __init__(self, pid: int, return_code: int | None):
        self.pid = pid
        self.returncode = return_code

    def poll(self) -> int | None:
        return self.returncode


def test_resource_receipt_binds_process_identity_to_popen_and_reports_live_worker_state() -> None:
    server = _FakeOwnedProcess(4101, 0)
    control = _FakeOwnedProcess(4102, None)
    receipt = _resource_ownership_receipt(
        server_proc=server, server_identity={"pid": 4101, "start_epoch_ms": 1700000000000},
        server_listener={"status": "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER",
                         "pid": 4101, "port": 50101, "endpoint": "127.0.0.1:50101"},
        control_proc=control, control_identity={"pid": 4102, "start_epoch_ms": 1700000000100},
        control_endpoint={"pid": 4102, "port": 50102, "token": "must-not-leak"},
        worker_state="CONNECTED", session={"project_id": "p", "session_id": "s",
            "worker_instance_id": "w", "worker_epoch": 7}, retirement_proof=None,
        process_cleanup={"server": {"status": "STOPPED_AND_REAPED", "listener_absent": True}})
    assert receipt["server"]["status"] == "EXITED_REAPED_EXACT_IDENTITY_BOUND"
    assert receipt["server"]["process_identity"] == {"pid": 4101, "start_epoch_ms": 1700000000000}
    assert receipt["server"]["child_reaped"] is True
    assert receipt["control_daemon"]["status"] == "LIVE_EXACT_IDENTITY_BOUND"
    assert "token" not in receipt["control_daemon"]["endpoint"]
    assert receipt["managed_worker"]["status"] == "CONNECTED"
    assert receipt["managed_worker"]["connected_worker_epoch"] == 7


def test_resource_receipt_does_not_bind_wrong_pid_or_missing_birth_to_popen() -> None:
    process = _FakeOwnedProcess(4101, None)
    receipt = _resource_ownership_receipt(
        server_proc=process, server_identity={"pid": 9999, "start_epoch_ms": 1700000000000},
        server_listener=None, control_proc=None, control_identity=None, control_endpoint=None,
        worker_state="NEVER_DISPATCHED", session=None, retirement_proof=None,
        process_cleanup={})
    assert receipt["server"]["status"] == "LIVE_IDENTITY_UNVERIFIED"
    assert receipt["server"]["process_identity"] is None
    assert receipt["server"]["exact_popen_handle"] is True
    assert receipt["control_daemon"]["status"] == "NOT_STARTED"
    assert receipt["managed_worker"]["status"] == "NEVER_DISPATCHED"


def _start_actual_isolation_helper_process(tmp_path: Path):
    """Start a harmless loopback child and obtain the real _process_snapshot shape."""
    from comsol_mcp._g2_isolation import _process_snapshot

    work = tmp_path.resolve()
    shadow = work / "comsol-shadow"
    shadow.mkdir()
    port_path = work / "listener.port"
    child = (
        "import socket,sys,time; s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); "
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        "s.bind(('127.0.0.1',0)); s.listen(1); "
        "open(sys.argv[1],'w').write(str(s.getsockname()[1])); time.sleep(60)"
    )
    proc = subprocess.Popen(
        [str(EXPECTED_PYTHON), "-B", "-S", "-c", child,
         str(port_path), str(shadow), str(work)],
        cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5.0
        while not port_path.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert port_path.is_file() and proc.poll() is None
        port = int(port_path.read_text())
        raw = _process_snapshot(proc.pid)
        assert isinstance(raw, dict)
        assert {"pid", "birth", "command", "command_sha256"} <= set(raw)
        assert "start_epoch_ms" not in raw
        assert raw["pid"] == proc.pid
        assert str(work) in raw["command"] and str(shadow) in raw["command"]
        server = types.SimpleNamespace(
            work=work, shadow_root=shadow, proc=proc, port=port,
            process_identity={**raw, "port": port})
        listener = {
            "status": "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER",
            "pid": proc.pid, "port": port,
            "endpoint": f"127.0.0.1:{port}",
            "process_identity": server.process_identity,
        }
        return proc, server, listener
    except BaseException:
        proc.terminate()
        proc.wait(timeout=3.0)
        raise


def _stop_helper_test_child(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=3.0)


def test_native_server_identity_binds_actual_helper_shape_to_exact_popen_birth(tmp_path: Path) -> None:
    from comsol_mcp._g2_isolation import _process_snapshot

    proc, server, listener = _start_actual_isolation_helper_process(tmp_path)
    try:
        assert "start_epoch_ms" not in server.process_identity
        bound = _bind_native_server_identity(server, listener)
        assert bound == _process_identity(proc.pid)
        assert type(bound["start_epoch_ms"]) is int and bound["start_epoch_ms"] > 0
        assert proc.poll() is None
        assert _process_snapshot(proc.pid)["birth"] == server.process_identity["birth"]
    finally:
        _stop_helper_test_child(proc)


@pytest.mark.parametrize("mutation", [
    "missing_birth", "wrong_pid", "malformed_command_hash", "tampered_command",
    "wrong_port", "listener_pid_mismatch", "listener_snapshot_mismatch",
])
def test_native_server_identity_rejects_actual_helper_shape_tampering(
    tmp_path: Path, mutation: str,
) -> None:
    import copy

    proc, server, listener = _start_actual_isolation_helper_process(tmp_path)
    try:
        identity = copy.deepcopy(server.process_identity)
        observed_listener = copy.deepcopy(listener)
        if mutation == "missing_birth":
            identity.pop("birth")
        elif mutation == "wrong_pid":
            identity["pid"] += 1
        elif mutation == "malformed_command_hash":
            identity["command_sha256"] = "not-a-sha256"
        elif mutation == "tampered_command":
            identity["command"] += " /unbound-command"
        elif mutation == "wrong_port":
            identity["port"] += 1
        elif mutation == "listener_pid_mismatch":
            observed_listener["pid"] += 1
        elif mutation == "listener_snapshot_mismatch":
            observed_listener["process_identity"] = {**observed_listener["process_identity"], "birth": "tampered"}
        if mutation not in {"listener_pid_mismatch", "listener_snapshot_mismatch"}:
            server.process_identity = identity
            observed_listener["process_identity"] = identity
        with pytest.raises(CandidateError):
            _bind_native_server_identity(server, observed_listener)
    finally:
        _stop_helper_test_child(proc)


def test_native_server_identity_rejects_popen_that_has_exited(tmp_path: Path) -> None:
    proc, server, listener = _start_actual_isolation_helper_process(tmp_path)
    _stop_helper_test_child(proc)
    assert proc.poll() is not None
    with pytest.raises(CandidateError):
        _bind_native_server_identity(server, listener)


def test_public_adapter_rejects_missing_or_mismatched_job_list_project_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from comsol_mcp import _control_client

    adapter = PublicDispatchAdapter(tmp_path, expected_control_home=tmp_path / "control-home",
                                    archive_root=REPO)
    monkeypatch.setattr(adapter, "_verify_owned_control_route", lambda: None)
    dispatched: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    monkeypatch.setattr(_control_client, "dispatch",
                        lambda operation, arguments, execution: dispatched.append(
                            (operation, dict(arguments), dict(execution))) or {"success": True, "data": {}})
    with pytest.raises(CandidateError, match="matching arguments and execution project_id"):
        adapter.dispatch({"operation": "job.list",
                          "arguments": {"project_id": "project-a", "limit": 500},
                          "execution": {}})
    with pytest.raises(CandidateError, match="matching arguments and execution project_id"):
        adapter.dispatch({"operation": "job.list",
                          "arguments": {"project_id": "project-a", "limit": 500},
                          "execution": {"project_id": "project-b"}})
    assert dispatched == []


@pytest.mark.parametrize("surface", ["sys_path", "path_hook", "meta_finder"])
def test_editable_finder_and_path_hook_are_rejected(surface: str) -> None:
    args: dict[str, Any] = {"sys_path": [], "path_hooks": [], "meta_path": []}
    if surface == "sys_path":
        args["sys_path"] = ["__editable__.comsol_mcp-0.1.9.finder.__path_hook__"]
    elif surface == "path_hook":
        args["path_hooks"] = [types.SimpleNamespace(__module__="__editable__.comsol_finder",
                                                     __name__="path_hook")]
    else:
        args["meta_path"] = [types.SimpleNamespace(__module__="editable_finder",
                                                    __name__="Finder")]
    with pytest.raises(Exception, match="editable Python finder/path hook"):
        _assert_no_editable_fallback(**args)


def test_live_project_module_injection_is_rejected_after_initial_archive_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    import comsol_mcp

    archive_root = Path.cwd().resolve()
    assert Path(comsol_mcp.__file__).resolve().is_relative_to(archive_root)
    assert _audit_loaded_project_modules(archive_root)["comsol_mcp"] == str(Path(comsol_mcp.__file__).resolve())
    injected = types.ModuleType("comsol_mcp.late_project_module")
    injected.__file__ = str(tmp_path / "live-checkout" / "comsol_mcp" / "late_project_module.py")
    (tmp_path / "live-checkout" / "comsol_mcp").mkdir(parents=True)
    Path(injected.__file__).write_text("# injected escape fixture\n")
    monkeypatch.setitem(sys.modules, "comsol_mcp.late_project_module", injected)
    with pytest.raises(Exception, match="loaded project module escaped the archive"):
        _audit_loaded_project_modules(archive_root)


def test_late_import_through_live_package_path_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    import comsol_mcp

    archive_root = Path.cwd().resolve()
    live_package = tmp_path / "live-checkout" / "comsol_mcp"
    live_package.mkdir(parents=True)
    (live_package / "late_import_escape.py").write_text("ORIGIN = 'live checkout'\n", encoding="utf-8")
    original_path = list(comsol_mcp.__path__)
    monkeypatch.setattr(comsol_mcp, "__path__", [*original_path, str(live_package)])
    importlib.invalidate_caches()
    try:
        escaped = importlib.import_module("comsol_mcp.late_import_escape")
        assert Path(escaped.__file__).resolve() == (live_package / "late_import_escape.py").resolve()
        with pytest.raises(Exception, match="package search path escaped the archive|module escaped the archive"):
            _audit_loaded_project_modules(archive_root)
    finally:
        sys.modules.pop("comsol_mcp.late_import_escape", None)
        monkeypatch.setattr(comsol_mcp, "__path__", original_path)
        importlib.invalidate_caches()


def test_comsol_package_search_path_cannot_include_live_checkout(tmp_path: Any) -> None:
    archive_root = Path.cwd().resolve()
    live_package = tmp_path / "live-checkout" / "comsol_mcp"
    live_package.mkdir(parents=True)
    fake = types.ModuleType("comsol_mcp")
    fake.__file__ = str(archive_root / "comsol_mcp" / "__init__.py")
    fake.__path__ = [str(archive_root / "comsol_mcp"), str(live_package)]
    with pytest.raises(Exception, match="package search path escaped the archive"):
        _audit_loaded_project_modules(archive_root, {"comsol_mcp": fake})


def test_real_control_daemon_public_dispatch_binds_exact_popen_without_comsol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    archive_root = Path.cwd().resolve()
    home_root = tmp_path / "owned-mcp-home"
    project_root = tmp_path / "owned-projects"
    project_root.mkdir()
    env = dict(os.environ)
    env.update({
        "COMSOL_SERVER_MCP_HOME": str(home_root),
        "COMSOL_PROJECT_ROOT": str(project_root),
        "COMSOL_MCP_TRUSTED_CODE": "1",
        "PYTHONPATH": os.pathsep.join((str(archive_root), str(EXPLICIT_SITE_PACKAGES.resolve(strict=True)))),
    })
    for key in ("COMSOL_SERVER_MCP_HOME", "COMSOL_PROJECT_ROOT", "COMSOL_MCP_TRUSTED_CODE", "PYTHONPATH"):
        monkeypatch.setenv(key, env[key])

    capture: dict[str, Any] = {}
    def on_child(proc: Any, identity: Any, endpoint: Any, stream: Any) -> None:
        capture.update(proc=proc, identity=identity, endpoint=endpoint, stream=stream)

    proc, identity, endpoint, stream = _start_control_daemon(
        tmp_path / "work", tmp_path, None, env, on_child)
    response: dict[str, Any] | None = None
    observed_control_home: str | None = None
    assertions_completed = False
    cleanup: dict[str, Any] | None = None
    try:
        assert proc.args == [str(EXPECTED_PYTHON), "-S", "-m",
                             "comsol_mcp._control_daemon", "--home",
                             str(home_root / "control-private")]
        assert "comsol_mcp._server" not in sys.modules
        launch = json.loads((tmp_path / "control_daemon_launch.json").read_text(encoding="utf-8"))
        source_binding = _source_inventory(
            archive_root, "a365420814b9159230364ad953ee13e8db9cc9be")
        assert launch["status"] == "Popen_BIRTH_ENDPOINT_BOUND"
        assert launch["python_no_site_switch"] is True
        assert launch["archive_base_commit"] == source_binding["base_commit"]
        assert launch["archive_source_closure_sha256"] == source_binding["source_closure_sha256"]
        assert launch["control_daemon_source_sha256"] == source_binding["source_files"][
            "comsol_mcp/_control_daemon.py"]["sha256"]
        assert launch["explicit_pythonpath"] == env["PYTHONPATH"]
        assert launch["popen_pid"] == endpoint["pid"]
        assert launch["birth_identity"] == identity
        assert launch["endpoint"]["pid"] == endpoint["pid"]
        assert "token" not in launch["endpoint"]
        assert endpoint["pid"] == proc.pid
        assert endpoint["process_start_epoch_ms"] == identity["start_epoch_ms"]
        assert _process_identity(proc.pid) == identity
        from comsol_mcp._control_client import control_home
        observed_control_home = str(control_home().resolve(strict=True))
        assert Path(observed_control_home) == (home_root / "control-private").resolve(strict=True)
        adapter = PublicDispatchAdapter(tmp_path, expected_control_home=home_root / "control-private",
                                       archive_root=archive_root)
        adapter.bind_owned_control(endpoint, identity)
        response = adapter.dispatch({
            "operation": "session.inspect",
            "arguments": {"session_id": "w23-control-smoke-no-session"},
            "execution": {"project_id": "w23-control-smoke-no-project",
                          "request_id": "w23-control-smoke-request",
                          "idempotency_key": "w23-control-smoke-idempotency",
                          "rpc_timeout_s": 2.0},
        })
        assert isinstance(response, dict)
        assert response.get("success") is False
        assert response.get("error", {}).get("code") != "EXECUTION_STATE_UNKNOWN"
        rows = _lsof_listeners(endpoint["port"])
        assert len(rows) == 1
        assert rows[0]["pid"] == proc.pid
        assert rows[0]["endpoint"] == f"127.0.0.1:{endpoint['port']}"
        assertions_completed = True
    finally:
        try:
            cleanup = _stop_owned_process(proc, identity, port=endpoint["port"])
            assert cleanup["status"] == "STOPPED_AND_REAPED"
        finally:
            if stream is not None:
                stream.close()
            receipt = {
                "schema_version": 1,
                "status": "PASS" if (assertions_completed and isinstance(cleanup, dict)
                                       and cleanup.get("status") == "STOPPED_AND_REAPED") else "FAIL",
                "comsol_engine_started": False,
                "native_scientific_result": "NOT_RUN",
                "control_home_observed_before_public_dispatch": observed_control_home,
                "expected_control_home": str((home_root / "control-private").resolve()),
                "control_daemon_command": list(proc.args),
                "control_daemon_cwd": str(archive_root),
                "archive_base_commit": launch["archive_base_commit"],
                "archive_manifest_sha256": launch["archive_manifest_sha256"],
                "archive_source_closure_sha256": launch["archive_source_closure_sha256"],
                "archive_source_file_count": launch["archive_source_file_count"],
                "control_daemon_source_sha256": launch["control_daemon_source_sha256"],
                "control_daemon_pythonpath": env["PYTHONPATH"],
                "control_daemon_no_site_switch": "-S" in proc.args,
                "runner_path_hooks": launch["runner_path_hooks"],
                "runner_meta_path_finders": launch["runner_meta_path_finders"],
                "editable_fallback_surfaces": launch["editable_fallback_surfaces"],
                "popen_pid": proc.pid,
                "popen_birth_identity": identity,
                "endpoint": {key: value for key, value in endpoint.items() if key != "token"},
                "public_dispatch": response,
                "cleanup": cleanup,
            }
            (tmp_path / "no_comsol_control_daemon_smoke_receipt.json").write_text(
                json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8")
def test_job_ledger_pages_real_public_control_daemon_sqlite_route(tmp_path: Path) -> None:
    from comsol_mcp._control_daemon import ControlDaemon

    project_id = "w23-public-route-project"
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control-home", project_root=project_root)

    def seed_job(index: int, *, project: str, status: str) -> None:
        record, reused = daemon.store.begin(
            request_id=f"w23-route-request-{index}",
            idempotency_key=f"w23-route-key-{index}",
            request_hash=hashlib.sha256(f"seed-{index}".encode()).hexdigest(),
            operation="w23.route.seed",
            metadata={"operation": "w23.route.seed", "project_id": project,
                      "execution": {"project_id": project}, "engine_dispatched": False},
        )
        assert reused is False
        daemon.store.finish(record["operation_id"], status=status,
                            result={"success": status == "SUCCEEDED", "data": {"engine_dispatched": False}})

    class DirectPublicControlRoute:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(json.loads(json.dumps(request)))
            return daemon.dispatch(request)

    route = DirectPublicControlRoute()
    try:
        for index in range(1007):
            seed_job(index, project=project_id, status="SUCCEEDED")
        seed_job(2000, project="w23-other-project", status="RUNNING")
        assert daemon.backend.worker is None

        ledger = _job_ledger_terminal(route, project_id)
        assert ledger["count"] == 1007
        assert ledger["total_count"] == 1007
        assert ledger["page_count"] == 3
        assert ledger["all_terminal"] is True
        assert ledger["nonterminal"] == []
        assert [request["arguments"]["offset"] for request in route.requests] == [0, 500, 1000]
        assert all(request["arguments"]["limit"] == 500 for request in route.requests)
        assert all(request["arguments"]["project_id"] == project_id
                   and request["execution"]["project_id"] == project_id
                   for request in route.requests)

        seed_job(2001, project=project_id, status="UNKNOWN")
        route.requests.clear()
        nonterminal = _job_ledger_terminal(route, project_id)
        assert nonterminal["count"] == 1008
        assert nonterminal["all_terminal"] is False
        assert len(nonterminal["nonterminal"]) == 1
        assert nonterminal["nonterminal"][0]["status"] == "UNKNOWN"

        action_catalog = json.loads(
            (REPO / "comsol_mcp/data/g2/02_ACTION_CATALOG.json").read_text(encoding="utf-8"))
        job_list = next(item for item in action_catalog["operations"]
                        if item["operation_id"] == "job.list")
        assert job_list["input_schema"]["properties"]["limit"]["maximum"] == 500
        too_large = daemon.dispatch({
            "operation": "job.list",
            "arguments": {"project_id": project_id, "offset": 0, "limit": 501},
            "execution": {"project_id": project_id},
        })
        assert too_large["success"] is False
        assert too_large["error"]["code"] == "INVALID_REQUEST"
    finally:
        daemon.close()
