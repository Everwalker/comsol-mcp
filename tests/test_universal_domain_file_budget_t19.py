"""T19 domain file-budget route evidence: W26 trusted disk admission on the model-save route.

Software scope only; no COMSOL/Worker/SSH is started and no prior UNKNOWN resource
is touched.  The frozen T19 obligation unifies file budgets across the domain,
general-API and controlled-Java routes.  The existing explicit budget for the
domain route is the trusted-startup disk admission (COMSOL_MCP_DISK_RESOURCE_CONFIG):
a per-project profile with operator-supplied trusted estimates, admission-only
capacity, reservation before RUNNING, terminal release, and fail-closed refusal of
disk-producing operations that the installed inventory does not cover.  The
domain save operations classify FILE_WRITE (save_model, save_main_model_snapshot,
commit_current_main_model, model.save, model.package, artifact.register) and the
G3 load/clone/restore operations classify WRITE; the legacy load names currently
sit outside the producer scope and remain an open issue for this work package.
No prior test pinned admission on a domain .mph save route.  These
cases prove admission is effective on model.save when configured and that the
unconfigured historical route is untouched, so large-model compatibility does not
depend on any guessed threshold.
"""
import os
from pathlib import Path

import pytest

from comsol_mcp import _g3_ops
from comsol_mcp import _w26_os_resources as resources
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._runtime_installation import runtime_id_for_root
from tests.test_g3_managed_models import _Worker, _runtime_root
from tests.test_w26_os_resources import _write_disk_config


def _profile_with(project_id, root_path, estimates, *, reserve_free=0, max_task=10 ** 18):
    # Same trusted-start shape as the W26 fixtures, but allowing the full slice of
    # the domain model lifecycle a case needs (with a trusted config installed the
    # daemon fail-closes every disk-producing route, model.create included).
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


@pytest.fixture
def environment(tmp_path, monkeypatch, request):
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    home = tmp_path / "daemon-home"
    projects = tmp_path / "projects"
    projects.mkdir()
    params = request.param
    bootstrap = ControlDaemon(home, project_root=projects)
    project = bootstrap.project_authority.dispatch("project.create", {
        "label": "domain-file-budget", "workspace": "domain-file-budget",
        "policy": {"permissions": ["inspect", "project_write", "compute"]}})["data"]["project"]
    project_path = Path(project["workspace"])
    bootstrap.close()
    config = None
    if params["disk_enabled"]:
        profile = _profile_with(project["project_id"], project_path,
                                 params["estimates"],
                                 reserve_free=params.get("reserve_free", 0))
        config = _write_disk_config(tmp_path, profile)
    install = _runtime_root(tmp_path / "install")
    endpoint = "127.0.0.1:25449"
    worker = _Worker(install, endpoint)
    service = ExecutionService(
        SessionLedger("software-session", "software-server",
                      permissions={"inspect", "project_write", "compute"}),
        worker, project_root=projects)
    daemon = ControlDaemon(home, service=service, worker=worker, registry={},
                           project_root=projects, disk_resource_config=config)
    daemon.backend.endpoint_key = endpoint
    daemon.backend.worker_identity = {
        "runtime_id": runtime_id_for_root(install), "worker_instance_id": "worker-1",
        "connection_epoch": 1, "server_instance_id": "software-server", "endpoint": endpoint}
    daemon.backend._bind_runtime_observation_context(
        installation_root=install, runtime_id=runtime_id_for_root(install), worker_instance_id="worker-1",
        connection_epoch=1, server_instance_id="software-server", endpoint=endpoint, legacy=False)
    daemon.backend.persist()
    created = daemon.dispatch({"operation": "model.create", "arguments": {"label": "budget-domain"},
                              "execution": {"project_id": project["project_id"],
                                            "session_id": service.ledger.session_id,
                                            "request_id": "model", "idempotency_key": "model"}})
    assert created["success"], created
    ref = created["execution"]["model_ref"]
    (project_path / "exports").mkdir()
    yield daemon, worker, project["project_id"], ref, project_path, service
    daemon.close()


def _save_request(project_id, ref, key):
    return {"operation": "model.save",
            "arguments": {"destination": "exports/model.mph", "overwrite": False,
                          "include_solution": True, "session_id": ref["session_id"]},
            "execution": {"project_id": project_id, "session_id": ref["session_id"],
                          "model_ref": ref, "expected_revision": 0,
                          "request_id": key, "idempotency_key": key}}


def _events(daemon, job_id):
    return [row[0] for row in daemon.store.db.execute(
        "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]


@pytest.mark.skipif(os.name == "nt", reason="POSIX trusted config and storage provider; Windows adapter NOT_RUN")
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {"model.create": 64 * 1024,
                                                        "model.save": 10 ** 17}}], indirect=True)
def test_model_save_route_is_disk_admitted_and_refuses_before_any_engine_call(environment):
    daemon, worker, project_id, ref, project_path, _service = environment
    result = daemon.dispatch(_save_request(project_id, ref, "save-low"))
    assert result["success"] is False, result
    assert result["error"]["code"] == "INSUFFICIENT_DISK_BUDGET", result["error"]
    assert result["data"]["engine_dispatched"] is False
    # Admission refused before RUNNING: the bound model received no engine call
    # at all and no destination bytes were produced.
    assert worker.models[ref["model_tag"]].java.calls == []
    assert not (project_path / "exports" / "model.mph").exists()
    job_id = result["execution"]["job_id"]
    assert daemon.store.job(job_id)["status"] == "FAILED"
    assert "RUNNING" not in _events(daemon, job_id)


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows kernel adapter NOT_RUN")
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {"model.create": 64 * 1024,
                                                        "model.save": 64 * 1024}}], indirect=True)
def test_model_save_route_reserves_before_running_and_releases_after_terminal(environment):
    daemon, worker, project_id, ref, project_path, _service = environment
    result = daemon.dispatch(_save_request(project_id, ref, "save-ok"))
    assert result["success"] is True, result
    assert (project_path / "exports" / "model.mph").is_file()
    job_id = result["execution"]["job_id"]
    names = _events(daemon, job_id)
    assert "W26DiskAdmission" in names
    assert names.index("W26DiskReservation") < names.index("W26DiskAdmission") < names.index("RUNNING")
    assert names.index("RUNNING") < names.index("SUCCEEDED")
    reservations = [item for bound_job, item in resources._load_reservations(daemon.store.db).values()
                    if bound_job == job_id]
    assert len(reservations) == 1 and reservations[0]["status"] == "RELEASED"
    calls = [method for method, _args in worker.models[ref["model_tag"]].java.calls]
    assert "saveModelPackage" in calls


@pytest.mark.skipif(os.name == "nt", reason="POSIX coordinator; Windows kernel adapter NOT_RUN")
@pytest.mark.parametrize("environment", [{"disk_enabled": True,
                                          "estimates": {"model.create": 64 * 1024}}], indirect=True)
def test_partial_trusted_inventory_fails_closed_on_the_domain_save_route(environment):
    # A trusted disk inventory that covers other operations but not model.save must
    # refuse the save before dispatch instead of running it uncapped.
    daemon, worker, project_id, ref, project_path, _service = environment
    result = daemon.dispatch(_save_request(project_id, ref, "save-uncovered"))
    assert result["success"] is False, result
    assert result["error"]["code"] == "RESOURCE_RESERVATION_OPERATION_UNSUPPORTED", result["error"]
    assert result["data"]["engine_dispatched"] is False
    assert worker.models[ref["model_tag"]].java.calls == []
    assert not (project_path / "exports" / "model.mph").exists()
    assert "RUNNING" not in _events(daemon, result["execution"]["job_id"])


@pytest.mark.parametrize("environment", [{"disk_enabled": False}], indirect=True)
def test_unconfigured_daemon_keeps_the_historical_model_save_route(environment):
    # No trusted disk config -> no coordinator, no reservations, unchanged route.
    # This is what keeps existing large-model behaviour compatible: enforcement
    # appears only with operator-supplied trusted estimates, never a guessed default.
    daemon, worker, project_id, ref, project_path, _service = environment
    result = daemon.dispatch(_save_request(project_id, ref, "save-unconfigured"))
    assert result["success"] is True, result
    assert (project_path / "exports" / "model.mph").is_file()
    names = _events(daemon, result["execution"]["job_id"])
    assert not any(name.startswith("W26Disk") for name in names)

