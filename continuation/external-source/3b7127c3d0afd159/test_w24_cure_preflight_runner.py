from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from tools import run_native_w24_cure_preflight as runner


def _probe_result(command: list[str], *, stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(command, code, stdout, stderr)


def _observer_ps_row() -> str:
    return f" {os.getpid()} {os.getppid()} python3.12 /usr/bin/python3.12 -m pytest\n"


def _empty_lsof_table() -> str:
    return "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"


def _unrelated_lsof_row(user: str = "local") -> str:
    return f"OtherApp 742 {user} 7u IPv4 0x1 0t0 TCP *:1234 (LISTEN)\n"


def test_w24_prebirth_inventory_requires_successful_process_and_listener_probes(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=_observer_ps_row())
        return _probe_result(command, stdout=(
            _empty_lsof_table() + _unrelated_lsof_row()))

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is True
    assert result["quiescent"] is True
    assert result["ps"]["observer_present"] is True
    assert result["comsol_listener_rows"] == []
    assert result["ps"]["command"] == ["/bin/ps", "-axo", "pid=,ppid=,comm=,args="]
    assert result["lsof"]["command"] == ["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]
    assert result["ps"]["stdout"]["bytes"] == len(_observer_ps_row().encode("utf-8"))
    assert len(result["ps"]["stdout"]["sha256"]) == 64
    assert len(calls) == 2


def test_w24_prebirth_inventory_fails_closed_when_ps_is_denied(monkeypatch):
    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stderr="PermissionError: SECRET_PS_STDERR", code=1)
        return _probe_result(command, stdout=_empty_lsof_table())

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is False
    assert result["quiescent"] is False
    assert result["ps"]["stderr"]["bytes"] > 0
    assert {cause["code"] for cause in result["ps"]["failure_causes"]} >= {
        "nonzero_exit", "stderr_nonempty"}
    assert "SECRET_PS_STDERR" not in json.dumps(result)


def test_w24_prebirth_inventory_blocks_existing_comsol_server_and_worker(monkeypatch):
    ps_rows = (
        _observer_ps_row() +
        " 700 1 /Applications/COMSOL64/Multiphysics/bin/comsol mphserver -port 62001\n"
        " 701 1 java com.comsol.mcp.worker_java.PersistentComsolWorker\n"
    )

    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=ps_rows)
        return _probe_result(command, stdout=_empty_lsof_table())

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is True
    assert result["quiescent"] is False
    assert [row["pid"] for row in result["comsol_engine_processes"]] == [700]
    assert [row["pid"] for row in result["persistent_worker_processes"]] == [701]
    assert "argv" not in result["comsol_engine_processes"][0]


def test_w24_prebirth_inventory_never_serializes_process_or_probe_secrets(monkeypatch):
    ps_rows = (
        _observer_ps_row() +
        " 711 1 java unrelated-client --api-key=SECRET_NATIVE_ARGV\n"
    )

    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=ps_rows, stderr="SECRET_PS_STDERR")
        return _probe_result(command,
                             stdout=_empty_lsof_table() + _unrelated_lsof_row("SECRET_LISTENER_USER"),
                             stderr="SECRET_LSOF_STDERR")

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()
    serialized = json.dumps(result)

    assert result["probes_ok"] is False
    assert "SECRET_NATIVE_ARGV" not in serialized
    assert "SECRET_PS_STDERR" not in serialized
    assert "SECRET_LSOF_STDERR" not in serialized
    assert "SECRET_LISTENER_USER" not in serialized
    assert all(isinstance(probe["stdout"], dict) and "sha256" in probe["stdout"]
               for probe in (result["ps"], result["lsof"]))


@pytest.mark.parametrize("ps_case", [
    ("", "inventory_empty"),
    (_observer_ps_row() + "malformed secret process row\n", "malformed_process_rows"),
    (" 799 1 background-worker /bin/background-worker\n", "observer_pid_missing"),
])
def test_w24_prebirth_inventory_rejects_empty_malformed_or_observer_missing_ps(
        monkeypatch, ps_case):
    ps_output, expected_cause = ps_case
    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=ps_output)
        return _probe_result(command, stdout=_empty_lsof_table())

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is False
    assert result["quiescent"] is False
    assert expected_cause in {cause["code"] for cause in result["ps"]["failure_causes"]}


def test_w24_prebirth_inventory_fails_closed_when_lsof_is_unavailable(monkeypatch):
    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=_observer_ps_row())
        return _probe_result(command, stderr="SECRET_LSOF_FAILURE", code=1)

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is False
    assert result["quiescent"] is False
    assert {cause["code"] for cause in result["lsof"]["failure_causes"]} >= {
        "listener_header_missing_or_malformed", "nonzero_exit", "stderr_nonempty"}
    assert "SECRET_LSOF_FAILURE" not in json.dumps(result)


@pytest.mark.parametrize("lsof_case", [
    (_empty_lsof_table() + "OtherApp 742\n", "malformed_listener_rows"),
    ("COMMAND PID USER FD\n", "listener_header_missing_or_malformed"),
])
def test_w24_prebirth_inventory_fails_closed_on_truncated_listener_inventory(
        monkeypatch, lsof_case):
    lsof_output, expected_cause = lsof_case

    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=_observer_ps_row())
        return _probe_result(command, stdout=lsof_output)

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is False
    assert result["quiescent"] is False
    assert expected_cause in {cause["code"] for cause in result["lsof"]["failure_causes"]}
    if expected_cause == "malformed_listener_rows":
        assert result["lsof"]["malformed_rows"] == 1


def test_w24_prebirth_inventory_allows_valid_kernel_rows_without_argv(monkeypatch):
    ps_rows = _observer_ps_row() + " 0 0 kernel_task\n"

    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=ps_rows)
        return _probe_result(command, stdout=_empty_lsof_table())

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is True
    assert result["quiescent"] is True
    assert result["ps"]["parsed_rows"] == 2
    assert result["ps"]["malformed_rows"] == 0


@pytest.mark.parametrize("comm", ["java", "/Applications/COMSOL64/Multiphysics/bin/comsol"])
def test_w24_prebirth_inventory_fails_closed_for_ambiguous_native_comm_without_argv(
        monkeypatch, comm):
    ps_rows = _observer_ps_row() + f" 811 1 {comm}\n"

    def run(command, **kwargs):
        if command[0] == "/bin/ps":
            return _probe_result(command, stdout=ps_rows)
        return _probe_result(command, stdout=_empty_lsof_table())

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._process_inventory()

    assert result["probes_ok"] is False
    assert result["quiescent"] is False
    assert "ambiguous_native_row_without_argv" in {
        cause["code"] for cause in result["ps"]["failure_causes"]}


def test_w24_runtime_appledouble_inventory_is_stable_across_relative_and_absolute_paths(monkeypatch):
    class Distribution:
        def __init__(self, path):
            self._path = Path(path)

    monkeypatch.setattr(runner.importlib.metadata, "distributions", lambda: [
        Distribution("._comsol_mcp.egg-info"),
        Distribution("/repo/._comsol_mcp.egg-info"),
    ])
    assert runner._runtime_appledouble_metadata() == ["._comsol_mcp.egg-info"]


def test_w24_runtime_gate_ignores_appledouble_sidecars_but_requires_frozen_packages():
    expected = runner._runtime_environment_candidate()
    actual = {key: expected[key] for key in (
        "python_executable", "python_resolved_executable", "python_version",
        "distributions", "distributions_sha256")}
    for sidecars in ([], ["._comsol_mcp.egg-info"],
                     ["._comsol_mcp.egg-info", "/repo/._comsol_mcp.egg-info"],
                     ["._comsol_mcp.egg-info", "._new-sidecar", "._new-sidecar"]):
        assert runner._validate_python_runtime_inventory(
            expected, {**actual, "ignored_appledouble_metadata": sidecars}) == {
                "jpype1": "1.7.1", "mcp": "1.30.0", "mph": "1.4.0"}

    missing_mcp = [row for row in actual["distributions"] if row["name"] != "mcp"]
    changed_mph = [{**row, "version": "9.9.9"} if row["name"] == "mph" else row
                   for row in actual["distributions"]]
    for invalid in (missing_mcp, changed_mph):
        try:
            runner._validate_python_runtime_inventory(
                expected, {**actual, "distributions": invalid,
                           "distributions_sha256": runner._json_sha256(invalid)})
        except RuntimeError as exc:
            assert "distribution inventory differs" in str(exc)
        else:
            raise AssertionError("a missing or changed installed dependency must fail closed")


def test_w24_source_closure_tracks_every_current_runtime_module_and_worker_java_source():
    closure = runner._all_source_paths()
    names = set(closure)
    assert "runtime:comsol_mcp/_runtime_control.py" in names
    assert "runtime:comsol_mcp/_runtime_installation.py" in names
    assert "control_daemon" in names
    assert "execution_service" in names
    assert "runtime:comsol_mcp/_execution_contract.py" in names
    assert "java_worker_source" in names
    assert "worker_transition_tests" in names
    assert closure["worker_transition_tests"].resolve() == (
        runner.REPO / "tests/test_w24_worker_transition.py").resolve()
    assert "runtime_data:comsol_mcp/data/g2/02_ACTION_CATALOG.json" in names
    assert "runtime_data:comsol_mcp/data/g2/common.schema.json" in names
    assert "workspace_action_catalog" in names
    assert {
        "runtime_control_tests", "g3_runtime_tests", "runtime_installation_tests",
        "control_daemon_tests", "control_daemon_projects_tests", "managed_backend_tests",
        "execution_service_tests", "session_context_tests", "session_lifecycle_tests",
        "operation_store_migration_tests", "project_authority_tests",
        "model_catalog_adapter_tests",
    }.issubset(names)
    for key in (
        "runtime_control_tests", "g3_runtime_tests", "runtime_installation_tests",
        "control_daemon_tests", "control_daemon_projects_tests", "managed_backend_tests",
        "execution_service_tests", "session_context_tests", "session_lifecycle_tests",
        "operation_store_migration_tests", "project_authority_tests",
        "model_catalog_adapter_tests",
    ):
        assert closure[key].is_file()

    package_files = {
        path.resolve() for path in (runner.REPO / "comsol_mcp").rglob("*.py")
        if not path.name.startswith("._")
    }
    closure_package_files = {
        path.resolve() for path in closure.values()
        if path.is_relative_to(runner.REPO / "comsol_mcp") and path.suffix == ".py"
    }
    assert closure_package_files == package_files
    package_data_files = {
        path.resolve() for path in (runner.REPO / "comsol_mcp").rglob("*.json")
        if not path.name.startswith("._")
    }
    closure_package_data_files = {
        path.resolve() for path in closure.values()
        if path.is_relative_to(runner.REPO / "comsol_mcp") and path.suffix == ".json"
    }
    assert closure_package_data_files == package_data_files
    assert not any(path.name.startswith("._") for path in closure.values())


def test_w24_worker_terminal_proof_requires_a_real_terminal_worker_status():
    assert runner._worker_request_terminal({"data": {"worker": {"status": "SUCCEEDED"}}}) is True
    assert runner._worker_request_terminal({"data": {"worker": {"status": "FAILED"}}}) is True
    assert runner._worker_request_terminal({"data": {
        "worker": {"status": "FAILED", "failure": {"execution_state_unknown": True}}
    }}) is True
    assert runner._worker_request_terminal({"data": {"worker": {"status": "RUNNING"}}}) is False
    assert runner._worker_request_terminal({"success": False}) is False


def test_w24_rpc_timeout_preserves_cleanup_reserve():
    assert runner._bounded_rpc_timeout(1000.0, 300.0, now=500.0) == 300.0
    assert runner._bounded_rpc_timeout(1000.0, 600.0, now=700.0) == 255.0


def test_w24_rpc_is_not_dispatched_inside_cleanup_reserve():
    try:
        runner._bounded_rpc_timeout(1000.0, 300.0, now=956.0)
    except TimeoutError as exc:
        assert "no Worker request dispatched" in str(exc)
    else:
        raise AssertionError("RPC timeout must fail closed inside cleanup reserve")


def test_w24_cleanup_job_inventory_distinguishes_unknown_work():
    class Store:
        def __init__(self, jobs):
            self.jobs = jobs
            self.project_ids = []

        def list_jobs(self, **kwargs):
            self.project_ids.append(kwargs.get("project_id"))
            return self.jobs

    terminal = runner._project_job_inventory(type("Daemon", (), {
        "store": Store([{"job_id": "j1", "status": "SUCCEEDED"}])
    })(), "project-run-a")
    unknown = runner._project_job_inventory(type("Daemon", (), {
        "store": Store([{"job_id": "j2", "status": "UNKNOWN"}])
    })(), "project-run-b")

    assert terminal["status"] == "TERMINAL"
    assert terminal["unknown"] == []
    assert unknown["status"] == "UNKNOWN"
    assert unknown["active"] == []
    assert unknown["unknown"] == [{"job_id": "j2", "status": "UNKNOWN"}]


def test_w24_study_submission_reader_accepts_list_subclass_job_pages():
    class Store:
        def list_jobs(self, **kwargs):
            return type("JobList", (list,), {})([
                {"job_id": "j1", "status": "UNKNOWN"}
            ])

        def events(self, job_id, **kwargs):
            return []

    daemon = type("Daemon", (), {"store": Store()})()
    assert runner._actual_study_run_submissions(daemon, "project-run") == []


def test_w24_ledger_helpers_read_every_job_and_event_page():
    class JobPage(list):
        def __init__(self, items, total):
            super().__init__(items)
            self.total = total

    jobs = [{"job_id": f"j{i}", "status": "SUCCEEDED"} for i in range(251)]
    events = [{"event": "observation", "metadata": {}} for _ in range(250)]
    events.append({"event": "worker_request", "metadata": {
        "phase": "submitted", "kind": "call",
        "metadata": {"type": "call", "method": "run"},
    }})

    class Store:
        project_ids = []

        def list_jobs(self, *, offset, limit, project_id):
            self.project_ids.append(project_id)
            items = jobs[offset:offset + limit]
            return JobPage(items, len(jobs))

        def events(self, job_id, *, offset, limit):
            source = events if job_id == "j250" else []
            return source[offset:offset + limit]

    store = Store()
    assert len(runner._all_project_jobs(store, "project-run", page_size=250)) == 251
    assert len(runner._all_job_events(store, "j250", page_size=250)) == 251
    daemon = type("Daemon", (), {"store": store})()
    assert runner._actual_study_run_submissions(daemon, "project-run") == [{
        "job_id": "j250", "status": "SUCCEEDED",
        "worker_event": events[-1]["metadata"],
    }]
    assert store.project_ids and set(store.project_ids) == {"project-run"}


def test_w24_project_workspace_is_created_through_canonical_dispatch_before_engine(tmp_path):
    root = tmp_path / "authorized-project-root"
    root.mkdir()

    class Daemon:
        requests = []
        backend = type("Backend", (), {"host_permission_ceiling": frozenset({
            "inspect", "project_write", "compute", "trusted_code"})})()

        def _project_host_permissions(self):
            return {"inspect", "project_write", "compute", "trusted_code"}

        def dispatch(self, request):
            self.requests.append(request)
            workspace = root / runner.PROJECT_WORKSPACE_NAME
            project_record = {
                "project_id": "project-created-by-production-route",
                "workspace": str(workspace.resolve()), "revision": 1,
                "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]},
            }
            if request["operation"] == "project.create":
                workspace.mkdir()
                return {"success": True, "data": {"project": project_record}}
            assert request["operation"] == "project.inspect"
            return {"success": True, "data": {
                "project": project_record,
                "effective_permissions": ["inspect", "project_write", "compute", "trusted_code"],
            }}

    daemon = Daemon()
    receipt = runner._create_registered_project_workspace(daemon, root)
    assert receipt["status"] == "PROJECT_CREATED_BEFORE_ENGINE_BIRTH"
    assert receipt["project_id"] == "project-created-by-production-route"
    assert Path(receipt["workspace"]) == root / "science"
    assert daemon.requests[0]["operation"] == "project.create"
    assert daemon.requests[0]["arguments"]["workspace"] == "science"
    assert daemon.requests[0]["arguments"]["policy"]["permissions"] == [
        "inspect", "project_write", "compute", "trusted_code"]
    assert daemon.requests[0]["arguments"]["idempotency_key"] == daemon.requests[0]["execution"]["idempotency_key"]
    assert daemon.requests[1]["operation"] == "project.inspect"
    assert daemon.requests[1]["execution"]["project_id"] == receipt["project_id"]
    assert "idempotency_key" not in daemon.requests[1]["execution"]
    assert set(daemon.requests[1]["arguments"]) == {"project_id", "request_id"}
    assert "trusted_code" in receipt["host_grants"]["startup_ceiling"]


def test_w24_project_workspace_gate_uses_real_control_daemon_before_engine_birth(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    root = tmp_path / "authorized-project-root"
    root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=root, registry={})
    try:
        receipt = runner._create_registered_project_workspace(daemon, root)
        project_id = receipt["project_id"]
        persisted = daemon.project_authority.get_project(project_id)

        assert receipt["status"] == "PROJECT_CREATED_BEFORE_ENGINE_BIRTH"
        assert persisted["project_id"] == project_id
        assert persisted["workspace"] == str(root / "science")
        assert set(persisted["policy"]["permissions"]) == {
            "inspect", "project_write", "compute", "trusted_code"}
        assert receipt["inspection_response"]["success"] is True
        assert receipt["inspection_response"]["data"]["project"] == persisted
        assert daemon.worker is None
        assert daemon.service is None

        # An explicit envelope key belongs to durable admission, not the
        # project domain arguments; production dispatch must keep that split.
        explicit_inspect = daemon.dispatch({
            "operation": "project.inspect",
            "arguments": {"project_id": project_id},
            "execution": {"project_id": project_id, "idempotency_key": "w24-explicit-inspect",
                           "request_id": "w24-explicit-inspect-request"},
        })
        assert explicit_inspect["success"] is True
        assert explicit_inspect["data"]["project"]["project_id"] == project_id
        assert daemon.worker is None
        assert daemon.service is None
    finally:
        daemon.close()


def test_w24_registered_project_refuses_without_prebirth_trusted_code_host_ceiling():
    daemon = type("Daemon", (), {
        "backend": type("Backend", (), {"host_permission_ceiling": frozenset({
            "inspect", "project_write", "compute"})})(),
        "_project_host_permissions": lambda self: {"inspect", "project_write", "compute"},
    })()
    with pytest.raises(RuntimeError, match="trusted_code is not enabled"):
        runner._require_trusted_code_host_grant(daemon)


def test_w24_trusted_code_startup_opt_in_is_separate_from_isolation_receipt(monkeypatch):
    monkeypatch.setenv(runner.ISOLATION_RECEIPT_ENV, "/stale/unverified/receipt.json")
    monkeypatch.delenv(runner.TRUSTED_CODE_STARTUP_ENV, raising=False)
    receipt = runner._configure_trusted_code_startup_opt_in()
    assert receipt["status"] == "EXPLICIT_TASK_DEPLOYMENT_OPT_IN_SET_BEFORE_DAEMON_START"
    assert receipt["trusted_code_isolation_claim"] is False
    assert receipt["isolation_receipt_installed_after_owned_server_verification"] is False
    assert os.environ[runner.TRUSTED_CODE_STARTUP_ENV] == "1"
    assert runner.ISOLATION_RECEIPT_ENV not in os.environ


def test_w24_reopened_model_binding_uses_canonical_project_scoped_adopt():
    session_id = "session-w24"
    project_id = "project-w24"
    model_tag = "w24_reopen_1234"
    calls = []

    class Daemon:
        backend = type("Backend", (), {
            "model_project_binding": lambda self, model_ref: {
                "attribution": "PROJECT_BOUND", "project_id": model_ref.get("project_id")},
        })()

        def dispatch(self, request):
            calls.append(request)
            return {"success": True, "execution": {
                "project_id": project_id,
                "session_id": session_id,
                "model_ref": {"project_id": project_id, "session_id": session_id,
                              "model_tag": model_tag},
                "revision": 0,
            }}

    result = runner._adopt_registered_model(
        Daemon(), project_id=project_id, session_id=session_id, model_tag=model_tag,
        idempotency_key="adopt-key", request_id="adopt-request")
    assert result["success"] is True
    assert calls == [{
        "operation": "model.adopt",
        "arguments": {"server_model_tag": model_tag},
        "execution": {"project_id": project_id, "session_id": session_id,
                       "idempotency_key": "adopt-key", "request_id": "adopt-request",
                       "rpc_timeout_s": 30.0, "queue_timeout_s": 30.0,
                       "execution_timeout_s": None},
    }]


def test_w24_reopened_model_binding_rejects_mismatched_project_or_tag():
    class Daemon:
        def __init__(self, project_id, model_tag):
            self.project_id = project_id
            self.model_tag = model_tag

        def dispatch(self, request):
            return {"success": True, "execution": {
                "project_id": self.project_id,
                "session_id": "session-w24",
                "model_ref": {"project_id": self.project_id, "session_id": "session-w24",
                              "model_tag": self.model_tag},
                "revision": 0,
            }}

    for daemon in (Daemon("other-project", "w24_reopen_1234"),
                   Daemon("project-w24", "w24_wrong_tag")):
        with pytest.raises(RuntimeError, match="exact project/session/native-tag identity"):
            runner._adopt_registered_model(
                daemon, project_id="project-w24", session_id="session-w24",
                model_tag="w24_reopen_1234", idempotency_key="adopt-key",
                request_id="adopt-request")


def test_w24_template_reopen_uses_project_scoped_managed_model_load(tmp_path):
    workspace = tmp_path / "science"
    workspace.mkdir()
    template = workspace / "cure_template.mph"
    template.write_bytes(b"native mph payload")
    project_id = "project-w24"
    ref = {"project_id": project_id, "session_id": "session-w24", "model_tag": "loaded_w24"}
    calls = []

    class Backend:
        @staticmethod
        def model_project_binding(model_ref):
            assert model_ref == ref
            return {"attribution": "PROJECT_BOUND", "project_id": project_id}

    class Daemon:
        backend = Backend()

        @staticmethod
        def dispatch(request):
            calls.append(request)
            return {"success": True, "data": {"model_tag": "loaded_w24"},
                    "execution": {"session_id": "session-w24",
                                  "model_ref": ref, "revision": 3}}

    result = runner._load_saved_template_managed(
        Daemon(), project_id=project_id, project_workspace=workspace, path=template,
        idempotency_key="managed-load-key", request_id="managed-load-request")

    assert result["status"] == "PROJECT_SCOPED_MANAGED_MODEL_LOAD_RETURNED"
    assert result["model_ref"] == ref
    assert result["revision"] == 3
    assert result["sha256"] == runner.sha256(template)
    assert len(calls) == 1
    assert calls[0]["operation"] == "model_load"
    assert calls[0]["arguments"] == {"path": str(template.resolve())}
    assert calls[0]["execution"]["project_id"] == project_id
    assert calls[0]["execution"]["idempotency_key"] == "managed-load-key"
    assert calls[0]["execution"]["request_id"] == "managed-load-request"
    assert calls[0]["execution"].get("model_ref") is None


def test_w24_outside_workspace_template_is_rejected_before_worker_dispatch(tmp_path):
    workspace = tmp_path / "science"
    workspace.mkdir()
    outside = tmp_path / "outside.mph"
    outside.write_bytes(b"not project scoped")
    calls = []

    class Daemon:
        @staticmethod
        def dispatch(request):
            calls.append(request)
            raise AssertionError("outside-workspace load must fail before daemon/Worker dispatch")

    with pytest.raises(RuntimeError, match="outside the registered workspace"):
        runner._load_saved_template_managed(
            Daemon(), project_id="project-w24", project_workspace=workspace, path=outside,
            idempotency_key="blocked-load-key", request_id="blocked-load-request")
    assert calls == []


def test_w24_workspace_template_path_rejects_symlink_and_parent_traversal_before_dispatch(tmp_path):
    workspace = tmp_path / "science"
    workspace.mkdir()
    outside = tmp_path / "outside.mph"
    outside.write_bytes(b"outside")
    link = workspace / "linked.mph"
    link.symlink_to(outside)
    calls = []

    class Daemon:
        @staticmethod
        def dispatch(request):
            calls.append(request)
            raise AssertionError("invalid path must fail before daemon/Worker dispatch")

    for path in (link, Path("../outside.mph")):
        with pytest.raises(RuntimeError, match="(symlink|parent traversal)"):
            runner._load_saved_template_managed(
                Daemon(), project_id="project-w24", project_workspace=workspace, path=path,
                idempotency_key="blocked-load-key", request_id="blocked-load-request")
    assert calls == []


def test_w24_registered_workspace_verification_rejects_outside_and_symlink_paths(tmp_path):
    root = tmp_path / "authorized-project-root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(RuntimeError, match="exact authorized science child"):
        runner._verify_registered_workspace({"project_id": "p", "workspace": str(outside)}, root)

    link = root / runner.PROJECT_WORKSPACE_NAME
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="exact authorized science child"):
        runner._verify_registered_workspace({"project_id": "p", "workspace": str(link)}, root)


def test_w24_terminal_failed_worker_unknown_effect_is_not_pending():
    request = "r1"
    worker_events = [
        {"event": "worker_request", "metadata": {
            "request_id": request, "phase": "submitted", "kind": "code_execute",
        }},
        {"event": "worker_request", "metadata": {
            "request_id": request, "phase": "observed", "kind": "code_execute",
            "reply": {"status": "FAILED", "failure": {
                "code": "ENGINE_CALL_FAILED", "execution_state_unknown": True,
            }},
        }},
    ]

    class Store:
        def list_jobs(self, **kwargs):
            return [{"job_id": "j1", "status": "UNKNOWN"}]

        def events(self, job_id, **kwargs):
            return worker_events

    inventory = runner._worker_request_activity_inventory(Store(), "project-run")
    assert inventory["status"] == "ALL_WORKER_REQUESTS_TERMINAL"
    assert inventory["pending_request_ids"] == []
    assert inventory["nonterminal_observed_request_ids"] == []
    assert inventory["execution_state_unknown_request_ids"] == [request]
    assert inventory["safe_to_cleanup"] is True


def test_w24_unobserved_worker_submission_blocks_cleanup():
    class Store:
        def list_jobs(self, **kwargs):
            return [{"job_id": "j1", "status": "UNKNOWN"}]

        def events(self, job_id, **kwargs):
            return [{"event": "worker_request", "metadata": {
                "request_id": "pending", "phase": "submitted", "kind": "code_execute",
            }}]

    inventory = runner._worker_request_activity_inventory(Store(), "project-run")
    assert inventory["status"] == "ACTIVE_OR_UNKNOWN_WORKER_REQUESTS"
    assert inventory["pending_request_ids"] == ["pending"]
    assert inventory["safe_to_cleanup"] is False


def test_w24_lost_worker_status_is_not_cleanup_terminal():
    worker_events = [
        {"event": "worker_request", "metadata": {
            "request_id": "r1", "phase": "submitted", "kind": "code_execute",
        }},
        {"event": "worker_request", "metadata": {
            "request_id": "r1", "phase": "observed", "kind": "code_execute",
            "reply": {"status": "LOST"},
        }},
    ]

    class Store:
        def list_jobs(self, **kwargs):
            return [{"job_id": "j1", "status": "UNKNOWN"}]

        def events(self, job_id, **kwargs):
            return worker_events

    worker_inventory = runner._worker_request_activity_inventory(Store(), "project-run")
    job_inventory = {"active": [], "unknown": [{"job_id": "j1", "status": "UNKNOWN"}]}
    reconciliation = runner._cleanup_reconciliation(
        job_inventory, worker_inventory, direct_native_calls_safe=True)

    assert worker_inventory["nonterminal_observed_request_ids"] == ["r1"]
    assert reconciliation["safe_for_owned_cleanup"] is False
    assert reconciliation["durable_result"]["project_jobs_unknown_preserved"] == job_inventory["unknown"]


def test_w24_terminal_worker_ledger_allows_exact_cleanup_while_preserving_unknown_job():
    worker_events = [
        {"event": "worker_request", "metadata": {
            "request_id": "r1", "phase": "submitted", "kind": "code_execute",
        }},
        {"event": "worker_request", "metadata": {
            "request_id": "r1", "phase": "observed", "kind": "code_execute",
            "reply": {"status": "FAILED", "failure": {"execution_state_unknown": True}},
        }},
    ]

    class Store:
        def list_jobs(self, **kwargs):
            return [{"job_id": "j1", "status": "UNKNOWN"}]

        def events(self, job_id, **kwargs):
            return worker_events

    worker_inventory = runner._worker_request_activity_inventory(Store(), "project-run")
    unknown = [{"job_id": "j1", "status": "UNKNOWN"}]
    reconciliation = runner._cleanup_reconciliation(
        {"active": [], "unknown": unknown}, worker_inventory,
        direct_native_calls_safe=True)

    assert reconciliation["safe_for_owned_cleanup"] is True
    assert reconciliation["durable_result"]["project_jobs_unknown_preserved"] == unknown
    assert reconciliation["durable_result"]["execution_state_unknown_request_ids"] == ["r1"]


def test_w24_direct_readback_timeout_keeps_exact_cleanup_blocked(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    guard = {"status": "NO_DIRECT_NATIVE_CALLS_YET", "all_returned": True, "calls": []}

    def unknown_readback():
        raise TimeoutError("worker readback did not return")

    try:
        runner._guarded_direct_native_call(
            guard, evidence, "direct_managed_signature_readback", unknown_readback)
    except TimeoutError:
        pass
    else:
        raise AssertionError("direct Worker readback timeout must propagate")

    persisted = json.loads((evidence / "direct_native_call_guard.json").read_text())
    worker_inventory = {
        "requests": {}, "pending_request_ids": [], "orphan_observed_request_ids": [],
        "nonterminal_observed_request_ids": [], "ambiguous_events": [],
        "duplicate_submitted_request_ids": [], "duplicate_observed_request_ids": [],
        "unique_submitted_request_ids": 0, "unique_observed_request_ids": 0,
        "execution_state_unknown_request_ids": [], "safe_to_cleanup": True,
    }
    reconciliation = runner._cleanup_reconciliation(
        {"active": [], "unknown": []}, worker_inventory,
        direct_native_calls_safe=(persisted["all_returned"] and all(
            row["status"] == "RETURNED" for row in persisted["calls"])))

    assert persisted["status"] == "UNRESOLVED_DIRECT_NATIVE_CALL"
    assert persisted["calls"][0]["status"] == "RAISED_OR_UNOBSERVED"
    assert reconciliation["safe_for_owned_cleanup"] is False
    assert reconciliation["checks"]["all_direct_native_calls_returned"] is False


def test_w24_reopened_template_readback_requires_every_field_to_match():
    before = {"geometry": {"domains": 4}, "solver": {"rtol": 1e-5},
              "status": "BUILT_NOT_SOLVED", "native_study_run_calls": 0}

    matching = runner._compare_reopened_readback(
        before, {"geometry": {"domains": 4}, "solver": {"rtol": 1e-5}})
    changed = runner._compare_reopened_readback(
        before, {"geometry": {"domains": 3}, "solver": {"rtol": 1e-5}})
    missing = runner._compare_reopened_readback(
        before, {"geometry": {"domains": 4}})
    added = runner._compare_reopened_readback(
        before, {"geometry": {"domains": 4}, "solver": {"rtol": 1e-5}, "extra": True})

    assert matching["matches"] is True
    assert changed["changed_keys"] == ["geometry"]
    assert missing["missing_keys"] == ["solver"]
    assert added["added_keys"] == ["extra"]


def test_w24_source_closure_includes_all_runtime_modules_and_worker_sources_but_not_appledouble(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    runtime = repo / "comsol_mcp"
    worker = runtime / "worker_java"
    runtime.mkdir(parents=True)
    worker.mkdir()
    explicit = repo / "tools" / "runner.py"
    explicit.parent.mkdir()
    explicit.write_text("pass\n")
    (runtime / "_g2_registry.py").write_text("REGISTRY = 1\n")
    (runtime / "._hidden.py").write_text("DO_NOT_FREEZE = 1\n")
    (worker / "WorkerExtra.java").write_text("class WorkerExtra {}\n")
    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "SOURCE_PATHS", {"explicit_runner": explicit})

    frozen = runner._all_source_paths()

    assert frozen["explicit_runner"] == explicit
    assert frozen["runtime:comsol_mcp/_g2_registry.py"] == runtime / "_g2_registry.py"
    assert frozen["worker_java:comsol_mcp/worker_java/WorkerExtra.java"] == worker / "WorkerExtra.java"
    assert all("._hidden" not in path.name for path in frozen.values())


def test_w24_native_execution_rejects_changed_offline_candidate_source(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    candidate_dir = repo / "docs/full_project_execution/w24/evidence/setup_candidate_test"
    candidate_dir.mkdir(parents=True)
    source = repo / "comsol_mcp" / "runtime.py"
    source.parent.mkdir()
    source.write_text("x = 1\n")
    plan = repo / "proposal.md"
    plan.write_text("candidate proposal\n")
    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "PLAN", plan)
    monkeypatch.setattr(runner, "SOURCE_PATHS", {"runtime": source})
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    plan_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
    candidate = {
        "schema": "W24_SETUP_CANDIDATE_OFFLINE_FREEZE_V1",
        "status": "FROZEN_OFFLINE_CANDIDATE_AWAITING_ROOT_NATIVE_SLOT",
        "candidate_id": "W24-CURE-HIST-AXISYM-01",
        "native_execution": {"status": "NOT_RUN", "engine_births_this_candidate": 0,
                             "study_run_submissions": 0, "macos_6_3_arm64": "USER_REQUESTED_SKIP",
                             "macos_6_3_x86_64": "USER_REQUESTED_SKIP"},
        "independent_native_budget": {
            "owned_server_processes": 1,
            "max_seconds_from_exact_birth_including_setup_reopen_and_cleanup": 900,
            "cleanup_reserve_seconds": 45,
            "worker_sessions_sequential": 1,
            "study_or_solver_submissions": 0,
        },
        "source_closure": {"runtime": {"path": str(source), "bytes": source.stat().st_size,
                                         "sha256": source_hash}},
        "runtime_environment": {
            "python_executable": "/usr/bin/python3",
            "python_resolved_executable": "/usr/bin/python3",
            "python_version": "3.12.13",
            "distributions": [
                {"name": "jpype1", "version": "1.7.1"},
                {"name": "mcp", "version": "1.30.0"},
                {"name": "mph", "version": "1.4.0"},
            ],
            "distributions_sha256": runner._json_sha256([
                {"name": "jpype1", "version": "1.7.1"},
                {"name": "mcp", "version": "1.30.0"},
                {"name": "mph", "version": "1.4.0"},
            ]),
            "ignored_appledouble_metadata": [],
            "pip_check_python": "/usr/bin/python3.12",
            "pip_check_python_resolved": "/usr/bin/python3.12",
            "pip_check_python_version": "3 12 13",
            "pip_check_version": "26.1",
        },
        "plan": {"path": str(plan), "sha256": plan_hash},
        "offline_java_compile": {"exit_code": 0, "classes": {"Fixture.class": {"sha256": "a" * 64}}},
        "python_compile": {"exit_code": 0},
        "focused_software_tests": {"exit_code": 0, "stdout": "3 passed"},
    }
    freeze_path = candidate_dir / "offline_candidate_freeze.json"
    freeze_path.write_text(json.dumps(candidate), encoding="utf-8")
    freeze_hash = hashlib.sha256(freeze_path.read_bytes()).hexdigest()

    verified = runner._verify_offline_candidate_freeze(freeze_path, freeze_hash)
    assert verified["status"] == candidate["status"]

    source.write_text("x = 2\n")
    try:
        runner._verify_offline_candidate_freeze(freeze_path, freeze_hash)
    except RuntimeError as exc:
        assert "source closure changed" in str(exc)
    else:
        raise AssertionError("native execution must fail closed after candidate-source drift")


def test_w24_missing_mcp_runtime_dependency_fails_before_native_inventory_or_birth(tmp_path, monkeypatch):
    expected_environment = runner._runtime_environment_candidate()
    original_distributions = expected_environment["distributions"]
    monkeypatch.setattr(
        runner, "_runtime_distributions",
        lambda: [row for row in original_distributions if row["name"] != "mcp"],
    )
    candidate = {"runtime_environment": expected_environment}
    monkeypatch.setattr(runner, "_verify_offline_candidate_freeze", lambda *_: candidate)
    inventory_calls = []
    monkeypatch.setattr(runner, "_process_inventory", lambda: inventory_calls.append("called"))

    root = Path("/private/tmp") / f"comsol-mcp-w24-cure-python-gate-{uuid4().hex}"
    work, evidence = root / "work", root / "evidence"
    args = type("Args", (), {
        "work": str(work), "evidence": str(evidence),
        "candidate_freeze": "/unused/candidate.json",
        "expected_candidate_sha256": "a" * 64,
    })()
    try:
        result = runner.run(args)
        receipt = json.loads((evidence / "python_runtime_preflight.json").read_text())
        assert result["status"] == "FAIL_OR_INCOMPLETE"
        assert result["engine_births"] == 0
        assert result["python_runtime_preflight"] == "FAIL"
        assert "distribution inventory differs" in receipt["error"]
        assert "production_registry" not in receipt
        assert inventory_calls == []
        assert not (evidence / "server_birth.json").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_w24_cli_resolves_repo_helpers_before_validating_paths():
    repo = Path(runner.__file__).resolve().parents[1]
    work = "/tmp/w24-invalid-path-import-smoke"
    evidence = "/tmp/w24-invalid-path-import-smoke-evidence"
    result = subprocess.run(
        [sys.executable, str(repo / "tools/run_native_w24_cure_preflight.py"),
         "--work", work, "--evidence", evidence],
        cwd=repo, text=True, capture_output=True, check=False, timeout=30,
    )

    assert result.returncode != 0
    assert "--work must be a unique task-owned" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert not Path(work).exists()
    assert not Path(evidence).exists()


def _expression_inventory_fixture(root: Path):
    evidence = root / "evidence"
    evidence.mkdir(parents=True)
    output = evidence / "solid_equation_view_expression_inventory.json"
    raw_rows = [
        ["solid.srr", "Radial normal Cauchy stress", "Pa"],
        ["solid.srz", "r-z shear stress", "Pa"],
        ["solid.u", "Radial displacement", "m"],
    ]
    candidates = [
        {"row_index": 0, "cues": ["stress", "cauchy", "radial", "solid.s-prefixed-identifier"],
         "raw_row": raw_rows[0]},
        {"row_index": 1, "cues": ["stress", "shear", "solid.s-prefixed-identifier"],
         "raw_row": raw_rows[1]},
    ]
    artifact = {
        "schema": "W24_COMSOL_EQUATION_VIEW_EXPRESSION_INVENTORY_V1",
        "status": "COMPLETE_NOT_EVALUATED",
        "complete": True,
        "read_only": True,
        "model_mutations": 0,
        "study_run_calls": 0,
        "component_tag": "comp1",
        "observed_component_physics_tags": ["ht", "solid", "ode"],
        "solid_physics_tags": ["solid"],
        "feature_tags_are_native_observations": True,
        "table_request": ["Expression", "recursive", "all"],
        "candidate_rule": runner.EXPRESSION_CANDIDATE_RULE,
        "feature_count": 1,
        "expression_row_count": len(raw_rows),
        "stress_candidate_row_count": len(candidates),
        "errors": [],
        "feature_tables": [{
            "physics_tag": "solid",
            "feature_tag": "lemm1",
            "status": "READ",
            "row_count": len(raw_rows),
            "raw_rows": raw_rows,
            "stress_candidate_rows": candidates,
        }],
    }
    data = (json.dumps(artifact, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    output.write_bytes(data)
    receipt = {
        "status": "NATIVE_EXPRESSION_INVENTORY_SAVED_NOT_EVALUATED",
        "path": str(output),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "complete": True,
        "feature_count": 1,
        "expression_row_count": len(raw_rows),
        "stress_candidate_row_count": len(candidates),
        "error_count": 0,
        "read_only": True,
        "model_mutations": 0,
        "study_run_calls": 0,
    }
    return output, receipt, artifact


def test_w24_expression_inventory_verifies_full_raw_table_hash_and_candidates():
    root = Path("/private/tmp") / f"comsol-mcp-w24-cure-inventory-test-{uuid4().hex}"
    root.mkdir()
    try:
        output, receipt, _artifact = _expression_inventory_fixture(root)
        verified = runner._verify_expression_inventory(receipt, output)
        assert verified["status"] == "COMPLETE_NOT_EVALUATED"
        assert verified["expression_row_count"] == 3
        assert verified["stress_candidate_row_count"] == 2
        assert verified["sha256"] == receipt["sha256"]
    finally:
        shutil.rmtree(root)


def test_w24_expression_inventory_rejects_truncated_native_feature_rows():
    root = Path("/private/tmp") / f"comsol-mcp-w24-cure-inventory-truncated-{uuid4().hex}"
    root.mkdir()
    try:
        output, receipt, artifact = _expression_inventory_fixture(root)
        artifact["feature_tables"][0]["raw_rows"].pop()
        data = (json.dumps(artifact, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        output.write_bytes(data)
        receipt["size_bytes"] = len(data)
        receipt["sha256"] = hashlib.sha256(data).hexdigest()
        try:
            runner._verify_expression_inventory(receipt, output)
        except RuntimeError as exc:
            assert "row count is incomplete" in str(exc)
        else:
            raise AssertionError("an internally truncated Equation View table must fail closed")
    finally:
        shutil.rmtree(root)
