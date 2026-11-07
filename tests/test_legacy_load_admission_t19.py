"""T19: legacy model-load routes under configured W26 trusted admission.

Software scope only: real ControlDaemon + ExecutionService + OperationStore with
synthetic registry callables as engine-call witnesses.  No COMSOL, persistent
Worker, SSH or prior UNKNOWN resource is started or touched.

Before the fix, model_load, load_visible_main_model and load_current_main_model
classified PROJECT_WRITE and sat outside the disk/memory producer sets, so a
daemon with an installed trusted disk config executed all three with zero
admission events while catalogue save/load routes were capacity-admitted
(pre-fix evidence: evidence/full/T19-legacy-load-bypass-probe-20261108.txt).
The fix adds an admission-only classification override for exactly these three
names in _w26_os_resources.  These cases pin the required semantics: with a
trusted config installed, an insufficient trusted budget or a missing profile
refuses the legacy load before the Worker with zero engine calls; sufficient
capacity runs the reservation-before-RUNNING route; the unconfigured daemon
keeps the historical route unchanged; and neither dispatch permissions nor any
other PROJECT_WRITE classification move.
"""
import os
from pathlib import Path

import pytest

from comsol_mcp import _server, _state
from comsol_mcp import _w26_memory_resources as memory_resources
from comsol_mcp import _w26_os_resources as resources
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import (
    LEGACY_TOOL_EFFECTS,
    SessionLedger,
    permission_for_legacy_tool,
)
from comsol_mcp._execution_service import ExecutionService
from tests.test_control_daemon_projects import Snapshot, _CreatedModel, _Worker
from tests.test_w26_os_resources import _write_disk_config, _write_memory_config

LEGACY_LOAD_NAMES = ("model_load", "load_visible_main_model", "load_current_main_model")


def _profile_with(project_id, root_path, estimates, *, reserve_free=0, max_task=10 ** 18):
    # Trusted-startup shape shared with the W26 and T19 domain fixtures.
    root_info = root_path.stat()
    root = resources.PathIdentity(str(root_path), str(root_info.st_dev), str(root_info.st_ino),
                                  str(root_info.st_uid))
    storage_id = resources.PosixStorageObservationProvider.current_storage_id(str(root_path), root)
    return resources.DiskResourceProfile(
        "disk-profile-1", project_id, resources._current_platform_name(), resources._current_host_id(),
        root, storage_id,
        resources.DiskBudgetPolicy("ADMISSION_ONLY", reserve_free, max_task, None),
        tuple(estimates),
        tuple(resources.DiskOperationEstimate(op, value) for op, value in estimates.items()))


def _events(daemon, job_id):
    return [row[0] for row in daemon.store.db.execute(
        "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]


@pytest.fixture
def environment(tmp_path, monkeypatch, request):
    params = request.param
    monkeypatch.setattr(_state, "WORKFLOW_FILE", tmp_path / "legacy-workflow.json")
    home = tmp_path / "control"
    projects = tmp_path / "projects"
    projects.mkdir()
    bootstrap = ControlDaemon(home, project_root=projects)
    projects_created = {}
    for label in ("owner", "unprofiled"):
        projects_created[label] = bootstrap.project_authority.dispatch("project.create", {
            "label": label, "workspace": label,
            "policy": {"permissions": ["inspect", "project_write", "compute"]}})["data"]["project"]
    bootstrap.close()
    config = None
    if params.get("disk_enabled"):
        profile = _profile_with(projects_created["owner"]["project_id"],
                                Path(projects_created["owner"]["workspace"]), params["estimates"])
        config = _write_disk_config(tmp_path, profile)
    engine_calls = []

    def _make_load(name):
        def _load(arguments):
            engine_calls.append(name)
            monkeypatch.setattr(_server, "_current_model", _CreatedModel(f"loaded-{name}"), raising=False)
            return {"success": True, "data": {"requested_path": arguments.get("path", "")}}
        return _load

    service = ExecutionService(
        SessionLedger("session", "test-server",
                      permissions={"inspect", "project_write", "compute"}),
        Snapshot(), project_root=projects)
    daemon = ControlDaemon(
        home, project_root=projects, service=service, worker=_Worker(),
        registry={name: _make_load(name) for name in LEGACY_LOAD_NAMES},
        disk_resource_config=config)
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime", "worker_instance_id": "test-worker",
        "connection_epoch": 1, "server_instance_id": "test-server"}
    yield daemon, projects_created, engine_calls
    daemon.close()


def _load_request(project, operation, key):
    arguments = {"path": "input.mph"} if operation != "load_current_main_model" else {}
    return {"operation": operation, "arguments": arguments,
            "execution": {"project_id": project["project_id"], "session_id": "session",
                          "request_id": key, "idempotency_key": key}}


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
@pytest.mark.parametrize("operation", LEGACY_LOAD_NAMES)
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {name: 64 * 1024 for name in LEGACY_LOAD_NAMES}}],
                         indirect=True)
def test_configured_sufficient_capacity_admits_each_legacy_load_before_running(environment, operation):
    daemon, created, engine_calls = environment
    result = daemon.dispatch(_load_request(created["owner"], operation, f"load-{operation}"))
    assert result["success"] is True, result
    job_id = result["execution"]["job_id"]
    names = _events(daemon, job_id)
    assert names.index("W26DiskReservation") < names.index("W26DiskAdmission") < names.index("RUNNING")
    assert names.index("RUNNING") < names.index("SUCCEEDED")
    reservations = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                    if bound_job == job_id]
    assert len(reservations) == 1 and reservations[0]["status"] == "RELEASED"
    assert engine_calls == [operation]


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {"model_load": 10 ** 17}}], indirect=True)
def test_insufficient_trusted_budget_refuses_legacy_load_before_worker(environment):
    daemon, created, engine_calls = environment
    result = daemon.dispatch(_load_request(created["owner"], "model_load", "load-huge"))
    assert result["success"] is False, result
    assert result["error"]["code"] == "INSUFFICIENT_DISK_BUDGET", result["error"]
    assert result["data"]["engine_dispatched"] is False
    # The engine-call witness saw nothing: refusal precedes Worker dispatch.
    assert engine_calls == []
    names = _events(daemon, result["execution"]["job_id"])
    assert "RUNNING" not in names
    assert daemon.store.job(result["execution"]["job_id"])["status"] == "FAILED"


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
@pytest.mark.parametrize("operation", LEGACY_LOAD_NAMES)
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {"save_model": 64 * 1024}}], indirect=True)
def test_partial_trusted_inventory_fails_closed_on_each_legacy_load(environment, operation):
    # An installed inventory that covers other disk-producing operations but not
    # this load name must refuse it before dispatch instead of running uncapped.
    daemon, created, engine_calls = environment
    result = daemon.dispatch(_load_request(created["owner"], operation, f"load-uncovered-{operation}"))
    assert result["success"] is False, result
    assert result["error"]["code"] == "RESOURCE_RESERVATION_OPERATION_UNSUPPORTED", result["error"]
    assert result["data"]["engine_dispatched"] is False
    assert engine_calls == []
    assert "RUNNING" not in _events(daemon, result["execution"]["job_id"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {"model_load": 64 * 1024}}], indirect=True)
def test_project_without_trusted_profile_is_refused_for_legacy_load(environment):
    daemon, created, engine_calls = environment
    result = daemon.dispatch(_load_request(created["unprofiled"], "model_load", "load-no-profile"))
    assert result["success"] is False, result
    assert result["error"]["code"] == "DISK_RESOURCE_PROFILE_NOT_CONFIGURED", result["error"]
    assert engine_calls == []
    assert "RUNNING" not in _events(daemon, result["execution"]["job_id"])


@pytest.mark.parametrize("operation", LEGACY_LOAD_NAMES)
@pytest.mark.parametrize("environment", [{"disk_enabled": False}], indirect=True)
def test_unconfigured_daemon_keeps_historical_legacy_load_route(environment, operation):
    # No trusted disk config -> no coordinator, no reservations, unchanged route.
    daemon, created, engine_calls = environment
    result = daemon.dispatch(_load_request(created["owner"], operation, f"load-unconfigured-{operation}"))
    assert result["success"] is True, result
    assert engine_calls == [operation]
    assert not any(name.startswith("W26Disk") for name in _events(daemon, result["execution"]["job_id"]))


def test_admission_override_does_not_move_permission_boundaries():
    # Admission sees these three names as WRITE, exactly like catalogue
    # model.load; dispatch permission, legacy classification and the disk grant
    # stay project_write, and no other PROJECT_WRITE operation is reclassified.
    for name in LEGACY_LOAD_NAMES:
        assert resources._registered_operation_effect(name) == "WRITE"
        assert resources._permission_for_disk_operation(name) == "project_write"
        assert permission_for_legacy_tool(name) == "project_write"
        assert LEGACY_TOOL_EFFECTS[name] == "project_write"
    # Untouched PROJECT_WRITE neighbours keep their non-producing admission view.
    for name in ("model_create", "node_create", "set_parameters", "server_connect"):
        assert resources._registered_operation_effect(name) == "PROJECT_WRITE"
    with pytest.raises(ValueError):
        resources._disk_operation_effect("node_create")
    assert resources._registered_operation_effect("model.load") == "WRITE"


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {"model_load": 64 * 1024}}], indirect=True)
def test_configured_legacy_load_without_project_identity_is_refused(environment):
    # Admission needs one exact project identity to resolve a trusted profile.
    # This tightens unscoped daemon loads only while a trusted config is
    # installed; the unconfigured route stays as before.
    daemon, _created, engine_calls = environment
    result = daemon.dispatch({
        "operation": "model_load", "arguments": {"path": "input.mph"},
        "execution": {"request_id": "load-unscoped", "idempotency_key": "load-unscoped"}})
    assert result["success"] is False, result
    assert result["error"]["code"] == "PROJECT_IDENTITY_REQUIRED", result["error"]
    assert result["error"]["stage"] == "pre_dispatch"
    assert result["data"]["resource_admission"] == "REFUSED"
    assert engine_calls == []
    assert "RUNNING" not in _events(daemon, result["execution"]["job_id"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config loader fixture shape; Windows NOT_RUN")
def test_memory_admission_classifies_legacy_loads_as_producing(tmp_path):
    # The memory coordinator shares the same classifier; installing a trusted
    # memory inventory now requires memory admission for the legacy load names.
    # (The full memory reservation route also needs live OS observation, which
    # is separately evidenced/fenced; this case pins the gate opening.)
    projects = tmp_path / "projects"
    projects.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=projects)
    try:
        config = memory_resources.TrustedMemoryResourceConfig.load(
            _write_memory_config(tmp_path, operation="model_load"))
        coordinator = memory_resources.MemoryResourceCoordinator(
            config=config, store=daemon.store, authority=daemon.project_authority)
        assert coordinator.requires_reservation(project_id="project-any", operation_id="model_load") is True
        for name in ("load_visible_main_model", "load_current_main_model"):
            # A trusted memory inventory listing only model_load still admits
            # model_load; the other two names fail closed for a missing estimate
            # rather than bypassing the gate.
            with pytest.raises(Exception) as refusal:
                coordinator.requires_reservation(project_id="project-any", operation_id=name)
            assert refusal.value.code == "MEMORY_OPERATION_NOT_CONFIGURED"
        assert coordinator.requires_reservation(project_id="project-any", operation_id="model.inspect") is False
    finally:
        daemon.close()
