from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

import pytest

from tools import w23_direction_discovery as direction


ROOT = {"segments": []}
COMPONENT = {"segments": [{"collection": "component", "tag": "comp1"}]}
PORT = {"segments": [
    {"collection": "component", "tag": "comp1"},
    {"collection": "physics", "tag": "emw"},
    {"collection": "feature", "tag": "portA"},
]}


def _request(operation_id, arguments):
    return {"operation": "operation_call", "arguments": {
        "operation_id": operation_id, "arguments": arguments,
    }}


def test_only_bounded_read_only_public_node_operations_are_allowed():
    allowed = [
        _request("node.children", direction.children_arguments(ROOT)),
        _request("node.children", direction.children_arguments(ROOT, collection="component")),
        _request("node.children", direction.children_arguments(COMPONENT, collection="physics")),
        _request("node.children", direction.children_arguments(COMPONENT)),
        _request("node.inspect", direction.inspect_arguments(PORT)),
        _request("node.property_schema", direction.property_schema_arguments(PORT, "PortOrientation")),
        _request("node.property_get", direction.property_get_arguments(PORT, "PortOrientation")),
    ]
    assert all(direction._allowed_direction_operation_request(row) for row in allowed)

    denied = [
        {"operation": "study.run", "arguments": {}},
        {"operation": "solver.run", "arguments": {}},
        {"operation": "model.save", "arguments": {}},
        _request("node.property_set", {"path": PORT, "properties": []}),
        _request("result.at_points", {"spec": {}, "points": []}),
        _request("code.execute_java", {"source_artifact": "fixture.java"}),
        _request("node.property_get", {"path": PORT, "names": ["arbitraryMethod"]}),
        _request("node.property_schema", {"path": PORT, "name": "PortExcitation", "include_values": True}),
        _request("node.inspect", {"path": PORT}),
        _request("node.inspect", {"path": PORT, "include_properties": True}),
        _request("node.inspect", {"path": PORT, "include_properties": False, "include_values": True}),
        _request("node.children", {"path": ROOT, "limit": direction.MAX_CHILDREN_PER_LIST,
                                    "cursor": "unreviewed"}),
        _request("node.children", {"path": ROOT, "limit": direction.MAX_CHILDREN_PER_LIST,
                                    "collection": "unreviewed"}),
        _request("node.children", {"path": ROOT, "limit": direction.MAX_CHILDREN_PER_LIST,
                                    "collection": None}),
        _request("node.children", {"path": ROOT, "limit": direction.MAX_CHILDREN_PER_LIST,
                                    "collection": True}),
        _request("node.children", {"path": ROOT, "limit": direction.MAX_CHILDREN_PER_LIST,
                                    "collection": "physics", "unreviewed": True}),
        _request("node.children", {"path": ROOT, "limit": direction.MAX_CHILDREN_PER_LIST + 1}),
        _request("node.children", {"path": {"segments": [{"collection": "component", "tag": "comp2"}]},
                                    "limit": direction.MAX_CHILDREN_PER_LIST}),
    ]
    assert not any(direction._allowed_direction_operation_request(row) for row in denied)
    with pytest.raises(direction.DirectionDiscoveryError):
        direction.children_arguments(ROOT, collection="unreviewed")


def _model_ref():
    return {"schema_version": 1, "session_id": "session-r21",
            "server_instance_id": "server-r21", "model_tag": "mcp1", "generation": 1}


def _science_dispatch_request(operation, arguments, *, project_id="project-r21",
                              model_ref=None, revision=None, timeout_s=30.0):
    from tools import run_native_w23_te_science as science

    class Capture:
        request = None

        def dispatch(self, request):
            self.request = deepcopy(request)
            return {"success": True}

    capture = Capture()
    science._dispatch(capture, operation, arguments, project_id=project_id,
                      ref=model_ref, revision=revision, timeout_s=timeout_s)
    return capture.request


def test_runner_selects_zero_point_direction_guard_without_changing_legacy_point_guard():
    from tools import run_native_w23_saved_mph_diagnostic as runner

    ref = _model_ref()
    read_body = direction.operation_call("node.children", direction.children_arguments(ROOT))
    read = _science_dispatch_request("operation_call", read_body, project_id="project-r21",
                                     model_ref=ref, revision=4, timeout_s=12.5)
    expressions = list(runner.plane_field_expressions("2"))
    point_arguments = {"spec": {"expressions": expressions,
                                 "solution": {"dataset": "dset1", "solution": "sol1"},
                                 "complex_mode": "preserve", "units": list(runner.plane_field_units("2")),
                                 "selection": runner.plane_selection("2"), "entity_dim": 1},
                       "points": runner._public_points(runner._field_points(8.0, runner.protocol.SAVED_MPH_SAMPLE_COUNT)),
                       "coordinate_unit": "um", "frame": "spatial"}
    point_body = {"operation_id": "result.at_points", "arguments": point_arguments}
    point = _science_dispatch_request("operation_call", point_body, project_id="project-r21",
                                     model_ref=ref, revision=4, timeout_s=12.5)
    context = {"expected_project_id": "project-r21", "expected_model_ref": ref,
               "expected_revision": 4}
    assert direction.allowed_direction_dispatch(
        read, expected_project_id="project-r21", expected_model_ref=ref,
        expected_revision=4) is True
    assert runner._allowed_direction_discovery_dispatch(read, **context) is True
    assert runner._allowed_direction_discovery_dispatch(point, **context) is False
    assert runner._allowed_diagnostic_dispatch(point) is True
    assert runner._allowed_direction_discovery_dispatch(
        {"operation": "study.run", "arguments": {}, "execution": read["execution"]},
        **context) is False

    model_path = "/private/tmp/work/inputs/activity009-coarse-phase0-provisional.mph"
    server_connect = _science_dispatch_request(
        "server_connect", {"host": "127.0.0.1", "port": 1234},
        project_id="project-r21", timeout_s=30.0)
    model_load = _science_dispatch_request(
        "model_load", {"path": model_path}, project_id="project-r21", timeout_s=120.0)
    model_inspect = _science_dispatch_request(
        "model.inspect", {"detail": "summary"}, project_id="project-r21",
        model_ref=ref, revision=4, timeout_s=60.0)
    assert runner._allowed_direction_discovery_dispatch(
        server_connect, expected_project_id="project-r21") is True
    assert runner._allowed_direction_discovery_dispatch(
        model_load, expected_project_id="project-r21",
        expected_model_path=Path(model_path)) is True
    assert runner._allowed_direction_discovery_dispatch(
        model_inspect, **context) is True
    bad_connect = deepcopy(server_connect)
    bad_connect["execution"]["rpc_timeout_s"] = 31.0
    assert runner._allowed_direction_discovery_dispatch(
        bad_connect, expected_project_id="project-r21") is False
    assert runner._allowed_direction_discovery_dispatch(
        model_load, expected_project_id="project-r21",
        expected_model_path=Path("/foreign/" + Path(model_path).name)) is False


def test_complete_production_envelope_rejects_malformed_identity_timeouts_and_extras():
    from tools import run_native_w23_saved_mph_diagnostic as runner

    ref = _model_ref()
    body = direction.operation_call("node.children", direction.children_arguments(ROOT))
    valid = _science_dispatch_request("operation_call", body, project_id="project-r21",
                                      model_ref=ref, revision=4, timeout_s=12.5)
    kwargs = {"expected_project_id": "project-r21",
              "expected_model_ref": ref, "expected_revision": 4}
    assert runner._allowed_direction_discovery_dispatch(valid, **kwargs)
    mutations = []
    extra_top = deepcopy(valid); extra_top["source"] = "unreviewed"; mutations.append(extra_top)
    extra_exec = deepcopy(valid); extra_exec["execution"]["extra"] = True; mutations.append(extra_exec)
    bad_project = deepcopy(valid); bad_project["execution"]["project_id"] = "foreign"; mutations.append(bad_project)
    bad_rpc_timeout = deepcopy(valid); bad_rpc_timeout["execution"]["rpc_timeout_s"] = 121.0; mutations.append(bad_rpc_timeout)
    bad_execution_timeout = deepcopy(valid); bad_execution_timeout["execution"]["execution_timeout_s"] = 1.0; mutations.append(bad_execution_timeout)
    bad_queue_timeout = deepcopy(valid); bad_queue_timeout["execution"]["queue_timeout_s"] = 30.0; mutations.append(bad_queue_timeout)
    bad_request_id = deepcopy(valid); bad_request_id["execution"]["request_id"] = "caller-id"; mutations.append(bad_request_id)
    bad_idempotency = deepcopy(valid); bad_idempotency["execution"]["idempotency_key"] = "caller-key"; mutations.append(bad_idempotency)
    bad_session = deepcopy(valid); bad_session["execution"]["session_id"] = "foreign-session"; mutations.append(bad_session)
    bad_revision = deepcopy(valid); bad_revision["execution"]["expected_revision"] = 5; mutations.append(bad_revision)
    bad_ref = deepcopy(valid); bad_ref["execution"]["model_ref"]["model_tag"] = "foreign-model"; mutations.append(bad_ref)
    extra_ref = deepcopy(valid); extra_ref["execution"]["model_ref"]["path"] = "/tmp/model.mph"; mutations.append(extra_ref)
    extra_inner = deepcopy(valid); extra_inner["arguments"]["extra"] = True; mutations.append(extra_inner)
    denied_inner = deepcopy(valid); denied_inner["arguments"]["operation_id"] = "node.property_set"; mutations.append(denied_inner)
    for request in mutations:
        assert not direction.allowed_direction_dispatch(
            request, expected_project_id="project-r21",
            expected_model_ref=ref, expected_revision=4)
        assert not runner._allowed_direction_discovery_dispatch(request, **kwargs)


class _SyntheticModel:
    def __init__(self, tag):
        self._tag = tag

    def tag(self):
        return self._tag


class _SyntheticWorker:
    def __init__(self):
        self.root_model = _SyntheticModel("mcp1")
        self.probe_calls = []

    def client(self):
        return self

    def model(self, tag):
        assert tag == "mcp1"
        return self.root_model

    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server-r21",
                "fingerprint": "synthetic-software-state", "external_event_counter": 0}

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def probe_children(self, node, collections):
        self.probe_calls.append((node, tuple(row["collection"] for row in collections)))
        return {"children": [{"collection": "component", "tag": "comp1"}], "errors": []}


def test_node_inspect_omission_option_keeps_legacy_default_property_enumeration():
    from comsol_mcp._g2_engine import inspect_node

    class Feature:
        def tag(self): return "feat1"
        def label(self): return "Feature"
        def getType(self): return "FeatureType"
        def properties(self): return ["name"]
        def getValueType(self, _name): return "String"
        def getAllowedPropertyValues(self, _name): return None

    class Component:
        def __init__(self): self.feature_node = Feature()
        def feature(self, _tag): return self.feature_node

    class Model:
        def __init__(self): self.component_node = Component()
        def component(self, _tag): return self.component_node

    class Worker:
        def __init__(self): self._model = Model()
        def client(self): return self
        def model(self, _tag): return self._model

    result = inspect_node(Worker(), "mcp1", {
        "segments": [{"collection": "component", "tag": "comp1"},
                     {"collection": "feature", "tag": "feat1"}],
    })
    assert [row["name"] for row in result["properties"]] == ["name"]
    assert result["properties"][0]["metadata_status"] == "KNOWN"
    assert "properties_status" not in result


def test_physics_identifier_engine_failure_is_not_downgraded_to_unavailable():
    from comsol_mcp._execution_contract import ExecutionContractError
    from comsol_mcp._g2_engine import inspect_node

    class Physics:
        def tag(self): return "ewfd"
        def label(self): return "Electromagnetic Waves"
        def getType(self): return "ElectromagneticWaves"
        def identifier(self): raise RuntimeError("synthetic Worker call failure")

    class Component:
        def physics(self, _tag): return Physics()

    class Model:
        def component(self, _tag): return Component()

    class Worker:
        def client(self): return self
        def model(self, _tag): return Model()

    with pytest.raises(ExecutionContractError) as captured:
        inspect_node(Worker(), "mcp1", {
            "segments": [{"collection": "component", "tag": "comp1"},
                         {"collection": "physics", "tag": "ewfd"}],
        }, include_properties=False)
    assert captured.value.code == "ENGINE_CALL_FAILED"


def test_active_node_inspect_catalog_copies_keep_count_and_publish_identity_mode():
    import json

    root = Path(__file__).resolve().parents[1]
    rels = (
        "comsol_mcp/data/g2/02_ACTION_CATALOG.json",
        "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
        "docs/full_project_execution/contract/02_ACTION_CATALOG.json",
    )
    catalogs = [(root / rel).read_bytes() for rel in rels]
    parsed = [json.loads(raw) for raw in catalogs]
    for catalog in parsed:
        assert catalog["operation_count"] == 273
        assert len(catalog["operations"]) == 273
        rows = [row for row in catalog["operations"]
                if row.get("operation_id") == "node.inspect"]
        assert len(rows) == 1
        operation = rows[0]
        assert operation["arguments_signature"] == (
            "path:NodePath,include_values:bool?,include_properties:bool?")
        schema = operation["input_schema"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["include_values"] == {"type": "boolean"}
        assert schema["properties"]["include_properties"] == {"type": "boolean"}
        assert "include_properties" not in schema["required"]
    assert catalogs[0] == catalogs[1]


def test_real_science_dispatch_guard_and_registered_control_daemon_accept_synthetic_read(tmp_path, monkeypatch):
    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._execution_contract import SessionLedger
    from comsol_mcp._execution_service import ExecutionService
    from tools import run_native_w23_saved_mph_diagnostic as runner
    from tools import run_native_w23_te_science as science

    project_root = tmp_path / "projects"
    project_root.mkdir()
    worker = _SyntheticWorker()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root,
                           registry={}, worker=worker)
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "W23 R21 envelope fixture", "workspace": "w23-r21",
                          "policy": {"permissions": ["inspect"]}},
            "execution": {"request_id": "w23-test-project-create",
                          "idempotency_key": "w23-test-project-create"},
        })
        assert created["success"] is True, created
        project_id = created["data"]["project"]["project_id"]
        service = ExecutionService(SessionLedger("session-r21", "server-r21"),
                                   worker, project_root=project_root)
        daemon.backend.service = service
        ref = daemon.service.bind_model("mcp1")["execution"]["model_ref"]
        daemon.backend._bind_model_project(ref, project_id)
        daemon.store.put_metadata(
            "revisions", daemon.backend._model_project_key(ref),
            {"model_ref": ref, "project_id": project_id, "revision": 0})
        assert daemon.backend.model_project_binding(ref) == {
            "attribution": "PROJECT_BOUND", "project_id": project_id}
        monkeypatch.setattr(science, "PROJECT_ID", project_id)
        journal = tmp_path / "managed-dispatch.jsonl"
        runner._set_native_dispatch_guard(
            daemon, journal, direction_only=True,
            expected_model_path=tmp_path / "inputs" / "activity009-coarse-phase0-provisional.mph",
            expected_project_id=project_id)
        runner._bind_direction_dispatch_context(daemon, project_id, ref, 0)

        operation = direction.operation_call("node.children", direction.children_arguments(ROOT))
        response = science._dispatch(
            daemon, "operation_call", operation, project_id=project_id,
            ref=ref, revision=0, timeout_s=7.5)
        assert response["success"] is True, response
        assert response["data"]["children"] == [{"collection": "component", "tag": "comp1"}]
        assert "collection_filter" not in response["data"]
        assert response["execution"]["model_ref"] == ref
        assert response["execution"]["revision"] == 0
        assert len(worker.probe_calls) == 1
        assert len(worker.probe_calls[0][1]) > 1  # omission keeps all-collection probing

        filtered_operation = direction.operation_call(
            "node.children", direction.children_arguments(ROOT, collection="component"))
        filtered_response = science._dispatch(
            daemon, "operation_call", filtered_operation, project_id=project_id,
            ref=ref, revision=0, timeout_s=7.5)
        assert filtered_response["success"] is True, filtered_response
        assert filtered_response["data"]["children"] == [{"collection": "component", "tag": "comp1"}]
        assert filtered_response["data"]["collection_filter"] == "component"
        assert worker.probe_calls[1][1] == ("component",)
        journal_rows = [__import__("json").loads(line) for line in journal.read_text().splitlines()]
        assert len(journal_rows) == 2
        assert all(row["status"] == "DISPATCHED" for row in journal_rows)
        assert all(row["operation"] == "operation_call" for row in journal_rows)

        malformed = []
        extra_execution = _science_dispatch_request(
            "operation_call", operation, project_id=project_id,
            model_ref=ref, revision=0, timeout_s=7.5)
        extra_execution["execution"]["unexpected"] = True
        malformed.append(extra_execution)
        wrong_revision = _science_dispatch_request(
            "operation_call", operation, project_id=project_id,
            model_ref=ref, revision=0, timeout_s=7.5)
        wrong_revision["execution"]["expected_revision"] = 1
        malformed.append(wrong_revision)
        extra_top = _science_dispatch_request(
            "operation_call", operation, project_id=project_id,
            model_ref=ref, revision=0, timeout_s=7.5)
        extra_top["unexpected"] = True
        malformed.append(extra_top)
        extra_inner = _science_dispatch_request(
            "operation_call", operation, project_id=project_id,
            model_ref=ref, revision=0, timeout_s=7.5)
        extra_inner["arguments"]["unexpected"] = True
        malformed.append(extra_inner)
        invalid_filter = _science_dispatch_request(
            "operation_call", {"operation_id": "node.children", "arguments": {
                "path": ROOT, "limit": direction.MAX_CHILDREN_PER_LIST,
                "collection": "unsupported"}}, project_id=project_id,
            model_ref=ref, revision=0, timeout_s=7.5)
        malformed.append(invalid_filter)
        for request in malformed:
            with pytest.raises(RuntimeError, match="managed operation blocked by saved-MPH diagnostic allowlist"):
                daemon.dispatch(request)
        assert len(worker.probe_calls) == 2
        journal_rows = [__import__("json").loads(line) for line in journal.read_text().splitlines()]
        assert len(journal_rows) == 7
        assert all(row["status"] == "DISPATCHED" for row in journal_rows[:2])
        assert all(row["status"] == "BLOCKED_NOT_IN_DIAGNOSTIC_ALLOWLIST" for row in journal_rows[2:])
    finally:
        daemon.close()


def test_schema_presence_and_type_gate_precedes_any_typed_getter():
    absent = direction.schema_read_decision({
        "name": "PortOrientation", "has_property": False, "metadata_status": "ABSENT",
    }, "PortOrientation")
    unknown_presence = direction.schema_read_decision({
        "name": "PortOrientation", "has_property": None, "metadata_status": "UNKNOWN",
    }, "PortOrientation")
    unknown_type = direction.schema_read_decision({
        "name": "PortOrientation", "has_property": True, "metadata_status": "UNKNOWN",
        "value_type": None, "getter": None, "kind": None, "shape_rank": None,
    }, "PortOrientation")
    assert absent["status"] == "PROPERTY_ABSENT" and absent["should_get"] is False
    assert unknown_presence["status"] == "PROPERTY_PRESENCE_UNKNOWN"
    assert unknown_type["status"] == "PROPERTY_TYPE_UNKNOWN"
    assert absent["should_get"] is False
    assert unknown_presence["should_get"] is False
    assert unknown_type["should_get"] is False

    schema = {
        "name": "PortName", "has_property": True, "metadata_status": "KNOWN",
        "value_type": "String", "getter": "getString", "kind": "string",
        "shape_rank": 0, "allowed_values": ["1", "2"],
    }
    decision = direction.schema_read_decision(schema, "PortName")
    assert decision["should_get"] is True
    captured = direction.typed_get_observation({"properties": [
        {"name": "PortName", "value": {"kind": "string", "shape": [], "data": "1"}},
    ]}, "PortName", schema)
    assert captured["typed_value"]["data"] == "1"
    with pytest.raises(direction.DirectionDiscoveryError, match="exactly the requested property"):
        direction.typed_get_observation({"properties": [
            {"name": "PortName", "value": {"kind": "string", "shape": [], "data": "1"}},
            {"name": "PortName", "value": {"kind": "string", "shape": [], "data": "2"}},
        ]}, "PortName", schema)


def test_children_must_be_complete_and_unique_before_tags_are_used():
    response = {"path": COMPONENT, "complete": True, "truncated": False,
                "next_cursor": None, "errors": [], "children": [
                    {"collection": "physics", "tag": "emw"},
                    {"collection": "component", "tag": "comp1"},
                ]}
    assert direction.exact_children(response, path=COMPONENT, collection="physics", maximum=8) == [
        {"collection": "physics", "tag": "emw"}]
    filtered_response = {**response, "collection_filter": "physics",
                         "children": [{"collection": "physics", "tag": "emw"}]}
    assert direction.exact_children(
        filtered_response, path=COMPONENT, collection="physics", maximum=8,
        requested_collection="physics") == [{"collection": "physics", "tag": "emw"}]
    with pytest.raises(direction.DirectionDiscoveryError, match="collection filter"):
        direction.exact_children(
            response, path=COMPONENT, collection="physics", maximum=8,
            requested_collection="physics")
    with pytest.raises(direction.DirectionDiscoveryError, match="collection filter"):
        direction.exact_children(
            filtered_response, path=COMPONENT, collection="physics", maximum=8)
    for invalid in (
        {**response, "complete": False},
        {**response, "truncated": True},
        {**response, "errors": [{"collection": "physics", "code": "ERROR"}]},
        {**response, "children": [{"collection": "physics", "tag": "emw"},
                                   {"collection": "physics", "tag": "emw"}]},
    ):
        with pytest.raises(direction.DirectionDiscoveryError):
            direction.exact_children(invalid, path=COMPONENT, collection="physics", maximum=8)


def test_physics_tag_is_compared_to_field_prefix_without_assuming_a_tag():
    match = direction.correlate_physics_tag("emw", variable_prefix="emw")
    mismatch = direction.correlate_physics_tag("ewfd", variable_prefix="emw")
    assert match["status"] == "TAG_NAMESPACE_MATCH" and match["tag_prefix_equal"] is True
    assert mismatch["status"] == "TAG_NAMESPACE_MISMATCH_UNRESOLVED"
    assert mismatch["physics_tag"] == "ewfd" and mismatch["observed_field_variable_prefix"] == "emw"


def test_physics_identifier_name_scope_is_separate_from_tag_and_fails_closed():
    observed_name = direction.correlate_physics_identity("ewfd", "emw", "OBSERVED")
    assert observed_name["status"] == "IDENTIFIER_SCOPE_MATCH"
    assert observed_name["physics_identifier_semantics"] == "DOCUMENTED_NAME_SCOPE_METADATA_CORRELATION_ONLY"
    assert observed_name["physics_identifier_api_deprecated"] is True
    assert observed_name["physics_identifier_tag_alias_equal"] is False
    assert observed_name["tag_prefix_equal"] is False
    assert observed_name["tag_comparison_status"] == "TAG_NAMESPACE_MISMATCH_UNRESOLVED"
    assert observed_name["identifier_prefix_equal"] is True
    assert observed_name["observed_field_variable_prefix"] == "emw"
    assert direction.physics_namespace_is_resolved(observed_name) is True

    wrong = direction.correlate_physics_identity("ewfd", "wrong", "OBSERVED")
    assert wrong["status"] == "IDENTIFIER_SCOPE_MISMATCH_UNRESOLVED"
    assert wrong["identifier_prefix_equal"] is False
    assert direction.physics_namespace_is_resolved(wrong) is False

    missing = direction.correlate_physics_identity("ewfd", None, "UNAVAILABLE")
    assert missing["status"] == "IDENTIFIER_UNRESOLVED"
    assert missing["identifier_prefix_equal"] is None
    assert direction.physics_namespace_is_resolved(missing) is False

    duplicate = direction.correlate_physics_identity(
        "ewfd", "emw", "OBSERVED",
        physics_identifier_inventory=[
            {"identifier": "emw", "identifier_status": "OBSERVED"},
            {"identifier": "emw", "identifier_status": "OBSERVED"},
        ])
    assert duplicate["status"] == "IDENTIFIER_SCOPE_AMBIGUOUS_UNRESOLVED"
    assert duplicate["physics_identifier_inventory_status"] == "AMBIGUOUS"
    assert direction.physics_namespace_is_resolved(duplicate) is False

    incomplete = direction.correlate_physics_identity(
        "ewfd", "emw", "OBSERVED",
        physics_identifier_inventory=[
            {"identifier": "emw", "identifier_status": "OBSERVED"},
            {"identifier": None, "identifier_status": "UNAVAILABLE"},
        ])
    assert incomplete["status"] == "IDENTIFIER_INVENTORY_INCOMPLETE_UNRESOLVED"
    assert direction.physics_namespace_is_resolved(incomplete) is False

    alias = direction.correlate_physics_identity("ewfd", "ewfd", "OBSERVED")
    assert alias["status"] == "IDENTIFIER_SCOPE_MISMATCH_UNRESOLVED"
    assert alias["physics_identifier_tag_alias_equal"] is True


def test_direction_plan_freezes_zero_solve_and_zero_point_read_budget():
    plan = {
        "mode": "direction_discovery_only",
        "max_public_operation_calls": direction.MAX_DIRECTION_CALLS,
        "max_children_per_list": direction.MAX_CHILDREN_PER_LIST,
        "allowed_operation_call_ids": sorted(direction.READONLY_OPERATION_IDS),
        "Study.run": 0, "solver.run": 0, "result.at_points": 0,
    }
    direction.validate_direction_limits(plan)
    tampered = deepcopy(plan)
    tampered["result.at_points"] = 1
    with pytest.raises(direction.DirectionDiscoveryError):
        direction.validate_direction_limits(tampered)


def test_readonly_graph_resolves_exact_port_bma_store_solution_and_dataset_links():
    component = COMPONENT
    physics = {"segments": COMPONENT["segments"] + [{"collection": "physics", "tag": "emw"}]}
    study = {"segments": [{"collection": "study", "tag": "std1"}]}
    solver = {"segments": [{"collection": "sol", "tag": "sol1"}]}
    eigen = {"segments": solver["segments"] + [{"collection": "feature", "tag": "eig1"}]}
    port1 = {"segments": physics["segments"] + [{"collection": "feature", "tag": "port1"}]}
    port2 = {"segments": physics["segments"] + [{"collection": "feature", "tag": "port2"}]}
    bma = {"segments": study["segments"] + [{"collection": "feature", "tag": "bma1"}]}
    step = {"segments": solver["segments"] + [{"collection": "feature", "tag": "step1"}]}
    store = {"segments": solver["segments"] + [{"collection": "feature", "tag": "store1"}]}
    result = {"segments": [{"accessor": "result"}]}
    dataset = {"segments": [{"accessor": "result"},
                              {"collection": "dataset", "tag": "dset1"}]}

    children = {
        (): [{"collection": "component", "tag": "comp1"},
             {"collection": "study", "tag": "std1"},
             {"collection": "sol", "tag": "sol1"}],
        tuple((row["collection"], row["tag"]) for row in component["segments"]):
            [{"collection": "physics", "tag": "emw"}],
        tuple((row.get("collection", "accessor"), row.get("tag", row.get("accessor")))
              for row in physics["segments"]):
            [{"collection": "feature", "tag": "port1"}, {"collection": "feature", "tag": "port2"}],
        (("study", "std1"),): [{"collection": "feature", "tag": "bma1"}],
        # Display rows are tag-sorted; native sequence order is carried only
        # by the explicit ModelEntityList.index witness below.
        (("sol", "sol1"),): [{"collection": "feature", "tag": "eig1"},
                              {"collection": "feature", "tag": "step1"},
                              {"collection": "feature", "tag": "store1"}],
        (("sol", "sol1"), ("feature", "step1")): [],
        (("sol", "sol1"), ("feature", "store1")): [],
        (("accessor", "result"),): [{"collection": "dataset", "tag": "dset1"}],
    }

    def key(path):
        return tuple(("accessor", row["accessor"]) if "accessor" in row
                     else (row["collection"], row["tag"]) for row in path["segments"])

    types = {
        key(physics): ("emw", "ElectromagneticWaves"),
        key(solver): ("sol1", "SolverSequence"),
        key(eigen): ("eig1", "Eigenvalue"),
        key(port1): ("port1", "Port"), key(port2): ("port2", "Port"),
        key(bma): ("bma1", "BoundaryModeAnalysis"),
        key(step): ("step1", "StudyStep"), key(store): ("store1", "StoreSolution"),
        key(dataset): ("dset1", "Solution"),
    }
    values = {
        (key(port1), "PortName"): "1", (key(port2), "PortName"): "2",
        (key(bma), "PortName"): "1", (key(bma), "modeFreq"): "2.45[GHz]",
        (key(step), "study"): "std1", (key(step), "studystep"): "bma1",
        (key(store), "sol"): "sol1", (key(dataset), "solution"): "sol1",
    }
    calls = []

    def call(operation_id, arguments):
        calls.append((operation_id, deepcopy(arguments)))
        path = arguments.get("path")
        path_key = key(path)
        if operation_id == "node.children":
            entries = children.get(path_key, [])
            selected_collection = arguments.get("collection")
            if selected_collection is not None:
                entries = [row for row in entries if row["collection"] == selected_collection]
            response = {"path": path, "children": deepcopy(entries), "complete": True,
                        "truncated": False, "next_cursor": None, "errors": []}
            if selected_collection is not None:
                response["collection_filter"] = selected_collection
            if path_key == key(solver) and selected_collection == "feature":
                tags = ["step1", "eig1", "store1"]
                indexes = {"step1": 0, "eig1": 1, "store1": 2}
                sample = [{"tag": tag, "index": indexes[tag]} for tag in tags]
                response["collection_order"] = {
                    "schema": "COMSOL_MODEL_ENTITY_LIST_INDEX_ORDER_WITNESS_V1",
                    "order_status": "VERIFIED_STABLE_TWO_SAMPLES",
                    "path": solver, "collection": "feature", "max_members": 500,
                    "sample_atomicity": "TWO_STABLE_SAMPLES_NOT_ATOMIC_TO_OTHER_CLIENTS",
                    "receiver_reused_for_all_reads": True,
                    "receiver": {"interface": "com.comsol.model.ModelEntityList",
                                 "method": "index", "parameters": ["java.lang.String"],
                                 "returns": "int", "runtime_version": "6.4.0.293"},
                    "tags_before": tags, "index_samples": [deepcopy(sample), deepcopy(sample)],
                    "tags_after": tags, "tag_indices": sample, "ordered_tags": tags,
                    "provenance_failure": None,
                }
            return response
        if operation_id == "node.inspect":
            tag, type_id = types[path_key]
            response = {"path": path, "tag": tag, "type_id": type_id,
                        "properties_requested": False, "properties_status": "NOT_REQUESTED"}
            if path_key == key(physics):
                response.update({"identifier": "emw", "identifier_status": "OBSERVED",
                                 "identifier_unavailable_reason": None})
            return response
        name = arguments.get("name") if operation_id == "node.property_schema" else arguments["names"][0]
        observed = values.get((path_key, name))
        if operation_id == "node.property_schema":
            if observed is None:
                return {"name": name, "has_property": False, "metadata_status": "ABSENT",
                        "value_type": None, "getter": None, "kind": None, "shape_rank": None,
                        "allowed_values": None}
            return {"name": name, "has_property": True, "metadata_status": "KNOWN",
                    "value_type": "String", "getter": "getString", "kind": "string",
                    "shape_rank": 0, "allowed_values": None}
        assert observed is not None
        return {"path": path, "properties": [{"name": name, "value": {
            "kind": "string", "shape": [], "data": observed,
            "java_signature": "public java.lang.String getString(java.lang.String)"}}]}

    graph = direction.discover_direction_graph(call)
    inspect_paths = [arguments["path"] for operation, arguments in calls if operation == "node.inspect"]
    assert dataset in inspect_paths
    assert not any(any(segment.get("accessor") == "dataset" for segment in path["segments"])
                   for path in inspect_paths)
    assert graph["electromagnetic_waves"]["namespace_correlation"]["status"] == "IDENTIFIER_SCOPE_MATCH"
    assert graph["port_name_binding_status"] == {
        "1": "UNIQUE_EXACT_PORT_NAME", "2": "UNIQUE_EXACT_PORT_NAME"}
    assert graph["bma_solution_bindings"][0]["status"] == "UNIQUE_EXACT_LINK"
    assert graph["bma_solution_bindings"][0]["dataset_matches"][0]["tag"] == "dset1"
    assert graph["solver_sequences"][0]["feature_order"]["order_status"] == "VERIFIED_STABLE_TWO_SAMPLES"
    assert graph["solver_sequences"][0]["feature_order"]["ordered_tags"] == ["step1", "eig1", "store1"]
    assert graph["bma_solution_bindings"][0]["order_link_evidence"]["status"] == "ORDERED_STORE_LINK_PROVEN"
    interval = graph["bma_solution_bindings"][0]["order_link_evidence"]["study_step_intervals"][0]
    assert interval["eigenvalue_interval"] == [{"tag": "eig1", "index": 1}]
    assert interval["store_solution_tag"] == "store1" and interval["store_solution_index"] == 2
    assert interval["nearest_preceding_study_step_tag"] == "step1"
    children_requests = [args for op, args in calls if op == "node.children"]
    assert [args["collection"] for args in children_requests[:3]] == [
        "component", "study", "sol"]
    assert all("collection" in args for args in children_requests)
    assert all(args["limit"] == direction.MAX_CHILDREN_PER_LIST for args in children_requests)
    assert not any(op in {"node.property_set", "result.at_points", "study.run", "solver.run", "model.save"}
                   for op, _ in calls)
    assert len(calls) <= direction.MAX_DIRECTION_CALLS


@pytest.mark.parametrize("case", [
    "eigenvalue_after_store", "missing_eigenvalue", "multiple_stores",
    "copy_solution_in_interval", "next_study_step_before_store", "order_unverified",
])
def test_store_solution_link_stays_unproven_without_unique_step_eigenvalue_store_interval(case):
    step = {"solver_tag": "sol1", "tag": "step1", "type_id": "StudyStep",
            "depth": 1, "collection_order_index": 0}
    next_step = {"solver_tag": "sol1", "tag": "step2", "type_id": "StudyStep",
                 "depth": 1, "collection_order_index": 2}
    store = {"solver_tag": "sol1", "tag": "store1", "type_id": "StoreSolution",
             "depth": 1, "collection_order_index": 2}
    eigen = {"solver_tag": "sol1", "tag": "eig1", "type_id": "Eigenvalue",
             "depth": 1, "collection_order_index": 1}
    copy_solution = {"solver_tag": "sol1", "tag": "copy1", "type_id": "CopySolution",
                     "depth": 1, "collection_order_index": 3}
    if case == "eigenvalue_after_store":
        store["collection_order_index"] = 1
        eigen["collection_order_index"] = 2
        ordered_tags = ["step1", "store1", "eig1"]
    elif case == "missing_eigenvalue":
        eigen = None
        ordered_tags = ["step1", "store1"]
        store["collection_order_index"] = 1
    elif case == "multiple_stores":
        second_store = {**store, "tag": "store2", "collection_order_index": 3}
        ordered_tags = ["step1", "eig1", "store1", "store2"]
    elif case == "copy_solution_in_interval":
        copy_solution["collection_order_index"] = 3
        ordered_tags = ["step1", "eig1", "store1", "copy1"]
    elif case == "next_study_step_before_store":
        next_step["collection_order_index"] = 2
        store["collection_order_index"] = 3
        ordered_tags = ["step1", "eig1", "step2", "store1"]
    else:
        ordered_tags = ["step1", "eig1", "store1"]
    if case == "multiple_stores":
        store_rows = [store, second_store]
    elif case == "copy_solution_in_interval":
        store_rows = [store, copy_solution]
    else:
        store_rows = [store]
    all_steps = [step, next_step] if case == "next_study_step_before_store" else [step]
    eigen_rows = [] if eigen is None else [eigen]
    order_status = "UNVERIFIED" if case == "order_unverified" else "VERIFIED_STABLE_TWO_SAMPLES"
    feature_orders = {"sol1": {"order_status": order_status,
                                "ordered_tags": ordered_tags,
                                "sample_atomicity": "TWO_STABLE_SAMPLES_NOT_ATOMIC_TO_OTHER_CLIENTS"}}
    linked, evidence = direction._ordered_store_solution_links(
        [step], all_steps, store_rows, eigen_rows, feature_orders)
    assert linked == []
    assert evidence["status"] == "UNRESOLVED_ORDER_OR_AMBIGUITY"


def test_store_solution_dataset_reference_must_be_unique_after_order_proof():
    def text_value(value):
        return {"typed_get": {"typed_value": {"kind": "string", "shape": [], "data": value}}}

    store = {"properties": {"sol": text_value("sol2")}}
    dataset1 = {"tag": "dset2", "properties": {"solution": text_value("sol2")}}
    dataset2 = {"tag": "dset2_duplicate", "properties": {"solution": text_value("sol2")}}
    order = {"status": "ORDERED_STORE_LINK_PROVEN"}
    assert direction._datasets_for_store_solution(store, [dataset1]) == [dataset1]
    unique_links = direction._datasets_for_store_solution(store, [dataset1])
    assert direction._bma_solution_binding_status([{"tag": "step1"}], [store], unique_links, order) == "UNIQUE_EXACT_LINK"
    ambiguous_links = direction._datasets_for_store_solution(store, [dataset1, dataset2])
    assert len(ambiguous_links) == 2
    assert direction._bma_solution_binding_status([{"tag": "step1"}], [store], ambiguous_links, order) == "UNRESOLVED_ZERO_OR_AMBIGUOUS_LINK"
    assert direction._bma_solution_binding_status([{"tag": "step1"}], [store], unique_links,
                                                  {"status": "UNRESOLVED_ORDER_OR_AMBIGUITY"}) == "UNRESOLVED_ZERO_OR_AMBIGUOUS_LINK"


def test_result_dataset_accessor_must_be_exact_and_complete():
    body = {"path": {"segments": [{"accessor": "result"}]},
            "children": [{"collection": "dataset", "tag": None, "accessor": True}],
            "complete": True, "truncated": False, "next_cursor": None, "errors": []}
    assert direction.exact_accessor(body, path={"segments": [{"accessor": "result"}]},
                                    collection="dataset", maximum=32)["accessor"] is True
    filtered = {**body, "collection_filter": "dataset"}
    assert direction.exact_accessor(
        filtered, path={"segments": [{"accessor": "result"}]},
        collection="dataset", maximum=32,
        requested_collection="dataset")["accessor"] is True
    with pytest.raises(direction.DirectionDiscoveryError, match="collection filter"):
        direction.exact_accessor(
            body, path={"segments": [{"accessor": "result"}]},
            collection="dataset", maximum=32,
            requested_collection="dataset")
    with pytest.raises(direction.DirectionDiscoveryError):
        direction.exact_accessor({**body, "complete": False},
                                 path={"segments": [{"accessor": "result"}]},
                                 collection="dataset", maximum=32)


@pytest.mark.parametrize("bad_execution", [None, [], "invalid", 4])
def test_model_inspect_guard_refuses_non_mapping_execution_without_exception(bad_execution):
    from tools import run_native_w23_saved_mph_diagnostic as runner

    request = {"operation": "model.inspect", "arguments": {"detail": "summary"},
               "execution": bad_execution}
    assert runner._allowed_direction_discovery_dispatch(
        request, expected_project_id="project-r21",
        expected_model_ref=_model_ref(), expected_revision=4) is False


def _load_session_server_control_helpers():
    import importlib.util

    path = Path(__file__).with_name("test_session_server_control.py")
    spec = importlib.util.spec_from_file_location("w23_r21_session_server_control_fixtures", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("existing managed-session fixture module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_science_dispatch_uses_actual_registered_session_runtime_after_public_model_load(
        tmp_path, monkeypatch):
    import zipfile
    from types import SimpleNamespace

    from tools import run_native_w23_saved_mph_diagnostic as runner
    from tools import run_native_w23_te_science as science

    fixtures = _load_session_server_control_helpers()
    worker_process = fixtures._sleeping_child()
    daemon = None
    server_command = None
    original_dispatch = None
    started = None
    connected = None
    disconnected = False
    stopped = False
    project_id = None
    session_id = None

    class SyntheticPhysics:
        # Mirrors COMSOL Physics: identity getters exist, while
        # PropFeature.properties() is deliberately absent.
        def __init__(self):
            self.calls = []
            self.identifier_value = "emw"
            self.identifier_available = True

        def tag(self):
            self.calls.append("tag")
            return "ewfd"

        def label(self):
            self.calls.append("label")
            return "Electromagnetic Waves"

        def getType(self):
            self.calls.append("getType")
            return "ElectromagneticWaves"

        def identifier(self):
            self.calls.append("identifier")
            if not self.identifier_available:
                raise AttributeError("identifier unavailable in this fixture")
            return self.identifier_value

    physics_node = SyntheticPhysics()

    class SyntheticComponent:
        def physics(self, tag):
            assert tag == "ewfd"
            return physics_node

    class LoadedModel(_SyntheticModel):
        def component(self, tag):
            assert tag == "comp1"
            return SyntheticComponent()

    class LoadedDirectionWorker(fixtures._AttachedWorker):
        def __init__(self, process):
            super().__init__(process)
            self._loaded_models = {}
            self.probe_calls = []

        def tags(self):
            return sorted(self._loaded_models)

        def load(self, path):
            assert Path(path).suffix == ".mph"
            tag = "mcp1"
            assert tag not in self._loaded_models
            model = LoadedModel(tag)
            self._loaded_models[tag] = model
            return SimpleNamespace(java=SimpleNamespace(tag=lambda: tag))

        def backend_snapshot(self, tag):
            assert tag in self._loaded_models
            return {
                "model_tag": tag,
                "server_instance_id": self._meta["server"],
                "generation": self._meta["generation"],
                "instance_id": self._meta["instance_id"],
                "fingerprint": "registered-session-r21-synthetic-model-v1",
                "external_event_counter": 0,
            }

        def model(self, tag):
            return self._loaded_models[tag]

        def probe_children(self, node, collections):
            self.probe_calls.append((node, tuple(row["collection"] for row in collections)))
            return {"children": [{"collection": "component", "tag": "comp1"}], "errors": []}

    worker = LoadedDirectionWorker(worker_process)
    try:
        daemon, project_id, server_command, _installed_xml, _installed_hash = fixtures._make_daemon(
            tmp_path, monkeypatch, worker=worker,
        )
        project_workspace = tmp_path / "workspaces" / "owned-server"
        inputs = project_workspace / "inputs"
        inputs.mkdir()
        model_file = inputs / "registered-source.mph"
        with zipfile.ZipFile(model_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("model.xml", b"synthetic MPH container used only by the registered Worker fixture")

        started = fixtures._start(daemon, project_id, key="w23-r21-public-lifecycle-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        endpoint = started["data"]["endpoint"]
        connected = daemon.dispatch({
            "operation": "session.connect",
            "arguments": {
                "project_id": project_id, "session_id": session_id,
                "runtime_id": "fixture-runtime", "endpoint": endpoint,
                "idempotency_key": "w23-r21-public-lifecycle-connect",
            },
            "execution": {},
        })
        assert connected["success"] is True, connected

        register_key = "w23-r21-public-lifecycle-artifact"
        registered = daemon.dispatch({
            "operation": "artifact.register",
            "arguments": {
                "project_id": project_id, "idempotency_key": register_key,
                "request_id": register_key + "-request", "path": "inputs/registered-source.mph",
                "role": "saved_model", "classification": "project_local",
                "artifact_type": "application/vnd.comsol.mph",
                "format_version": "comsol-mph-savecopy/1",
            },
            "execution": {"project_id": project_id, "session_id": session_id,
                          "idempotency_key": register_key,
                          "request_id": register_key + "-request"},
        })
        assert registered["success"] is True, registered
        load_key = "w23-r21-public-lifecycle-model-load"
        loaded = daemon.dispatch({
            "operation": "model.load",
            "arguments": {"artifact_id": registered["data"]["artifact_id"],
                          "path_policy": {"mode": "registered_artifact"}},
            "execution": {"project_id": project_id, "session_id": session_id,
                          "idempotency_key": load_key, "request_id": load_key + "-request"},
        })
        assert loaded["success"] is True, loaded
        model_ref = loaded["execution"]["model_ref"]
        revision = loaded["execution"]["revision"]
        context = daemon.session_registry.get(project_id, session_id)
        backend = daemon._session_backends[(project_id, session_id)]
        assert context is not None
        assert context.backend is backend
        assert context.worker is worker
        assert context.service is backend.service
        assert loaded["data"]["model_ref"] == model_ref
        assert backend.model_project_binding(model_ref) == {
            "attribution": "PROJECT_BOUND", "project_id": project_id}
        assert model_ref["model_tag"] == "mcp1" and revision == 0

        monkeypatch.setattr(science, "PROJECT_ID", project_id)
        journal = tmp_path / "registered-session-guard.jsonl"
        original_dispatch = daemon.dispatch
        runner._set_native_dispatch_guard(
            daemon, journal, direction_only=True,
            expected_model_path=model_file, expected_project_id=project_id)
        runner._bind_direction_dispatch_context(daemon, project_id, model_ref, revision)

        operation = direction.operation_call("node.children", direction.children_arguments(ROOT))
        response = science._dispatch(
            daemon, "operation_call", operation, project_id=project_id,
            ref=model_ref, revision=revision, timeout_s=7.5)
        assert response["success"] is True, response
        assert response["data"]["children"] == [{"collection": "component", "tag": "comp1"}]
        assert "collection_filter" not in response["data"]
        assert response["execution"]["model_ref"] == model_ref
        assert response["execution"]["revision"] == revision
        assert len(worker.probe_calls) == 1
        assert len(worker.probe_calls[0][1]) > 1  # legacy default still probes all collections

        filtered_operation = direction.operation_call(
            "node.children", direction.children_arguments(ROOT, collection="component"))
        filtered_response = science._dispatch(
            daemon, "operation_call", filtered_operation, project_id=project_id,
            ref=model_ref, revision=revision, timeout_s=7.5)
        assert filtered_response["success"] is True, filtered_response
        assert filtered_response["data"]["children"] == [{"collection": "component", "tag": "comp1"}]
        assert filtered_response["data"]["collection_filter"] == "component"
        assert worker.probe_calls[1][1] == ("component",)

        physics_path = {"segments": [
            {"collection": "component", "tag": "comp1"},
            {"collection": "physics", "tag": "ewfd"},
        ]}
        inspect_operation = direction.operation_call(
            "node.inspect", direction.inspect_arguments(physics_path))
        inspected = science._dispatch(
            daemon, "operation_call", inspect_operation, project_id=project_id,
            ref=model_ref, revision=revision, timeout_s=7.5)
        assert inspected["success"] is True, inspected
        identity = inspected["data"]
        assert identity["path"] == physics_path
        assert (identity["tag"], identity["label"], identity["type_id"]) == (
            "ewfd", "Electromagnetic Waves", "ElectromagneticWaves")
        assert identity["properties_requested"] is False
        assert identity["properties_status"] == "NOT_REQUESTED"
        assert "properties" not in identity and "values" not in identity
        assert identity["identifier"] == "emw"
        assert identity["identifier_status"] == "OBSERVED"
        assert direction.correlate_physics_identity(
            identity["tag"], identity["identifier"], identity["identifier_status"]
        )["status"] == "IDENTIFIER_SCOPE_MATCH"
        assert physics_node.calls == ["tag", "label", "getType", "identifier"]

        physics_node.identifier_available = False
        unavailable = science._dispatch(
            daemon, "operation_call", inspect_operation, project_id=project_id,
            ref=model_ref, revision=revision, timeout_s=7.5)
        assert unavailable["success"] is True, unavailable
        unavailable_identity = unavailable["data"]
        assert unavailable_identity["identifier"] is None
        assert unavailable_identity["identifier_status"] == "UNAVAILABLE"
        assert direction.correlate_physics_identity(
            unavailable_identity["tag"], unavailable_identity["identifier"],
            unavailable_identity["identifier_status"]
        )["status"] == "IDENTIFIER_UNRESOLVED"

        physics_node.identifier_available = True
        physics_node.identifier_value = "ewfd"
        wrong_identifier = science._dispatch(
            daemon, "operation_call", inspect_operation, project_id=project_id,
            ref=model_ref, revision=revision, timeout_s=7.5)
        assert wrong_identifier["success"] is True, wrong_identifier
        wrong_identity = wrong_identifier["data"]
        assert direction.correlate_physics_identity(
            wrong_identity["tag"], wrong_identity["identifier"], wrong_identity["identifier_status"]
        )["status"] == "IDENTIFIER_SCOPE_MISMATCH_UNRESOLVED"

        # Use the same registered ControlDaemon route without the outer
        # diagnostic guard so the Core registry itself rejects this pair.
        bad_inspect = {"operation_id": "node.inspect", "arguments": {
            "path": physics_path, "include_properties": False, "include_values": True,
        }}
        bad_request = _science_dispatch_request(
            "operation_call", bad_inspect, project_id=project_id,
            model_ref=model_ref, revision=revision, timeout_s=7.5)
        before_calls = list(physics_node.calls)
        bad_response = original_dispatch(bad_request)
        assert bad_response["success"] is False, bad_response
        assert bad_response["error"]["code"] == "INVALID_REQUEST", bad_response
        assert physics_node.calls == before_calls

        for bad_flag in (1, "false"):
            bad_inspect = {"operation_id": "node.inspect", "arguments": {
                "path": physics_path, "include_properties": bad_flag,
            }}
            bad_request = _science_dispatch_request(
                "operation_call", bad_inspect, project_id=project_id,
                model_ref=model_ref, revision=revision, timeout_s=7.5)
            bad_response = original_dispatch(bad_request)
            assert bad_response["success"] is False, bad_response
            assert bad_response["error"]["code"] == "INVALID_REQUEST", bad_response
            assert physics_node.calls == before_calls

        wrong_project = "foreign-project"
        with pytest.raises(RuntimeError, match="managed operation blocked by saved-MPH diagnostic allowlist"):
            science._dispatch(daemon, "operation_call", operation, project_id=wrong_project,
                              ref=model_ref, revision=revision, timeout_s=7.5)
        wrong_ref = dict(model_ref)
        wrong_ref["model_tag"] = "foreign-model"
        with pytest.raises(RuntimeError, match="managed operation blocked by saved-MPH diagnostic allowlist"):
            science._dispatch(daemon, "operation_call", operation, project_id=project_id,
                              ref=wrong_ref, revision=revision, timeout_s=7.5)
        with pytest.raises(RuntimeError, match="managed operation blocked by saved-MPH diagnostic allowlist"):
            science._dispatch(daemon, "operation_call", operation, project_id=project_id,
                              ref=model_ref, revision=revision + 1, timeout_s=7.5)

        extra_envelope = _science_dispatch_request(
            "operation_call", operation, project_id=project_id,
            model_ref=model_ref, revision=revision, timeout_s=7.5)
        extra_envelope["execution"]["unreviewed"] = True
        with pytest.raises(RuntimeError, match="managed operation blocked by saved-MPH diagnostic allowlist"):
            daemon.dispatch(extra_envelope)
        assert len(worker.probe_calls) == 2
        rows = [__import__("json").loads(line) for line in journal.read_text().splitlines()]
        assert [row["status"] for row in rows] == [
            "DISPATCHED", "DISPATCHED", "DISPATCHED", "DISPATCHED", "DISPATCHED",
            "BLOCKED_NOT_IN_DIAGNOSTIC_ALLOWLIST",
            "BLOCKED_NOT_IN_DIAGNOSTIC_ALLOWLIST", "BLOCKED_NOT_IN_DIAGNOSTIC_ALLOWLIST",
            "BLOCKED_NOT_IN_DIAGNOSTIC_ALLOWLIST",
        ]
        assert all(row["operation"] == "operation_call" for row in rows)

        daemon.dispatch = original_dispatch
        retired = daemon.dispatch({
            "operation": "session.disconnect",
            "arguments": {"project_id": project_id, "session_id": session_id,
                          "idempotency_key": "w23-r21-public-lifecycle-disconnect",
                          "retire_worker": True},
            "execution": {},
        })
        assert retired["success"] is True, retired
        assert retired["data"]["worker_retirement"]["child_reaped"] is True
        disconnected = True
        stopped_response = fixtures._stop(
            daemon, project_id, session_id, key="w23-r21-public-lifecycle-stop")
        assert stopped_response["success"] is True, stopped_response
        assert stopped_response["data"]["stop_evidence"]["child_reaped"] is True
        assert stopped_response["data"]["stop_evidence"]["listener_absent"] is True
        stopped = True
        assert worker_process.poll() is not None
        assert server_command.processes[0].poll() is not None
    finally:
        if daemon is not None:
            if original_dispatch is not None:
                daemon.dispatch = original_dispatch
            if project_id is not None and session_id is not None and connected is not None and not disconnected:
                try:
                    daemon.dispatch({
                        "operation": "session.disconnect",
                        "arguments": {"project_id": project_id, "session_id": session_id,
                                      "idempotency_key": "w23-r21-public-lifecycle-disconnect",
                                      "retire_worker": True},
                        "execution": {},
                    })
                except Exception:
                    pass
            if project_id is not None and session_id is not None and started is not None and not stopped:
                try:
                    fixtures._stop(daemon, project_id, session_id,
                                   key="w23-r21-public-lifecycle-stop")
                except Exception:
                    pass
            daemon.close()
        exact_processes = [worker_process]
        if server_command is not None:
            exact_processes.extend(server_command.processes)
        for process in exact_processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


# Exact R28 public native response at call151; retained as a regression input.
_CAPTURED_R28_DATASET_BYTES = b'{\n  "children": [\n    {\n      "collection": "dataset",\n      "tag": "dset1"\n    },\n    {\n      "collection": "dataset",\n      "tag": "dset2"\n    },\n    {\n      "collection": "dataset",\n      "tag": "dset3"\n    }\n  ],\n  "collection_filter": "dataset",\n  "complete": true,\n  "errors": [],\n  "incomplete": false,\n  "next_cursor": null,\n  "path": {\n    "segments": [\n      {\n        "accessor": "result"\n      }\n    ]\n  },\n  "rpc_calls": 1,\n  "total": 3,\n  "truncated": false\n}\n'
_CAPTURED_R28_DATASET_SHA = '7fa23c8ba915f387689abd12a9ef226bfe8e3a0bbfd8d67475654a4730249c01'

def test_native_r28_filtered_result_datasets_are_consumed_as_tagged_rows():
    import hashlib, json
    assert hashlib.sha256(_CAPTURED_R28_DATASET_BYTES).hexdigest() == _CAPTURED_R28_DATASET_SHA
    body = json.loads(_CAPTURED_R28_DATASET_BYTES)
    calls = []
    def call(operation, arguments):
        calls.append((operation, arguments))
        assert operation == "node.children"
        assert arguments == direction.children_arguments(body["path"], collection="dataset")
        return body
    rows = direction._result_dataset_rows(call)
    assert rows == body["children"]
    assert [row["tag"] for row in rows] == ["dset1", "dset2", "dset3"]
    assert len(calls) == 1


def test_dataset_tag_path_resolves_on_exact_fake_java_result_receiver_tree():
    from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
    from comsol_mcp._g2_contract import _resolve_node_path_with_tags
    from comsol_mcp._g3_results import _dataset_path_target

    dataset_node = object()
    calls = []

    class FakeJavaResults:
        # The actual receiver exposes dataset(tag) directly; it has no no-arg
        # dataset accessor and no intermediate dataset-container object.
        def dataset(self, tag):
            calls.append(("result.dataset", tag))
            if tag != "dset1":
                raise KeyError(tag)
            return dataset_node

    results_node = FakeJavaResults()

    class FakeJavaModel:
        def result(self):
            calls.append(("model.result",))
            return results_node

    model = FakeJavaModel()
    exact = {"segments": [{"accessor": "result"},
                          {"collection": "dataset", "tag": "dset1"}]}
    assert direction._safe_path(exact) is True
    resolved, used = _resolve_node_path_with_tags(model, exact)
    assert resolved is dataset_node
    assert used == exact
    assert calls == [("model.result",), ("result.dataset", "dset1")]
    assert not hasattr(results_node, "dataset_container")

    # This is the R29 result().dataset().dataset(tag) shape. The receiver has no
    # zero-argument dataset method/container, so the actual resolver must reject it.
    calls.clear()
    old_nested = {"segments": [{"accessor": "result"}, {"accessor": "dataset"},
                               {"collection": "dataset", "tag": "dset1"}]}
    assert direction._safe_path(old_nested) is False
    with pytest.raises(ExecutionContractError) as error:
        _resolve_node_path_with_tags(model, old_nested)
    assert error.value.code == "NODE_NOT_FOUND"
    assert calls == [("model.result",)]
    with pytest.raises(PreWriteRefusal) as refusal:
        _dataset_path_target(None, "m", old_nested)
    assert refusal.value.code == "INVALID_NODE_PATH"


@pytest.mark.parametrize("change", ["accessor_header", "duplicate_tag", "missing_filter", "incomplete"])
def test_native_r28_dataset_contract_tampering_stays_rejected(change):
    import json
    body = json.loads(_CAPTURED_R28_DATASET_BYTES)
    if change == "accessor_header":
        body["children"] = [{"collection": "dataset", "tag": None, "accessor": True}]
    elif change == "duplicate_tag":
        body["children"].append(deepcopy(body["children"][0]))
    elif change == "missing_filter":
        body.pop("collection_filter")
    else:
        body["complete"] = False
    with pytest.raises(direction.DirectionDiscoveryError):
        direction._result_dataset_rows(lambda operation, arguments: body)
