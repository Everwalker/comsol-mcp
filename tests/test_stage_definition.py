from __future__ import annotations

import asyncio
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._java_worker import JavaWorkerTimeout, PersistentJavaWorker, RemoteJava
from comsol_mcp._g2_registry import operation_describe, validate_call
from comsol_mcp._mcp_gateway import GatewayRegistry
from comsol_mcp._operation_store import OperationStore, StagePlanStoreConflict
from comsol_mcp._stage_contract import StagePlanDefinition, validate_stage_plan_definition
from comsol_mcp._tools_w21 import register as register_w21
from comsol_mcp._g2_tools import register as register_g2
from comsol_mcp import _w21_execution


class _Adapter:
    def __init__(self):
        self.fingerprint = "stage-model-fingerprint"

    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server-stage", "external_event_counter": 0,
                "fingerprint": self.fingerprint}


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


def _plan_v2():
    plan = _plan()
    stages = plan["stages"]
    stages[0]["mapping_profile"] = "same_name_same_mesh_initialization"
    stages[0]["variable_mappings"] = [{
        "source_variable": "T", "target_variable": "T", "source_unit": "K",
        "target_unit": "K", "mapping_method": "identity",
    }]
    stages[0]["checks"] = []
    stages[1]["target_variables"] = {"segments": [
        {"collection": "sol", "tag": "sol2"},
        {"collection": "feature", "tag": "v2"},
    ]}
    stages[1]["mapping_profile"] = "same_name_same_mesh_initialization"
    stages[1]["source_selection"]["selection"] = {
        "dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [2],
    }
    stages[1]["target_selection"] = {
        "dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1],
    }
    stages[1]["variable_mappings"] = [{
        "source_variable": "T", "target_variable": "T", "source_unit": "K",
        "target_unit": "K", "mapping_method": "identity",
    }]
    stages[1]["checks"] = [{
        "kind": "continuity", "check_id": "temperature-boundary",
        "operator": "pointwise_max_abs", "source_variable": "T", "target_variable": "T",
        "source_solution": {"dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [2]},
        "target_solution": {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1]},
        "source_selection": {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 3},
        "target_selection": {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 3},
        "frame": "spatial", "boundary_time": {"value": 1.0, "unit": "s"},
        "unit": "K", "tolerance": {"absolute": 0.1, "relative": 0.01},
    }, {
        "kind": "conservation", "check_id": "temperature-balance",
        "operator": "native_integral", "quantity": "temperature",
        "source_solution": {"dataset": "dset1", "solution": "sol1", "outer": [1], "inner": [2]},
        "target_solution": {"dataset": "dset2", "solution": "sol2", "outer": [1], "inner": [1]},
        "terms": [
            {"side": "source", "variable": "T", "coefficient": 1.0,
             "selection": {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 3},
             "entity_dimension": 3},
            {"side": "target", "variable": "T", "coefficient": 1.0,
             "selection": {"kind": "all", "component": "comp1", "geometry": "geom1", "entity_dimension": 3},
             "entity_dimension": 3},
        ],
        "unit": "K", "tolerance": {"absolute": 0.2, "relative": 0.01},
    }]
    return {"version": 2, "plan_id": "heat-cycle-v2", "stages": stages}


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


class _StateMapFeature:
    def __init__(self):
        self.values = {"useinitsol": "off", "initmethod": "init", "initsol": "zero",
                       "initsoluse": "current", "initsolusesolnum": 1,
                       "solnum": "last", "manualsolnum": 1}
        self.set_calls = []

    def getType(self):
        return "Variables"

    def set(self, name, value):
        self.set_calls.append((name, value))
        self.values[name] = value

    def getString(self, name):
        return self.values[name]

    def getInt(self, name):
        return self.values[name]


class _StateMapSequence:
    def __init__(self, study, features=None, attached=True):
        self.study_tag = study
        self.features = {"v1": _StateMapFeature()} if features is None else features
        self.attached = attached
        self.run_calls = 0

    def study(self):
        return self.study_tag

    def isAttached(self):
        return self.attached

    def feature(self, tag=None):
        return SimpleNamespace(tags=lambda: list(self.features), get=lambda name: self.features[name]) if tag is None else self.features[tag]

    def run(self):
        self.run_calls += 1


class _StateMapList:
    def __init__(self, values):
        self.values = values

    def tags(self):
        return list(self.values)

    def get(self, tag):
        return self.values[tag]


class _StateMapModel:
    def __init__(self):
        self.sequences = _StateMapList({
            "sol2": _StateMapSequence("std1", {"source": _StateMapFeature()}),
            "sol3": _StateMapSequence("std2"),
        })
        self.studies = _StateMapList({"std1": object(), "std2": object()})

    def sol(self, tag=None):
        return self.sequences if tag is None else self.sequences.get(tag)

    def study(self, tag=None):
        return self.studies if tag is None else self.studies.get(tag)


def _state_map_request():
    return {
        "source": {"dataset": "dset1", "solution": "sol2", "outer": 1, "inner": 1},
        "target": {"segments": [{"collection": "sol", "tag": "sol3"},
                                {"collection": "feature", "tag": "v1"}]},
        "mapping": {"profile": "same_name_same_mesh_initialization", "variables": [{
            "source_variable": "T", "target_variable": "T", "source_unit": "K",
            "target_unit": "K", "mapping_method": "identity",
        }]},
    }


def test_stage_plan_schema_is_closed_and_semantically_validated():
    plan = _plan()
    validate_stage_plan_definition(plan)
    model = StagePlanDefinition.model_validate(plan)
    assert model.model_dump(mode="json", exclude_none=True) == plan
    schema = operation_describe("experiment.stage_define")["input_schema"]
    catalog_definition = schema["properties"]["definition"]
    assert len(catalog_definition["oneOf"]) == 2
    assert all(item["additionalProperties"] is False for item in catalog_definition["oneOf"])
    assert catalog_definition["oneOf"][1]["properties"]["version"]["const"] == 2
    from mcp.server.fastmcp import FastMCP
    public = FastMCP("stage-schema-test")
    register_w21(GatewayRegistry(public))
    direct_tool = next(item for item in asyncio.run(public.list_tools()) if item.name == "experiment_stage_define")
    direct_schema = direct_tool.inputSchema
    definition_ref = direct_schema["properties"]["definition"]["$ref"].split("/")[-1]
    definition_schema = direct_schema["$defs"][definition_ref]
    assert "anyOf" in definition_schema
    version1_ref, version2_ref = [entry["$ref"].split("/")[-1] for entry in definition_schema["anyOf"]]
    assert direct_schema["$defs"][version1_ref]["additionalProperties"] is False
    assert direct_schema["$defs"][version2_ref]["additionalProperties"] is False
    assert "experiment_stage_define" in {item.name for item in asyncio.run(public.list_tools())}
    assert validate_call("experiment.stage_define", {
        "project_id": "p", "session_id": "s",
        "model_ref": {"session_id": "s", "server_instance_id": "server", "model_tag": "m", "generation": 1, "schema_version": 1},
        "expected_revision": 0, "idempotency_key": "key", "definition": plan,
    }).operation_id == "experiment.stage_define"


def test_stage_plan_v2_exact_profile_and_readback_contract():
    plan = _plan_v2()
    normalized = validate_stage_plan_definition(plan)
    assert normalized["version"] == 2
    assert normalized["stages"][1]["target_variables"] == plan["stages"][1]["target_variables"]
    assert normalized["stages"][1]["checks"][0]["operator"] == "pointwise_max_abs"
    assert normalized["stages"][1]["checks"][1]["operator"] == "native_integral"
    assert StagePlanDefinition.model_validate(plan).model_dump(mode="json", exclude_none=True) == plan


def test_stage_plan_v2_integral_unit_is_declared_result_unit_not_field_unit():
    plan = _plan_v2()
    check = plan["stages"][1]["checks"][1]
    check["unit"] = "K*m^3"

    normalized = validate_stage_plan_definition(plan)

    # The term maps a K-valued field and integrates over 3D selections. The
    # declared integrated unit is retained; no native dimensionality proof is
    # implied by definition validation.
    assert normalized["stages"][1]["checks"][1]["unit"] == "K*m^3"
    assert normalized["stages"][1]["variable_mappings"][0]["source_unit"] == "K"


def test_stage_plan_v2_allows_nonzero_signed_terms_over_distinct_selections():
    plan = _plan_v2()
    check = plan["stages"][1]["checks"][1]
    check["unit"] = "K*m^3"
    check["terms"] = [
        {"side": "source", "variable": "T", "coefficient": 1.0,
         "selection": {"kind": "explicit", "entities": [1], "entity_dimension": 3},
         "entity_dimension": 3},
        {"side": "source", "variable": "T", "coefficient": -1.0,
         "selection": {"kind": "explicit", "entities": [2], "entity_dimension": 3},
         "entity_dimension": 3},
        {"side": "target", "variable": "T", "coefficient": 1.0,
         "selection": {"kind": "explicit", "entities": [3], "entity_dimension": 3},
         "entity_dimension": 3},
        {"side": "target", "variable": "T", "coefficient": -1.0,
         "selection": {"kind": "explicit", "entities": [4], "entity_dimension": 3},
         "entity_dimension": 3},
    ]

    normalized = validate_stage_plan_definition(plan)

    assert [term["selection"]["entities"] for term in normalized["stages"][1]["checks"][1]["terms"]] == [
        [1], [2], [3], [4],
    ]
    assert all(term["coefficient"] != 0 for term in normalized["stages"][1]["checks"][1]["terms"])


@pytest.mark.parametrize("bad_coefficient", [float("inf"), float("-inf"), float("nan")])
def test_stage_plan_v2_rejects_nonfinite_integral_coefficients(bad_coefficient):
    plan = _plan_v2()
    plan["stages"][1]["checks"][1]["terms"][0]["coefficient"] = bad_coefficient
    with pytest.raises(ExecutionContractError):
        validate_stage_plan_definition(plan)


def test_stage_plan_v2_rejects_term_selection_dimension_mismatch():
    plan = _plan_v2()
    term = plan["stages"][1]["checks"][1]["terms"][0]
    term["selection"] = {"kind": "explicit", "entities": [1], "entity_dimension": 2}
    with pytest.raises(ExecutionContractError, match="conflicts with term entity_dimension"):
        validate_stage_plan_definition(plan)


def test_stage_plan_v1_conservation_keeps_field_unit_contract():
    plan = _plan()
    normalized = validate_stage_plan_definition(plan)
    canonical = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == "fe033e7d1c1d9d1a8169923990cf6041dcf4e196520f3017f682b8270bc88a29"
    plan["stages"][1]["checks"][0]["unit"] = "K*m^3"
    with pytest.raises(ExecutionContractError, match="term units must match"):
        validate_stage_plan_definition(plan)


@pytest.mark.parametrize("integrated_unit", ["K", "K*m^3"])
def test_v2_public_stage_define_keeps_integral_units_unverified(tmp_path, integrated_unit):
    daemon, _service, worker, project_id, model_ref, execution, host = _setup(tmp_path)
    try:
        plan = _plan_v2()
        plan["stages"][1]["checks"][1]["unit"] = integrated_unit
        result = _call_public(host, "experiment_stage_define", definition=plan, execution=execution)

        assert result["success"] is True, result
        assert result["data"]["declaration_status"] == "DECLARED_UNVERIFIED"
        assert result["data"]["worker_rpc_performed"] is False
        assert result["data"]["solve_started"] is False
        stored = daemon.store.get_stage_plan(project_id, model_ref, plan["plan_id"])
        assert stored["definition"]["stages"][1]["checks"][1]["unit"] == integrated_unit
        assert stored["declaration_evidence"]["mapping_method_execution"] == "NOT_CHECKED"
        assert worker.calls == []
    finally:
        daemon.close()


@pytest.mark.parametrize("mutation", [
    lambda p: p["stages"][1].pop("target_variables"),
    lambda p: p["stages"][1]["target_variables"]["segments"][0].update(collection="study"),
    lambda p: p["stages"][1]["variable_mappings"][0].update(target_variable="T2"),
    lambda p: p["stages"][1]["checks"][0].update(operator="max_norm"),
    lambda p: p["stages"][1]["checks"][0]["source_solution"].update(inner="all"),
    lambda p: p["stages"][1]["checks"][0].update(frame="unknown"),
    lambda p: p["stages"][1]["checks"][0]["boundary_time"].update(value=True),
    lambda p: p["stages"][1]["checks"][1]["terms"][0].update(coefficient=True),
    lambda p: p["stages"][1]["checks"][1]["terms"][0].update(entity_dimension=True),
    lambda p: p["stages"][1]["checks"][1]["terms"][0]["selection"].update(extra="no"),
    lambda p: [term.update(coefficient=0.0) for term in p["stages"][1]["checks"][1]["terms"]],
])
def test_stage_plan_v2_rejects_ambiguous_or_unbound_contract(mutation):
    plan = _plan_v2()
    mutation(plan)
    with pytest.raises(ExecutionContractError):
        validate_stage_plan_definition(plan)


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


def test_stage_attempts_are_project_model_plan_bound_idempotent_and_cas_persisted(tmp_path):
    daemon, _service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    plan = _plan_v2()
    try:
        created = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": plan}, "execution": execution,
        })
        assert created["success"] is True, created
        first, reused = daemon.store.begin_stage_attempt(
            project_id=project_id, model_ref=model_ref, stage_id="preheat", expected_revision=0,
            request_id="attempt-1", operation_id="attempt-operation-1",
            idempotency_key="stage-attempt-1", request_hash="a" * 64,
        )
        assert reused is False
        assert first["status"] == "ADMITTED" and first["version"] == 1
        assert first["engine_dispatched"] is False and first["acceptance_status"] == "NOT_EVALUATED"
        replay, reused = daemon.store.begin_stage_attempt(
            project_id=project_id, model_ref=model_ref, stage_id="preheat", expected_revision=0,
            request_id="attempt-1", operation_id="attempt-operation-1",
            idempotency_key="stage-attempt-1", request_hash="a" * 64,
        )
        assert reused is True and replay == first

        blocked = daemon.store.update_stage_attempt(
            project_id, model_ref, first["attempt_id"], expected_version=1,
            status="NOT_DISPATCHED_UNVERIFIED", engine_dispatched=False,
            execution_status="NOT_DISPATCHED", acceptance_status="UNVERIFIED",
            evidence=[{"reason": "native mapping/mesh/frame proof is not available in this route"}],
        )
        assert blocked["version"] == 2 and blocked["status"] == "NOT_DISPATCHED_UNVERIFIED"
        assert blocked["engine_dispatched"] is False and blocked["acceptance_status"] == "UNVERIFIED"
        with pytest.raises(StagePlanStoreConflict, match="changed before"):
            daemon.store.update_stage_attempt(
                project_id, model_ref, first["attempt_id"], expected_version=1,
                status="FAILED", engine_dispatched=False, execution_status="FAILED",
                acceptance_status="NOT_EVALUATED", evidence=blocked["evidence"],
            )

        second, reused = daemon.store.begin_stage_attempt(
            project_id=project_id, model_ref=model_ref, stage_id="preheat", expected_revision=0,
            request_id="attempt-2", operation_id="attempt-operation-2",
            idempotency_key="stage-attempt-2", request_hash="b" * 64,
        )
        assert reused is False and second["attempt_number"] == 2
        dispatched = daemon.store.update_stage_attempt(
            project_id, model_ref, second["attempt_id"], expected_version=1,
            status="RUNNING", engine_dispatched=True, execution_status="RUNNING",
            acceptance_status="NOT_EVALUATED", evidence=[{"dispatch": "test-only CAS negative control"}],
        )
        assert dispatched["engine_dispatched"] is True
        with pytest.raises(StagePlanStoreConflict, match="cannot certify scientific acceptance"):
            daemon.store.update_stage_attempt(
                project_id, model_ref, second["attempt_id"], expected_version=2,
                status="ACCEPTED", engine_dispatched=True, execution_status="SOLVE_SUCCEEDED",
                acceptance_status="ACCEPTED", evidence=dispatched["evidence"],
            )
        with pytest.raises(StagePlanStoreConflict, match="dispatched"):
            daemon.store.begin_stage_attempt(
                project_id=project_id, model_ref=model_ref, stage_id="preheat", expected_revision=0,
                request_id="attempt-3", operation_id="attempt-operation-3",
                idempotency_key="stage-attempt-3", request_hash="c" * 64,
            )
        with pytest.raises(StagePlanStoreConflict, match="scientifically accepted"):
            daemon.store.begin_stage_attempt(
                project_id=project_id, model_ref=model_ref, stage_id="cooldown", expected_revision=0,
                request_id="successor-attempt", operation_id="successor-operation",
                idempotency_key="successor-attempt", request_hash="d" * 64,
            )
        assert worker.calls == []
        database_path = daemon.store.path
    finally:
        daemon.close()

    reopened = OperationStore(database_path)
    try:
        records = reopened.list_stage_attempts(project_id, model_ref, stage_id="preheat")
        assert [record["status"] for record in records] == ["NOT_DISPATCHED_UNVERIFIED", "RUNNING"]
        assert reopened.get_stage_attempt(project_id, model_ref, first["attempt_id"]) == blocked
        other_model = {**model_ref, "server_instance_id": "different-server"}
        assert reopened.get_stage_attempt(project_id, other_model, first["attempt_id"]) is None
        # Same-session/model-tag identity with another server instance cannot
        # inherit either the plan or attempts.
        with pytest.raises(StagePlanStoreConflict, match="not registered"):
            reopened.begin_stage_attempt(
                project_id=project_id, model_ref=other_model, stage_id="preheat", expected_revision=0,
                request_id="other-server", operation_id="other-server-operation",
                idempotency_key="other-server", request_hash="e" * 64,
            )
    finally:
        reopened.close()


def test_stage_attempt_readback_rejects_record_tamper(tmp_path):
    daemon, _service, _worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        created = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()}, "execution": execution,
        })
        assert created["success"] is True, created
        record, _ = daemon.store.begin_stage_attempt(
            project_id=project_id, model_ref=model_ref, stage_id="preheat", expected_revision=0,
            request_id="tamper-attempt", operation_id="tamper-attempt-operation",
            idempotency_key="tamper-attempt", request_hash="f" * 64,
        )
        daemon.store.db.execute(
            "UPDATE stage_attempts SET status='FAILED' WHERE attempt_id=?", (record["attempt_id"],),
        )
        with pytest.raises(StagePlanStoreConflict, match="columns disagree"):
            daemon.store.get_stage_attempt(project_id, model_ref, record["attempt_id"])
    finally:
        daemon.close()


def test_public_stage_run_direct_and_fallback_persist_no_dispatch_unverified_attempt(tmp_path):
    daemon, _service, worker, project_id, model_ref, execution, host = _setup(tmp_path)
    try:
        defined = _call_public(host, "experiment_stage_define", definition=_plan_v2(), execution=execution)
        assert defined["success"] is True, defined
        run_execution = {**execution, "request_id": "stage-run-1", "idempotency_key": "stage-run-key-1"}
        direct = _call_public(host, "experiment_stage_run", stage_id="preheat", execution=run_execution)
        assert direct["success"] is False, direct
        assert direct["error"]["code"] == "STAGE_PROFILE_UNVERIFIED"
        attempt = direct["data"]["attempt"]
        assert attempt["status"] == "NOT_DISPATCHED_UNVERIFIED"
        assert attempt["project_id"] == project_id and attempt["model_ref"] == model_ref
        assert attempt["plan_id"] == "heat-cycle-v2" and attempt["stage_id"] == "preheat"
        assert attempt["expected_revision"] == 0 and attempt["engine_dispatched"] is False
        assert direct["data"]["worker_rpc_performed"] is False
        assert direct["data"]["solve_started"] is False
        assert len(direct["data"]["missing_evidence"]) == 4
        replay = _call_public(host, "experiment_stage_run", stage_id="preheat", execution=run_execution)
        assert replay == direct

        fallback = _call_public(
            host, "operation_call", operation_id="experiment.stage_run",
            arguments={"stage_id": "preheat"},
            execution={**execution, "request_id": "stage-run-2", "idempotency_key": "stage-run-key-2"},
        )
        assert fallback["success"] is False and fallback["error"]["code"] == "STAGE_PROFILE_UNVERIFIED"
        assert fallback["data"]["attempt"]["attempt_number"] == 2
        assert fallback["data"]["attempt"]["operation_id"] != attempt["operation_id"]
        assert worker.calls == []
        persisted = daemon.store.list_stage_attempts(project_id, model_ref, stage_id="preheat")
        assert [row["status"] for row in persisted] == ["NOT_DISPATCHED_UNVERIFIED", "NOT_DISPATCHED_UNVERIFIED"]
        operation_rows = daemon.store.db.execute(
            "SELECT operation,status,result FROM operations WHERE operation='experiment.stage_run' ORDER BY created_at,operation_id"
        ).fetchall()
        assert len(operation_rows) == 2 and all(row["status"] == "FAILED" for row in operation_rows)
        assert all(json.loads(row["result"])["error"]["code"] == "STAGE_PROFILE_UNVERIFIED" for row in operation_rows)
        database_path = daemon.store.path
    finally:
        daemon.close()

    reopened = OperationStore(database_path)
    try:
        records = reopened.list_stage_attempts(project_id, model_ref, stage_id="preheat")
        assert [row["attempt_number"] for row in records] == [1, 2]
        for record in records:
            assert reopened.get_stage_attempt_for_operation(project_id, model_ref, record["operation_id"]) == record
    finally:
        reopened.close()


def _install_fake_stage_backend(daemon, service, *, mode, monkeypatch=None,
                                dispatched_event=None, release_event=None):
    """Install a test-only fake proof/Worker backend; it is never native proof."""
    from comsol_mcp._w21_stage_backend import ADMISSION_CONTRACT

    backend = daemon.backend
    calls = []

    def fake_admission(*, binding, plan, stage):
        del plan, stage
        return {
            "contract": ADMISSION_CONTRACT,
            "producer": "managed-backend-native-readback",
            "status": "VERIFIED",
            "binding": deepcopy(binding),
            "facts": {
                "source_attempt_binding": "VERIFIED", "target_field_identity": "VERIFIED",
                "source_target_units": "VERIFIED", "source_target_mesh": "VERIFIED",
                "frame_identity": "VERIFIED", "history_identity": "VERIFIED",
            },
            "evidence_refs": [{"kind": "TEST_FIXTURE", "sha256": "e" * 64}],
        }

    backend.stage_native_admission = fake_admission
    backend.stage_output_readback = lambda **_kwargs: None

    def invoke(operation, arguments, execution, operation_id, event_callback):
        calls.append(operation)
        if operation == "run_study" and mode == "before_dispatch":
            current = daemon.store.get_stage_attempt(
                execution["project_id"], execution["model_ref"],
                daemon.store.list_stage_attempts(execution["project_id"], execution["model_ref"])[-1]["attempt_id"],
            )
            assert current["status"] == "DISPATCH_INTENT"
            raise RuntimeError("injected failure after persisted intent and before Worker submission")

        ref = model_ref_from_mapping(execution["model_ref"])

        project = daemon.project_authority.get_project(execution["project_id"])
        save_target = (Path(project["workspace"]) / arguments["path"]).resolve() if operation == "save_model" else None
        backend_binding = {
            "phase": "solve" if operation == "run_study" else "save",
            "model_tag": ref.model_tag,
            "model_handle": "fake-model-handle",
            "worker_generation": 71,
            "save_target_path": str(save_target) if save_target is not None else None,
        }

        def worker_rpc(method, receiver, args, result=None):
            import hashlib

            worker_request_id = f"wrk-{uuid4()}"
            request_hash = hashlib.sha256(json.dumps(
                {"type": "call", "handle": receiver, "generation": 71, "method": method, "args": args},
                sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            common = {
                "request_id": worker_request_id, "kind": "call", "operation_id": operation_id,
                "request_hash": request_hash,
                "metadata": {"type": "call", "request_id": worker_request_id, "handle": receiver,
                             "generation": 71, "method": method, "args": args},
                "w21_backend_binding": backend_binding,
            }
            event_callback({**common, "phase": "submitted"})
            return_value = result
            event_callback({
                **common, "phase": "observed", "status": "ok",
                "reply": {"ok": True, "status": "ok", "result": return_value},
            })

        def fake_study_run():
            worker_rpc("study", "fake-model-handle", [], {
                "$worker_handle": "fake-study-collection", "generation": 71, "java_type": "StudyList",
            })
            worker_rpc("get", "fake-study-collection", [arguments["study_tag"]], {
                "$worker_handle": "fake-study-handle", "generation": 71, "java_type": "Study",
            })
            worker_rpc("label", "fake-study-handle", [], "Study 1")
            worker_rpc("study", "fake-model-handle", [], {
                "$worker_handle": "fake-study-collection", "generation": 71, "java_type": "StudyList",
            })
            worker_rpc("tags", "fake-study-collection", [], [arguments["study_tag"]])
            worker_rpc("study", "fake-model-handle", [arguments["study_tag"]], {
                "$worker_handle": "fake-study-handle", "generation": 71, "java_type": "Study",
            })
            worker_rpc("label", "fake-study-handle", [], "Study 1")
            worker_rpc("study", "fake-model-handle", [arguments["study_tag"]], {
                "$worker_handle": "fake-study-handle", "generation": 71, "java_type": "Study",
            })
            worker_rpc("run", "fake-study-handle", [])

        def fake_model_save():
            from pathlib import Path as LocalPath

            temp_name = f".{save_target.name}.{uuid4().hex}.tmp.mph"
            worker_rpc("save", "fake-model-handle", [str(save_target.parent / temp_name), True])

        def perform(args):
            if operation == "run_study":
                fake_study_run()
                if mode == "after_dispatch":
                    raise RuntimeError("injected loss after Worker submitted solve and before response")
                if mode == "hold_after_dispatch":
                    if dispatched_event is not None:
                        dispatched_event.set()
                    if release_event is None or not release_event.wait(timeout=3):
                        raise RuntimeError("test release did not arrive after Worker submission")
                service.adapter.fingerprint = "after-stage-solve"
                return {"success": True, "data": {"study_tag": args["study_tag"]}}
            if operation == "save_model":
                target = Path(backend.project_root) / args["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"TEST-ONLY-SAVED-MODEL")
                fake_model_save()
                return {"success": True, "data": {"saved_path": str(target)}}
            raise AssertionError(f"unexpected fake backend operation: {operation}")

        result = service.execute_legacy(
            operation, perform, arguments, model_ref=ref,
            expected_revision=execution["expected_revision"],
            request_id=execution["request_id"], session_id=execution["session_id"],
        )
        result["execution"] = {
            **result["execution"], "project_id": execution["project_id"],
        }
        phase = "solve" if operation == "run_study" else "save"
        if mode.startswith(f"bad_{phase}_"):
            field = mode.removeprefix(f"bad_{phase}_")
            reply = result["execution"]
            if field == "project":
                reply["project_id"] = "wrong-project"
            elif field == "session":
                reply["session_id"] = "wrong-session"
            elif field == "model_ref":
                reply["model_ref"] = {**reply["model_ref"], "generation": reply["model_ref"]["generation"] + 1}
            elif field == "revision":
                reply["revision"] += 1
            elif field == "dirty":
                reply["dirty"] = True
            else:
                raise AssertionError(f"unknown fake reply tamper field: {field}")
        return result

    backend.invoke = invoke
    return calls


def _persistent_worker_with_stub(project_root, request):
    """Build the production Worker/RemoteModel client without starting Java."""
    worker = PersistentJavaWorker.__new__(PersistentJavaWorker)
    worker.paths = SimpleNamespace(resolved_project_root=Path(project_root), is_windows=False)
    worker.state_dir = Path(project_root) / ".stub-worker-state"
    worker._token = "offline-test-token"
    worker._process = None
    worker._port = 1
    worker._generation = 71
    worker._classes_dir = None
    worker._lock = threading.RLock()
    worker._known_requests = {}
    worker._next_generation = 72
    worker._on_request_event = None
    worker._operation_context = threading.local()
    worker._request = request
    return worker


def _fake_stage_admission(binding):
    from comsol_mcp._w21_stage_backend import ADMISSION_CONTRACT

    return {
        "contract": ADMISSION_CONTRACT,
        "producer": "managed-backend-native-readback",
        "status": "VERIFIED",
        "binding": deepcopy(binding),
        "facts": {
            "source_attempt_binding": "VERIFIED", "target_field_identity": "VERIFIED",
            "source_target_units": "VERIFIED", "source_target_mesh": "VERIFIED",
            "frame_identity": "VERIFIED", "history_identity": "VERIFIED",
        },
        "evidence_refs": [{"kind": "EXPLICIT_FAKE_BACKEND_FIXTURE", "sha256": "e" * 64}],
    }


@pytest.mark.parametrize("mode,expected_runs,expected_save,expected_dispatched", [
    ("normal", 1, 1, True),
    ("timeout", 1, 0, True),
    ("duplicate_run", 1, 0, True),
    ("wrong_target", 0, 0, False),
    ("wrong_operation", 0, 0, False),
])
def test_stage_uses_real_worker_rpc_identity_and_persists_before_send(
    tmp_path, mode, expected_runs, expected_save, expected_dispatched,
):
    """Exercise submit/RemoteModel/ManagedBackend with only transport stubbed."""
    import comsol_mcp._server as srv
    from comsol_mcp._tools_workflow import _run_study_on_model

    daemon, service, _old_worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    sent = []
    stage_request = f"actual-worker-{mode}"
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        project = daemon.project_authority.get_project(project_id)

        def transport(body, *, timeout_s=None):
            del timeout_s
            sent.append(dict(body))
            request_type = body["type"]
            if request_type == "model":
                return {"ok": True, "status": "OK", "generation": 71,
                        "result": {"$worker_handle": "bound-model-handle", "generation": 71,
                                   "java_type": "Model"}}
            assert request_type == "call", body
            method = body["method"]
            args = body.get("args", [])
            rows = daemon.store.list_stage_attempts(project_id, model_ref)
            attempt = rows[-1]
            if method in {"run", "save"}:
                assert attempt["status"] == "RUNNING" and attempt["engine_dispatched"] is True
                dispatch_rows = [item for item in attempt["evidence"] if item.get("kind") == "worker-dispatch"]
                assert dispatch_rows and dispatch_rows[-1]["worker_request_id"] == body["request_id"]
                assert dispatch_rows[-1]["worker_request_id"] != dispatch_rows[-1]["stage_request_id"]
            elif method in {"study", "get", "tags", "label"} and not attempt["engine_dispatched"]:
                assert attempt["status"] == "DISPATCH_INTENT"
                assert not any(item.get("kind") == "worker-dispatch" for item in attempt["evidence"])

            if method == "study" and not args:
                result = {"$worker_handle": "study-collection", "generation": 71, "java_type": "StudyList"}
            elif method == "study" and args == ["std1"]:
                result = {"$worker_handle": "study-std1", "generation": 71, "java_type": "Study"}
            elif method == "get" and args == ["std1"]:
                result = {"$worker_handle": "study-std1", "generation": 71, "java_type": "Study"}
            elif method == "tags":
                result = ["std1"]
            elif method == "label":
                result = "Study 1"
            elif method == "run":
                if mode == "timeout":
                    service.adapter.fingerprint = "stage-solve-may-have-run"
                    raise JavaWorkerTimeout("offline injected response loss")
                service.adapter.fingerprint = "stage-solve-completed"
                result = None
            elif method == "save":
                assert len(args) == 2 and args[1] is True
                with zipfile.ZipFile(args[0], "w") as archive:
                    archive.writestr("synthetic/fixture.txt", "explicit test fixture only")
                result = None
            else:
                raise AssertionError(f"unexpected Worker request: {body}")
            return {"ok": True, "status": "OK", "generation": 71, "result": result}

        worker = _persistent_worker_with_stub(daemon.backend.project_root, transport)
        daemon.backend.worker = worker
        daemon.backend.stage_native_admission = lambda *, binding, **_kwargs: _fake_stage_admission(binding)
        daemon.backend.stage_output_readback = lambda **_kwargs: None

        def run_callback(args):
            if mode == "wrong_target":
                RemoteJava(worker, "unassociated-study-handle", 71, "Study")._call("run")
            elif mode == "wrong_operation":
                target = Path(project["workspace"]) / "stage_outputs" / "unauthorized.mph"
                srv._current_model.save(str(target))
            else:
                _run_study_on_model(srv._current_model, args["study_tag"])
            if mode == "duplicate_run":
                _run_study_on_model(srv._current_model, args["study_tag"])
            return {"success": True, "data": {"study_tag": args["study_tag"]}}

        def save_callback(args):
            target = Path(args["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            srv._current_model.save(str(target))
            return {"success": True, "data": {"saved_path": str(target)}}

        daemon.backend.registry.update({"run_study": run_callback, "save_model": save_callback})
        result = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": stage_request, "idempotency_key": stage_request},
        })
        rows = daemon.store.list_stage_attempts(project_id, model_ref)
        attempt = rows[-1]
        actual_runs = [item for item in sent if item.get("method") == "run"]
        actual_saves = [item for item in sent if item.get("method") == "save"]
        assert len(actual_runs) == expected_runs, json.dumps({
            "result": result, "attempt": attempt,
            "submitted": [item.get("metadata") for item in sent],
        }, default=str, sort_keys=True)
        assert len(actual_saves) == expected_save
        assert attempt["engine_dispatched"] is expected_dispatched
        assert all(item["request_id"].startswith("wrk-") for item in actual_runs + actual_saves)
        assert all(item["request_id"] != stage_request for item in actual_runs + actual_saves)
        if mode == "normal":
            assert result["success"] is False
            assert result["error"]["code"] == "STAGE_ACCEPTANCE_UNVERIFIED"
            assert attempt["status"] == "SUCCEEDED_PARTIAL"
            assert attempt["acceptance_status"] == "PARTIAL"
            assert len([item for item in sent if item.get("method") in {"study", "get", "tags", "label"}]) >= 5
            assert (Path(project["workspace"]) / "stage_outputs" / f"{attempt['attempt_id']}.mph").is_file()
            dispatch_evidence = [item for item in attempt["evidence"] if item.get("kind") == "worker-dispatch"]
            assert [item["operation"] for item in dispatch_evidence] == ["run_study", "save_model"]
            assert all(item.get("worker_request_hash") and item.get("worker_receiver") for item in dispatch_evidence)
        else:
            assert result["success"] is False and result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
            assert attempt["status"] == "UNKNOWN"
            retry_id = f"{stage_request}-retry"
            retried = daemon.dispatch({
                "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
                "execution": {**execution, "request_id": retry_id, "idempotency_key": retry_id},
            })
            assert retried["success"] is False
            assert len([item for item in sent if item.get("method") == "run"]) == expected_runs
            assert all(item["status"] != "ACCEPTED" for item in daemon.store.list_stage_attempts(project_id, model_ref))
    finally:
        daemon.close()
        if "worker" in locals():
            worker.close()


@pytest.mark.parametrize("mode,expected_dispatched", [
    ("before_dispatch", False),
    ("after_dispatch", True),
])
def test_stage_dispatch_intent_and_unknown_response_are_never_replayed(tmp_path, mode, expected_dispatched):
    daemon, service, _worker, project_id, model_ref, execution, host = _setup(tmp_path)
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        calls = _install_fake_stage_backend(daemon, service, mode=mode)
        first = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": f"fault-{mode}", "idempotency_key": f"fault-{mode}"},
        })
        assert first["success"] is False and first["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        attempt = first["data"]["attempt"]
        assert attempt["status"] == "UNKNOWN"
        assert attempt["engine_dispatched"] is expected_dispatched, json.dumps(attempt["evidence"], indent=2, sort_keys=True)
        assert any(row["kind"] == "dispatch-intent" for row in attempt["evidence"])

        retry = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": f"retry-{mode}", "idempotency_key": f"retry-{mode}"},
        })
        assert retry["success"] is False
        assert retry["error"]["code"] in {"STAGE_ATTEMPT_UNRESOLVED", "REVISION_CONFLICT"}
        assert calls == ["run_study"]
        assert all(row["status"] != "ACCEPTED" for row in daemon.store.list_stage_attempts(project_id, model_ref))
    finally:
        daemon.close()


def test_inflight_stage_reentry_reports_dispatch_without_second_solve(tmp_path):
    daemon, service, _worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    dispatched = threading.Event()
    release = threading.Event()
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        calls = _install_fake_stage_backend(
            daemon, service, mode="hold_after_dispatch",
            dispatched_event=dispatched, release_event=release,
        )
        request = {
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": "stage-inflight", "idempotency_key": "stage-inflight",
                           "rpc_timeout_s": 0.3},
        }
        first = daemon.dispatch(request)
        assert dispatched.wait(timeout=1)
        assert first["success"] is True and first["data"]["engine_dispatched"] is True
        assert first["data"]["stage_attempt_status"] == "RUNNING"

        repeated = daemon.dispatch(request)
        assert repeated["success"] is True and repeated["data"]["engine_dispatched"] is True
        assert repeated["data"]["stage_attempt_id"] == first["data"]["stage_attempt_id"]
        assert calls == ["run_study"]

        release.set()
        for _ in range(200):
            attempt = daemon.store.get_stage_attempt(project_id, model_ref, first["data"]["stage_attempt_id"])
            if attempt and attempt["status"] in {"SUCCEEDED_PARTIAL", "UNKNOWN", "FAILED"}:
                break
            threading.Event().wait(0.01)
        assert attempt is not None and attempt["status"] == "SUCCEEDED_PARTIAL", attempt
        assert calls == ["run_study", "save_model"]
        assert attempt["acceptance_status"] == "PARTIAL"
    finally:
        release.set()
        daemon.close()


def test_stage_save_success_followed_by_hash_failure_is_unknown_and_not_accepted(tmp_path, monkeypatch):
    from comsol_mcp import _w21_stage_backend

    daemon, service, _worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        calls = _install_fake_stage_backend(daemon, service, mode="save_hash_failure")

        def fail_after_save(_path):
            raise OSError("injected artifact-hash failure")

        monkeypatch.setattr(_w21_stage_backend, "hash_saved_artifact", fail_after_save)
        result = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": "fault-save-hash", "idempotency_key": "fault-save-hash"},
        })
        assert result["success"] is False and result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        attempt = result["data"]["attempt"]
        assert attempt["status"] == "UNKNOWN" and attempt["engine_dispatched"] is True
        assert calls == ["run_study", "save_model"]
        assert not any(row["status"] == "ACCEPTED" for row in daemon.store.list_stage_attempts(project_id, model_ref))
        assert list((Path(daemon.project_authority.get_project(project_id)["workspace"]) / "stage_outputs").glob("*.mph"))
    finally:
        daemon.close()


@pytest.mark.parametrize("mutation", ["dirty", "revision"])
def test_stage_ledger_change_during_saved_artifact_hash_blocks_verified_binding(tmp_path, monkeypatch, mutation):
    from comsol_mcp import _w21_stage_backend
    from comsol_mcp._execution_contract import model_ref_from_mapping

    daemon, service, _worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        calls = _install_fake_stage_backend(daemon, service, mode="save_hash_failure")
        original_hash = _w21_stage_backend.hash_saved_artifact

        def mutate_ledger_after_hash(path):
            artifact_hash, size = original_hash(path)
            state = service.ledger._state_for(model_ref_from_mapping(model_ref))
            if mutation == "dirty":
                state.dirty = True
            else:
                state.revision += 1
            return artifact_hash, size

        monkeypatch.setattr(_w21_stage_backend, "hash_saved_artifact", mutate_ledger_after_hash)
        result = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": f"ledger-{mutation}", "idempotency_key": f"ledger-{mutation}"},
        })
        assert result["success"] is False and result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        attempt = result["data"]["attempt"]
        assert attempt["status"] == "UNKNOWN" and attempt["engine_dispatched"] is True
        assert calls == ["run_study", "save_model"]
        assert not any(row["kind"] == "saved-stage-artifact" for row in attempt["evidence"])
        assert all(row["status"] != "ACCEPTED" for row in daemon.store.list_stage_attempts(project_id, model_ref))
    finally:
        daemon.close()


@pytest.mark.parametrize(
    "mode,expected_calls",
    [
        *((f"bad_solve_{field}", ["run_study"]) for field in ("project", "session", "model_ref", "revision")),
        *((f"bad_save_{field}", ["run_study", "save_model"])
          for field in ("project", "session", "model_ref", "revision", "dirty")),
    ],
)
def test_stage_requires_exact_solve_and_save_reply_identity(tmp_path, mode, expected_calls):
    daemon, service, _worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        calls = _install_fake_stage_backend(daemon, service, mode=mode)
        result = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": mode, "idempotency_key": mode},
        })
        assert result["success"] is False and result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        attempt = result["data"]["attempt"]
        assert attempt["status"] == "UNKNOWN" and attempt["engine_dispatched"] is True
        assert calls == expected_calls
        assert not any(row["kind"] == "saved-stage-artifact" for row in attempt["evidence"])
        assert all(row["status"] != "ACCEPTED" for row in daemon.store.list_stage_attempts(project_id, model_ref))
    finally:
        daemon.close()


def test_stage_contradictory_backend_proof_and_caller_proof_cannot_dispatch(tmp_path):
    from comsol_mcp._w21_stage_backend import ADMISSION_CONTRACT

    daemon, _service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()},
            "execution": execution,
        })
        assert defined["success"] is True, defined
        proof_calls = []

        def contradictory(*, binding, **_kwargs):
            proof_calls.append(True)
            wrong = deepcopy(binding)
            wrong["model_ref"]["generation"] += 1
            return {
                "contract": ADMISSION_CONTRACT, "producer": "managed-backend-native-readback",
                "status": "VERIFIED",
                "binding": wrong,
                "facts": {name: "VERIFIED" for name in (
                    "source_attempt_binding", "target_field_identity", "source_target_units",
                    "source_target_mesh", "frame_identity", "history_identity",
                )},
                "evidence_refs": [{"sha256": "f" * 64}],
            }

        daemon.backend.stage_native_admission = contradictory
        daemon.backend.invoke = lambda *_args, **_kwargs: pytest.fail("contradictory backend proof must not dispatch")
        result = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": "proof-mismatch", "idempotency_key": "proof-mismatch"},
        })
        assert result["success"] is False and result["error"]["code"] == "STAGE_PROFILE_UNVERIFIED"
        assert result["data"]["attempt"]["status"] == "NOT_DISPATCHED_UNVERIFIED"
        assert proof_calls == [True] and worker.calls == []
        assert not any(row["status"] == "ACCEPTED" for row in daemon.store.list_stage_attempts(project_id, model_ref))

        caller_supplied = daemon.dispatch({
            "operation": "experiment.stage_run",
            "arguments": {"stage_id": "preheat", "native_admission": {"status": "VERIFIED"}},
            "execution": {**execution, "request_id": "caller-proof", "idempotency_key": "caller-proof"},
        })
        assert caller_supplied["success"] is False
        assert caller_supplied["error"]["code"] in {"INVALID_REQUEST", "UNKNOWN_ARGUMENT"}
        assert worker.calls == []
    finally:
        daemon.close()


def test_public_state_map_direct_and_fallback_read_back_selector_without_solving(tmp_path, monkeypatch):
    daemon, _service, worker, project_id, model_ref, execution, host = _setup(tmp_path)
    model = _StateMapModel()
    sequence_rows = {
        "dataset": "dset1", "solution": "sol2", "binding_complete": True,
        "binding_source": "typed dataset + SolutionInfo.getSolnum(outer, strict)",
        "pair_mapping_complete": True,
        "parameters_complete": True,
        "parameters": {"by_pair": {"1:1": {"names": [], "values": [], "units": [], "solnum": 1}}},
        "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1}],
    }
    monkeypatch.setattr(_w21_execution, "bound_model", lambda _worker, _tag: model)
    monkeypatch.setattr(_w21_execution, "dataset_solution_indices", lambda *_args: sequence_rows)
    # Exact field selector/cleanup and historical mesh association are tested
    # independently in test_w21_canonical_contracts; this route test isolates
    # public direct/fallback plumbing and target Variables readback.
    monkeypatch.setattr(
        _w21_execution, "_strict_source_field_readback",
        lambda *_args: {"status": "VERIFIED", "selection_readback": {"geometry": "geom1"}},
    )
    monkeypatch.setattr(
        _w21_execution, "_read_source_solution_mesh_association",
        lambda *_args: {"status": "VERIFIED", "mesh_tag": "mesh1"},
    )
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_only": True})
    # The G3 route enters the persistent Worker's request-event context, but
    # this synthetic test resolves the COMSOL model/solution helpers locally
    # and must issue no Java Worker RPC.
    monkeypatch.setattr(worker, "operation_context", lambda *_args, **_kwargs: nullcontext())
    try:
        direct = _call_public(
            host, "experiment_state_map", **_state_map_request(), execution=execution,
        )
        assert direct["success"] is True, direct.get("error", direct)
        assert direct["data"]["contract"] == "experiment.state_map/v1"
        assert direct["data"]["coverage_status"] == "PARTIAL"
        assert direct["data"]["target_solver_attachment_readback"]["status"] == "VERIFIED"
        assert direct["data"]["variable_mapping_applied"] is False
        assert direct["data"]["mapping_evidence"]["source_target_units"] == "UNVERIFIED"
        assert direct["data"]["solve_dispatched"] is False
        assert direct["execution"]["revision"] == 1

        fallback = _call_public(
            host,
            "operation_call",
            operation_id="experiment.state_map",
            arguments=_state_map_request(),
            execution={**execution, "request_id": "state-map-fallback", "idempotency_key": "state-map-fallback",
                       "expected_revision": 1},
        )
        assert fallback["success"] is True, fallback
        assert fallback["data"]["coverage_status"] == "PARTIAL"
        assert fallback["data"]["target_solver_attachment_readback"]["unique_attached_solver_tags"] == ["sol3"]
        assert fallback["data"]["solve_dispatched"] is False
        assert fallback["execution"]["revision"] == 2

        feature = model.sequences.get("sol3").features["v1"]
        assert len(feature.set_calls) == 14
        assert model.sequences.get("sol2").run_calls == model.sequences.get("sol3").run_calls == 0
        assert worker.calls == []
    finally:
        daemon.close()


def test_stage_run_refuses_wrong_source_and_unaccepted_predecessor_without_attempt(tmp_path):
    daemon, _service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan_v2()}, "execution": execution,
        })
        assert defined["success"] is True, defined
        wrong_source = daemon.dispatch({
            "operation": "experiment.stage_run",
            "arguments": {"stage_id": "cooldown", "source_attempt_id": "foreign-attempt",
                           "source": {"dataset": "wrong", "solution": "sol1", "outer": [1], "inner": [2]}},
            "execution": {**execution, "request_id": "wrong-source", "idempotency_key": "wrong-source"},
        })
        assert wrong_source["success"] is False
        assert wrong_source["error"]["code"] == "STAGE_SOURCE_SELECTION_MISMATCH"
        no_predecessor = daemon.dispatch({
            "operation": "experiment.stage_run",
            "arguments": {"stage_id": "cooldown", "source_attempt_id": "nonexistent"},
            "execution": {**execution, "request_id": "no-predecessor", "idempotency_key": "no-predecessor"},
        })
        assert no_predecessor["success"] is False
        assert no_predecessor["error"]["code"] == "STAGE_PREDECESSOR_UNAVAILABLE"
        assert daemon.store.list_stage_attempts(project_id, model_ref) == []
        assert worker.calls == []
    finally:
        daemon.close()


def test_stage_run_compute_permission_is_checked_before_attempt_creation(tmp_path):
    daemon, _service, worker, project_id, model_ref, execution, _host = _setup(
        tmp_path, permissions=["inspect", "project_write"],
    )
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": execution,
        })
        assert defined["success"] is True, defined
        denied = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": "compute-denied", "idempotency_key": "compute-denied"},
        })
        assert denied["success"] is False and denied["error"]["code"] == "PERMISSION_DENIED"
        assert daemon.store.list_stage_attempts(project_id, model_ref) == []
        assert worker.calls == []
    finally:
        daemon.close()


def test_stage_run_keeps_v1_declaration_only(tmp_path):
    daemon, _service, worker, project_id, model_ref, execution, _host = _setup(tmp_path)
    try:
        defined = daemon.dispatch({
            "operation": "experiment.stage_define", "arguments": {"definition": _plan()}, "execution": execution,
        })
        assert defined["success"] is True, defined
        result = daemon.dispatch({
            "operation": "experiment.stage_run", "arguments": {"stage_id": "preheat"},
            "execution": {**execution, "request_id": "v1-stage-run", "idempotency_key": "v1-stage-run"},
        })
        assert result["success"] is False
        assert result["error"]["code"] == "STAGE_PLAN_V1_DECLARATION_ONLY"
        assert result["data"]["stage_attempt_created"] is False
        assert daemon.store.list_stage_attempts(project_id, model_ref) == []
        assert worker.calls == []
    finally:
        daemon.close()
