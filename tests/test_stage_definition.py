from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
import threading

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_registry import operation_describe, validate_call
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._operation_store import OperationStore, StagePlanStoreConflict
from comsol_mcp._stage_contract import StagePlanDefinition, validate_stage_plan_definition
from comsol_mcp._tools_w21 import register as register_w21
from comsol_mcp._g2_tools import register as register_g2


class _Adapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server-stage", "external_event_counter": 0,
                "fingerprint": "stage-model-fingerprint"}


class _Worker:
    generation = 1

    def __init__(self):
        self.calls = []

    def client(self):
        self.calls.append("client")
        raise AssertionError("stage definition must not access the Java client")

    def operation_context(self, *_args, **_kwargs):
        self.calls.append("operation_context")
        raise AssertionError("stage definition must not enter a Worker operation context")

    def submit(self, *_args, **_kwargs):
        self.calls.append("submit")
        raise AssertionError("stage definition must not issue a Worker request")


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def add_tool(self, function, **options):
        self.tools[options.get("name", function.__name__)] = function


def _plan(plan_id="heat-cycle", first_id="preheat"):
    return {
        "plan_id": plan_id,
        "stages": [
            {
                "stage_id": first_id,
                "ordinal": 1,
                "depends_on": [],
                "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
                "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
                "target_selection": {"dataset": "dset1", "solution": "sol1"},
                "variable_mappings": [{
                    "source_variable": "T", "target_variable": "T_preheat",
                    "source_unit": "K", "target_unit": "K", "mapping_method": "identity",
                }],
                "reference_state": {"strategy": "initial_state"},
                "checks": [{
                    "kind": "continuity", "check_id": "temperature-boundary",
                    "source_variable": "T", "target_variable": "T_preheat", "unit": "K",
                    "tolerance": {"absolute": 0.1, "relative": 0.0},
                }],
            },
            {
                "stage_id": "cooldown",
                "ordinal": 2,
                "depends_on": [first_id],
                "study_target": {"segments": [{"collection": "study", "tag": "std2"}]},
                "source_selection": {"kind": "stage", "stage_id": first_id,
                                     "selection": {"dataset": "dset1", "solution": "sol1"}},
                "target_selection": {"dataset": "dset2", "solution": "sol2"},
                "variable_mappings": [{
                    "source_variable": "T_preheat", "target_variable": "T_cool",
                    "source_unit": "K", "target_unit": "K", "mapping_method": "interpolate",
                }],
                "reference_state": {"strategy": "predecessor_stage", "source_stage_id": first_id},
                "checks": [{
                    "kind": "conservation", "check_id": "temperature-balance", "quantity": "temperature",
                    "terms": [
                        {"side": "source", "variable": "T_preheat", "coefficient": 1.0},
                        {"side": "target", "variable": "T_cool", "coefficient": -1.0},
                    ],
                    "unit": "K", "tolerance": {"absolute": 0.2, "relative": 0.01},
                }],
            },
        ],
    }


def _setup(tmp_path, permissions=None):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    service = ExecutionService(
        SessionLedger("session-stage", "server-stage"), _Adapter(), project_root=project_root,
    )
    bound = service.bind_model("model-main")
    model_ref = model_ref_from_mapping(bound["execution"]["model_ref"]).as_dict()
    worker = _Worker()
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=worker,
                           registry={}, project_root=project_root)
    project = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": "stage-plan", "workspace": "stage-plan",
                      "policy": {"permissions": permissions or ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": "stage-project", "idempotency_key": "stage-project"},
    })
    assert project["success"] is True, project
    project_id = project["data"]["project"]["project_id"]
    daemon.backend._bind_model_project(model_ref, project_id)
    daemon.store.put_metadata("revisions", daemon.backend._model_project_key(model_ref), {
        "model_ref": model_ref, "revision": 0, "dirty": False,
        "attribution": "PROJECT_BOUND", "project_id": project_id,
    })
    worker.calls.clear()
    execution = {
        "project_id": project_id, "session_id": "session-stage", "model_ref": model_ref,
        "expected_revision": 0, "idempotency_key": "stage-key-1", "request_id": "stage-request-1",
    }
    host = _FakeMcp()
    gateway = GatewayRegistry(
        host,
        dispatcher=lambda operation, arguments, execution: daemon.dispatch({
            "operation": operation, "arguments": arguments, "execution": execution,
        }),
    )
    register_w21(gateway)
    register_g2(gateway)
    return daemon, service, worker, project_id, model_ref, execution, host


def _call_public(host, name, **kwargs):
    return asyncio.run(host.tools[name](**kwargs)).structuredContent


def test_stage_plan_schema_is_closed_and_semantically_validated():
    plan = _plan()
    validate_stage_plan_definition(plan)
    model = StagePlanDefinition.model_validate(plan)
    assert model.model_dump(mode="json", exclude_none=True) == plan
    schema = operation_describe("experiment.stage_define")["input_schema"]
    assert schema["properties"]["definition"]["additionalProperties"] is False
    assert schema["properties"]["definition"]["properties"]["stages"]["items"]["additionalProperties"] is False
    from mcp.server.fastmcp import FastMCP
    public = FastMCP("stage-schema-test")
    register_w21(GatewayRegistry(public))
    direct_tool = next(item for item in asyncio.run(public.list_tools()) if item.name == "experiment_stage_define")
    direct_schema = direct_tool.inputSchema
    definition_ref = direct_schema["properties"]["definition"]["$ref"].split("/")[-1]
    definition_schema = direct_schema["$defs"][definition_ref]
    assert definition_schema["additionalProperties"] is False
    stage_ref = definition_schema["properties"]["stages"]["items"]["$ref"].split("/")[-1]
    assert direct_schema["$defs"][stage_ref]["additionalProperties"] is False
    assert "experiment_stage_define" in {item.name for item in asyncio.run(public.list_tools())}
    assert validate_call("experiment.stage_define", {
        "project_id": "p", "session_id": "s",
        "model_ref": {"session_id": "s", "server_instance_id": "server", "model_tag": "m", "generation": 1, "schema_version": 1},
        "expected_revision": 0, "idempotency_key": "key", "definition": plan,
    }).operation_id == "experiment.stage_define"


@pytest.mark.parametrize("mutate", [
    lambda p: p["stages"][0].update(ordinal=True),
    lambda p: p["stages"][1].update(depends_on=["missing"]),
    lambda p: p["stages"][1].update(depends_on=["cooldown"]),
    lambda p: p["stages"][1].update(source_selection={"kind": "stage", "stage_id": "missing", "selection": {"dataset": "dset"}}),
    lambda p: p["stages"][0].update(unrecognized=True),
    lambda p: p["stages"][0]["variable_mappings"].append(dict(p["stages"][0]["variable_mappings"][0])),
    lambda p: p["stages"][0]["checks"][0]["tolerance"].update(absolute=0.0, relative=0.0),
    lambda p: p["stages"][0]["checks"][0].update(unit="s"),
    lambda p: p["stages"][0]["checks"][0]["tolerance"].update(absolute=float("inf")),
    lambda p: p.update(unrecognized=True),
])
def test_stage_plan_rejects_invalid_graph_or_scientific_declaration(mutate):
    plan = _plan()
    mutate(plan)
    with pytest.raises(ExecutionContractError):
        validate_stage_plan_definition(plan)


def test_public_direct_and_fallback_stage_definition_persist_without_worker_or_solve(tmp_path):
    daemon, _service, worker, project_id, model_ref, execution, host = _setup(tmp_path)
    try:
        direct = _call_public(host, "experiment_stage_define", definition=_plan(), execution=execution)
        assert direct["success"] is True, direct
        assert direct["data"]["registration"] == "CREATED"
        assert direct["data"]["declaration_status"] == "DECLARED_UNVERIFIED"
        assert direct["data"]["worker_rpc_performed"] is False
        assert direct["data"]["solve_started"] is False
        assert direct["data"]["stable_model_identity"] == {
            "session_id": model_ref["session_id"],
            "server_instance_id": model_ref["server_instance_id"],
            "model_tag": model_ref["model_tag"], "generation": model_ref["generation"],
        }
        # The public operation fallback reaches the same plan key and returns
        # its prior immutable row, without sending a second model request.
        replay_execution = {**execution, "request_id": "stage-request-fallback", "idempotency_key": "stage-key-fallback"}
        fallback = _call_public(
            host, "operation_call", operation_id="experiment.stage_define",
            arguments={"definition": _plan()}, execution=replay_execution,
        )
        assert fallback["success"] is True, fallback
        assert fallback["data"]["registration"] == "IDEMPOTENT_EXISTING"
        assert fallback["data"]["sha256"] == direct["data"]["sha256"]
        assert worker.calls == []
        assert len([row for row in daemon.store.list_metadata("artifacts") if row.get("kind") == "w21_stage_plan"]) == 1
        assert len([row for row in daemon.store.list_metadata("artifacts") if row.get("kind") == "w21_stage_id_index"]) == 2
        operation_rows = daemon.store.db.execute(
            "SELECT operation,status,result FROM operations WHERE operation='experiment.stage_define' ORDER BY created_at"
        ).fetchall()
        assert len(operation_rows) == 2 and all(row["status"] == "SUCCEEDED" for row in operation_rows)
        assert all(json.loads(row["result"])["data"]["worker_rpc_performed"] is False for row in operation_rows)
    finally:
        database_path = daemon.store.path
        daemon.close()

    reopened = OperationStore(database_path)
    try:
        persisted = reopened.get_stage_plan(project_id, model_ref, "heat-cycle")
        assert persisted is not None
        assert persisted["definition"] == _plan()
        assert reopened.get_stage_id_index(project_id, model_ref, "preheat")["plan_id"] == "heat-cycle"
    finally:
        reopened.close()


def test_stage_plan_idempotency_conflicts_and_revision_not_scope(tmp_path):
    daemon, service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        first = daemon.dispatch({"operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": execution})
        assert first["success"] is True, first
        same_request = daemon.dispatch({"operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": execution})
        assert same_request == first

        changed_content = _plan()
        changed_content["stages"][0]["target_selection"]["solution"] = "sol-new"
        conflict = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": changed_content},
            "execution": {**execution, "idempotency_key": "changed-plan-key", "request_id": "changed-plan"},
        })
        assert conflict["success"] is False and conflict["error"]["code"] == "STAGE_PLAN_CONFLICT"
        duplicate_stage = _plan(plan_id="other-plan")
        duplicate = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": duplicate_stage},
            "execution": {**execution, "idempotency_key": "duplicate-stage-key", "request_id": "duplicate-stage"},
        })
        assert duplicate["success"] is False and duplicate["error"]["code"] == "STAGE_ID_CONFLICT"

        state = service.ledger._state_for(model_ref_from_mapping(model_ref))
        state.revision = 1
        daemon.store.put_metadata("revisions", daemon.backend._model_project_key(model_ref), {
            "model_ref": model_ref, "revision": 1, "dirty": False,
            "attribution": "PROJECT_BOUND", "project_id": project_id,
        })
        next_revision = {**execution, "expected_revision": 1, "idempotency_key": "revision-reuse-key", "request_id": "revision-reuse"}
        revision_reuse = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": next_revision,
        })
        assert revision_reuse["success"] is True, revision_reuse
        assert revision_reuse["data"]["registration"] == "IDEMPOTENT_EXISTING"
        assert revision_reuse["data"]["declaration_revision"] == 0
        assert revision_reuse["execution"]["revision"] == 1
        assert worker.calls == []
    finally:
        daemon.close()


def test_multiple_disjoint_plans_retry_independently_after_revision_and_reopen(tmp_path):
    daemon, service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    first_plan = _plan()
    second_plan = _plan("second-plan", "warmup")
    second_plan["stages"][1]["stage_id"] = "second-cooldown"
    try:
        first = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": first_plan}, "execution": execution,
        })
        second = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": second_plan},
            "execution": {**execution, "request_id": "second-plan-create", "idempotency_key": "second-plan-create"},
        })
        assert first["success"] is True and first["data"]["registration"] == "CREATED", first
        assert second["success"] is True and second["data"]["registration"] == "CREATED", second
        assert [first["data"]["stage_ids"], second["data"]["stage_ids"]] == [
            ["preheat", "cooldown"], ["warmup", "second-cooldown"],
        ]

        # Reproduce the reported order exactly: after a second disjoint plan
        # is registered, retry the first plan with a fresh request identity.
        for index, plan in enumerate((first_plan, second_plan)):
            immediate_replay = daemon.dispatch({
                "operation": "experiment.stage_define", "arguments": {"definition": plan},
                "execution": {
                    **execution,
                    "request_id": f"immediate-retry-{index}",
                    "idempotency_key": f"immediate-retry-key-{index}",
                },
            })
            assert immediate_replay["success"] is True, immediate_replay
            assert immediate_replay["data"]["registration"] == "IDEMPOTENT_EXISTING"

        for plan in (first_plan, second_plan):
            stored = daemon.store.get_stage_plan(project_id, model_ref, plan["plan_id"])
            assert stored is not None and stored["definition"] == plan
            for stage in plan["stages"]:
                resolved = daemon.store.resolve_stage(project_id, model_ref, stage["stage_id"])
                assert resolved is not None
                assert resolved["plan"]["plan_id"] == plan["plan_id"]
                assert resolved["stage"] == stage
                assert resolved["index"]["plan_id"] == plan["plan_id"]
                assert resolved["index"]["ordinal"] == stage["ordinal"]

        # A later model revision preserves both immutable plans and their IDs.
        state = service.ledger._state_for(model_ref_from_mapping(model_ref))
        state.revision = 1
        daemon.store.put_metadata("revisions", daemon.backend._model_project_key(model_ref), {
            "model_ref": model_ref, "revision": 1, "dirty": False,
            "attribution": "PROJECT_BOUND", "project_id": project_id,
        })
    finally:
        daemon.close()

    # Reopen the same durable DB through a fresh daemon and same stable ModelRef.
    reopened_service = ExecutionService(
        SessionLedger("session-stage", "server-stage"), _Adapter(), project_root=tmp_path / "projects",
    )
    rebound = reopened_service.bind_model("model-main")
    reopened_model_ref = model_ref_from_mapping(rebound["execution"]["model_ref"]).as_dict()
    assert reopened_model_ref == model_ref
    reopened_worker = _Worker()
    reopened_daemon = ControlDaemon(
        tmp_path / "control", service=reopened_service, worker=reopened_worker,
        registry={}, project_root=tmp_path / "projects",
    )
    try:
        reopened_daemon.backend._bind_model_project(reopened_model_ref, project_id)
        reopened_state = reopened_service.ledger._state_for(model_ref_from_mapping(reopened_model_ref))
        reopened_state.revision = 1
        reopened_daemon.store.put_metadata("revisions", reopened_daemon.backend._model_project_key(reopened_model_ref), {
            "model_ref": reopened_model_ref, "revision": 1, "dirty": False,
            "attribution": "PROJECT_BOUND", "project_id": project_id,
        })
        for index, plan in enumerate((first_plan, second_plan)):
            replay = reopened_daemon.dispatch({
                "operation": "experiment.stage_define", "arguments": {"definition": plan},
                "execution": {
                    **execution,
                    "model_ref": reopened_model_ref,
                    "expected_revision": 1,
                    "request_id": f"reopened-retry-{index}",
                    "idempotency_key": f"reopened-retry-key-{index}",
                },
            })
            assert replay["success"] is True, replay
            assert replay["data"]["registration"] == "IDEMPOTENT_EXISTING"
            assert replay["data"]["declaration_revision"] == 0
            persisted = reopened_daemon.store.get_stage_plan(project_id, reopened_model_ref, plan["plan_id"])
            assert persisted is not None and persisted["definition"] == plan
            for stage in plan["stages"]:
                resolved = reopened_daemon.store.resolve_stage(project_id, reopened_model_ref, stage["stage_id"])
                assert resolved is not None
                assert resolved["plan"]["plan_id"] == plan["plan_id"]
                assert resolved["stage"] == stage
            assert reopened_worker.calls == []
    finally:
        reopened_daemon.close()


@pytest.mark.parametrize("tamper", [
    "missing", "wrong_plan_hash", "extra", "wrong_owner", "wrong_ordinal", "boolean_ordinal", "boolean_generation",
])
def test_stage_plan_and_resolve_stage_readback_reject_corrupt_index_ownership(tmp_path, tamper):
    daemon, _service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        created = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": execution,
        })
        assert created["success"] is True, created
        first_index_key = daemon.store.stage_id_key(project_id, model_ref, "preheat")
        if tamper == "missing":
            daemon.store.db.execute("DELETE FROM artifacts WHERE artifact_id=?", (first_index_key,))
        elif tamper == "extra":
            plan_record = daemon.store.get_stage_plan(project_id, model_ref, "heat-cycle")
            extra_stage = {"stage_id": "unplanned-stage", "ordinal": 99}
            extra = daemon.store._stage_index_record(project_id, model_ref, plan_record, extra_stage)
            daemon.store.db.execute(
                "INSERT INTO artifacts(artifact_id,metadata) VALUES(?,?)",
                (daemon.store.stage_id_key(project_id, model_ref, "unplanned-stage"), json.dumps(extra, sort_keys=True)),
            )
        else:
            row = daemon.store.db.execute(
                "SELECT metadata FROM artifacts WHERE artifact_id=?", (first_index_key,),
            ).fetchone()
            index = json.loads(row[0])
            if tamper == "wrong_plan_hash":
                index["plan_sha256"] = "0" * 64
            elif tamper == "wrong_owner":
                index["model_ref"] = {**model_ref, "generation": model_ref["generation"] + 10}
            elif tamper == "wrong_ordinal":
                index["ordinal"] = 77
            elif tamper == "boolean_ordinal":
                index["ordinal"] = True
            elif tamper == "boolean_generation":
                index["model_ref"] = {**model_ref, "generation": True}
            from comsol_mcp._stage_contract import sha256_json
            index["sha256"] = sha256_json({key: value for key, value in index.items() if key != "sha256"})
            daemon.store.db.execute(
                "UPDATE artifacts SET metadata=? WHERE artifact_id=?", (json.dumps(index, sort_keys=True), first_index_key),
            )

        with pytest.raises(StagePlanStoreConflict) as plan_error:
            daemon.store.get_stage_plan(project_id, model_ref, "heat-cycle")
        assert plan_error.value.code == "STAGE_PLAN_STATE_UNKNOWN"
        with pytest.raises(StagePlanStoreConflict) as resolve_error:
            daemon.store.resolve_stage(project_id, model_ref, "preheat")
        assert resolve_error.value.code == "STAGE_PLAN_STATE_UNKNOWN"
        with pytest.raises(StagePlanStoreConflict) as index_error:
            daemon.store.get_stage_id_index(project_id, model_ref, "preheat")
        assert index_error.value.code == "STAGE_PLAN_STATE_UNKNOWN"
        assert worker.calls == []
    finally:
        daemon.close()


def test_stage_plan_scope_uses_full_modelref_and_project_authority(tmp_path):
    daemon, service, worker, project_id, old_ref, execution, _host = _setup(tmp_path)
    try:
        created = daemon.dispatch({"operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": execution})
        assert created["success"] is True
        # Rebinding the same session/tag produces a different generation and
        # must not inherit the previous plan or stage IDs.
        new_ref = model_ref_from_mapping(service.bind_model("model-main")["execution"]["model_ref"]).as_dict()
        assert new_ref["session_id"] == old_ref["session_id"] and new_ref["model_tag"] == old_ref["model_tag"]
        assert new_ref["generation"] != old_ref["generation"]
        daemon.backend._bind_model_project(new_ref, project_id)
        daemon.store.put_metadata("revisions", daemon.backend._model_project_key(new_ref), {
            "model_ref": new_ref, "revision": 0, "dirty": False,
            "attribution": "PROJECT_BOUND", "project_id": project_id,
        })
        assert daemon.store.get_stage_plan(project_id, new_ref, "heat-cycle") is None
        isolated = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan()},
            "execution": {**execution, "model_ref": new_ref, "expected_revision": 0,
                          "idempotency_key": "new-generation-key", "request_id": "new-generation"},
        })
        assert isolated["success"] is True and isolated["data"]["registration"] == "CREATED"
        other_server_ref = {**new_ref, "server_instance_id": "replacement-server"}
        server_swap = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan("server-swap-plan", "server-swap-stage")},
            "execution": {**execution, "model_ref": other_server_ref, "expected_revision": 0,
                          "idempotency_key": "server-swap-key", "request_id": "server-swap"},
        })
        assert server_swap["success"] is False
        assert server_swap["error"]["code"] in {"MODEL_IDENTITY_MISMATCH", "PROJECT_IDENTITY_MISMATCH"}
        assert daemon.store.get_stage_plan(project_id, other_server_ref, "server-swap-plan") is None
        assert daemon.store.get_stage_id_index(project_id, other_server_ref, "server-swap-stage") is None
        # Reusing the old full ModelRef under a different project is denied
        # before either project-scoped plan key is examined.
        foreign = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan()},
            "execution": {**execution, "project_id": "project-does-not-own-model",
                          "idempotency_key": "foreign-project-key", "request_id": "foreign-project"},
        })
        assert foreign["success"] is False
        assert foreign["error"]["code"] in {"PROJECT_NOT_FOUND", "PROJECT_IDENTITY_MISMATCH"}
        assert worker.calls == []
    finally:
        daemon.close()


def test_stage_definition_requires_project_write_permission(tmp_path):
    daemon, _service, worker, _project, _model, execution, _host = _setup(tmp_path, permissions=["inspect"])
    try:
        result = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan()},
            "execution": execution,
        })
        assert result["success"] is False
        assert result["error"]["code"] == "PERMISSION_DENIED"
        assert not any(row.get("kind") == "w21_stage_plan" for row in daemon.store.list_metadata("artifacts"))
        assert worker.calls == []
    finally:
        daemon.close()


def test_stage_plan_sqlite_registration_rolls_back_whole_graph_on_injected_index_failure(tmp_path):
    daemon, _service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        daemon.store.db.execute(
            "CREATE TRIGGER reject_stage_index BEFORE INSERT ON artifacts "
            "WHEN NEW.artifact_id LIKE 'w21-stage-id:%' "
            "BEGIN SELECT RAISE(ABORT,'injected stage index failure'); END"
        )
        result = daemon.dispatch({"operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": execution})
        assert result["success"] is False
        assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert daemon.store.get_stage_plan(project_id, model_ref, "heat-cycle") is None
        assert daemon.store.get_stage_id_index(project_id, model_ref, "preheat") is None
        assert not any(row.get("kind") == "w21_stage_plan" for row in daemon.store.list_metadata("artifacts"))
        assert worker.calls == []
    finally:
        daemon.close()


def test_concurrent_plans_cannot_claim_same_stage_id_in_one_exact_scope(tmp_path):
    daemon, _service, worker, _project, _model, execution, _host = _setup(tmp_path)
    plans = [_plan("concurrent-a", "same-stage"), _plan("concurrent-b", "same-stage")]
    barrier = threading.Barrier(3)
    results = []
    result_lock = threading.Lock()

    def submit(index):
        barrier.wait()
        value = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": plans[index]},
            "execution": {**execution, "request_id": f"race-{index}", "idempotency_key": f"race-key-{index}"},
        })
        with result_lock:
            results.append(value)

    threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
    try:
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        assert sorted(result["success"] for result in results) == [False, True]
        failure = next(result for result in results if not result["success"])
        assert failure["error"]["code"] == "STAGE_ID_CONFLICT"
        assert len([row for row in daemon.store.list_metadata("artifacts") if row.get("kind") == "w21_stage_plan"]) == 1
        assert worker.calls == []
    finally:
        daemon.close()
