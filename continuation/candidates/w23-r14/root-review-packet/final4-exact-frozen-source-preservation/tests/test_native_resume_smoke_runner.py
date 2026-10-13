from __future__ import annotations

from tools.run_native_resume_smoke import (
    _frozen_point_records,
    _is_native_study_run_submission,
    _remaining_native_solve_budget,
    _study_node_path,
)
from tools.verify_native_resume_saved_artifact import ZeroSolverCallGuard


def test_saved_readback_requires_exact_restoration_provenance(tmp_path) -> None:
    import hashlib
    import json
    import pytest
    from tools.verify_native_resume_saved_artifact import _verify_restored_input

    model = tmp_path / "restored.mph"
    model.write_bytes(b"saved fixture")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    receipt = {"run_id": "original", "saved_path": str(tmp_path / "former.mph"),
               "saved_sha256": digest, "saved_size_bytes": model.stat().st_size}
    original = tmp_path / "original.json"
    original.write_text(json.dumps(receipt))
    manifest = {"source_receipt_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                "source_run_id": "original", "source_saved_path": receipt["saved_path"],
                "restored_path": str(model), "sha256": digest, "size_bytes": model.stat().st_size}
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps(manifest))
    assert _verify_restored_input(model, original, receipt, digest, mapping) == manifest
    with pytest.raises(RuntimeError, match="explicit restoration manifest"):
        _verify_restored_input(model, original, receipt, digest, None)
    for field in manifest:
        changed = {**manifest, field: "wrong"}
        mapping.write_text(json.dumps(changed))
        with pytest.raises(RuntimeError, match="does not bind"):
            _verify_restored_input(model, original, receipt, digest, mapping)


def test_saved_readback_explicit_cli_does_not_open_missing_legacy_receipt(tmp_path) -> None:
    from tools.verify_native_resume_saved_artifact import parse_args

    args = parse_args(["--source-receipt", str(tmp_path / "missing.json"),
                       "--saved-model", str(tmp_path / "restored.mph"),
                       "--work", str(tmp_path / "work"), "--evidence", str(tmp_path / "evidence")])
    assert args.saved_model == str(tmp_path / "restored.mph")


def test_saved_readback_stages_exact_input_inside_registered_project(tmp_path) -> None:
    import hashlib
    from types import SimpleNamespace
    from tools.verify_native_resume_saved_artifact import _stage_project_readback_input

    project_root = tmp_path / "private-project-root"
    workspace = project_root / "saved-readback-project"
    workspace.mkdir(parents=True)
    source = tmp_path / "preserved.mph"
    source.write_bytes(b"unchanged saved model bytes")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    staged, receipt = _stage_project_readback_input(
        SimpleNamespace(project=project_root),
        {"project_id": "project-1", "workspace": str(workspace)},
        source,
    )

    assert staged.read_bytes() == source.read_bytes()
    assert staged.is_relative_to(workspace)
    assert receipt["source_sha256_before"] == source_hash
    assert receipt["source_sha256_after"] == source_hash
    assert receipt["staged_sha256"] == source_hash
    assert receipt["staging_is_input_preparation_not_engine_write"] is True


def test_saved_readback_uses_managed_project_bound_model_load() -> None:
    from pathlib import Path
    from tools.verify_native_resume_saved_artifact import _load_project_bound_model

    expected_ref = {"session_id": "session-1", "model_tag": "loaded_1", "model_id": "model-1"}

    class Backend:
        def model_project_binding(self, model_ref):
            assert dict(model_ref) == expected_ref
            return {"attribution": "PROJECT_BOUND", "project_id": "project-1"}

        def bind_model(self, *args, **kwargs):
            raise AssertionError("saved readback must not manually bind an unattributed model")

    class Daemon:
        backend = Backend()

        def __init__(self):
            self.requests = []

        def dispatch(self, request):
            self.requests.append(request)
            return {"success": True,
                    "data": {"model_tag": "loaded_1"},
                    "execution": {"model_ref": expected_ref, "revision": 4}}

    daemon = Daemon()
    result = _load_project_bound_model(daemon, Path("/project/input.mph"), "project-1")

    assert result["status"] == "PASS_MANAGED_MODEL_LOAD_PROJECT_BOUND"
    assert result["route"] == "ControlDaemon.dispatch:model_load"
    assert result["model_ref"] == expected_ref
    assert result["revision"] == 4
    assert result["binding"]["project_id"] == "project-1"
    assert len(daemon.requests) == 1
    request = daemon.requests[0]
    assert request["operation"] == "model_load"
    assert request["arguments"] == {"path": "/project/input.mph"}
    assert request["execution"]["project_id"] == "project-1"
    assert request["execution"]["idempotency_key"].startswith("native-readback-model-load-")
    assert request["execution"]["request_id"].startswith("native-readback-model-load-")


def test_saved_readback_project_create_uses_default_host_grants(tmp_path, monkeypatch) -> None:
    from pathlib import Path
    from comsol_mcp._control_daemon import ControlDaemon
    from tools.verify_native_resume_saved_artifact import _connect_daemon

    monkeypatch.delenv("COMSOL_MCP_HOST_CONTROL", raising=False)
    monkeypatch.delenv("COMSOL_MCP_TRUSTED_CODE", raising=False)
    project_root = tmp_path / "private-project-root"
    project_root.mkdir()

    class Server:
        project = project_root
        port = 51234
        worker = object()

    original_dispatch = ControlDaemon.dispatch
    observed = {"project_create": [], "server_connect": []}

    def dispatch_with_connected_server(daemon, request):
        operation = request["operation"]
        if operation == "server_connect":
            observed["server_connect"].append(request)
            return {
                "success": True,
                "data": {"endpoint": f"127.0.0.1:{Server.port}"},
                "execution": {"project_id": request["execution"]["project_id"]},
            }
        response = original_dispatch(daemon, request)
        if operation == "project.create":
            observed["project_create"].append((request, response))
        return response

    monkeypatch.setattr(ControlDaemon, "dispatch", dispatch_with_connected_server)
    phase_state = {}
    daemon, connection, project_id = _connect_daemon(
        Server(), tmp_path / "control", phase_state=phase_state,
    )
    try:
        assert daemon.backend.host_permission_ceiling == {"inspect", "project_write", "compute"}
        assert len(observed["project_create"]) == 1
        request, response = observed["project_create"][0]
        assert request["arguments"]["policy"]["permissions"] == ["inspect", "project_write"]
        assert response["success"] is True
        project = response["data"]["project"]
        assert project["project_id"] == project_id
        assert project["policy"]["permissions"] == ["inspect", "project_write"]
        assert Path(project["workspace"]).is_relative_to(project_root)
        job = daemon.store.job(response["execution"]["job_id"])
        assert job["status"] == "SUCCEEDED"
        assert job["metadata"]["engine_dispatched"] is False
        assert len(observed["server_connect"]) == 1
        assert observed["server_connect"][0]["execution"]["project_id"] == project_id
        assert connection["data"]["endpoint"] == f"127.0.0.1:{Server.port}"
        assert phase_state["registered_project"] == project
    finally:
        daemon.close()


def test_saved_readback_project_create_rejects_ungranted_host_control(tmp_path, monkeypatch) -> None:
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.delenv("COMSOL_MCP_HOST_CONTROL", raising=False)
    monkeypatch.delenv("COMSOL_MCP_TRUSTED_CODE", raising=False)
    project_root = tmp_path / "private-project-root"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    try:
        assert "host_control" not in daemon.backend.host_permission_ceiling
        response = daemon.dispatch({
            "operation": "project.create",
            "arguments": {
                "label": "must refuse host control",
                "workspace": "host-control-project",
                "policy": {"permissions": ["inspect", "project_write", "host_control"]},
            },
            "execution": {"request_id": "host-control-request", "idempotency_key": "host-control-key"},
        })
        assert response["success"] is False
        assert response["error"]["code"] == "POLICY_ESCALATION_REFUSED"
        job = daemon.store.job(response["execution"]["job_id"])
        assert job["status"] == "FAILED"
        assert job["metadata"]["engine_dispatched"] is False
        assert daemon.store.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert not (project_root / "host-control-project").exists()
    finally:
        daemon.close()


def test_saved_readback_progress_updates_only_selected_overlay(tmp_path) -> None:
    import json
    from tools.verify_native_resume_saved_artifact import write_resume_progress

    historical = tmp_path / "original.json"
    historical.write_text('{"status":"UNKNOWN","safe_retry":false}')
    before = historical.read_bytes()
    overlay = tmp_path / "overlay.json"
    overlay.write_text('{"status":"RESUMED","historical_unknown_policy":"DO_NOT_REPLAY"}')
    write_resume_progress({"supplement_id": "new-readback", "status": "PREPARED"}, True, path=overlay)
    assert historical.read_bytes() == before
    result = json.loads(overlay.read_text())
    assert result["historical_unknown_policy"] == "DO_NOT_REPLAY"
    assert result["active_jobs"][0]["solve_calls_allowed"] == 0


def _event(*, phase: str = "submitted", kind: str = "call", operation: str = "study.run",
           worker_type: str = "call", method: str = "run") -> dict:
    return {
        "phase": phase,
        "kind": kind,
        "operation_id": operation,
        "metadata": {"type": worker_type, "method": method},
    }


def test_counts_only_persisted_study_run_worker_submission() -> None:
    # Worker operation_id is a generated UUID, distinct from the domain action.
    assert _is_native_study_run_submission(_event(operation="48c17d30-d746-47d2-a730-9f429727a7b5"))


def test_fixture_study_reference_uses_nodepath_wire_shape() -> None:
    assert _study_node_path("std1") == {
        "segments": [{"collection": "study", "tag": "std1"}],
    }


def test_frozen_two_dimensional_points_use_catalog_object_coordinates() -> None:
    assert _frozen_point_records([[0.25, 0.5], [0.5, 0.75]]) == [
        {"x": 0.25, "y": 0.5},
        {"x": 0.5, "y": 0.75},
    ]


def test_zero_solver_guard_blocks_protected_study_run_before_submit(tmp_path) -> None:
    class FakeWorker:
        def __init__(self):
            self.submitted = []

        def submit(self, kind, payload, **kwargs):
            self.submitted.append((kind, payload, kwargs))
            return {"status": "SUCCEEDED"}

    worker = FakeWorker()
    guard = ZeroSolverCallGuard(worker, event_path=tmp_path / "worker-events.jsonl")
    guard.protect("study-handle", kind="study", tag="std1", java_type="Study")
    try:
        try:
            worker.submit("call", {"handle": "study-handle", "method": "run", "args": []})
        except RuntimeError as exc:
            assert "zero-solve guard blocked" in str(exc)
        else:
            raise AssertionError("guard must refuse protected study execution")
        assert worker.submitted == []
        assert guard.safe_to_stop()
        assert guard.blocked_attempts[0]["phase"] == "blocked_before_worker_submission"
    finally:
        guard.remove()


def test_zero_solver_guard_allows_numerical_feature_sampling_run(tmp_path) -> None:
    class FakeWorker:
        def __init__(self):
            self.submitted = []

        def submit(self, kind, payload, **kwargs):
            self.submitted.append((kind, payload, kwargs))
            return {"status": "SUCCEEDED"}

    worker = FakeWorker()
    guard = ZeroSolverCallGuard(worker, event_path=tmp_path / "worker-events.jsonl")
    guard.protect("study-handle", kind="study", tag="std1", java_type="Study")
    try:
        response = worker.submit("call", {"handle": "interp-handle", "method": "run", "args": []})
        assert response["status"] == "SUCCEEDED"
        assert guard.safe_to_stop()
        assert guard.method_run_events[0]["protected_node"] is None
    finally:
        guard.remove()


def test_remaining_solve_budget_accounts_for_prior_native_call() -> None:
    assert _remaining_native_solve_budget(0) == 2
    assert _remaining_native_solve_budget(1) == 1
    assert _remaining_native_solve_budget(2) == 0


def test_does_not_count_adapter_validation_failure_without_worker_event() -> None:
    assert not _is_native_study_run_submission({"adapter_entered": True})


def test_does_not_count_observation_as_a_second_solver_call() -> None:
    assert not _is_native_study_run_submission(_event(phase="observed"))


def test_does_not_count_other_engine_methods_or_operations() -> None:
    assert not _is_native_study_run_submission(_event(method="getTag"))
    assert not _is_native_study_run_submission(_event(kind="modelutil"))
    assert not _is_native_study_run_submission(_event(worker_type="modelutil"))
