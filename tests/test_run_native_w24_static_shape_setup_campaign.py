from __future__ import annotations

import hashlib
from contextlib import contextmanager
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import run_native_w24_static_shape_setup_campaign as campaign


class _Monitor:
    stop_reason = None

    def __init__(self):
        self.samples = 0

    def sample_once(self):
        self.samples += 1


class _Daemon:
    def __init__(self, responses: list[dict[str, Any]] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.store = object()
        self.session_registry = object()

    def dispatch(self, request):
        self.calls.append(request)
        if self.responses:
            return self.responses.pop(0)
        return {"success": True, "data": {"worker": {"status": "SUCCEEDED"}}}


def _gate(tmp_path: Path, daemon: _Daemon | None = None):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    events = tmp_path / "events.jsonl"
    events.write_bytes(b"")
    return campaign.BudgetedSetupDaemon(
        daemon or _Daemon(), monitor=_Monitor(),
        deadline_monotonic=time.monotonic() + campaign.MAX_WALL_SECONDS,
        events_path=events, workspace=workspace, project_id="project-1",
    )


def _operation_call(entrypoint: str, role: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": "operation_call",
        "arguments": {
            "operation_id": "code.execute_java",
            "arguments": {"source_artifact": role, "entrypoint": entrypoint,
                          "arguments": arguments, "mode": "trusted"},
        },
        "execution": {},
    }


def _java_response(readback: dict[str, Any], *, status: str = "SUCCEEDED") -> dict[str, Any]:
    return {"success": True, "data": {
        "worker": {"ok": status == "SUCCEEDED", "status": status,
                   "result": {"readback": readback}},
        "readback": {"executed": True, "readback": readback},
    }}


def test_setup_slots_are_the_exact_preregistered_fourteen_with_no_solve_budget():
    slots = campaign.build_setup_slots()
    assert len(slots) == campaign.SETUP_BUDGET["planned_setup_receipts"] == 14
    assert [row["configuration_id"] for row in slots[::2]] == campaign.CONFIGURATION_ORDER
    assert [row["case_id"] for row in slots] == ["flat", "step"] * 7
    assert all(row["study_run_calls"] == 0 and not row["phase_initialization_executed"]
               for row in slots)
    assert campaign.SETUP_BUDGET["study_run_submissions"] == 0
    assert campaign.SETUP_BUDGET["solver_calls"] == 0
    assert campaign.SETUP_BUDGET["max_solver_threads"] == 2


def test_epoch_binding_rejects_prior_worker_and_bool_generation():
    binding = {
        "project_id": "p1", "session_id": "s1", "revision": 8,
        "model_ref": {"session_id": "s1", "server_instance_id": "server1",
                      "generation": 4, "model_tag": "modelA"},
    }
    assert campaign.validate_current_epoch_binding(
        binding, project_id="p1", session_id="s1", server_instance_id="server1",
        worker_epoch=4) == binding
    with pytest.raises(campaign.CandidateError, match="prior project/session/Worker epoch"):
        campaign.validate_current_epoch_binding(
            binding, project_id="p1", session_id="s1", server_instance_id="server1",
            worker_epoch=5)
    bool_generation = {**binding, "model_ref": {**binding["model_ref"], "generation": True}}
    with pytest.raises(campaign.CandidateError):
        campaign.validate_current_epoch_binding(
            bool_generation, project_id="p1", session_id="s1", server_instance_id="server1",
            worker_epoch=1)


def test_campaign_receipt_acceptance_route_rejects_prior_worker_epoch(tmp_path, monkeypatch):
    daemon, project, worker, connected = _sqlite_control_daemon_with_stub_worker(
        tmp_path, monkeypatch)
    try:
        session = connected["data"]
        epoch = session["worker_epoch"]
        assert type(epoch) is int and epoch > 1
        workspace = Path(project["workspace"])
        source_manifest = campaign.importlib.import_module(
            "tools.run_native_w24_static_shape_setup").prepare_project_sources(
                workspace,
                fixture_source=campaign.REPO / "tools/java/W24StaticShapeFixture.java",
                readback_source=campaign.REPO / "tools/java/W24StaticShapeReadback.java")
        artifact = Path(source_manifest["outputs_directory"]) / "historical.mph"
        payload = b"synthetic historical setup output"
        artifact.write_bytes(payload)
        stale_ref = {"session_id": session["session_id"],
                     "server_instance_id": session["server_instance_id"],
                     "generation": epoch - 1, "model_tag": "old-worker-model"}
        stale_binding = {"project_id": project["project_id"],
                         "session_id": session["session_id"],
                         "model_ref": stale_ref, "revision": 17}
        result = {
            "status": "MANAGED_BUILD_SAVE_REOPEN_READBACK_COMPLETE",
            "configuration_id": "baseline", "case_id": "flat",
            "project_id": project["project_id"],
            "native_acceptance": "NOT_RUN",
            "phase_initialization_executed": False,
            "study_run_submission_count": 0, "study_run_submissions": [],
            "parent_model_binding": stale_binding,
            "new_model_binding": stale_binding,
            "reopened_model_binding": stale_binding,
            "reopened_configuration_readback_binding": stale_binding,
            "readback_comparison": {"matches": True},
            "source_sha256": {
                "fixture": source_manifest["sources"]["fixture"]["sha256"],
                "readback": source_manifest["sources"]["readback"]["sha256"],
            },
            "project_artifact": {"path": str(artifact), "size_bytes": len(payload),
                                 "sha256": hashlib.sha256(payload).hexdigest()},
        }
        with pytest.raises(campaign.CandidateError, match="prior project/session/Worker epoch"):
            campaign._validate_slot_receipt(
                result, slot={"configuration_id": "baseline", "case_id": "flat"},
                project_id=project["project_id"], session=session,
                workspace=workspace, expected_sources=source_manifest["sources"])
        assert worker.connect_calls == 1
        assert worker.disconnect_calls == 0
    finally:
        daemon.close()


def _scoped_inventory_command_results(monkeypatch, *, ps_stdout: str,
                                      lsof_stdout: str | None = None):
    calls = []

    def run(args, **_kwargs):
        calls.append(tuple(args))
        if args[0] == "/bin/ps":
            return subprocess.CompletedProcess(args=args, returncode=0,
                                               stdout=ps_stdout, stderr="")
        assert args[0] == "/usr/sbin/lsof"
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout=(lsof_stdout if lsof_stdout is not None else
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"),
            stderr="")

    monkeypatch.setattr(campaign.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    ("identity_result", "expected_status"),
    [
        ({"alive": True}, "IDENTITY_UNKNOWN"),
        ({"alive": None, "start_epoch_ms": 1_800_000_000_123}, "IDENTITY_UNKNOWN"),
        (None, "IDENTITY_UNKNOWN"),
        ({"alive": False, "start_epoch_ms": 1_800_000_000_123}, "IDENTITY_NOT_ALIVE"),
        ({"alive": True, "start_epoch_ms": 1_800_000_000_123}, "VERIFIED_ALIVE"),
    ],
)
def test_fresh_inventory_never_drops_ps_matched_process_on_identity_result(
        monkeypatch, identity_result, expected_status):
    import comsol_mcp._platform_process as platform_process

    ps_stdout = ("43210 1 Mon Sep 28 12:00:00 2026 "
                 "/Applications/COMSOL64/Multiphysics/bin/comsol mphserver\n")
    _scoped_inventory_command_results(monkeypatch, ps_stdout=ps_stdout)
    monkeypatch.setattr(platform_process, "process_identity",
                        lambda _pid: identity_result)

    inventory = campaign.fresh_scoped_inventory()

    assert len(inventory["matching_processes"]) == 1
    row = inventory["matching_processes"][0]
    assert row["pid"] == 43210
    assert row["identity_status"] == expected_status
    assert inventory["fresh_quiescent_for_scope"] is False
    assert inventory["process_identity_complete"] is (expected_status == "VERIFIED_ALIVE")


def test_fresh_inventory_identity_exception_is_retained_as_unknown_process(monkeypatch):
    import comsol_mcp._platform_process as platform_process

    ps_stdout = ("43211 1 Mon Sep 28 12:00:01 2026 "
                 "/Applications/COMSOL64/Multiphysics/bin/comsol mphserver\n")
    _scoped_inventory_command_results(monkeypatch, ps_stdout=ps_stdout)

    def fail_identity(_pid):
        raise OSError("birth lookup unavailable")

    monkeypatch.setattr(platform_process, "process_identity", fail_identity)
    inventory = campaign.fresh_scoped_inventory()

    assert inventory["matching_processes"] == [{
        "kind": "comsol_server", "pid": 43211, "ppid": 1,
        "identity_status": "IDENTITY_QUERY_FAILED", "identity_error_type": "OSError",
    }]
    assert inventory["process_identity_complete"] is False
    assert inventory["fresh_quiescent_for_scope"] is False


def test_fresh_inventory_is_quiescent_only_for_successful_empty_scoped_queries(monkeypatch):
    import comsol_mcp._platform_process as platform_process

    calls = _scoped_inventory_command_results(monkeypatch, ps_stdout="")
    monkeypatch.setattr(
        platform_process, "process_identity",
        lambda _pid: pytest.fail("identity lookup should not run without a ps match"),
    )

    inventory = campaign.fresh_scoped_inventory()

    assert inventory["matching_processes"] == []
    assert inventory["matching_listeners"] == []
    assert inventory["process_identity_complete"] is True
    assert inventory["fresh_quiescent_for_scope"] is True
    assert [args[0] for args in calls] == ["/bin/ps", "/usr/sbin/lsof"]


@pytest.mark.parametrize("failed_command", ["/bin/ps", "/usr/sbin/lsof"])
def test_fresh_inventory_empty_output_is_not_quiescent_if_either_query_failed(
        monkeypatch, failed_command):
    import comsol_mcp._platform_process as platform_process

    def run(args, **_kwargs):
        failed = args[0] == failed_command
        stdout = "" if args[0] == "/bin/ps" else "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
        return subprocess.CompletedProcess(args=args, returncode=1 if failed else 0,
                                           stdout=stdout, stderr="query failed" if failed else "")

    monkeypatch.setattr(campaign.subprocess, "run", run)
    monkeypatch.setattr(
        platform_process, "process_identity",
        lambda _pid: pytest.fail("identity lookup should not run without a ps match"),
    )

    inventory = campaign.fresh_scoped_inventory()

    assert inventory["matching_processes"] == []
    assert inventory["fresh_quiescent_for_scope"] is False


def test_campaign_dependency_preflight_fails_before_candidate_or_native_birth(tmp_path, monkeypatch):
    evidence = campaign.EVIDENCE_ROOT / "dependency_preflight_must_not_be_created"
    assert not evidence.exists()
    monkeypatch.setattr(campaign, "configure_archive_python",
                        lambda _repo: {"site_processing_disabled": True})
    monkeypatch.setattr(
        campaign, "preflight_campaign_dependencies",
        lambda _repo: (_ for _ in ()).throw(campaign.CandidateError(
            "pre-birth campaign dependency is unavailable: injected missing module")),
    )
    monkeypatch.setattr(campaign, "_compile_sources",
                        lambda *_args, **_kwargs: pytest.fail("javac must not start after missing dependency"))
    with pytest.raises(campaign.CandidateError, match="pre-birth campaign dependency"):
        campaign.prepare_candidate(repo=campaign.REPO, evidence=evidence)
    assert not evidence.exists()


def test_campaign_cleanup_proof_validators_fail_closed_on_worker_or_epoch_mismatch():
    valid = {"success": True, "data": {
        "project_id": "project-1", "session_id": "session-1",
        "state": "DISCONNECTED", "client_state": "RETIRED", "server_stopped": False,
        "worker_retirement": {
            "status": "RETIRED", "worker_instance_id": "worker-1", "worker_epoch": 8,
            "process_identity": {"pid": 4321, "start_epoch_ms": 123456},
            "exact_popen_handle": True, "birth_identity_matched_before_close": True,
            "child_exit_confirmed": True, "child_reaped": True,
            "admission_fence": "RETIRED", "disconnect_rpc_dispatched": True,
            "worker_close_started": True,
        },
    }}
    assert campaign._validate_worker_retirement_response(
        valid, project_id="project-1", session_id="session-1",
        worker_instance_id="worker-1", connected_epoch=7)["worker_epoch"] == 8
    for bad in (
        {**valid, "data": {**valid["data"], "worker_retirement": {
            **valid["data"]["worker_retirement"], "worker_instance_id": "foreign"}}},
        {**valid, "data": {**valid["data"], "worker_retirement": {
            **valid["data"]["worker_retirement"], "child_reaped": False}}},
    ):
        with pytest.raises(campaign.CandidateError):
            campaign._validate_worker_retirement_response(
                bad, project_id="project-1", session_id="session-1",
                worker_instance_id="worker-1", connected_epoch=7)
    inspect = {"success": True, "data": {"runtime_live": False, "worker_binding": None,
        "lifecycle": {"project_id": "project-1", "session_id": "session-1",
                      "state": "DISCONNECTED", "client_state": "RETIRED",
                      "worker_instance_id": "worker-1", "worker_epoch": 8}}}
    campaign._validate_disconnected_session_inspect(
        inspect, project_id="project-1", session_id="session-1",
        worker_instance_id="worker-1", worker_epoch=8)
    wrong_epoch = {**inspect, "data": {**inspect["data"], "lifecycle": {
        **inspect["data"]["lifecycle"], "worker_epoch": 7}}}
    with pytest.raises(campaign.CandidateError, match="exact retired session"):
        campaign._validate_disconnected_session_inspect(
            wrong_epoch, project_id="project-1", session_id="session-1",
            worker_instance_id="worker-1", worker_epoch=8)


def test_setup_route_fence_blocks_study_and_phase_initialization_without_dispatch(tmp_path):
    daemon = _Daemon()
    gate = _gate(tmp_path, daemon)
    for operation in ("study.run", "study_run", "phase_initialization", "solver.run"):
        with pytest.raises(campaign.CandidateError, match="setup-only route fence"):
            gate.dispatch({"operation": operation, "arguments": {}, "execution": {}})
    assert daemon.calls == []


def test_new_rpc_deadline_stops_at_cleanup_reserve(monkeypatch):
    monkeypatch.setattr(campaign.time, "monotonic", lambda: 100.0)
    assert campaign._remaining_admission_rpc(710.0, 120.0) == 10.0
    with pytest.raises(campaign.CandidateError, match="cleanup window"):
        campaign._remaining_admission_rpc(700.5, 120.0)


def test_setup_route_state_machine_requires_build_save_exact_reopen_and_full_readback(tmp_path):
    workspace = tmp_path / "workspace"
    outputs = workspace / "outputs"
    outputs.mkdir(parents=True)
    saved = outputs / "static_shape_flat.mph"
    payload = b"test artifact fixture bytes"
    saved.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    daemon = _Daemon([
        _java_response({"model_tag": "native-tag-flat"}),
        {"success": True, "execution": {"model_ref": {"model_tag": "native-tag-flat"}}},
        {"success": True, "data": {"worker": {"status": "SUCCEEDED"}}},
        _java_response({"status": "READBACK"}),
        _java_response({"status": "SAVED_UNSOLVED_STATIC_SHAPE_MODEL",
                        "path": str(saved), "size_bytes": len(payload), "sha256": digest}),
        {"success": True, "data": {"worker": {"status": "SUCCEEDED"}}},
        {"success": True, "data": {"worker": {"status": "SUCCEEDED"}}},
        _java_response({"status": "READBACK"}),
    ])
    gate = _gate(tmp_path, daemon)
    # Use the same workspace as the artifact route validator.
    gate.workspace = workspace.resolve()
    gate.set_slot("baseline", "flat")
    gate.dispatch(_operation_call("W24StaticShapeFixture#run", "W24StaticShapeFixture.java",
                                  {"action": "build", "case_id": "flat",
                                   "configuration_id": "baseline"}))
    assert gate.slot_stage == "built"
    with pytest.raises(campaign.CandidateError):
        gate.dispatch(_operation_call("W24StaticShapeFixture#run", "W24StaticShapeFixture.java",
                                      {"action": "build", "case_id": "flat",
                                       "configuration_id": "baseline"}))
    gate.dispatch({"operation": "model.adopt", "arguments": {"server_model_tag": "native-tag-flat"},
                   "execution": {}})
    gate.dispatch({"operation": "model.inspect", "arguments": {"detail": "summary"},
                   "execution": {}})
    gate.dispatch(_operation_call("W24StaticShapeReadback#run", "W24StaticShapeReadback.java",
                                  {"action": "readback", "expected_configuration_id": "baseline"}))
    gate.dispatch(_operation_call("W24StaticShapeReadback#run", "W24StaticShapeReadback.java",
                                  {"action": "save", "workspace_path": str(workspace),
                                   "path": str(saved)}))
    assert gate.slot_stage == "saved"
    gate.dispatch({"operation": "model_load", "arguments": {"path": str(saved)},
                   "execution": {}})
    gate.dispatch({"operation": "model.inspect", "arguments": {"detail": "summary"},
                   "execution": {}})
    gate.dispatch(_operation_call("W24StaticShapeReadback#run", "W24StaticShapeReadback.java",
                                  {"action": "readback", "expected_configuration_id": "baseline"}))
    assert gate.slot_stage == "readback_complete"
    assert len(daemon.calls) == 8
    gate.clear_slot()
    assert ("baseline", "flat") in gate.completed_slots
    with pytest.raises(campaign.CandidateError, match="duplicated"):
        gate.set_slot("baseline", "flat")


def test_model_load_refuses_saved_artifact_changed_after_java_sha_readback(tmp_path):
    daemon = _Daemon()
    gate = _gate(tmp_path, daemon)
    outputs = gate.workspace / "outputs"
    outputs.mkdir()
    artifact = outputs / "static_shape_flat.mph"
    original = b"original saved mph"
    artifact.write_bytes(original)
    gate.set_slot("baseline", "flat")
    gate.slot_stage = "saved"
    gate.slot_artifact = {"path": str(artifact), "size_bytes": len(original),
                          "sha256": hashlib.sha256(original).hexdigest()}
    artifact.write_bytes(b"changed after save verification")
    with pytest.raises(campaign.CandidateError, match="unchanged MPH"):
        gate.dispatch({"operation": "model_load", "arguments": {"path": str(artifact)},
                       "execution": {}})
    assert daemon.calls == []


def test_unknown_setup_call_is_not_replayed_or_followed_by_another_route(tmp_path):
    daemon = _Daemon([{"success": False, "execution_state_unknown": True,
                       "data": {"worker": {"status": "UNKNOWN"}}}])
    gate = _gate(tmp_path, daemon)
    gate.set_slot("baseline", "flat")
    build = _operation_call("W24StaticShapeFixture#run", "W24StaticShapeFixture.java",
                            {"action": "build", "case_id": "flat",
                             "configuration_id": "baseline"})
    with pytest.raises(campaign.CandidateError, match="terminal identity"):
        gate.dispatch(build)
    with pytest.raises(campaign.CandidateError, match="earlier setup call is UNKNOWN"):
        gate.dispatch(build)
    assert len(daemon.calls) == 1
    assert gate.unknown is True
    route_dir = tmp_path / "managed_routes"
    intents = list(route_dir.glob("*_intent.json"))
    results = list(route_dir.glob("*_result.json"))
    assert len(intents) == len(results) == 1
    intent = campaign.json.loads(intents[0].read_text())
    result = campaign.json.loads(results[0].read_text())
    assert intent["request_id"] == result["request_id"]
    assert intent["idempotency_key"] == result["idempotency_key"]
    assert result["response"]["execution_state_unknown"] is True


def test_terminal_failed_worker_mutation_stops_without_followup(tmp_path):
    daemon = _Daemon([{"success": False,
                       "data": {"worker": {"status": "FAILED"}}}])
    gate = _gate(tmp_path, daemon)
    gate.set_slot("baseline", "flat")
    build = _operation_call("W24StaticShapeFixture#run", "W24StaticShapeFixture.java",
                            {"action": "build", "case_id": "flat",
                             "configuration_id": "baseline"})
    with pytest.raises(campaign.CandidateError, match="Worker-backed setup route failed"):
        gate.dispatch(build)
    with pytest.raises(campaign.CandidateError, match="UNKNOWN"):
        gate.dispatch(build)
    assert gate.unknown is True
    assert len(daemon.calls) == 1


def test_direct_unknown_route_has_durable_intent_before_dispatch_and_no_replay(tmp_path):
    daemon = _Daemon()

    def uncertain_dispatch(_request):
        daemon.calls.append(_request)
        raise TimeoutError("test injected unknown operation result")

    daemon.dispatch = uncertain_dispatch
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    with pytest.raises(campaign.OutcomeUnknown, match="do not replay"):
        campaign._direct_dispatch(
            daemon, evidence, operation="session.connect",
            arguments={"endpoint": {"host": "127.0.0.1", "port": 5555}},
            request_label="connect", timeout_seconds=2.0)
    intents = list((evidence / "direct_routes").glob("*_intent.json"))
    exceptions = list((evidence / "direct_routes").glob("*_exception.json"))
    assert len(daemon.calls) == len(intents) == len(exceptions) == 1
    intent = campaign.json.loads(intents[0].read_text())
    exception = campaign.json.loads(exceptions[0].read_text())
    assert intent["persisted_before_dispatch"] is True
    assert intent["idempotency_key"] == exception["idempotency_key"]
    assert exception["outcome"] == "UNKNOWN_NO_REPLAY"


def test_prebirth_project_routes_use_real_control_daemon_with_persisted_identity(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    authorized_root = tmp_path / "authorized-project-root"
    authorized_root.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    daemon = ControlDaemon(tmp_path / "daemon-home", project_root=authorized_root)
    try:
        created, response = campaign._project_create_before_birth(
            daemon, authorized_root, evidence)
        assert response["success"] is True
        assert created["status"] == "PROJECT_CREATED_BEFORE_ENGINE_BIRTH"
        assert Path(created["workspace"]).is_dir()
        intents = sorted((evidence / "direct_routes").glob("*_intent.json"))
        results = sorted((evidence / "direct_routes").glob("*_result.json"))
        assert len(intents) == len(results) == 2
        rows = [campaign.json.loads(path.read_text()) for path in intents]
        create = rows[0]["request"]
        assert create["operation"] == "project.create"
        assert create["arguments"]["request_id"] == create["execution"]["request_id"]
        assert create["arguments"]["idempotency_key"] == create["execution"]["idempotency_key"]
        assert rows[1]["request"]["operation"] == "project.inspect"
    finally:
        daemon.close()


def test_sampled_rss_sums_only_exact_server_and_private_worker_and_stops_at_threshold(tmp_path):
    worker_root = tmp_path / "session-runtime-state" / "sessions"
    output = ("111 1 1024 /Applications/COMSOL64/Multiphysics/bin/comsol mphserver\n"
              f"222 111 3072 /java PersistentComsolWorker --endpoint-file {worker_root}/p/s/worker/worker_endpoint.json\n")
    identity = {111: {"alive": True, "start_epoch_ms": 1001},
                222: {"alive": True, "start_epoch_ms": 2002}}
    runner = lambda *_args, **_kwargs: subprocess.CompletedProcess(
        args=[], returncode=0, stdout=output, stderr="")
    monitor = campaign.SampledRssMonitor(
        server_pid=111, server_birth_ms=1001, worker_search_root=worker_root,
        events_path=tmp_path / "rss.jsonl", threshold_bytes=5 * 1024 * 1024,
        ps_runner=runner, identity_reader=lambda pid: identity[pid])
    monitor.bind_worker(222, 2002)
    sample = monitor.sample_once()
    assert sample["status"] == "SAMPLED_WITHIN_THRESHOLD"
    assert sample["combined_comsol_worker_rss_bytes"] == 4 * 1024 * 1024
    assert {row["kind"] for row in sample["processes"]} == {"server", "worker"}

    over = campaign.SampledRssMonitor(
        server_pid=111, server_birth_ms=1001, worker_search_root=worker_root,
        events_path=tmp_path / "rss_over.jsonl", threshold_bytes=4 * 1024 * 1024,
        ps_runner=runner, identity_reader=lambda pid: identity[pid])
    over.bind_worker(222, 2002)
    assert over.sample_once()["status"] == "RSS_THRESHOLD_REACHED"
    assert over.stop_reason == "RSS_THRESHOLD_REACHED"


def test_sampled_rss_fails_closed_if_bound_worker_disappears_or_birth_changes(tmp_path):
    worker_root = tmp_path / "worker-root"
    server_only = "111 1 1024 /Applications/COMSOL64/Multiphysics/bin/comsol mphserver\n"
    runner = lambda *_args, **_kwargs: subprocess.CompletedProcess(
        args=[], returncode=0, stdout=server_only, stderr="")
    monitor = campaign.SampledRssMonitor(
        server_pid=111, server_birth_ms=1001, worker_search_root=worker_root,
        events_path=tmp_path / "missing.jsonl", ps_runner=runner,
        identity_reader=lambda _pid: {"alive": True, "start_epoch_ms": 1001})
    monitor.bind_worker(222, 2002)
    assert monitor.sample_once()["status"] == "RSS_MONITOR_FAILED"
    assert monitor.stop_reason == "RSS_MONITOR_FAILED"


def test_sampled_rss_fails_closed_if_evidence_cannot_be_persisted(tmp_path):
    worker_root = tmp_path / "worker-root"
    ps = "111 1 1024 /Applications/COMSOL64/Multiphysics/bin/comsol mphserver\n"
    runner = lambda *_args, **_kwargs: subprocess.CompletedProcess(
        args=[], returncode=0, stdout=ps, stderr="")
    evidence_dir = tmp_path / "events_directory"
    evidence_dir.mkdir()
    monitor = campaign.SampledRssMonitor(
        server_pid=111, server_birth_ms=1001, worker_search_root=worker_root,
        events_path=evidence_dir, ps_runner=runner,
        identity_reader=lambda _pid: {"alive": True, "start_epoch_ms": 1001})
    assert monitor.sample_once()["status"] == "RSS_MONITOR_FAILED"
    assert monitor.stop_reason == "RSS_MONITOR_EVIDENCE_WRITE_FAILED"


def test_unknown_hold_persists_no_replay_no_auto_cleanup_policy(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_bytes(b"")
    server = SimpleNamespace(proc=None, server_snapshot=None, port=None)
    stop = threading.Event()
    stop.set()  # test-only escape from the production indefinite hold
    campaign._unknown_hold(
        daemon=None, server=server, monitor=None, run_dir=run_dir,
        work=tmp_path, project_id=None, session=None,
        candidate_sha256="a" * 64, reason="uncertain RPC", stop_event=stop)
    hold = campaign.json.loads((run_dir / "unknown_handle_hold.json").read_text())
    assert hold["status"] == "UNKNOWN_PRESERVED_NO_REPLAY_NO_CLOSE_NO_SERVER_STOP"
    assert "No automatic retry" in hold["policy"]
    assert "reconciliation_release_path" not in hold


class _StubOwnedWorkerProcess:
    def __init__(self, pid=876543):
        self.pid = pid
        self.returncode = None
        self.wait_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.returncode = 0
        return 0


class _SQLiteControlDaemonWorker:
    """Protocol-compatible harmless Worker stub; no COMSOL or JVM is started."""

    def __init__(self, process):
        self._process = process
        self.event_callback = None
        self.start_calls = 0
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.close_calls = 0
        self.request_status = {}
        self.metadata = {"pid": process.pid, "instance_id": "w24-cleanup-stub",
                         "generation": 1, "connected": False, "server": ""}

    def start(self):
        self.start_calls += 1
        return {"status": "HEALTHY", **self.metadata}

    def runtime_metadata(self):
        return dict(self.metadata)

    @contextmanager
    def operation_context(self, _operation_id, *, on_request_event=None):
        self.event_callback = on_request_event
        yield

    def client(self):
        return self

    def connect(self, port, host, **kwargs):
        self.connect_calls += 1
        request_id = kwargs.get("request_id")
        self._emit({"phase": "submitted", "request_id": request_id,
                    "kind": "connect", "metadata": {}})
        server = f"{host}:{port}"
        self.metadata.update(generation=self.metadata["generation"] + 1,
                             connected=True, server=server)
        reply = {"connected": True, "server": server,
                 "generation": self.metadata["generation"],
                 "instance_id": self.metadata["instance_id"],
                 "engine_version": "6.4.0.293"}
        self._emit({"phase": "observed", "request_id": request_id,
                    "status": "SUCCEEDED",
                    "reply": {"status": "SUCCEEDED", "result": reply}})
        return reply

    def disconnect(self, **kwargs):
        self.disconnect_calls += 1
        request_id = kwargs.get("request_id")
        self._emit({"phase": "submitted", "request_id": request_id,
                    "kind": "disconnect", "metadata": {}})
        self.metadata.update(generation=self.metadata["generation"] + 1,
                             connected=False, server="")
        reply = {"connected": False, "generation": self.metadata["generation"],
                 "instance_id": self.metadata["instance_id"]}
        self.request_status[request_id] = {
            "ok": True, "request_id": request_id, "status": "SUCCEEDED", "result": reply}
        self._emit({"phase": "observed", "request_id": request_id,
                    "status": "SUCCEEDED",
                    "reply": {"status": "SUCCEEDED", "result": reply}})
        return reply

    def status(self, request_id, *, timeout_s=1.0):
        return dict(self.request_status.get(request_id, {
            "ok": False, "request_id": request_id, "status": "UNKNOWN",
            "failure": {"code": "REQUEST_NOT_FOUND"}}))

    def close(self):
        self.close_calls += 1

    def _emit(self, event):
        if self.event_callback is not None:
            self.event_callback(event)


def _sqlite_control_daemon_with_stub_worker(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._session_context import (
        CanonicalSocket, SessionRuntimeConfig, session_state_directory,
    )
    import comsol_mcp._control_daemon as control_daemon_module

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(parents=True)
    process = _StubOwnedWorkerProcess()
    worker = _SQLiteControlDaemonWorker(process)
    monkeypatch.setattr(
        control_daemon_module, "process_identity",
        lambda pid: {"alive": pid == process.pid,
                     "start_epoch_ms": 1_800_000_000_123 if pid == process.pid else None},
    )

    def runtime_resolver(runtime_id, project_id, session_id, _project_root, state_root):
        assert runtime_id == "fixture-runtime"
        home = session_state_directory(state_root, project_id, session_id)
        return SessionRuntimeConfig(
            runtime_id=runtime_id, comsol_version="6.4.0.293",
            installation_root=tmp_path / "synthetic-comsol",
            java_executable=tmp_path / "synthetic-jdk/bin/java",
            classpath=(tmp_path / "synthetic-comsol/client.jar",),
            preferences_dir=home / "preferences", session_state_root=state_root,
        )

    daemon = ControlDaemon(
        tmp_path / "control-daemon", project_root=workspace_root, registry={},
        session_runtime_resolver=runtime_resolver,
        session_worker_factory=lambda _runtime, _worker_state: worker,
        session_peer_observer=lambda _worker, _port, _metadata: CanonicalSocket("127.0.0.1", 2046),
    )
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": "w24-cleanup-test", "workspace": "w24-cleanup-test",
                      "policy": {"permissions": ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": "w24-cleanup-project-create",
                      "idempotency_key": "w24-cleanup-project-create"},
    })
    assert created["success"] is True, created
    project = created["data"]["project"]
    connected = daemon.dispatch({
        "operation": "session.connect",
        "arguments": {"project_id": project["project_id"],
                      "idempotency_key": "w24-cleanup-session-connect",
                      "runtime_id": "fixture-runtime",
                      "endpoint": {"host": "127.0.0.1", "port": 2046}},
        "execution": {},
    })
    assert connected["success"] is True, connected
    return daemon, project, worker, connected


def test_exact_campaign_cleanup_uses_real_sqlite_daemon_and_stub_worker_only(tmp_path, monkeypatch):
    daemon, project, worker, connected = _sqlite_control_daemon_with_stub_worker(
        tmp_path, monkeypatch)
    run_dir = tmp_path / "cleanup-evidence"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_bytes(b"")

    class Monitor:
        stop_reason = None

        def __init__(self):
            self.worker_required_live = True
            self.samples = 0
            self.stopped = False
            self.last_sample = {"status": "TEST_STUB_SAMPLE"}

        def set_worker_required_live(self, required):
            self.worker_required_live = required

        def sample_once(self):
            self.samples += 1
            return self.last_sample

        def stop(self):
            self.stopped = True

    class LogHandle:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    server = SimpleNamespace(
        proc=SimpleNamespace(pid=654321), port=2047,
        process_identity={"pid": 654321, "start_epoch_ms": 1_800_000_000_456,
                          "birth": "Sep 27 12:00:00 2026",
                          "command_sha256": "b" * 64},
        _server_log_handle=LogHandle(),
    )
    stop_calls = []

    def stop_exact_server(observed_server, expected, port, *, timeout_seconds):
        stop_calls.append((observed_server, expected, port, timeout_seconds))
        return {"status": "STOPPED_AND_REAPED", "pid": expected["pid"],
                "start_epoch_ms": expected["start_epoch_ms"],
                "signal": "SIGTERM", "sigkill_used": False,
                "child_exit_confirmed": True, "child_reaped": True,
                "listener_absent": True}

    monkeypatch.setattr(campaign, "_stop_exact_server", stop_exact_server)
    monitor = Monitor()
    session = connected["data"]
    try:
        result = campaign._retire_worker_and_server(
            daemon=daemon, server=server, monitor=monitor,
            project_id=project["project_id"], session=session,
            run_dir=run_dir, deadline_monotonic=time.monotonic() + 600,
        )
        assert result["status"] == "EXACT_WORKER_RETIRED_SERVER_TERM_REAPED"
        assert campaign.json.loads((run_dir / "pre_retirement_ledger.json").read_text())[
            "safe_to_retire"] is True
        proof = campaign.json.loads((run_dir / "worker_retirement_proof.json").read_text())
        assert proof["worker_instance_id"] == session["worker_instance_id"]
        assert proof["worker_epoch"] > session["worker_epoch"]
        inspect = campaign.json.loads((run_dir / "disconnected_session_inspect.json").read_text())
        assert inspect["data"]["lifecycle"]["client_state"] == "RETIRED"
        assert worker.disconnect_calls == worker.close_calls == 1
        assert worker._process.wait_calls == 1
        assert monitor.worker_required_live is False and monitor.stopped
        assert len(stop_calls) == 1 and server._server_log_handle.closed
        assert len(list((run_dir / "direct_routes").glob("*_intent.json"))) == 2
        assert len(list((run_dir / "direct_routes").glob("*_result.json"))) == 2
        assert daemon.store.path.is_file()
        assert (tmp_path / "control-daemon").is_dir()
    finally:
        daemon.close()
