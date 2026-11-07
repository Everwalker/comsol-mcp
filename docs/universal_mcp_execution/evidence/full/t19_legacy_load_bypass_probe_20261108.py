#!/usr/bin/env python3
"""T19 pre-fix probe: legacy model-load routes bypass configured W26 admission.

Software scope only: real ControlDaemon + ExecutionService + OperationStore with a
synthetic registry callback as engine-call witness; no COMSOL/Worker/SSH starts and
no prior UNKNOWN resource is touched.  Run BEFORE the admission-classifier fix;
the JSON summary pins that, under an installed trusted disk config, save_model
(FILE_WRITE) is capacity-admitted while the legacy load names run with zero
admission events and cannot even be expressed in the trusted profile.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from comsol_mcp import _server, _state  # noqa: E402
from comsol_mcp import _w26_os_resources as resources  # noqa: E402
from comsol_mcp._control_daemon import ControlDaemon  # noqa: E402
from comsol_mcp._execution_contract import SessionLedger  # noqa: E402
from comsol_mcp._execution_service import ExecutionService  # noqa: E402


class Snapshot:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "test-server",
                "fingerprint": "test-fingerprint", "external_event_counter": 0}


class _JavaModel:
    def __init__(self, tag):
        self.tag_value = tag

    def tag(self):
        return self.tag_value


class _Model:
    def __init__(self, tag):
        self.java = _JavaModel(tag)


class _WorkerClient:
    def model(self, tag):
        return _Model(tag)


class _Worker:
    def client(self):
        return _WorkerClient()

    def operation_context(self, *_args, **_kwargs):
        from contextlib import nullcontext
        return nullcontext()


def _profile_with(project_id, root_path, estimates, *, reserve_free=0, max_task=10 ** 18):
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


def _write_disk_config(tmp_path: Path, profile: resources.DiskResourceProfile) -> Path:
    document = {"schema": resources.DISK_CONFIG_SCHEMA,
                "host_id": resources._current_host_id(),
                "platform": resources._current_platform_name(),
                "profiles": [{
                    "profile_id": profile.profile_id, "project_id": profile.project_id,
                    "root": profile.root.as_dict(), "storage_id": profile.storage_id,
                    "disk_budget": profile.disk_budget.as_dict(),
                    "allowed_operations": list(profile.allowed_operations),
                    "trusted_estimates": [item.as_dict() for item in profile.estimates]}]}
    directory = tmp_path / "disk-startup-config"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "profile.json"
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return path


def _events(daemon, job_id):
    return [row[0] for row in daemon.store.db.execute(
        "SELECT event FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]


def main() -> int:
    out: dict[str, object] = {}
    tmp = Path(tempfile.mkdtemp(prefix="t19-legacy-probe-")).resolve()
    include_model_load = "--include-model-load" in sys.argv
    _state.WORKFLOW_FILE = tmp / "legacy-workflow.json"
    home = tmp / "daemon-home"
    projects = tmp / "projects"
    projects.mkdir()

    bootstrap = ControlDaemon(home, project_root=projects)
    project = bootstrap.project_authority.dispatch("project.create", {
        "label": "legacy-load-probe", "workspace": "legacy-load-probe",
        "policy": {"permissions": ["inspect", "project_write", "compute"]}})["data"]["project"]
    bootstrap.close()

    # Trusted disk inventory: legacy save_model (FILE_WRITE) is expressed with a
    # huge estimate so admission must refuse it; the legacy load names cannot be
    # expressed at all before the fix.
    estimates = {"save_model": 10 ** 17}
    if include_model_load:
        estimates["model_load"] = 10 ** 17
    profile = _profile_with(project["project_id"], Path(project["workspace"]), estimates)
    config_path = _write_disk_config(tmp, profile)
    try:
        resources.DiskResourceProfile(
            "disk-profile-2", project["project_id"], profile.platform, profile.host_id,
            profile.root, profile.storage_id, profile.disk_budget,
            ("model_load",), (resources.DiskOperationEstimate("model_load", 1),))
        out["trusted_profile_can_name_model_load"] = "ACCEPTED"
    except ValueError as exc:
        out["trusted_profile_can_name_model_load"] = f"ValueError: {exc}"

    engine_calls: list[tuple[str, dict]] = []

    def _make_load(name):
        def _load(arguments):
            engine_calls.append((name, dict(arguments)))
            _server._current_model = _Model(f"loaded-{name}")
            return {"success": True, "data": {"requested_path": arguments.get("path", "")}}
        return _load

    def _save(arguments):
        engine_calls.append(("save_model", dict(arguments)))
        return {"success": True, "data": {}}

    service = ExecutionService(
        SessionLedger("probe-session", "test-server",
                      permissions={"inspect", "project_write", "compute"}),
        Snapshot(), project_root=projects)
    daemon = ControlDaemon(
        home, project_root=projects, service=service, worker=_Worker(),
        registry={"model_load": _make_load("model_load"),
                  "load_visible_main_model": _make_load("load_visible_main_model"),
                  "load_current_main_model": _make_load("load_current_main_model"),
                  "save_model": _save},
        disk_resource_config=config_path)
    daemon.backend.worker_identity = {
        "runtime_id": "test-runtime", "worker_instance_id": "test-worker",
        "connection_epoch": 1, "server_instance_id": "test-server"}
    try:
        out["effect_model_load"] = resources._registered_operation_effect("model_load")
        out["effect_load_visible_main_model"] = resources._registered_operation_effect("load_visible_main_model")
        out["effect_load_current_main_model"] = resources._registered_operation_effect("load_current_main_model")
        coord = daemon._disk_resource_coordinator
        out["disk_requires_reservation_model_load"] = coord.requires_reservation(
            project_id=project["project_id"], operation_id="model_load")
        out["disk_requires_reservation_save_model"] = coord.requires_reservation(
            project_id=project["project_id"], operation_id="save_model")

        execution = {"project_id": project["project_id"], "session_id": "probe-session"}
        load = daemon.dispatch({**{"operation": "model_load", "arguments": {"path": "input.mph"}},
                                "execution": {**execution, "request_id": "probe-load",
                                              "idempotency_key": "probe-load"}})
        out["model_load_success"] = load["success"]
        load_job = (load.get("execution") or {}).get("job_id")
        out["model_load_events"] = _events(daemon, load_job) if load_job else []
        if not load["success"]:
            out["model_load_error"] = (load.get("error") or {}).get("code")

        loaded_ref = (load.get("execution") or {}).get("model_ref")
        if loaded_ref is not None:
            save = daemon.dispatch({
                "operation": "save_model", "arguments": {"path": "exports/out.mph"},
                "execution": {**execution, "model_ref": loaded_ref, "request_id": "probe-save",
                              "idempotency_key": "probe-save"}})
            out["save_model_success"] = save["success"]
            out["save_model_error"] = (save.get("error") or {}).get("code")
            out["save_model_events"] = _events(daemon, save["execution"]["job_id"])

        for name in ("load_visible_main_model", "load_current_main_model"):
            res = daemon.dispatch({
                "operation": name, "arguments": {"path": "input.mph"} if name == "load_visible_main_model" else {},
                "execution": {**execution, "request_id": f"probe-{name}", "idempotency_key": f"probe-{name}"}})
            out[f"{name}_success"] = res["success"]
            res_job = (res.get("execution") or {}).get("job_id")
            out[f"{name}_events"] = _events(daemon, res_job) if res_job else []
            if not res["success"]:
                out[f"{name}_error"] = (res.get("error") or {}).get("code")

        out["engine_call_witness"] = [name for name, _args in engine_calls]
    finally:
        daemon.close()
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
