from __future__ import annotations

import pytest

from comsol_mcp import _g2_registry, _g3_ops
from comsol_mcp._control_daemon import ControlDaemon


def _project(daemon: ControlDaemon, name: str, permissions=None):
    response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": name, "workspace": name,
                      "policy": {"permissions": permissions or ["inspect", "project_write"]}},
        "execution": {"request_id": "create-" + name, "idempotency_key": "create-" + name},
    })
    assert response["success"] is True, response
    return response["data"]["project"]


def _job(daemon: ControlDaemon, project_id: str, *, owner=None):
    owner = owner or project_id
    record, reused = daemon.store.begin(
        request_id="run-" + owner, idempotency_key="run-" + owner,
        request_hash="run-hash-" + owner, operation="study.run",
        metadata={"operation": "study.run", "arguments": {},
                  "execution": {"project_id": owner, "session_id": "session-1"}},
    )
    assert not reused
    daemon.store.update_job(record["job_id"], "RUNNING")
    daemon.store.add_event(record["job_id"], "RUNNING", {"phase": "queued"})
    daemon.store.add_event(record["job_id"], "SolverStarted", {"study": "std1"})
    return record["job_id"]


def test_six_catalogue_routes_are_published_with_catalogue_effects():
    model_ops = {
        "solver.solution_inspect": "READ",
        "solver.solution_clear": "WRITE",
        "solver.resource_configure": "WRITE",
        "study.initial_solution_set": "WRITE",
        "result.sample_grid": "EVALUATE",
    }
    for operation_id, effect in model_ops.items():
        assert _g3_ops.DISPATCH[operation_id].__module__.endswith("_g3_solver_ops")
        assert _g3_ops.EFFECTS[operation_id] == effect
        assert _g2_registry.is_implemented(operation_id)
    assert "solver.log_read" in _g2_registry.CONTROL_IMPLEMENTED_OPERATIONS
    assert _g2_registry.is_implemented("solver.log_read")
    assert _g3_ops.effect_of("solver.log_read") is None


def test_solver_log_read_direct_and_registry_paths_are_project_bound_and_worker_free(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    try:
        project = _project(daemon, "log-read-project")
        job_id = _job(daemon, project["project_id"])
        direct = daemon.dispatch({
            "operation": "solver.log_read",
            "arguments": {"project_id": project["project_id"], "job_id": job_id, "limit": 1},
            "execution": {"project_id": project["project_id"]},
        })
        assert direct["success"] is True, direct
        assert direct["data"]["events"][0]["event"] == "RUNNING"
        assert direct["data"]["next_cursor"]
        assert direct["data"]["solver_output_log"]["status"] == "NOT_CAPTURED"
        from jsonschema import validate
        validate(instance=direct["data"], schema=_g2_registry.BY_ID["solver.log_read"].as_dict()["data_schema"])

        nested = daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "solver.log_read", "arguments": {
                "project_id": project["project_id"], "job_id": job_id,
            }},
            "execution": {"project_id": project["project_id"]},
        })
        assert nested["success"] is True, nested
        assert [row["event"] for row in nested["data"]["events"]] == ["RUNNING", "SolverStarted"]
        assert daemon.backend.worker is None
    finally:
        daemon.close()


def test_solver_log_read_refuses_unbound_mismatched_and_foreign_job_reads_before_leakage(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    try:
        first = _project(daemon, "first-project")
        second = _project(daemon, "second-project")
        job_id = _job(daemon, second["project_id"])

        unbound = daemon.dispatch({
            "operation": "solver.log_read",
            "arguments": {"project_id": first["project_id"], "job_id": job_id},
            "execution": {},
        })
        assert unbound["error"]["code"] == "PROJECT_IDENTITY_REQUIRED"

        mismatched = daemon.dispatch({
            "operation": "solver.log_read",
            "arguments": {"project_id": second["project_id"], "job_id": job_id},
            "execution": {"project_id": first["project_id"]},
        })
        assert mismatched["error"]["code"] == "PROJECT_IDENTITY_MISMATCH"

        foreign_job = daemon.dispatch({
            "operation": "solver.log_read",
            "arguments": {"project_id": first["project_id"], "job_id": job_id},
            "execution": {"project_id": first["project_id"]},
        })
        assert foreign_job["error"]["code"] == "PROJECT_IDENTITY_MISMATCH"
        assert foreign_job["data"].get("events") is None
    finally:
        daemon.close()


def test_solver_log_read_requires_project_inspect_permission_before_store_lookup(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    try:
        project = _project(daemon, "no-inspect-project", ["project_write"])
        response = daemon.dispatch({
            "operation": "solver.log_read",
            "arguments": {"project_id": project["project_id"], "job_id": "nonexistent"},
            "execution": {"project_id": project["project_id"]},
        })
        assert response["error"]["code"] == "PERMISSION_DENIED"
    finally:
        daemon.close()


def test_solution_inspect_reaches_the_real_w26_adapter_through_managed_dispatch(tmp_path, monkeypatch):
    from tests.test_artifact_registration_import import _make_daemon, _parameter_action
    from comsol_mcp import _g3_solver_ops as solver_ops

    daemon, _project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    rows = [{"outer": 1, "inner": 1, "solnum": 4}]
    indices = {
        "dataset": "dset1", "solution": "sol1", "binding_complete": True,
        "dataset_binding": {"binding_complete": True, "solution": "sol1"},
        "solution_count": 1, "inner_indices": [1], "outer_indices": [1],
        "solnum_pairs": rows, "time_values": [], "parameters_complete": True,
        "parameters": {"by_pair": {"1:1": {"names": ["t"], "values": [0.1],
                                             "units": ["s"], "solnum": 4}}},
        "read_errors": [],
    }

    class _Solution:
        def isEmpty(self):
            return False

        def isInitialized(self):
            return True

        def getDefaultSolnum(self):
            return 4

    class _Solutions:
        def tags(self):
            return ["sol1"]

        def get(self, tag):
            assert tag == "sol1"
            return _Solution()

    class _BoundModel:
        def sol(self):
            return _Solutions()

    monkeypatch.setattr(solver_ops, "bound_model", lambda *_: _BoundModel())
    monkeypatch.setattr(solver_ops, "dataset_solution_indices", lambda *_args, **_kwargs: indices)
    try:
        response = _parameter_action(
            daemon, "solver.solution_inspect", model_execution,
            {"solution": {"dataset": "dset1", "time": [{"value": 0.1, "unit": "s"}]}},
            key="w26-solver-inspect", request_id="w26-solver-inspect-request", entrypoint="direct",
        )
        assert response["success"] is True, response
        assert response["data"]["selection_status"] == "MATCHED"
        assert response["data"]["selected_pairs"] == rows
        assert response["execution"]["revision"] == model_execution["revision"]
    finally:
        daemon.close()


def test_solver_write_and_grid_actions_cross_managed_dispatch(tmp_path, monkeypatch):
    from tests.test_artifact_registration_import import (
        _enable_synthetic_parameter_isolation, _make_daemon, _parameter_action,
    )
    from tests.test_g3_solver_ops import _direct_node, _indices, _state_model, _study_step
    from comsol_mcp import _g2_engine, _g3_solver_ops as solver_ops

    daemon, _project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    direct, step = _direct_node(), _study_step()
    engine_model, source_solver = _state_model(direct=direct, target=step)
    monkeypatch.setattr(solver_ops, "bound_model", lambda *_: engine_model)
    monkeypatch.setattr(solver_ops, "dataset_solution_indices", lambda *_args, **_kwargs: _indices())
    # This preserves the production property_set/readback path while giving it
    # the engine-shaped fixture instead of the control fixture's empty model.
    monkeypatch.setattr(_g2_engine, "_model", lambda *_: engine_model)
    monkeypatch.setattr(solver_ops, "result_at_points", lambda _worker, _tag, args: {
        "status": "VERIFIED", "verification_status": "PASSED",
        "expressions": args["spec"]["expressions"], "samples": [{"x": point[0], "u": point[0] + 1}
                                                                  for point in args["points"]],
    })
    _enable_synthetic_parameter_isolation(monkeypatch)
    # This fixture worker is synthetic and is not the Worker used by the
    # shared helper's model-inspection tests; supply the runtime pid required
    # by the real G2 owned-server isolation preflight.
    monkeypatch.setattr(daemon.backend.worker, "runtime_metadata", lambda: {"pid": 10101}, raising=False)
    try:
        step_path = {"segments": [{"collection": "study", "tag": "target"},
                                   {"collection": "feature", "tag": "step1"}]}
        initial = _parameter_action(
            daemon, "study.initial_solution_set", model_execution,
            {"step": step_path, "source": {"dataset": "dset1", "time": [{"value": 1, "unit": "s"}]},
             "mapping": {}},
            key="w26-initial-set", request_id="w26-initial-set-request", entrypoint="direct",
        )
        assert initial["success"] is True, initial
        assert initial["data"]["status"] == "APPLIED"
        assert initial["data"]["source"]["inner"] == 2
        assert initial["data"]["source"]["solnum"] == 2
        assert initial["data"]["solve_dispatched"] is False
        model_execution = initial["execution"]

        resource = _parameter_action(
            daemon, "solver.resource_configure", model_execution,
            {"scope": "direct_solver", "resources": {
                "path": {"segments": [{"collection": "sol", "tag": "solsrc"},
                                       {"collection": "feature", "tag": "d1"}]},
                "settings": {"ooc": "on", "oocmemory": 1024, "mumpsalloc": 1.3},
            }},
            key="w26-resource-configure", request_id="w26-resource-configure-request", entrypoint="registry_call",
        )
        assert resource["success"] is True, resource
        assert resource["data"]["status"] == "APPLIED"
        assert len(resource["data"]["applied"]) == 3
        assert resource["data"]["process_resources"] == "NOT_CHANGED"
        model_execution = resource["execution"]

        sampled = _parameter_action(
            daemon, "result.sample_grid", model_execution,
            {"spec": {"expressions": ["u"], "solution": {"dataset": "dset1"}},
             "grid": {"axes": [[0.0, 1.0]], "coordinate_unit": "m"}},
            key="w26-sample-grid", request_id="w26-sample-grid-request", entrypoint="operation_call",
        )
        assert sampled["success"] is True, sampled
        assert sampled["data"]["status"] == "SAMPLED"
        assert sampled["data"]["grid"]["point_count"] == 2
        assert sampled["data"]["scientific_acceptance"] == "NOT_EVALUATED"
        model_execution = sampled["execution"]

        cleared = _parameter_action(
            daemon, "solver.solution_clear", model_execution,
            {"path": {"segments": [{"collection": "sol", "tag": "solsrc"}]},
             "scope": "solution_data"},
            key="w26-solution-clear", request_id="w26-solution-clear-request", entrypoint="direct",
        )
        assert cleared["success"] is True, cleared
        assert cleared["data"]["status"] == "CLEARED"
        assert source_solver.clear_calls == 1
        assert direct.values["mumpsalloc"] == 1.3
    finally:
        daemon.close()


@pytest.mark.parametrize("failure_mode", ["incomplete_readback", "exception_after_dispatch"])
def test_solution_clear_keeps_unknown_through_managed_dispatch_on_missing_proof(
    tmp_path, monkeypatch, failure_mode,
):
    from tests.test_artifact_registration_import import (
        _enable_synthetic_parameter_isolation, _make_daemon, _parameter_action,
    )
    from tests.test_g3_solver_ops import _state_model
    from comsol_mcp import _g3_solver_ops as solver_ops

    daemon, _project_root, model_execution = _make_daemon(tmp_path, with_model=True)
    engine_model, source_solver = _state_model()
    if failure_mode == "incomplete_readback":
        source_solver.clearSolutionData = lambda: None
    else:
        def _raise_after_dispatch():
            raise RuntimeError("synthetic lost clear reply")
        source_solver.clearSolutionData = _raise_after_dispatch
    monkeypatch.setattr(solver_ops, "bound_model", lambda *_: engine_model)
    _enable_synthetic_parameter_isolation(monkeypatch)
    monkeypatch.setattr(daemon.backend.worker, "runtime_metadata", lambda: {"pid": 10101}, raising=False)
    try:
        response = _parameter_action(
            daemon, "solver.solution_clear", model_execution,
            {"path": {"segments": [{"collection": "sol", "tag": "solsrc"}]},
             "scope": "solution_data"},
            key=f"w26-clear-unknown-{failure_mode}",
            request_id=f"w26-clear-unknown-{failure_mode}-request", entrypoint="direct",
        )
        assert response["success"] is False, response
        assert response["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert response["data"]["status"] == "EXECUTION_STATE_UNKNOWN"
        assert response["data"]["execution_state_unknown"] is True
        assert response["data"]["domain_outcome"]["state"] == "unknown"
        assert response["execution"]["dirty"] is True
    finally:
        daemon.close()


def test_worker_clear_solution_data_is_narrowly_allowlisted_for_the_sequence_route():
    from pathlib import Path
    import re

    source = (Path(__file__).parents[1] / "comsol_mcp/worker_java/PersistentComsolWorker.java").read_text()
    methods = re.search(r"^  private static final Set<String> METHODS = new HashSet<>\(Arrays\.asList\((.*?)\)\);", source, re.S | re.M)
    model_util = re.search(r"^  private static final Set<String> MODEL_UTIL = new HashSet<>\(Arrays\.asList\((.*?)\)\);", source, re.S | re.M)
    assert methods and model_util
    assert '"clearSolutionData"' in methods.group(1)
    assert '"clearSolutionData"' not in model_util.group(1)
    assert 'segments[0].collection != "sol"' in (
        Path(__file__).parents[1] / "comsol_mcp/_g3_solver_ops.py"
    ).read_text()
