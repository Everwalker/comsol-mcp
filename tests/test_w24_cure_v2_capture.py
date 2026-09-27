import copy
import gzip
import hashlib
import io
import json
import struct
from types import SimpleNamespace
from pathlib import Path

import pytest
from contextlib import nullcontext

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import canonical_request_hash
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._operation_store import OperationStore
from tools.w24_cure_law_v2 import AcceptanceError, maxwell_control_reference
from tools.w24_cure_v2_capture import (
    CONTROL_COORDINATE_M,
    GEL_EXPRESSIONS,
    GEL_TIMES_S,
    GEL_UNITS,
    HISTORY_EXPRESSIONS,
    HISTORY_UNITS,
    MAXWELL_EXPRESSIONS,
    MAXWELL_TIMES_S,
    MAXWELL_UNITS,
    CaptureError,
    dispatch_capture,
    validate_capture_artifact,
    _verify_public_capture_record,
    verify_public_capture,
)


def _capture_artifact(action):
    if action == "capture_maxwell_control":
        times = list(MAXWELL_TIMES_S)
        reference = maxwell_control_reference(times)
        expressions, units = list(MAXWELL_EXPRESSIONS), list(MAXWELL_UNITS)
        data = [
            [[value] for value in reference["sigma_xx_pa"]],
            [[value] for value in reference["sigma_yy_pa"]],
        ]
        coords = [list(CONTROL_COORDINATE_M[0])]
        case_id = "maxwell_ramp_hold_control"
        tlist = "range(0[s],1[s],901[s])"
        study, solver = "stdMaxwell", "sol1"
        schema = "W24_CURE_V2_NATIVE_CONTROL_CAPTURE_V1"
        status = "NATIVE_CONTROL_CAPTURED_NO_SOLVE_SUBMITTED"
        branch_key = "maxwell_branch_reference_state"
    elif action == "capture_gel_control":
        times = list(GEL_TIMES_S)
        expressions, units = list(GEL_EXPRESSIONS), list(GEL_UNITS)
        stress = [[0.0] for _ in times]
        isactive = [[1.0 if time >= 2.0 else 0.0] for time in times]
        wasactive = [[1.0 if time >= 2.0 else 0.0] for time in times]
        data = [stress[:] for _ in range(6)] + [isactive, wasactive]
        coords = [list(CONTROL_COORDINATE_M[0])]
        case_id = "gel_stress_free_control"
        tlist = "range(0[s],0.5[s],3[s])"
        study, solver = "stdGel", "sol1"
        schema = "W24_CURE_V2_NATIVE_CONTROL_CAPTURE_V1"
        status = "NATIVE_CONTROL_CAPTURED_NO_SOLVE_SUBMITTED"
        branch_key = "maxwell_branch_reference_state"
    elif action == "history_capture_v2":
        times = [0.0, 10.0]
        expressions, units = list(HISTORY_EXPRESSIONS), list(HISTORY_UNITS)
        coords = [[25e-6, 520e-6], [50e-6, 530e-6], [75e-6, 540e-6]]
        data = [[[300.0] * 3, [300.0] * 3], [[0.2] * 3, [0.3] * 3],
                [[0.0] * 3, [10.0] * 3], [[0.0] * 3, [0.1] * 3],
                [[0.0] * 3, [1e-6] * 3], [[0.0] * 3, [2e-6] * 3],
                [[0.0] * 3, [1.0] * 3], [[0.0] * 3, [1.0] * 3]]
        case_id = None
        tlist = "range(0[s],10[s],10[s])"
        study, solver = "stdCont", "sol2"
        schema = "W24_CURE_LAW_V2_HISTORY_CAPTURE_V1"
        status = "NATIVE_HISTORY_CAPTURED_NO_SOLVE_SUBMITTED"
        branch_key = "maxwell_branch_state"
    else:
        raise AssertionError(action)
    dataset = "dsetV2"
    shape = [len(expressions), len(times), len(coords)]
    feature = {
        "type": "Interp", "dataset": dataset, "expressions": expressions,
        "units": units, "solnum": "all", "coorderr": "on", "matherr": "on",
        "coordinates_m": coords,
        "coordinate_source": "fixed Java coordinates passed to setInterpolationCoordinates",
        "shape": shape,
    }
    row = {
        "schema": schema, "status": status, "case_id": case_id,
        "study_tag": study, "solver_tag": solver, "dataset_tag": dataset,
        "dataset_type_requested": "Solution", "dataset_solution_readback": solver,
        "stored_times_s": times, "time_source": "SolverSequence.getPVals",
        "study_tlist_readback": tlist, "quasistatic_readback": "Quasistatic",
        "expressions": expressions, "units": units, "coordinates_m": coords,
        "shape": shape, "data": data, "feature_readback": feature,
        "native_study_run_calls": 0,
        branch_key: "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
    }
    return row


def _write_java_utf(stream, value):
    encoded = value.encode("utf-8")
    stream.write(struct.pack(">H", len(encoded)))
    stream.write(encoded)


def _solution_snapshot_v2_bytes():
    frame = io.BytesIO()
    names = ["comp1.T", "comp1.u", "comp1.v"]
    rows = [
        (1, 1, 0, 0, (0.0, 0.0, 0.0)),
        (1, 2, 1, 1, (1e-6, 0.0, 0.0)),
        (1, 3, 2, 2, (0.0, 1e-6, 0.0)),
    ]
    _write_java_utf(frame, "W24-DOF-SNAPSHOT-2")
    frame.write(struct.pack(">ii", 3, len(names)))
    for name in names:
        _write_java_utf(frame, name)
    frame.write(struct.pack(">i", len(rows)))
    for geom, node, name_index, vector_index, coordinates in rows:
        frame.write(struct.pack(">iiii", geom, node, name_index, vector_index))
        frame.write(struct.pack(">ddd", *coordinates))
    frame.write(struct.pack(">i", 2))
    for time_s, values in ((0.0, (300.0, 0.0, 0.0)), (1.0, (301.0, 1e-6, 0.0))):
        frame.write(struct.pack(">di", time_s, len(values)))
        frame.write(struct.pack(">ddd", *values))
    return gzip.compress(frame.getvalue())


def _route_fixture(tmp_path, action="capture_maxwell_control", *, revision_before=7):
    project = tmp_path / "project"
    fixture_name = ("W24CureScienceFixture.java" if action == "history_capture_v2"
                    else "W24CureLawV2ControlFixture.java")
    source_path = project / f"tools/java/{fixture_name}"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("public final class W24CureLawV2ControlFixture {}\n", encoding="utf-8")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    artifact = None if action == "solution_snapshot_v2" else _capture_artifact(action)
    output_path = project / ("native/fields-v2.bin.gz" if action == "solution_snapshot_v2" else "native/capture.json")
    output_path.parent.mkdir(parents=True)
    if action == "solution_snapshot_v2":
        raw_artifact = _solution_snapshot_v2_bytes()
        output_path.write_bytes(raw_artifact)
        receipt = {
            "status": "SOLUTION_SNAPSHOT_V2_WRITTEN", "schema": "W24-DOF-SNAPSHOT-2",
            "solver_tag": "sol1", "path": str(output_path),
            "size_bytes": len(raw_artifact), "sha256": hashlib.sha256(raw_artifact).hexdigest(),
            "stored_time_count": 2, "dof_count": 3,
            "dof_names": ["comp1.T", "comp1.u", "comp1.v"],
            "max_solution_vector_index": 2,
            "coordinate_axes": 3, "real_solution": True, "complete_xmesh_dofs": True,
            "study_tag": "stdCont", "study_tlist_readback": "range(0[s],1[s],1[s])",
            "quasistatic_readback": "Quasistatic",
        }
    else:
        raw_artifact = (json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n").encode()
        output_path.write_bytes(raw_artifact)
        receipt = {
            "status": "NATIVE_HISTORY_CAPTURE_WRITTEN" if action == "history_capture_v2" else "NATIVE_CONTROL_CAPTURE_WRITTEN",
            "schema": artifact["schema"], "case_id": artifact.get("case_id"),
            "study_tag": artifact["study_tag"], "solver_tag": artifact["solver_tag"],
            "dataset_tag": artifact["dataset_tag"], "path": str(output_path),
            "size_bytes": len(raw_artifact), "sha256": hashlib.sha256(raw_artifact).hexdigest(),
        }
    model_ref = {"session_id": "session-a", "server_instance_id": "server-a",
                 "model_tag": "model-a", "generation": 3, "schema_version": 1}
    project_id, session_id = "project-a", "session-a"
    entrypoint = "W24CureScienceFixture#run" if action == "history_capture_v2" else "W24CureLawV2ControlFixture#run"
    request_arguments = {"action": action, "solver_tag": artifact["solver_tag"] if artifact else "sol1",
                         "path": str(output_path)}
    if action == "history_capture_v2":
        request_arguments["study_tag"] = artifact["study_tag"]
    elif action == "solution_snapshot_v2":
        request_arguments["study_tag"] = "stdCont"
    body = {"source_artifact": f"tools/java/{fixture_name}",
            "entrypoint": entrypoint, "arguments": request_arguments, "mode": "trusted"}
    operation_metadata = {
        "arguments": {"operation_id": "code.execute_java", "arguments": body},
        "execution": {"project_id": project_id, "session_id": session_id,
                      "model_ref": model_ref, "expected_revision": revision_before},
    }
    timeouts = {"rpc_timeout_s": 120.0, "queue_timeout_s": 60.0,
                "execution_timeout_s": None, "no_progress_warning_s": None}
    request_id, idempotency_key = "request-a", "key-a"
    request_hash = canonical_request_hash(
        "code.execute_java", body, model_ref, revision_before,
        project_id=project_id, session_id=session_id, queue_timeout_s=60.0,
        execution_timeout_s=None, no_progress_warning_s=None)
    store = OperationStore(tmp_path / "operations.sqlite")
    record, reused = store.begin(request_id=request_id, idempotency_key=idempotency_key,
                                 request_hash=request_hash, operation="operation_call",
                                 metadata=operation_metadata, timeouts=timeouts)
    assert not reused
    java = {"executed": True, "model_tag": model_ref["model_tag"],
            "source_sha256": source_sha, "entrypoint": entrypoint, "readback": receipt}
    response = {
        "success": True,
        "data": {"source_sha256": source_sha, "entrypoint": entrypoint,
                 "worker": {"ok": True, "status": "SUCCEEDED", "result": {"readback": java}},
                 "readback": {"readback": java}},
        "execution": {"project_id": project_id, "session_id": session_id,
                      "model_ref": model_ref, "revision": revision_before + 1,
                      "request_id": request_id, "idempotency_key": idempotency_key,
                      "request_hash": request_hash, "operation_id": record["operation_id"],
                      "job_id": record["job_id"]},
    }
    # Worker result and public operation readback both carry the exact Java envelope.
    store.finish(record["operation_id"], status="SUCCEEDED", result=response)
    stored = store.get_operation(record["operation_id"])
    job = store.operation_job(record["operation_id"])
    stored["job_id"] = job["job_id"]
    stored["job_status"] = job["status"]
    stored["job_result"] = job["result"]
    return project, source_path, source_sha, response, stored, model_ref, revision_before, store


@pytest.mark.parametrize("action", ["capture_maxwell_control", "capture_gel_control", "history_capture_v2"])
def test_capture_schemas_require_complete_dataset_solver_times_units_coordinates_and_quasistatic(action):
    artifact = _capture_artifact(action)
    report = validate_capture_artifact(artifact, expected_action=action)
    assert report["status"] == "CAPTURE_SCHEMA_VALIDATED_NATIVE_NOT_RUN"
    assert report["quasistatic_readback"] == "Quasistatic"
    assert report["maxwell_branch_reference_state"] == "UNVERIFIED"


@pytest.mark.parametrize("field", ["dataset_solution_readback", "stored_times_s", "units", "shape", "coordinates_m", "quasistatic_readback"])
def test_capture_schema_rejects_dataset_time_unit_shape_coordinate_and_quasistatic_mutations(field):
    artifact = _capture_artifact("capture_maxwell_control")
    if field == "dataset_solution_readback":
        artifact[field] = "foreign-solver"
    elif field == "stored_times_s":
        artifact[field] = artifact[field][:-1]
    elif field == "units":
        artifact[field] = ["MPa", "MPa"]
        artifact["feature_readback"]["units"] = list(artifact[field])
    elif field == "shape":
        artifact[field] = [2, len(MAXWELL_TIMES_S) - 1, 1]
        artifact["feature_readback"]["shape"] = list(artifact[field])
    elif field == "coordinates_m":
        artifact[field] = [[0.0, 0.0, 0.0]]
        artifact["feature_readback"]["coordinates_m"] = artifact[field]
    else:
        artifact[field] = "Dynamic"
    with pytest.raises(CaptureError):
        validate_capture_artifact(artifact, expected_action="capture_maxwell_control")


def test_public_capture_verifier_binds_source_operation_hash_job_model_revision_and_raw_artifact(tmp_path):
    project, source, digest, response, record, ref, revision, store = _route_fixture(tmp_path)
    result = _verify_public_capture_record(
        response, record, project_root=project, source_artifact_path=source,
        expected_source_sha256=digest, expected_action="capture_maxwell_control",
        expected_project_id="project-a", expected_session_id="session-a",
        expected_model_ref=ref, expected_revision=revision)
    assert result["status"] == "PUBLIC_CAPTURE_ENVELOPE_AND_ARTIFACT_MATCHED_NATIVE_REVIEW_REQUIRED"
    assert result["native_acceptance"] == "NOT_RUN"
    assert result["capture_validation"]["maxwell_branch_reference_state"] == "UNVERIFIED"
    assert result["artifact"]["sha256"] == response["data"]["readback"]["readback"]["readback"]["sha256"]
    store.close()


@pytest.mark.parametrize("mutation", ["route", "project", "session", "model_ref", "revision", "source_hash", "request_hash", "job_id"])
def test_public_capture_verifier_rejects_foreign_or_unbound_operation_envelopes(tmp_path, mutation):
    project, source, digest, response, record, ref, revision, store = _route_fixture(tmp_path)
    response = copy.deepcopy(response)
    record = copy.deepcopy(record)
    if mutation == "route":
        record["metadata"]["arguments"]["operation_id"] = "model.inspect"
    elif mutation == "project":
        response["execution"]["project_id"] = "project-b"
    elif mutation == "session":
        response["execution"]["session_id"] = "session-b"
    elif mutation == "model_ref":
        response["execution"]["model_ref"]["generation"] += 1
    elif mutation == "revision":
        response["execution"]["revision"] = revision + 2
    elif mutation == "source_hash":
        digest = "0" * 64
    elif mutation == "request_hash":
        record["request_hash"] = "0" * 64
    else:
        response["execution"]["job_id"] = "foreign-job"
    with pytest.raises(CaptureError):
        _verify_public_capture_record(
            response, record, project_root=project, source_artifact_path=source,
            expected_source_sha256=digest, expected_action="capture_maxwell_control",
            expected_project_id="project-a", expected_session_id="session-a",
            expected_model_ref=ref, expected_revision=revision)
    store.close()


def test_public_capture_verifier_rejects_changed_source_or_artifact_bytes(tmp_path):
    project, source, digest, response, record, ref, revision, store = _route_fixture(tmp_path)
    source.write_text(source.read_text() + "// tampered\n", encoding="utf-8")
    with pytest.raises(CaptureError, match="source SHA-256"):
        _verify_public_capture_record(
            response, record, project_root=project, source_artifact_path=source,
            expected_source_sha256=digest, expected_action="capture_maxwell_control",
            expected_project_id="project-a", expected_session_id="session-a",
            expected_model_ref=ref, expected_revision=revision)
    store.close()


def test_public_capture_verifier_rejects_response_not_equal_to_operationstore_result(tmp_path):
    project, source, digest, response, record, ref, revision, store = _route_fixture(tmp_path)
    response["data"]["worker"]["status"] = "UNKNOWN"
    with pytest.raises(CaptureError, match="terminal successful"):
        _verify_public_capture_record(
            response, record, project_root=project, source_artifact_path=source,
            expected_source_sha256=digest, expected_action="capture_maxwell_control",
            expected_project_id="project-a", expected_session_id="session-a",
            expected_model_ref=ref, expected_revision=revision)
    store.close()


def test_public_capture_verifier_accepts_hashed_complete_3d_xmesh_snapshot_v2(tmp_path):
    project, source, digest, response, record, ref, revision, store = _route_fixture(
        tmp_path, action="solution_snapshot_v2")
    result = _verify_public_capture_record(
        response, record, project_root=project, source_artifact_path=source,
        expected_source_sha256=digest, expected_action="solution_snapshot_v2",
        expected_project_id="project-a", expected_session_id="session-a",
        expected_model_ref=ref, expected_revision=revision)
    assert result["capture_validation"]["snapshot_schema"] == "W24-DOF-SNAPSHOT-2"
    assert result["capture_validation"]["coordinate_axes"] == 3
    assert result["capture_validation"]["complete_dof_count"] == 3
    assert result["capture_validation"]["stored_times_s"] == [0.0, 1.0]
    assert result["native_acceptance"] == "NOT_RUN"
    store.close()


@pytest.mark.parametrize("mutation", ["wrong_receipt_hash", "incomplete_dof_receipt", "wrong_dof_names",
                                      "wrong_max_vector", "complex_solution", "tampered_file_bytes"])
def test_public_capture_verifier_rejects_unhashed_or_incomplete_xmesh_snapshot_v2(tmp_path, mutation):
    project, source, digest, response, record, ref, revision, store = _route_fixture(
        tmp_path, action="solution_snapshot_v2")
    response = copy.deepcopy(response)
    record = copy.deepcopy(record)
    if mutation == "tampered_file_bytes":
        snapshot = Path(response["data"]["readback"]["readback"]["readback"]["path"])
        snapshot.write_bytes(snapshot.read_bytes() + b"tampered")
    else:
        java = response["data"]["readback"]["readback"]
        receipt = java["readback"]
        if mutation == "wrong_receipt_hash":
            receipt["sha256"] = "0" * 64
        elif mutation == "incomplete_dof_receipt":
            receipt["complete_xmesh_dofs"] = False
        elif mutation == "wrong_dof_names":
            receipt["dof_names"] = ["comp1.T", "comp1.u"]
        elif mutation == "wrong_max_vector":
            receipt["max_solution_vector_index"] = 1
        else:
            receipt["real_solution"] = False
        response["data"]["worker"]["result"]["readback"] = copy.deepcopy(java)
        # Keep both durable views equal to the response so this specifically
        # exercises the binary receipt contract after the OperationStore gate.
        record["result"] = copy.deepcopy(response)
        record["job_result"] = copy.deepcopy(response)
    with pytest.raises(CaptureError):
        _verify_public_capture_record(
            response, record, project_root=project, source_artifact_path=source,
            expected_source_sha256=digest, expected_action="solution_snapshot_v2",
            expected_project_id="project-a", expected_session_id="session-a",
            expected_model_ref=ref, expected_revision=revision)
    store.close()


def test_public_verifier_reads_original_private_store_rows_and_authorized_project(tmp_path):
    project, source, digest, response, record, ref, revision, store = _route_fixture(tmp_path)
    calls = []

    class ProjectAuthority:
        def authorize_operation(self, project_id, permission):
            calls.append((project_id, permission))
            assert project_id == "project-a"
            assert permission == "trusted_code"

    # A caller-owned forged copy is deliberately contradictory. The public
    # helper receives only the operation id and rereads original SQLite rows.
    forged_copy = copy.deepcopy(record)
    forged_copy["metadata"]["execution"]["project_id"] = "project-foreign"
    result = verify_public_capture(
        SimpleNamespace(store=store, project_authority=ProjectAuthority()), response,
        operation_id=record["operation_id"], project_root=project,
        source_artifact_path=source, expected_source_sha256=digest,
        expected_action="capture_maxwell_control", expected_project_id="project-a",
        expected_session_id="session-a", expected_model_ref=ref,
        expected_revision=revision)
    assert forged_copy["metadata"]["execution"]["project_id"] == "project-foreign"
    assert calls == [("project-a", "trusted_code")]
    assert result["project_id"] == "project-a"
    store.close()


def test_public_verifier_rejects_caller_supplied_metadata_store_copy():
    untrusted = SimpleNamespace(
        store={"operation_id": {"metadata": {"execution": {"project_id": "project-a"}}}},
        project_authority=SimpleNamespace(authorize_operation=lambda *_args: None))
    with pytest.raises(CaptureError, match="private OperationStore"):
        verify_public_capture(
            untrusted, {}, operation_id="operation-a", project_root=Path("."),
            source_artifact_path=Path("source.java"), expected_source_sha256="0" * 64,
            expected_action="capture_maxwell_control", expected_project_id="project-a",
            expected_session_id="session-a", expected_model_ref={}, expected_revision=0)


@pytest.mark.parametrize("mutation", ["foreign_project", "wrong_operation", "wrong_job"])
def test_public_verifier_rejects_corrupted_original_operation_or_job_rows(tmp_path, mutation):
    project, source, digest, response, record, ref, revision, store = _route_fixture(tmp_path)
    operation_id = record["operation_id"]
    with store.lock:
        if mutation == "wrong_job":
            store.db.execute("UPDATE jobs SET job_id=? WHERE operation_id=?",
                             ("foreign-job", operation_id))
        else:
            raw = store.db.execute("SELECT metadata FROM operations WHERE operation_id=?",
                                   (operation_id,)).fetchone()[0]
            metadata = json.loads(raw)
            if mutation == "foreign_project":
                metadata["execution"]["project_id"] = "project-foreign"
            else:
                metadata["arguments"]["operation_id"] = "model.inspect"
            store.db.execute("UPDATE operations SET metadata=? WHERE operation_id=?",
                             (json.dumps(metadata, sort_keys=True), operation_id))
        store.db.commit()

    class ProjectAuthority:
        def authorize_operation(self, _project_id, _permission):
            return None

    with pytest.raises(CaptureError):
        verify_public_capture(
            SimpleNamespace(store=store, project_authority=ProjectAuthority()), response,
            operation_id=operation_id, project_root=project,
            source_artifact_path=source, expected_source_sha256=digest,
            expected_action="capture_maxwell_control", expected_project_id="project-a",
            expected_session_id="session-a", expected_model_ref=ref,
            expected_revision=revision)
    store.close()


class _PublicRouteSnapshot:
    def model_snapshot(self, model_tag):
        return {"model_tag": model_tag, "server_instance_id": "server-a",
                "fingerprint": "stable-test-fingerprint", "external_event_counter": 0}


class _PublicRouteWorker:
    """Harmless Worker substitute; exercises public daemon persistence, not COMSOL."""

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def backend_snapshot(self, model_tag):
        return _PublicRouteSnapshot().model_snapshot(model_tag)

    def execute_java(self, model_tag, source_artifact, entrypoint, arguments, *, request_id=None):
        action = arguments["action"]
        path = Path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if action == "solution_snapshot_v2":
            artifact = None
            raw = _solution_snapshot_v2_bytes()
        else:
            artifact = _capture_artifact(action)
            raw = (json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n").encode()
        path.write_bytes(raw)
        if artifact is None:
            receipt = {
                "status": "SOLUTION_SNAPSHOT_V2_WRITTEN", "schema": "W24-DOF-SNAPSHOT-2",
                "solver_tag": arguments["solver_tag"], "path": str(path),
                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "stored_time_count": 2, "dof_count": 3,
                "dof_names": ["comp1.T", "comp1.u", "comp1.v"],
                "max_solution_vector_index": 2,
                "coordinate_axes": 3, "real_solution": True, "complete_xmesh_dofs": True,
                "study_tag": arguments["study_tag"],
                "study_tlist_readback": "range(0[s],1[s],1[s])",
                "quasistatic_readback": "Quasistatic",
            }
        else:
            receipt = {
                "status": "NATIVE_CONTROL_CAPTURE_WRITTEN" if action != "history_capture_v2" else "NATIVE_HISTORY_CAPTURE_WRITTEN",
                "schema": artifact["schema"], "case_id": artifact.get("case_id"),
                "study_tag": artifact["study_tag"], "solver_tag": artifact["solver_tag"],
                "dataset_tag": artifact["dataset_tag"], "path": str(path),
                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            }
        java_result = {
            "executed": True, "model_tag": model_tag,
            "source_sha256": hashlib.sha256(Path(source_artifact).read_bytes()).hexdigest(),
            "entrypoint": entrypoint, "readback": receipt,
        }
        return {"ok": True, "status": "SUCCEEDED", "result": {"readback": java_result}}


@pytest.mark.parametrize("action", ["capture_maxwell_control", "solution_snapshot_v2"])
def test_dispatch_capture_uses_real_control_daemon_and_operation_store_with_stub_worker(tmp_path, monkeypatch, action):
    monkeypatch.setenv("COMSOL_MCP_TRUSTED_CODE", "1")
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    service = ExecutionService(
        SessionLedger("session-a", "server-a", permissions={"inspect", "project_write", "compute", "trusted_code"}),
        _PublicRouteSnapshot(), project_root=projects_root)
    daemon = ControlDaemon(
        tmp_path / "control", service=service, registry={}, worker=_PublicRouteWorker(),
        project_root=projects_root)
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "v2-public-route", "workspace": "v2-public-route",
                          "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}},
            "execution": {"request_id": "create-v2-project", "idempotency_key": "create-v2-project"},
        })
        assert created["success"] is True, created
        project_id = created["data"]["project"]["project_id"]
        project_root = projects_root / "v2-public-route"
        source = project_root / "tools/java/W24CureLawV2ControlFixture.java"
        source.parent.mkdir(parents=True)
        source.write_text("public final class W24CureLawV2ControlFixture {}\n", encoding="utf-8")
        bound = service.bind_model("model-a")
        model_ref = bound["execution"]["model_ref"]
        daemon.backend._bind_model_project(model_ref, project_id)
        revision_key = daemon.backend._model_project_key(model_ref)
        daemon.store.put_metadata("revisions", revision_key,
                                  {"model_ref": model_ref, "project_id": project_id,
                                   "attribution": "PROJECT_BOUND", "revision": 0,
                                   "dirty": False, "fingerprint": "stable-test-fingerprint",
                                   "active_operation_id": None})
        # This test has no owned native Server, so bypass only the separate
        # isolation receipt gate while retaining ControlDaemon's real route,
        # canonical request hash, Job, and OperationStore behavior.
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
        result = dispatch_capture(
            daemon, project_root=project_root,
            source_artifact="tools/java/W24CureLawV2ControlFixture.java",
            expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            action=action,
            arguments={"solver_tag": "sol1", "path": str(project_root / "native/capture.json"),
                       **({"study_tag": "stdCont"} if action == "solution_snapshot_v2" else {})},
            project_id=project_id, session_id="session-a", model_ref=model_ref,
            revision=0,
        )
        assert result["status"] == "PUBLIC_CAPTURE_ENVELOPE_AND_ARTIFACT_MATCHED_NATIVE_REVIEW_REQUIRED"
        assert result["native_acceptance"] == "NOT_RUN"
        if action == "solution_snapshot_v2":
            assert result["capture_validation"]["snapshot_schema"] == "W24-DOF-SNAPSHOT-2"
            assert result["capture_validation"]["coordinate_axes"] == 3
        else:
            assert result["capture_validation"]["maxwell_branch_reference_state"] == "UNVERIFIED"
        operation = daemon.store.get_operation(result["operation_id"])
        job = daemon.store.operation_job(result["operation_id"])
        assert operation["operation"] == "operation_call"
        assert operation["status"] == job["status"] == "SUCCEEDED"
        assert operation["metadata"]["execution"]["project_id"] == project_id
    finally:
        daemon.close()
