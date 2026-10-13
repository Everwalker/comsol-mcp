from __future__ import annotations

from contextlib import contextmanager

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._java_worker import RemoteClient, _redact, _request_hash
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._stage_contract import sha256_json, validate_stage_plan_definition


class _Adapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "mesh-server",
                "external_event_counter": 0, "fingerprint": "stable-model"}


class _FakeWorker:
    """Java Worker protocol fake that emits real submit/observe event shapes."""

    generation = 7

    def __init__(self, *, fault=None):
        import threading

        self.calls = []
        self.fault = fault
        self._context = threading.local()

    @contextmanager
    def operation_context(self, operation_id, *, on_request_event=None):
        old = getattr(self._context, "value", None)
        self._context.value = (operation_id, on_request_event)
        try:
            yield
        finally:
            self._context.value = old

    def client(self):
        return RemoteClient(self)

    def submit(self, kind, payload, *, request_id=None, **_kwargs):
        body = {**payload, "type": kind, "request_id": request_id or f"worker-{len(self.calls) + 1}"}
        self.calls.append(body)
        context = getattr(self._context, "value", None)
        operation_id, callback = context if context else ("", None)

        def emit(phase, **extra):
            if callback is not None:
                callback({"phase": phase, "request_id": body["request_id"], "kind": kind,
                          "operation_id": operation_id, "request_hash": _request_hash(body),
                          "metadata": _redact(body), **extra})

        emit("submitted")
        if self.fault == "unknown_first" and len(self.calls) == 1:
            error = RuntimeError("injected unresolved first Worker request")
            error.code = "EXECUTION_STATE_UNKNOWN"
            error.execution_state_unknown = True
            emit("unknown", status="UNKNOWN", error=str(error))
            raise error

        if kind == "model":
            result = self._handle("model")
        elif kind == "call":
            result = self._call(payload["handle"], payload["method"], payload["args"])
        else:
            raise AssertionError(f"unexpected Worker command {kind}")
        emit("observed", status="SUCCEEDED", reply={"ok": True, "status": "SUCCEEDED", "result": result})
        return {"ok": True, "status": "SUCCEEDED", "result": result}

    @staticmethod
    def _handle(name):
        return {"$worker_handle": f"mesh-{name}", "generation": 7, "java_type": name}

    def _call(self, handle, method, args):
        if handle == "mesh-model" and method == "component":
            return self._handle("component-list" if not args else "component-comp1")
        if handle == "mesh-component-list" and method == "tags":
            return ["comp1"]
        if handle == "mesh-component-comp1" and method == "mesh":
            return self._handle("mesh-list" if not args else "sequence-mesh1")
        if handle == "mesh-mesh-list" and method == "tags":
            return ["mesh1"]
        if handle == "mesh-sequence-mesh1" and method == "feature":
            return self._handle("feature-list")
        if handle == "mesh-feature-list" and method == "tags":
            return []
        if handle == "mesh-sequence-mesh1":
            if method == "geom":
                return "geom-other" if self.fault == "wrong_geom" else "geom1"
            if method == "getSDim":
                return 2
            if method == "getNumVertex":
                return 3
            if method == "getTypes":
                if self.fault == "bad_metadata":
                    return ["tri", "tri"]
                return ["tri"]
            if method == "getNumElem" and args == ["tri"]:
                return 2
            if method == "getNumElem" and not args:
                return 2
            if method == "getVertex" and args == [0, 3]:
                if self.fault == "bad_snapshot":
                    return [[0.0], [0.0]]
                return [[0.0, 1.0, 1.0], [0.0, 0.0, 1.0]]
            if method == "getElem" and args == ["tri", 0, 2]:
                return [[0, 1], [1, 2], [2, 0]]
            if method == "getElemEntity" and args == ["tri", 0, 2]:
                return [1, 2]
            if method == "isAutomatic":
                return False
            if method == "current":
                return "mesh1"
            if method == "isComplete":
                return True
            if method == "isEmpty":
                return False
            if method == "lengthUnit":
                return "m"
            if method == "label":
                return "mesh one"
        raise AssertionError(f"unexpected Worker call: {handle}.{method}{args}")


_PLAN = {
    "version": 2,
    "plan_id": "mesh-readback-plan",
    "stages": [{
        "stage_id": "initial",
        "ordinal": 1,
        "depends_on": [],
        "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
        "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
        "target_selection": {"dataset": "dset1", "solution": "sol1"},
        "mapping_profile": "same_name_same_mesh_initialization",
        "variable_mappings": [{"source_variable": "T", "target_variable": "T",
                               "source_unit": "K", "target_unit": "K", "mapping_method": "identity"}],
        "reference_state": {"strategy": "initial_state"},
        "checks": [],
    }],
}


@pytest.fixture
def managed_mesh(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = OperationStore(tmp_path / "operations.sqlite3")
    service = ExecutionService(SessionLedger("mesh-session", "mesh-server"), _Adapter(),
                                project_root=project_root)
    bound = service.bind_model("model-main")
    ref = model_ref_from_mapping(bound["execution"]["model_ref"]).as_dict()
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    state.revision = 5
    worker = _FakeWorker()
    backend = ManagedBackend(tmp_path / "control", store, service=service, worker=worker,
                             registry={}, project_root=project_root)
    project_id = "mesh-project"
    backend._bind_model_project(ref, project_id)
    store.put_metadata("revisions", backend._model_project_key(ref), {
        "model_ref": ref, "revision": 5, "dirty": False,
        "attribution": "PROJECT_BOUND", "project_id": project_id,
    })

    definition = validate_stage_plan_definition(_PLAN)
    plan_record = {
        "schema_version": 1, "kind": "w21_stage_plan", "project_id": project_id,
        "model_ref": ref, "plan_id": definition["plan_id"], "declaration_revision": 0,
        "definition": definition, "definition_sha256": sha256_json(definition),
        "declaration_status": "DECLARED_UNVERIFIED",
        "declaration_evidence": {"schema_and_internal_plan_consistency": "VALIDATED",
                                 "study_solution_variable_existence": "NOT_CHECKED",
                                 "mapping_method_execution": "NOT_CHECKED",
                                 "worker_rpc_performed": False, "solve_started": False},
    }
    plan_record["sha256"] = sha256_json(plan_record)
    store.register_stage_plan(project_id=project_id, model_ref=ref, plan_record=plan_record)
    operation, reused = store.begin(
        request_id="stage-parent-request", idempotency_key="stage-parent-idempotency",
        request_hash="a" * 64, operation="experiment.stage_run", metadata={"project_id": project_id},
    )
    assert not reused
    attempt, reused = store.begin_stage_attempt(
        project_id=project_id, model_ref=ref, stage_id="initial", expected_revision=4,
        request_id="stage-attempt-request", operation_id=operation["operation_id"],
        idempotency_key="stage-attempt-idempotency", request_hash="b" * 64,
    )
    assert not reused
    attempt = store.update_stage_attempt(
        project_id, ref, attempt["attempt_id"], expected_version=1,
        status="RUNNING", engine_dispatched=True, execution_status="RUNNING",
        acceptance_status="NOT_EVALUATED", evidence=[{"dispatch": "synthetic test setup"}],
    )
    return backend, service, store, worker, project_id, ref, attempt


def test_managed_backend_captures_and_persists_post_stage_mesh_without_write_ticket(managed_mesh):
    backend, service, store, worker, project_id, ref, attempt = managed_mesh
    result = backend.stage_mesh_snapshot_readback(
        project_id=project_id, model_ref=ref, attempt_id=attempt["attempt_id"],
        phase="post-stage", model_revision=5, component="comp1", mesh="mesh1", geometry="geom1",
    )

    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.revision == 5 and state.dirty is False
    assert result["model_revision"] == 5
    assert result["attempt_binding"]["expected_revision"] == 4
    assert result["historical_mesh"] == "UNVERIFIED"
    assert result["source_target_mapping"] == "UNVERIFIED"
    assert result["frame_identity"] == "UNVERIFIED"
    assert result["dof_identity"] == "UNVERIFIED"
    assert result["mesh_snapshot"]["content"]["element_count"] == 2

    job = store.operation_job(attempt["operation_id"])
    assert job is not None
    artifact = store.get_metadata("artifacts", result["artifact_ref"]["artifact_id"])
    assert artifact is not None
    assert artifact["sha256"] == result["artifact_ref"]["sha256"]
    assert artifact["capture_status"] == "CAPTURED"
    assert artifact["claim_scope"] == "CURRENT_MESH_CAPTURE_ONLY"
    assert artifact["managed_execution"]["request_hash_scope"] == "NOT_APPLICABLE_READ_NO_WRITE_TICKET"
    assert "request_hash" not in artifact["managed_execution"]
    receiver = artifact["resolved_mesh_sequence"]["receiver_handle"]
    assert artifact["resolved_mesh_sequence"]["path"] == {
        "segments": [{"collection": "component", "tag": "comp1"},
                     {"collection": "mesh", "tag": "mesh1"}],
    }
    assert all(call["handle"] == receiver for call in worker.calls if call["type"] == "call"
               and call["method"] in {"getSDim", "getNumVertex", "getTypes", "getNumElem",
                                      "getVertex", "getElem", "getElemEntity"})
    events = store.events(job["job_id"], limit=1000)
    worker_events = [row["metadata"] for row in events if row["event"] == "worker_request"]
    assert worker_events
    assert all(event["operation_id"] == artifact["child_operation_id"] for event in worker_events)
    assert all(event["w21_stage_output_binding"]["worker_generation"] == worker.generation
               for event in worker_events)
    assert {event["phase"] for event in worker_events} == {"submitted", "observed"}

    request_rows = artifact["worker_requests"]
    assert len(request_rows) == 2 * len({row["request_id"] for row in request_rows})
    for request_id in {row["request_id"] for row in request_rows}:
        pair = [row for row in request_rows if row["request_id"] == request_id]
        assert {row["phase"] for row in pair} == {"submitted", "observed"}
        assert pair[0]["request_hash"] == pair[1]["request_hash"]


@pytest.mark.parametrize(("change", "code"), [
    ("wrong_project", "PROJECT_IDENTITY_MISMATCH"),
    ("wrong_model", "PROJECT_IDENTITY_MISMATCH"),
    ("wrong_revision", "REVISION_CONFLICT"),
    ("wrong_attempt", "STAGE_ATTEMPT_STATE_UNKNOWN"),
])
def test_wrong_project_model_revision_or_attempt_is_refused_before_worker(managed_mesh, change, code):
    backend, _service, _store, worker, project_id, ref, attempt = managed_mesh
    args = {"project_id": project_id, "model_ref": ref, "attempt_id": attempt["attempt_id"],
            "phase": "post-stage", "model_revision": 5,
            "component": "comp1", "mesh": "mesh1", "geometry": "geom1"}
    if change == "wrong_project":
        args["project_id"] = "another-project"
    elif change == "wrong_model":
        args["model_ref"] = {**ref, "model_tag": "another-model"}
    elif change == "wrong_revision":
        args["model_revision"] = 4
    else:
        args["attempt_id"] = "missing-attempt"
    with pytest.raises(ExecutionContractError) as exc:
        backend.stage_mesh_snapshot_readback(**args)
    assert getattr(exc.value, "code", None) == code
    assert worker.calls == []


def test_actual_mesh_geometry_mismatch_stops_before_mesh_content_reads(managed_mesh):
    backend, service, store, worker, project_id, ref, attempt = managed_mesh
    worker.fault = "wrong_geom"
    with pytest.raises(ExecutionContractError) as exc:
        backend.stage_mesh_snapshot_readback(
            project_id=project_id, model_ref=ref, attempt_id=attempt["attempt_id"],
            phase="post-stage", model_revision=5, component="comp1", mesh="mesh1", geometry="geom1",
        )
    assert getattr(exc.value, "code", None) in {"MODEL_IDENTITY_MISMATCH", "EXECUTION_STATE_UNKNOWN"}
    assert any(call.get("method") == "geom" for call in worker.calls)
    assert not any(call.get("method") in {"getSDim", "getVertex", "getElem", "getElemEntity"}
                   for call in worker.calls)
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.revision == 5 and state.dirty is False


def test_unknown_first_worker_request_is_retained_and_stops_all_followup_rpc(managed_mesh):
    backend, service, store, worker, project_id, ref, attempt = managed_mesh
    worker.fault = "unknown_first"
    with pytest.raises(ExecutionContractError) as exc:
        backend.stage_mesh_snapshot_readback(
            project_id=project_id, model_ref=ref, attempt_id=attempt["attempt_id"],
            phase="post-stage", model_revision=5, component="comp1", mesh="mesh1", geometry="geom1",
        )
    assert getattr(exc.value, "code", None) == "EXECUTION_STATE_UNKNOWN"
    assert len(worker.calls) == 1
    job = store.operation_job(attempt["operation_id"])
    rows = [row["metadata"] for row in store.events(job["job_id"], limit=100)
            if row["event"] == "worker_request"]
    assert [row["phase"] for row in rows] == ["submitted", "unknown"]
    assert rows[0]["request_id"] == rows[1]["request_id"]
    assert rows[0]["operation_id"] == rows[1]["operation_id"]
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.revision == 5 and state.dirty is True and state.fingerprint is None


def test_non_mapping_managed_reply_is_persisted_as_unknown_and_freezes_model(managed_mesh):
    backend, service, store, worker, project_id, ref, attempt = managed_mesh
    original = backend.invoke
    calls_at_return = []

    def malformed_reply(*args, **kwargs):
        original(*args, **kwargs)
        calls_at_return.append(len(worker.calls))
        return None

    backend.invoke = malformed_reply
    with pytest.raises(ExecutionContractError) as exc:
        backend.stage_mesh_snapshot_readback(
            project_id=project_id, model_ref=ref, attempt_id=attempt["attempt_id"],
            phase="post-stage", model_revision=5, component="comp1", mesh="mesh1", geometry="geom1",
        )
    assert getattr(exc.value, "code", None) == "EXECUTION_STATE_UNKNOWN"
    assert len(calls_at_return) == 1 and len(worker.calls) == calls_at_return[0]
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.revision == 5 and state.dirty is True and state.fingerprint is None
    artifacts = [row for row in store.list_metadata("artifacts")
                 if row.get("schema") == "w21-stage-current-mesh-readback/v1"]
    assert len(artifacts) == 1
    assert artifacts[0]["capture_status"] == "UNKNOWN"
    assert artifacts[0]["error"]["type"] == "non_mapping_managed_reply"
    assert artifacts[0]["claim_scope"] == "CURRENT_MESH_CAPTURE_ONLY"


@pytest.mark.parametrize(("path", "bound_component", "bound_mesh", "error_code"), [
    ({"segments": [{"collection": "mesh", "tag": "mesh1"}]}, "comp1", "mesh1", "INVALID_NODE_PATH"),
    ({"segments": [{"collection": "component", "tag": "comp1"},
                   {"collection": "mesh", "tag": "mesh1"},
                   {"collection": "feature", "tag": "size1"}]},
     "comp1", "mesh1", "INVALID_NODE_PATH"),
    ({"segments": [{"collection": "component", "tag": "comp2"},
                   {"collection": "mesh", "tag": "mesh1"}]},
     "comp1", "mesh1", "MODEL_IDENTITY_MISMATCH"),
    ({"segments": [{"collection": "component", "tag": "comp1"},
                   {"collection": "mesh", "tag": "mesh2"}]},
     "comp1", "mesh1", "MODEL_IDENTITY_MISMATCH"),
])
def test_private_strict_nodepath_shape_and_binding_refuse_before_domain_worker_rpc(
        managed_mesh, path, bound_component, bound_mesh, error_code):
    backend, _service, _store, worker, project_id, ref, _attempt = managed_mesh
    binding = {
        "project_id": project_id, "model_ref": ref, "revision": 5,
        "attempt_id": "strict-path-attempt", "phase": "post-stage",
        "component": bound_component, "mesh": bound_mesh, "geometry": "geom1",
    }
    execution = {"project_id": project_id, "session_id": ref["session_id"], "model_ref": ref,
                 "expected_revision": 5, "request_id": "strict-path-request",
                 "idempotency_key": "strict-path-idempotency"}
    mode_token = backend._stage_mesh_snapshot_context.set({"binding": binding, "resolved": None})
    try:
        with backend.context("strict-path-operation", lambda _event: None):
            result = backend.invoke("mesh.inspect", {"path": path}, execution,
                                    "strict-path-operation", lambda _event: None)
    finally:
        backend._stage_mesh_snapshot_context.reset(mode_token)
    assert result["success"] is False
    assert result["error"]["code"] == error_code
    # ExecutionService may have performed its adapter pre-snapshot; this
    # assertion is specifically about domain/Worker dispatch from mesh.inspect.
    assert worker.calls == []


def test_admitted_undispatched_attempt_can_capture_a_pre_stage_mesh(managed_mesh, tmp_path):
    _backend, service, _store, worker, project_id, ref, _attempt = managed_mesh
    pre_store = OperationStore(tmp_path / "pre-stage-operations.sqlite3")
    pre_backend = ManagedBackend(tmp_path / "pre-stage-control", pre_store, service=service,
                                 worker=worker, registry={}, project_root=tmp_path / "project")
    pre_backend._bind_model_project(ref, project_id)
    pre_store.put_metadata("revisions", pre_backend._model_project_key(ref), {
        "model_ref": ref, "revision": 5, "dirty": False,
        "attribution": "PROJECT_BOUND", "project_id": project_id,
    })
    definition = validate_stage_plan_definition(_PLAN)
    plan_record = {
        "schema_version": 1, "kind": "w21_stage_plan", "project_id": project_id,
        "model_ref": ref, "plan_id": definition["plan_id"], "declaration_revision": 0,
        "definition": definition, "definition_sha256": sha256_json(definition),
        "declaration_status": "DECLARED_UNVERIFIED",
        "declaration_evidence": {"schema_and_internal_plan_consistency": "VALIDATED",
                                 "study_solution_variable_existence": "NOT_CHECKED",
                                 "mapping_method_execution": "NOT_CHECKED",
                                 "worker_rpc_performed": False, "solve_started": False},
    }
    plan_record["sha256"] = sha256_json(plan_record)
    pre_store.register_stage_plan(project_id=project_id, model_ref=ref, plan_record=plan_record)
    operation, reused = pre_store.begin(
        request_id="pre-stage-parent-request", idempotency_key="pre-stage-parent-idempotency",
        request_hash="c" * 64, operation="experiment.stage_run", metadata={"project_id": project_id},
    )
    assert not reused
    attempt, reused = pre_store.begin_stage_attempt(
        project_id=project_id, model_ref=ref, stage_id="initial", expected_revision=5,
        request_id="pre-stage-attempt-request", operation_id=operation["operation_id"],
        idempotency_key="pre-stage-attempt-idempotency", request_hash="d" * 64,
    )
    assert not reused
    assert attempt["status"] == "ADMITTED" and attempt["engine_dispatched"] is False

    result = pre_backend.stage_mesh_snapshot_readback(
        project_id=project_id, model_ref=ref, attempt_id=attempt["attempt_id"],
        phase="pre-stage", model_revision=5, component="comp1", mesh="mesh1", geometry="geom1",
    )
    assert result["status"] == "CURRENT_MESH_CAPTURE_ONLY"
    assert result["attempt_binding"]["expected_revision"] == 5
    artifact = pre_store.get_metadata("artifacts", result["artifact_ref"]["artifact_id"])
    assert artifact["capture_status"] == "CAPTURED" and artifact["phase"] == "pre-stage"
    assert artifact["attempt_status"] == "ADMITTED"
    state = service.ledger._state_for(model_ref_from_mapping(ref))
    assert state.revision == 5 and state.dirty is False


def test_bad_worker_mesh_metadata_is_rejected_and_never_captured(managed_mesh):
    backend, service, store, worker, project_id, ref, attempt = managed_mesh
    worker.fault = "bad_metadata"
    with pytest.raises(ExecutionContractError) as exc:
        backend.stage_mesh_snapshot_readback(
            project_id=project_id, model_ref=ref, attempt_id=attempt["attempt_id"],
            phase="post-stage", model_revision=5, component="comp1", mesh="mesh1", geometry="geom1",
        )
    assert getattr(exc.value, "code", None) == "MESH_TYPES_INVALID"
    assert service.ledger._state_for(model_ref_from_mapping(ref)).dirty is False
    persisted = store.list_metadata("artifacts")
    assert not any(row.get("schema") == "w21-stage-current-mesh-readback/v1"
                   and row.get("capture_status") == "CAPTURED" for row in persisted)


def test_corrupted_snapshot_digest_is_rejected_after_managed_read(managed_mesh):
    backend, _service, store, worker, project_id, ref, attempt = managed_mesh
    original = backend.invoke

    def corrupt(*args, **kwargs):
        reply = original(*args, **kwargs)
        detail = reply.get("data") if isinstance(reply, dict) else None
        if isinstance(detail, dict) and isinstance(detail.get("mesh_snapshot"), dict):
            detail = dict(detail)
            snapshot = dict(detail["mesh_snapshot"])
            content = dict(snapshot["content"])
            content["element_count"] += 1
            snapshot["content"] = content
            detail["mesh_snapshot"] = snapshot
            return {**reply, "data": detail}
        return reply

    backend.invoke = corrupt
    with pytest.raises(ExecutionContractError) as exc:
        backend.stage_mesh_snapshot_readback(
            project_id=project_id, model_ref=ref, attempt_id=attempt["attempt_id"],
            phase="post-stage", model_revision=5, component="comp1", mesh="mesh1", geometry="geom1",
        )
    assert getattr(exc.value, "code", None) == "MESH_SNAPSHOT_INVALID"
    assert worker.calls
    assert not any(row.get("schema") == "w21-stage-current-mesh-readback/v1"
                   and row.get("capture_status") == "CAPTURED" for row in store.list_metadata("artifacts"))


def test_public_mesh_inspect_keeps_default_shape_and_rejects_private_flag_spoof(managed_mesh):
    backend, _service, _store, worker, project_id, ref, _attempt = managed_mesh
    execution = {"project_id": project_id, "session_id": ref["session_id"], "model_ref": ref,
                 "expected_revision": 5, "request_id": "public-mesh-inspect",
                 "idempotency_key": "public-mesh-inspect"}
    path = {"segments": [{"collection": "component", "tag": "comp1"},
                         {"collection": "mesh", "tag": "mesh1"}]}
    with backend.context("public-mesh-inspect", lambda _event: None):
        public = backend.invoke("mesh.inspect", {"path": path}, execution,
                                "public-mesh-inspect", lambda _event: None)
    assert public["success"] is True
    assert public["data"]["kind"] == "mesh_sequence"
    assert "mesh_snapshot" not in public["data"]

    before = len(worker.calls)
    spoofed_execution = {**execution, "_stage_mesh_snapshot_context": {
        "binding": {"project_id": project_id, "model_ref": ref, "revision": 5,
                    "attempt_id": "caller", "phase": "post-stage", "component": "comp1",
                    "mesh": "mesh1", "geometry": "geom1"}}}
    with backend.context("public-mesh-inspect-spoof", lambda _event: None):
        spoofed = backend.invoke(
            "mesh.inspect", {"path": path, "_strict_snapshot_mode": spoofed_execution["_stage_mesh_snapshot_context"]},
            spoofed_execution, "public-mesh-inspect-spoof", lambda _event: None,
        )
    assert spoofed["success"] is False
    assert spoofed.get("data", {}).get("kind") != "mesh_sequence_current_snapshot"
    assert len(worker.calls) == before
