from __future__ import annotations

import asyncio
import hashlib
import json
import math
from uuid import uuid4

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._g2_registry import operation_describe, validate_call
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._observation_store import observation_context
from comsol_mcp._tools_w21 import register as register_w21
from comsol_mcp._g2_tools import register as register_g2
from comsol_mcp import _w21_execution as w21_execution


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def add_tool(self, function, **options):
        self.tools[options.get("name", function.__name__)] = function


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def _create_project(daemon, label, permissions=None):
    result = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": label,
            "workspace": label,
            "policy": {"permissions": permissions or ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": f"create-{label}", "idempotency_key": f"create-{label}"},
    })
    assert result["success"] is True
    return result["data"]["project"]["project_id"]


def _seed_operation(daemon, project_id, operation, arguments, *, status="SUCCEEDED", fallback=False,
                    include_project=True):
    row_operation = "operation_call" if fallback else operation
    persisted_arguments = (
        {"operation_id": operation, "arguments": dict(arguments)} if fallback else dict(arguments)
    )
    metadata = {
        "operation": row_operation,
        "arguments": persisted_arguments,
        "execution": ({"project_id": project_id} if include_project else {}),
    }
    record, reused = daemon.store.begin(
        request_id=f"request-{uuid4().hex}",
        idempotency_key=f"key-{uuid4().hex}",
        request_hash=_digest([row_operation, operation, arguments, project_id, uuid4().hex]),
        operation=row_operation,
        metadata=metadata,
    )
    assert reused is False
    if status != "QUEUED":
        result = {"success": True, "data": {}} if status in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "LOST"} else None
        daemon.store.update_job(record["job_id"], status, result=result)
    return record["operation_id"]


def _seed_design(daemon, project_id, experiment_id="exp_test", *, include_project=True,
                 producer_project=True, case_ids=("case-0001",), fallback=False):
    definition = {
        "study": "std1",
        "sampling": {"kind": "cartesian_grid"},
        "parameters": {"p": [float(index) for index in range(1, len(case_ids) + 1)]},
    }
    producer = _seed_operation(
        daemon, project_id if producer_project else "", "experiment.design",
        {"definition": definition}, include_project=producer_project, fallback=fallback,
    )
    record = {
        "schema_version": 1,
        "kind": "w21experiment",
        "experiment_id": experiment_id,
        "model_ref": {
            "schema_version": 1, "session_id": "session-historical",
            "server_instance_id": "worker-historical", "model_tag": "model-main", "generation": 1,
        },
        "model_revision": 4,
        "producer": producer,
        "study": "std1",
        "sampling_profile": "cartesian_grid",
        "definition": definition,
        "definition_sha256": _digest(definition),
        "cases": [
            {"case_id": case_id, "parameters": {"p": float(index)}}
            for index, case_id in enumerate(case_ids, 1)
        ],
        "budget": {"max_cases": len(case_ids), "max_wall_time_s": 60.0},
    }
    if include_project:
        record["project_id"] = project_id
    record["sha256"] = _digest(record)
    daemon.store.persist_artifact("w21experiment:" + experiment_id, record)
    return record


def _seed_run(daemon, project_id, design, *, status="COMPLETE", producer_status="SUCCEEDED",
              include_project=True, producer_project=True, case_results=(), fallback=False):
    experiment_id = design["experiment_id"]
    producer = _seed_operation(
        daemon, project_id if producer_project else "", "experiment.run",
        {"experiment_id": experiment_id}, status=producer_status, fallback=fallback,
        include_project=producer_project,
    )
    run_id = "run_" + experiment_id
    cases = [dict(row) for row in case_results]
    run = {
        "schema_version": 1,
        "kind": "w21experiment_run",
        "run_id": run_id,
        "experiment_id": experiment_id,
        "model_ref": design["model_ref"],
        "design_sha256": design["sha256"],
        "producer": producer,
        "status": status,
        "effective_budget": {"max_cases": len(design["cases"]), "max_wall_time_s": 60.0},
        "cases": cases,
    }
    if include_project:
        run["project_id"] = project_id
    if status in {"COMPLETE", "PARTIAL", "FAILED", "EXECUTION_STATE_UNKNOWN"}:
        run["completion_status"] = {
            "COMPLETE": "ALL_CASES_COMPLETED",
            "PARTIAL": "BUDGET_EXHAUSTED",
            "FAILED": "CASE_FAILED",
            "EXECUTION_STATE_UNKNOWN": "EXECUTION_STATE_UNKNOWN",
        }[status]
        run["budget"] = {"cases_evaluated": len(cases), "cases_failed": 0, "total_failures": 0}
    run["sha256"] = _digest(run)
    daemon.store.persist_artifact("w21experimentrun:" + experiment_id, run)
    return run, producer


def _seed_case(daemon, project_id, design, run, run_producer, case_id, *, include_project=True, result=None):
    planned = next(row for row in design["cases"] if row["case_id"] == case_id)
    result = result or {
        "case_id": case_id,
        "case_ordinal": int(case_id.split("-")[-1]),
        "parameters": planned["parameters"],
        "status": "COMPLETED",
        "raw_result": {"metric": 3.5},
    }
    record = {
        "kind": "w21experiment_case",
        "model_ref": design["model_ref"],
        "producer": run_producer,
        "experiment_id": design["experiment_id"],
        "run_id": run["run_id"],
        "case": result,
    }
    if include_project:
        record["project_id"] = project_id
    record["sha256"] = _digest(record)
    daemon.store.persist_artifact(
        f"w21experimentcase:{design['experiment_id']}:{case_id}", record,
    )
    return result


def _rewrite_artifact(daemon, key, mutate):
    record = daemon.store.get_metadata("artifacts", key)
    assert isinstance(record, dict)
    mutate(record)
    record.pop("sha256", None)
    record["sha256"] = _digest(record)
    daemon.store.persist_artifact(key, record)
    return record


def _direct(daemon, operation, arguments, execution=None):
    return daemon.dispatch({
        "operation": operation,
        "arguments": arguments,
        "execution": execution or {},
    })


def _error_code(result):
    return result["error"]["code"]


def _read_data(daemon, project_id, experiment_id):
    return _direct(daemon, "experiment.inspect", {
        "project_id": project_id,
        "experiment_id": experiment_id,
    })


def _public_host(daemon):
    host = _FakeMcp()
    gateway = GatewayRegistry(
        host,
        dispatcher=lambda operation, arguments, execution: daemon.dispatch({
            "operation": operation, "arguments": arguments, "execution": execution,
        }),
    )
    register_w21(gateway)
    register_g2(gateway)
    return host


def _call_public(host, name, **arguments):
    response = asyncio.run(host.tools[name](**arguments))
    return response.structuredContent


def test_public_direct_and_fallback_routes_read_one_real_sqlite_snapshot_without_queue(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "public-read")
    design = _seed_design(daemon, project_id)
    case_result = {
        "case_id": "case-0001", "case_ordinal": 1, "parameters": {"p": 1.0},
        "status": "COMPLETED", "raw_result": {"metric": 3.5},
    }
    run, run_producer = _seed_run(daemon, project_id, design, case_results=[case_result])
    _seed_case(daemon, project_id, design, run, run_producer, "case-0001")
    host = _public_host(daemon)
    calls = []
    monkeypatch.setattr(daemon.session_scheduler, "submit", lambda *_a, **_k: calls.append("submit"))
    operations_before = daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0]
    trace = []
    daemon.store.db.set_trace_callback(trace.append)
    try:
        direct = _call_public(
            host, "experiment_inspect", project_id=project_id, experiment_id=design["experiment_id"],
        )
        assert direct["success"] is True
        assert direct["data"]["status"] == "COMPLETE"
        assert direct["data"]["cases"][0]["case_id"] == "case-0001"
        assert direct["data"]["cases"][0]["case_ordinal"] == 1

        exact_case = _call_public(
            host, "experiment_case_result", project_id=project_id,
            experiment_id=design["experiment_id"], case_id="case-0001",
        )
        assert exact_case["success"] is True
        assert exact_case["data"]["result"]["raw_result"] == {"metric": 3.5}
        assert exact_case["data"]["record_source"] == "case_artifact"

        fallback = _call_public(
            host, "operation_call", operation_id="experiment.inspect",
            arguments={"project_id": project_id, "experiment_id": design["experiment_id"]},
        )
        assert fallback["success"] is True
        registry_fallback = _call_public(
            host, "registry_call", operation_id="experiment.case_result",
            arguments={"project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "case-0001"},
        )
        assert registry_fallback["success"] is True
        assert registry_fallback["data"]["case_id"] == "case-0001"
        operations_after = daemon.store.db.execute("SELECT count(*) FROM operations").fetchone()[0]
    finally:
        daemon.store.db.set_trace_callback(None)
        daemon.close()
    assert calls == []
    assert daemon.backend.worker is None
    assert operations_after == operations_before
    normalized = [statement.strip().upper() for statement in trace]
    assert normalized.count("BEGIN") == 4
    assert normalized.count("COMMIT") == 4


def test_queued_running_and_unknown_experiment_progress_are_cached_and_truthful(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "progress-read")
    design = _seed_design(daemon, project_id, case_ids=("case-0001", "case-0002"))
    run_producer = _seed_operation(
        daemon, project_id, "experiment.run", {"experiment_id": design["experiment_id"]},
        status="QUEUED", fallback=True,
    )
    monkeypatch.setattr(
        daemon.session_scheduler, "submit",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("durable read entered engine queue")),
    )
    try:
        queued = _read_data(daemon, project_id, design["experiment_id"])
        assert queued["success"] is True
        assert queued["data"]["status"] == "QUEUED"
        assert queued["data"]["run"]["status"] == "QUEUED"

        job = daemon.store.operation_job(run_producer)
        daemon.store.update_job(job["job_id"], "RUNNING")
        running = _read_data(daemon, project_id, design["experiment_id"])
        assert running["success"] is True
        assert running["data"]["status"] == "RUNNING"
        assert running["data"]["run"]["status"] == "RUNNING"
        assert [row["status"] for row in running["data"]["cases"]] == ["NOT_RECORDED", "NOT_RECORDED"]

        daemon.store.update_job(job["job_id"], "UNKNOWN")
        unknown = _read_data(daemon, project_id, design["experiment_id"])
        assert unknown["success"] is True
        assert unknown["data"]["status"] == "UNKNOWN"
        assert unknown["data"]["run"]["status"] == "UNKNOWN"
        assert [row["status"] for row in unknown["data"]["cases"]] == ["UNKNOWN", "UNKNOWN"]
    finally:
        daemon.close()


def test_partial_case_results_and_case_id_compatibility_do_not_accept_numeric_alias(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "partial-read")
    design = _seed_design(daemon, project_id, case_ids=("case-0001", "case-0002"))
    case = {
        "case_id": "case-0001", "case_ordinal": 1, "parameters": {"p": 1.0},
        "status": "COMPLETED", "raw_result": {"metric": 3.5},
    }
    run, run_producer = _seed_run(
        daemon, project_id, design, status="PARTIAL", case_results=[case],
    )
    try:
        inspected = _read_data(daemon, project_id, design["experiment_id"])
        assert inspected["data"]["status"] == "PARTIAL"
        assert [row["status"] for row in inspected["data"]["cases"]] == ["COMPLETED", "NOT_RECORDED"]

        recorded = _direct(daemon, "experiment.case_result", {
            "project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "case-0001",
        })
        assert recorded["data"]["case_id"] == "case-0001"
        assert recorded["data"]["case_ordinal"] == 1
        assert recorded["data"]["status"] == "COMPLETED"
        assert recorded["data"]["record_source"] == "run_artifact"

        absent = _direct(daemon, "experiment.case_result", {
            "project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "case-0002",
        })
        assert absent["success"] is True
        assert absent["data"]["status"] == "NOT_RECORDED"
        assert absent["data"]["result"] is None

        index_alias = _direct(daemon, "experiment.case_result", {
            "project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "1",
        })
        assert _error_code(index_alias) == "CASE_NOT_FOUND"
        empty_experiment = _direct(daemon, "experiment.inspect", {
            "project_id": project_id, "experiment_id": "",
        })
        assert _error_code(empty_experiment) == "INVALID_REQUEST"
        empty_case = _direct(daemon, "experiment.case_result", {
            "project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "",
        })
        assert _error_code(empty_case) == "INVALID_REQUEST"
    finally:
        daemon.close()


def test_project_authorization_and_foreign_experiment_ids_are_indistinguishable_from_missing(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_a = _create_project(daemon, "reader-a")
    project_b = _create_project(daemon, "reader-b")
    restricted = _create_project(daemon, "reader-no-inspect", permissions=["project_write", "compute"])
    foreign = _seed_design(daemon, project_b, experiment_id="exp-foreign")
    try:
        cross_project = _read_data(daemon, project_a, foreign["experiment_id"])
        nonexistent = _read_data(daemon, project_a, "exp-does-not-exist")
        assert _error_code(cross_project) == "EXPERIMENT_NOT_FOUND"
        assert _error_code(nonexistent) == "EXPERIMENT_NOT_FOUND"
        assert cross_project["error"]["message"] == nonexistent["error"]["message"]
        assert "reader-b" not in json.dumps(cross_project)

        denied = _read_data(daemon, restricted, "exp-does-not-exist")
        assert _error_code(denied) == "PERMISSION_DENIED"

        mismatch = _direct(daemon, "experiment.inspect", {
            "project_id": project_a, "experiment_id": "exp-does-not-exist",
        }, execution={"project_id": project_b})
        assert _error_code(mismatch) == "PROJECT_IDENTITY_MISMATCH"
    finally:
        daemon.close()


def test_legacy_missing_project_fields_require_authoritative_producer_metadata(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "legacy-read")
    attributable = _seed_design(
        daemon, project_id, experiment_id="exp-legacy-attributable", include_project=False,
    )
    unowned = _seed_design(
        daemon, project_id, experiment_id="exp-legacy-unowned", include_project=False,
        producer_project=False,
    )
    try:
        readable = _read_data(daemon, project_id, attributable["experiment_id"])
        assert readable["success"] is True
        assert readable["data"]["status"] == "DESIGNED"

        refused = _read_data(daemon, project_id, unowned["experiment_id"])
        assert _error_code(refused) == "EXPERIMENT_NOT_FOUND"
    finally:
        daemon.close()


def test_legacy_project_attribution_and_results_survive_registry_fallback_records(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "legacy-fallback-read")
    design = _seed_design(
        daemon, project_id, experiment_id="exp-fallback-legacy", include_project=False,
        case_ids=("case-0001",), fallback=True,
    )
    case = {
        "case_id": "case-0001", "case_ordinal": 1, "parameters": {"p": 1.0},
        "status": "COMPLETED", "raw_result": {"metric": 3.5},
    }
    run, run_producer = _seed_run(
        daemon, project_id, design, status="COMPLETE", include_project=False,
        fallback=True, case_results=[case],
    )
    _seed_case(daemon, project_id, design, run, run_producer, "case-0001", include_project=False)
    try:
        inspected = _read_data(daemon, project_id, design["experiment_id"])
        assert inspected["success"] is True
        assert inspected["data"]["status"] == "COMPLETE"
        case_read = _direct(daemon, "experiment.case_result", {
            "project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "case-0001",
        })
        assert case_read["success"] is True
        assert case_read["data"]["record_source"] == "case_artifact"
        assert case_read["data"]["result"] == case
    finally:
        daemon.close()


def test_tampered_design_or_case_lineage_fails_closed(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "integrity-read")
    design = _seed_design(daemon, project_id)
    case = {
        "case_id": "case-0001", "case_ordinal": 1, "parameters": {"p": 1.0},
        "status": "COMPLETED", "raw_result": {"metric": 3.5},
    }
    run, run_producer = _seed_run(daemon, project_id, design, case_results=[case])
    valid_case = _seed_case(daemon, project_id, design, run, run_producer, "case-0001")
    case_key = f"w21experimentcase:{design['experiment_id']}:case-0001"
    try:
        mismatched_lineage = {
            "kind": "w21experiment_case",
            "model_ref": design["model_ref"],
            "producer": run_producer,
            "experiment_id": design["experiment_id"],
            "run_id": "run-foreign",
            "case": valid_case,
        }
        mismatched_lineage["project_id"] = project_id
        mismatched_lineage["sha256"] = _digest(mismatched_lineage)
        daemon.store.persist_artifact(case_key, mismatched_lineage)
        bad_case = _direct(daemon, "experiment.case_result", {
            "project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "case-0001",
        })
        assert _error_code(bad_case) == "EXPERIMENT_STATE_UNKNOWN"

        daemon.store.persist_artifact(case_key, {
            "kind": "w21experiment_case",
            "model_ref": design["model_ref"],
            "producer": run_producer,
            "experiment_id": design["experiment_id"],
            "run_id": run["run_id"],
            "case": valid_case,
            "project_id": project_id,
            "sha256": _digest({
                "kind": "w21experiment_case",
                "model_ref": design["model_ref"],
                "producer": run_producer,
                "experiment_id": design["experiment_id"],
                "run_id": run["run_id"],
                "case": valid_case,
                "project_id": project_id,
            }),
        })
        tampered_design = dict(design)
        tampered_design["study"] = "std-tampered"
        daemon.store.persist_artifact("w21experiment:" + design["experiment_id"], tampered_design)
        bad_design = _read_data(daemon, project_id, design["experiment_id"])
        assert _error_code(bad_design) == "EXPERIMENT_STATE_UNKNOWN"
    finally:
        daemon.close()


@pytest.mark.parametrize("record_path", ["aggregate", "case_artifact"])
@pytest.mark.parametrize("bad_identity", [
    {"case_ordinal": 2},
    {"parameters": {"p": 999.0}},
    {"parameters": {"p": True}},
    {"parameters": {"p": 1.0, "extra": 2.0}},
    {"parameters": {}},
])
def test_case_read_rejects_wrong_planned_ordinal_or_parameters_on_each_durable_path(
    tmp_path, record_path, bad_identity,
):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, f"case-binding-{record_path}-{len(bad_identity)}")
    design = _seed_design(daemon, project_id)
    valid = {
        "case_id": "case-0001", "case_ordinal": 1, "parameters": {"p": 1.0},
        "status": "COMPLETED", "raw_result": {"metric": 4.0},
    }
    run, producer = _seed_run(daemon, project_id, design, case_results=[valid])
    _seed_case(daemon, project_id, design, run, producer, "case-0001", result=dict(valid))
    key = ("w21experimentrun:" + design["experiment_id"] if record_path == "aggregate"
           else f"w21experimentcase:{design['experiment_id']}:case-0001")

    def corrupt(record):
        if record_path == "aggregate":
            row = record["cases"][0]
        else:
            row = record["case"]
        row.update(bad_identity)

    _rewrite_artifact(daemon, key, corrupt)
    try:
        response = _direct(daemon, "experiment.case_result", {
            "project_id": project_id, "experiment_id": design["experiment_id"], "case_id": "case-0001",
        })
        assert _error_code(response) == "EXPERIMENT_STATE_UNKNOWN"
    finally:
        daemon.close()


def test_nonfinite_and_non_grid_parameter_values_never_match_planned_grid():
    planned = {"p": 1.0}
    for actual in (
        {"p": math.nan}, {"p": math.inf}, {"p": -math.inf},
        {"p": True}, {"p": "1"}, {"p": 1.0, "extra": 2.0}, {},
    ):
        assert ControlDaemon._experiment_parameters_match(actual, planned) is False
    assert ControlDaemon._experiment_parameters_match({"p": 1}, planned) is True


def test_duplicate_case_ordinal_in_aggregate_fails_closed(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "duplicate-ordinal")
    design = _seed_design(daemon, project_id, case_ids=("case-0001", "case-0002"))
    rows = [
        {"case_id": "case-0001", "case_ordinal": 1, "parameters": {"p": 1.0},
         "status": "COMPLETED", "raw_result": {"metric": 1}},
        {"case_id": "case-0002", "case_ordinal": 2, "parameters": {"p": 2.0},
         "status": "COMPLETED", "raw_result": {"metric": 2}},
    ]
    run, _producer = _seed_run(daemon, project_id, design, case_results=rows)

    def duplicate(record):
        record["cases"][1]["case_ordinal"] = 1

    _rewrite_artifact(daemon, "w21experimentrun:" + design["experiment_id"], duplicate)
    try:
        response = _read_data(daemon, project_id, design["experiment_id"])
        assert _error_code(response) == "EXPERIMENT_STATE_UNKNOWN"
    finally:
        daemon.close()


def test_missing_historical_ordinal_is_inferred_from_plan_order_not_case_id_suffix(tmp_path):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "historical-ordinal")
    design = _seed_design(daemon, project_id, case_ids=("case-0002", "case-0001"))
    historical = {
        "case_id": "case-0002", "parameters": {"p": 1.0},
        "status": "COMPLETED", "raw_result": {"metric": 7.0},
    }
    run, producer = _seed_run(daemon, project_id, design, case_results=[dict(historical)])
    _seed_case(daemon, project_id, design, run, producer, "case-0002", result=dict(historical))
    try:
        inspected = _read_data(daemon, project_id, design["experiment_id"])
        assert inspected["success"] is True
        assert inspected["data"]["cases"][0]["case_id"] == "case-0002"
        assert inspected["data"]["cases"][0]["case_ordinal"] == 1
        result = _direct(daemon, "experiment.case_result", {
            "project_id": project_id,
            "experiment_id": design["experiment_id"],
            "case_id": "case-0002",
        })
        assert result["success"] is True
        assert result["data"]["case_ordinal"] == 1
        assert result["data"]["result"] == historical
    finally:
        daemon.close()


def test_actual_w21_design_and_run_callbacks_read_back_through_public_routes(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "callback-readback")
    model_ref = {
        "schema_version": 1, "session_id": "session-synthetic",
        "server_instance_id": "worker-synthetic", "model_tag": "model-main", "generation": 1,
    }
    definition = {
        "study": "std1",
        "sample": {"spec": {"solution": {"dataset": "dset1"}, "expressions": ["T"]},
                   "points": [[0.0, 0.0]], "coordinate_unit": "m"},
        "metrics": {"peak": {"expression": "T", "unit": "K", "indices": [0]}},
        "times": [0.0],
        "validation": {"range": [250.0, 400.0]},
        # Durable JSON sorts mapping keys; the design callback's original
        # insertion order is represented by its case rows, not dict order.
        "parameters": {"z": [1.0, 2.0], "a": [10.0, 20.0]},
        "units": {"z": "1", "a": "1"},
        "sampling": {"kind": "cartesian_grid"},
        "budget": {"max_cases": 4, "max_wall_time_s": 60.0},
    }
    monkeypatch.setattr(w21_execution, "validate_definition", lambda *_args: None)
    monkeypatch.setattr(w21_execution, "validate_parameters", lambda *_args: None)
    execution_calls = []

    def fake_execute_case(_worker, _model_tag, _study, _definition, values, budget, case_key, *,
                          metric_definitions=None, experiment_binding=None):
        assert isinstance(experiment_binding, dict)
        attempt = daemon.store.get_metadata(
            "artifacts",
            "w21experimentattempt:" + experiment_binding["experiment_id"] + ":" + experiment_binding["case_id"],
        )
        assert attempt["attempt_id"] == experiment_binding["attempt_id"]
        execution_calls.append((dict(values), case_key))
        budget.cases_evaluated += 1
        return {"status": "COMPLETED", "parameters": dict(values), "raw_result": {"metric": 12.5},
                "case_attempt_id": experiment_binding["attempt_id"]}

    monkeypatch.setattr(w21_execution, "execute_case", fake_execute_case)
    design_operation = _seed_operation(
        daemon, project_id, "experiment.design", {"definition": definition}, status="RUNNING",
    )
    with observation_context(daemon.store, model_ref, 7, design_operation, project_id=project_id):
        design = w21_execution.op_experiment_design(object(), "model-main", {"definition": definition})
    design_job = daemon.store.operation_job(design_operation)
    daemon.store.update_job(design_job["job_id"], "SUCCEEDED", result={"success": True, "data": design})

    run_operation = _seed_operation(
        daemon, project_id, "experiment.run", {"experiment_id": design["experiment_id"]}, status="RUNNING",
    )
    with observation_context(daemon.store, model_ref, 8, run_operation, project_id=project_id):
        run = w21_execution.op_experiment_run(
            object(), "model-main", {"experiment_id": design["experiment_id"]},
        )
    run_job = daemon.store.operation_job(run_operation)
    daemon.store.update_job(run_job["job_id"], "SUCCEEDED", result={"success": True, "data": run})

    host = _public_host(daemon)
    try:
        inspected = _call_public(
            host, "experiment_inspect", project_id=project_id, experiment_id=design["experiment_id"],
        )
        assert inspected["success"] is True
        assert inspected["data"]["status"] == "COMPLETE"
        assert [row["case_ordinal"] for row in inspected["data"]["cases"]] == [1, 2, 3, 4]
        assert [row["parameters"] for row in inspected["data"]["cases"]] == [
            {"z": 1.0, "a": 10.0}, {"z": 1.0, "a": 20.0},
            {"z": 2.0, "a": 10.0}, {"z": 2.0, "a": 20.0},
        ]
        case_result = _call_public(
            host, "experiment_case_result", project_id=project_id,
            experiment_id=design["experiment_id"], case_id="case-0004",
        )
        assert case_result["success"] is True
        assert case_result["data"]["case_ordinal"] == 4
        assert case_result["data"]["result"]["parameters"] == {"z": 2.0, "a": 20.0}
        assert case_result["data"]["record_source"] == "case_artifact"
        assert len(execution_calls) == 4
        assert daemon.backend.worker is None
    finally:
        daemon.close()


def test_registry_contract_publishes_durable_read_outputs_and_exact_case_key(tmp_path):
    assert validate_call("experiment.inspect", {
        "project_id": "project-a", "experiment_id": "exp-a",
    }).operation_id == "experiment.inspect"
    assert validate_call("experiment.case_result", {
        "project_id": "project-a", "experiment_id": "exp-a", "case_id": "case-0001",
    }).operation_id == "experiment.case_result"
    inspect_contract = operation_describe("experiment.inspect")
    case_contract = operation_describe("experiment.case_result")
    assert inspect_contract["implementation_status"] == "SUPPORTED_UNVERIFIED"
    assert inspect_contract["runtime_dispatch_contract"]["engine_queue"] == "bypassed; no Worker RPC or current ModelRef is required"
    assert case_contract["data_schema"]["properties"]["case_id"]["type"] == "string"
    assert case_contract["runtime_dispatch_contract"]["case_identity"].startswith("case_id is matched exactly")



def test_actual_callbacks_public_inspect_project_case_failure_cache_and_not_defined_objective(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, registry={})
    project_id = _create_project(daemon, "failure-cache-read")
    model_ref = {
        "schema_version": 1, "session_id": "session-synthetic",
        "server_instance_id": "worker-synthetic", "model_tag": "model-main", "generation": 1,
    }
    definition = {
        "study": "std1",
        "sample": {"spec": {"solution": {"dataset": "dset1"}, "expressions": ["T"]},
                   "points": [[0.0, 0.0]], "coordinate_unit": "m"},
        "metrics": {"peak": {"expression": "T", "unit": "K", "indices": [0]}},
        "times": [0.0],
        "validation": {"range": [250.0, 400.0]},
        "parameters": {"p": [1.0, 2.0]},
        "units": {"p": "1"},
        "sampling": {"kind": "cartesian_grid"},
        "budget": {"max_cases": 2, "max_wall_time_s": 60.0},
    }
    monkeypatch.setattr(w21_execution, "validate_definition", lambda *_args: None)
    monkeypatch.setattr(w21_execution, "validate_parameters", lambda *_args: None)
    callback_rows = []

    def fake_execute_case(_worker, _tag, _study, _definition, values, budget, _case_key, *,
                          metric_definitions=None, experiment_binding=None):
        assert isinstance(experiment_binding, dict)
        budget.cases_evaluated += 1
        row = {"parameters": dict(values), "cache_policy": "SYNTHETIC_REUSE_VERIFIED_CONFIGURATION",
               "case_attempt_id": experiment_binding["attempt_id"]}
        if values["p"] == 1.0:
            row.update(status="CACHED", cache_hit=True)
        else:
            row.update(status="FAILED", cache_hit=False, error="synthetic convergence failure")
        callback_rows.append(row)
        return row

    monkeypatch.setattr(w21_execution, "execute_case", fake_execute_case)
    design_arguments = {"definition": definition}
    design_operation = _seed_operation(
        daemon, project_id, "experiment.design", design_arguments, status="RUNNING",
    )
    with observation_context(daemon.store, model_ref, 7, design_operation, project_id=project_id):
        design = w21_execution.op_experiment_design(object(), "model-main", design_arguments)
    design_job = daemon.store.operation_job(design_operation)
    daemon.store.update_job(design_job["job_id"], "SUCCEEDED", result={"success": True, "data": design})

    run_arguments = {"experiment_id": design["experiment_id"]}
    run_operation = _seed_operation(
        daemon, project_id, "experiment.run", run_arguments, status="RUNNING",
    )
    with observation_context(daemon.store, model_ref, design["model_revision"], run_operation,
                            project_id=project_id):
        run = w21_execution.op_experiment_run(object(), "model-main", run_arguments)
    run_job = daemon.store.operation_job(run_operation)
    daemon.store.update_job(run_job["job_id"], "SUCCEEDED", result={"success": True, "data": run})
    monkeypatch.setattr(
        daemon.session_scheduler, "submit",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("inspect must not enter the engine queue")),
    )

    try:
        inspected = _call_public(
            _public_host(daemon), "experiment_inspect", project_id=project_id,
            experiment_id=design["experiment_id"],
        )
        assert inspected["success"] is True, inspected
        data = inspected["data"]
        assert data["status"] == "FAILED"
        assert data["best_feasible"] == {
            "status": "NOT_DEFINED",
            "reason_code": "NO_FROZEN_OBJECTIVE_AND_CONSTRAINTS",
            "case_id": None,
        }
        assert data["run"]["failure_reason"] == {
            "state": "REPORTED", "code": None, "message": "synthetic convergence failure",
        }
        assert data["run"]["cache_summary"] == {
            "state": "COMPLETE", "hits": 1, "misses": 1, "unknown_cases": 0, "total_cases": 2,
        }
        assert data["cases"][0]["cache"] == {
            "state": "HIT", "hit": True, "policy": "SYNTHETIC_REUSE_VERIFIED_CONFIGURATION",
        }
        assert data["cases"][0]["failure_reason"]["state"] == "NOT_APPLICABLE"
        assert data["cases"][1]["failure_reason"] == {
            "state": "REPORTED", "code": None, "message": "synthetic convergence failure",
        }
        assert callback_rows[0]["status"] == "CACHED"
        assert callback_rows[1]["status"] == "FAILED"
    finally:
        daemon.close()
