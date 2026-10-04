"""Production-path software checks for the bounded two-ModelRef READ action.

The receivers are synthetic and fixed typed getter fixtures, not COMSOL
runtime or native acceptance evidence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from comsol_mcp import _g2_model_compare as model_compare
from comsol_mcp import _g2_registry as registry
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from test_unit_c_checkpoint_diff import PureList, PureNode, S, model, path, member
from test_unit_a_node_public_api import method

P = "com.comsol.model."


class CompareWorker:
    def __init__(self):
        self.models = {}
        self.calls = []
        self.descriptors = []
        self.fingerprint = "fixed"
        self.counter = 0
        self._generation = 1
        self.runtime_calls = 0
        self.change_on_runtime_call = None
        self.revision_change_on_runtime_call = None
        self.counter_change_on_runtime_call = None
        self.handle_change_on_lookup = None
        self.model_lookups = 0
        self.snapshot_calls = []
        self.require_admitted_snapshot = False
        self.operation_context_calls = 0
        self.service = None
        self.refs = {}
        self._add_model("m")
        self._add_model("n")

    @property
    def generation(self):
        return self._generation

    def _add_model(self, tag):
        value = model()
        value.tag_value = tag
        value._handle = "model-handle:" + tag
        value._generation = self._generation
        value._worker = self
        self.models[tag] = value
        return value

    def client(self):
        return self

    def model(self, tag):
        self.calls.append(("model", tag))
        self.model_lookups += 1
        if self.handle_change_on_lookup == self.model_lookups:
            self.models[tag]._handle += ":rebound"
        return self.models[tag]

    def runtime_metadata(self):
        self.runtime_calls += 1
        if self.change_on_runtime_call == self.runtime_calls:
            node = self.models["n"].children["feature"].items["a"]
            node.props["flag"] = ("Boolean", False, [])
        if self.revision_change_on_runtime_call == self.runtime_calls and self.service is not None:
            state = self.service.ledger._state_for(model_ref_from_mapping(self.refs["n"]))
            state.revision += 1
        if self.counter_change_on_runtime_call == self.runtime_calls:
            self.counter += 1
        return {"instance_id": "synthetic-worker", "generation": self._generation,
                "connected": True, "server": "synthetic"}

    def backend_snapshot(self, tag):
        self.snapshot_calls.append(tag)
        if self.require_admitted_snapshot and self.model_lookups < 2:
            raise AssertionError("snapshot ran before both ModelRefs resolved to current Worker handles")
        if tag not in self.models:
            raise AssertionError("snapshot selected an unbound model")
        return {"model_tag": tag, "server_instance_id": "server",
                "fingerprint": self.fingerprint if tag == "m" else "fixed:" + tag,
                "external_event_counter": self.counter if tag == "m" else 0}

    model_snapshot = backend_snapshot

    def describe_public(self, node):
        self.descriptors.append(node)
        return {"runtime_version": model_compare.VERSION, "interfaces": [P + role for role in node.roles],
                "methods": list(node.specs.values())}

    def operation_context(self, *_args, **_kwargs):
        from contextlib import nullcontext
        self.operation_context_calls += 1
        return nullcontext()


def _add_geometry(worker, tag, *, unit="mm", box=None):
    geometry = PureNode("g1", ("GeomSequence", "GeomInfo", "ModelEntity"))
    geometry.add("getSDim", "int", 3, "GeomInfo")
    geometry.add("getNEntities", "int[]", [0, 1, 0, 0], "GeomSequence")
    geometry.add("getBoundingBox", "double[]", list(box or [0.0, 1.0, 0.0, 2.0, 0.0, 3.0]), "GeomSequence")
    geometry.add("lengthUnit", S, unit, "GeomSequence")
    worker.models[tag].child("geom", PureList([geometry]), "ModelEntityList")
    return geometry


@pytest.fixture
def env(tmp_path, monkeypatch):
    worker = CompareWorker()
    store = OperationStore(tmp_path / "compare.sqlite")
    service = ExecutionService(SessionLedger("session", "server"), worker, project_root=tmp_path)
    left_ref = service.bind_model("m")["execution"]["model_ref"]
    right_ref = service.bind_model("n")["execution"]["model_ref"]
    worker.service = service
    worker.refs = {"m": left_ref, "n": right_ref}
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    backend.project_root = tmp_path
    backend.worker_identity = {"runtime_id": "synthetic", "worker_instance_id": "synthetic-worker",
                               "connection_epoch": 1, "server_instance_id": "server"}
    backend._bind_model_project(left_ref, "p")
    backend._bind_model_project(right_ref, "p")
    backend.persist()
    writes = []
    begin_write = SessionLedger.begin_write
    monkeypatch.setattr(SessionLedger, "begin_write",
                        lambda ledger, *args, **kwargs: (writes.append((args, kwargs)), begin_write(ledger, *args, **kwargs))[1])
    yield backend, worker, left_ref, right_ref, writes
    backend.docs_index.close()
    store.close()


def envelope(left_ref, *, project="p"):
    return {"project_id": project, "session_id": "session", "model_ref": copy.deepcopy(left_ref),
            "request_id": "compare-request", "idempotency_key": "compare-key"}


def _call(env, *, route="model.compare", scope=None, other=None):
    backend, worker, left_ref, right_ref, writes = env
    worker.require_admitted_snapshot = True
    worker.snapshot_calls.clear()
    other = other or right_ref
    body = {"other_model_ref": model_compare.canonical(other)}
    if scope is not None:
        body["scope"] = scope
    if route in {"registry_call", "operation_call"}:
        body = {"operation_id": "model.compare", "arguments": body}
    return backend.invoke(route, body, envelope(left_ref), "compare-op", None)


@pytest.mark.parametrize("route", ["model.compare", "model_compare", "registry_call", "operation_call"])
def test_two_current_models_complete_equal_on_direct_and_nested_routes(env, route):
    backend, worker, left_ref, right_ref, writes = env
    result = _call(env, route=route)
    assert result["success"] is True, result
    data = result["data"]
    assert data["operation"] == "model.compare"
    assert data["comparison"] == {"status": "EQUAL", "equal": True, "complete": True}
    assert data["left_identity"]["model_ref"] == left_ref
    assert data["right_identity"]["model_ref"] == right_ref
    assert data["evidence"]["write_tickets"] == 0
    assert data["evidence"]["model_loads_or_temporary_models"] == 0
    assert data["observations"]["requested_scope_readback_stable"] is True
    assert worker.operation_context_calls == 1
    assert worker.snapshot_calls and worker.snapshot_calls[0] == "m"
    assert not writes
    assert not any(call[0] in {"load", "remove", "save", "create"} for call in worker.calls)


def test_complete_property_difference_never_uses_tag_or_hash_as_value(env):
    backend, worker, left_ref, right_ref, writes = env
    worker.models["n"].children["feature"].items["a"].props["flag"] = ("Boolean", False, [])
    scope = {"mode": "paths", "properties": [{"path": path(member("feature", "a")), "names": ["flag"]}]}
    result = _call(env, scope=scope)
    assert result["success"] is True, result
    assert result["data"]["comparison"] == {"status": "DIFFERENT", "equal": False, "complete": True}
    assert result["data"]["changes"][0]["field"] == "property:flag"
    assert not writes


def test_property_type_mismatch_is_incomplete_not_different(env):
    backend, worker, left_ref, right_ref, writes = env
    worker.models["n"].children["feature"].items["a"].props["flag"] = ("String", "true", [])
    scope = {"mode": "paths", "properties": [{"path": path(member("feature", "a")), "names": ["flag"]}]}
    result = _call(env, scope=scope)
    assert result["data"]["comparison"] == {"status": "INCOMPLETE", "equal": None, "complete": False}
    assert any(row["code"] == "TYPE_MISMATCH" for row in result["data"]["errors"])
    assert not writes


def test_complete_structural_addition_is_different_only_with_complete_coverage(env):
    backend, worker, left_ref, right_ref, writes = env
    extra = PureNode("b", ("ModelEntity", "PropFeature"), properties={"flag": ("Boolean", False, [])})
    right_list = worker.models["n"].children["feature"]
    right_list.items["b"] = extra
    right_list.order.append("b")
    result = _call(env)
    assert result["data"]["comparison"] == {"status": "DIFFERENT", "equal": False, "complete": True}
    assert any(row["change"] == "added" for row in result["data"]["changes"])
    assert not writes


def test_missing_getter_and_missing_metric_unit_stay_incomplete(env):
    backend, worker, left_ref, right_ref, writes = env
    worker.models["n"].children["feature"].items["a"].specs.pop(("getBoolean", (S,)))
    scope = {"mode": "paths", "properties": [{"path": path(member("feature", "a")), "names": ["flag"]}]}
    result = _call(env, scope=scope)
    assert result["data"]["comparison"] == {"status": "INCOMPLETE", "equal": None, "complete": False}
    assert result["data"]["status"] == "INCOMPLETE"
    assert any(row["status"] == "UNSUPPORTED" for row in result["data"]["coverage"])
    assert not writes

    _add_geometry(worker, "m")
    right_geom = _add_geometry(worker, "n")
    right_geom.specs.pop(("lengthUnit", ()))
    metric_scope = {"schema_version": 1, "mode": "paths", "metrics": [
        {"path": path(member("geom", "g1")), "names": ["geometry.bounding_box"]}]}
    metric_result = _call(env, scope=metric_scope)
    assert metric_result["data"]["comparison"] == {"status": "INCOMPLETE", "equal": None, "complete": False}
    assert any(row["field"] == "metric:geometry.bounding_box.unit" and row["status"] == "UNSUPPORTED"
               for row in metric_result["data"]["coverage"])
    assert not writes


def test_named_geometry_metric_uses_typed_value_and_matching_units(env):
    backend, worker, left_ref, right_ref, writes = env
    _add_geometry(worker, "m", unit="mm")
    _add_geometry(worker, "n", unit="mm")
    scope = {"mode": "paths", "metrics": [{"path": path(member("geom", "g1")),
                                                "names": ["geometry.bounding_box", "geometry.spatial_dimension"]}]}
    result = _call(env, scope=scope)
    assert result["success"] is True, result
    assert result["data"]["comparison"] == {"status": "EQUAL", "equal": True, "complete": True}
    field = result["data"]["projections"]["left"]["nodes"][0]["fields"]["metric:geometry.bounding_box"]
    assert field["unit"] == "mm"
    assert field["value"]["value"]["kind"] == "float64"
    assert result["data"]["projections"]["left"]["nodes"][0]["fields"]["metric:geometry.spatial_dimension"]["unit"] == "1"
    assert not writes


def test_unit_mismatch_is_a_gap_and_never_a_numeric_difference(env):
    backend, worker, left_ref, right_ref, writes = env
    _add_geometry(worker, "m", unit="mm")
    _add_geometry(worker, "n", unit="cm", box=[0.0, 0.1, 0.0, 0.2, 0.0, 0.3])
    scope = {"mode": "paths", "metrics": [{"path": path(member("geom", "g1")),
                                                "names": ["geometry.bounding_box"]}]}
    result = _call(env, scope=scope)
    assert result["data"]["comparison"] == {"status": "INCOMPLETE", "equal": None, "complete": False}
    assert any(row["code"] == "UNIT_MISMATCH" for row in result["data"]["errors"])
    assert not writes


def test_default_model_scope_pairs_bounding_box_with_length_unit(env):
    backend, worker, left_ref, right_ref, writes = env
    _add_geometry(worker, "m", unit="mm")
    _add_geometry(worker, "n", unit="cm", box=[0.0, 0.1, 0.0, 0.2, 0.0, 0.3])
    result = _call(env)
    assert result["data"]["comparison"] == {"status": "INCOMPLETE", "equal": None, "complete": False}
    assert any(row["code"] == "UNIT_MISMATCH" for row in result["data"]["errors"])
    assert not writes


def test_stale_or_foreign_other_ref_is_refused_before_public_getters(env):
    backend, worker, left_ref, right_ref, writes = env
    worker.calls.clear()
    worker.descriptors.clear()
    foreign = {**right_ref, "session_id": "foreign-session"}
    with pytest.raises(ExecutionContractError):
        _call(env, other=foreign)
    assert not worker.calls
    assert not worker.descriptors
    assert not writes


@pytest.mark.parametrize("identity_change", ["stale_model_generation", "foreign_server", "foreign_project", "stale_primary"])
def test_exact_modelref_and_project_admission_happens_before_getters(env, identity_change):
    backend, worker, left_ref, right_ref, writes = env
    other = copy.deepcopy(right_ref)
    primary = copy.deepcopy(left_ref)
    if identity_change == "stale_model_generation":
        other["generation"] += 1
    elif identity_change == "foreign_server":
        other["server_instance_id"] = "other-server"
    elif identity_change == "foreign_project":
        key = backend._model_project_key(right_ref)
        persisted_binding = backend.store.get_metadata("revisions", key)
        backend.store.put_metadata("revisions", key,
                                  {**persisted_binding, "project_id": "other-project"})
    elif identity_change == "stale_primary":
        primary["generation"] += 1
    worker.require_admitted_snapshot = True
    worker.snapshot_calls.clear()
    with pytest.raises(ExecutionContractError):
        backend.invoke("model.compare", {"other_model_ref": model_compare.canonical(other)},
                       envelope(primary), "invalid-identity", None)
    assert not worker.descriptors
    assert not worker.snapshot_calls
    assert not writes


def test_worker_or_model_generation_mismatch_refuses_before_snapshot_or_getters(env):
    backend, worker, left_ref, right_ref, writes = env
    worker._generation = 2
    worker.models["m"]._generation = 2
    worker.models["n"]._generation = 2
    worker.require_admitted_snapshot = True
    worker.snapshot_calls.clear()
    with pytest.raises(ExecutionContractError):
        _call(env)
    assert not worker.descriptors
    assert not worker.snapshot_calls
    assert not writes


def test_model_handle_generation_mismatch_refuses_before_snapshot_or_getters(env):
    backend, worker, left_ref, right_ref, writes = env
    worker.models["n"]._generation += 1
    worker.require_admitted_snapshot = True
    worker.snapshot_calls.clear()
    with pytest.raises(ExecutionContractError):
        _call(env)
    assert not worker.descriptors
    assert not worker.snapshot_calls
    assert not writes


def test_handle_and_requested_value_drift_fail_closed_and_fence_both_models(env):
    backend, worker, left_ref, right_ref, writes = env
    worker.handle_change_on_lookup = 4
    first = _call(env)
    assert first["success"] is False
    assert first["data"]["status"] == "UNKNOWN"
    assert first["data"]["comparison"]["equal"] is None
    assert backend.service.ledger._state_for(model_ref_from_mapping(left_ref)).dirty
    assert backend.service.ledger._state_for(model_ref_from_mapping(right_ref)).dirty
    assert not writes


def test_scope_value_change_between_projection_and_readback_is_unknown(env):
    backend, worker, left_ref, right_ref, writes = env
    worker.change_on_runtime_call = 3
    scope = {"mode": "paths", "properties": [{"path": path(member("feature", "a")), "names": ["flag"]}]}
    result = _call(env, scope=scope)
    assert result["success"] is False
    assert result["data"]["status"] == "UNKNOWN"
    assert result["data"]["comparison"] == {"status": "INCOMPLETE", "equal": None, "complete": False}
    assert backend.service.ledger._state_for(model_ref_from_mapping(left_ref)).dirty
    assert backend.service.ledger._state_for(model_ref_from_mapping(right_ref)).dirty
    assert not writes


@pytest.mark.parametrize("drift", ["revision", "counter"])
def test_revision_or_external_counter_drift_is_unknown(env, drift):
    backend, worker, left_ref, right_ref, writes = env
    if drift == "revision":
        worker.revision_change_on_runtime_call = 3
    else:
        worker.counter_change_on_runtime_call = 3
    result = _call(env, scope={"mode": "paths", "paths": [path(member("feature", "a"))]})
    assert result["success"] is False
    assert result["data"]["status"] == "UNKNOWN"
    assert result["data"]["comparison"]["equal"] is None
    assert backend.service.ledger._state_for(model_ref_from_mapping(left_ref)).dirty
    assert backend.service.ledger._state_for(model_ref_from_mapping(right_ref)).dirty
    assert not writes


def test_budget_exhaustion_cannot_be_equal_or_different(env):
    backend, worker, left_ref, right_ref, writes = env
    scope = {"mode": "paths", "paths": [path(member("feature", "a"))], "budget": {"max_rpc": 1}}
    result = _call(env, scope=scope)
    assert result["data"]["status"] == "INCOMPLETE"
    assert result["data"]["comparison"] == {"status": "INCOMPLETE", "equal": None, "complete": False}
    assert not backend.service.ledger._state_for(model_ref_from_mapping(left_ref)).dirty
    assert not backend.service.ledger._state_for(model_ref_from_mapping(right_ref)).dirty
    assert not writes


@pytest.mark.parametrize("body", [
    {"other_model_ref": "n"},
    {"other_model_ref": '{"schema_version":1,"schema_version":1}'},
    {"other_model_ref": '{"schema_version":true,"session_id":"session","server_instance_id":"server","model_tag":"n","generation":1}'},
    {"other_model_ref": '{ "schema_version":1,"session_id":"session","server_instance_id":"server","model_tag":"n","generation":1}'},
    {"other_model_ref": "{}", "scope": {"mode": "paths", "metrics": [{"path": {"segments": []}, "names": ["java.lang.Runtime.exec"]}]}},
    {"other_model_ref": "{}", "scope": {"mode": "paths", "metrics": [{"path": {"segments": []}, "names": ["geometry.bounding_box"], "tolerance": 0.1}]}},
    {"other_model_ref": "{}", "scope": {"mode": "paths", "budget": {"max_rpc": True}, "metrics": [{"path": {"segments": []}, "names": ["mesh.vertex_count"]}]}},
])
def test_wire_scope_and_other_ref_are_closed_before_getters(env, body):
    backend, worker, left_ref, right_ref, writes = env
    worker.calls.clear()
    worker.descriptors.clear()
    with pytest.raises(ExecutionContractError):
        backend.invoke("model.compare", body, envelope(left_ref), "invalid", None)
    assert not worker.calls
    assert not worker.descriptors
    assert not writes


def test_public_effective_schema_catalog_and_control_daemon_entrypoint(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=root, registry={})
    try:
        project = daemon.dispatch({"operation": "project.create", "arguments": {
            "label": "synthetic model compare", "workspace": "work",
            "policy": {"permissions": ["inspect"]}},
            "execution": {"request_id": "project-create", "idempotency_key": "project-create"}})["data"]["project"]
        worker = CompareWorker()
        backend = daemon.backend
        backend.worker = worker
        backend.worker_identity = {"runtime_id": "synthetic", "worker_instance_id": "synthetic-worker",
                                   "connection_epoch": 1, "server_instance_id": "server"}
        backend.service = ExecutionService(SessionLedger("session", "server"), worker, project_root=root)
        left_ref = backend.service.bind_model("m")["execution"]["model_ref"]
        right_ref = backend.service.bind_model("n")["execution"]["model_ref"]
        backend._bind_model_project(left_ref, project["project_id"])
        backend._bind_model_project(right_ref, project["project_id"])
        backend.persist()
        worker.require_admitted_snapshot = True
        wire = {"project_id": project["project_id"], "session_id": "session", "model_ref": left_ref,
                "request_id": "daemon-compare", "idempotency_key": "daemon-compare", "rpc_timeout_s": 5}
        body = {"other_model_ref": model_compare.canonical(right_ref)}
        for route, arguments in (
            ("model_compare", body),
            ("registry_call", {"operation_id": "model.compare", "arguments": body}),
            ("operation_call", {"operation_id": "model.compare", "arguments": body}),
        ):
            result = daemon.dispatch({"operation": route, "arguments": arguments,
                                      "execution": {**wire, "request_id": route, "idempotency_key": route}})
            assert result["success"] is True, result
            assert result["data"]["comparison"]["status"] == "EQUAL"
        assert worker.operation_context_calls == 3
        effective = registry._effective_input_schema(registry.BY_ID["model.compare"].input_schema, "model.compare")
        assert effective["properties"]["other_model_ref"]["type"] == "string"
        assert "model_ref" in effective["required"] and "other_model_ref" in effective["required"]
        assert registry.BY_ID["model.compare"].as_dict()["implementation_status"] == "SUPPORTED_UNVERIFIED"
        assert registry.BY_ID["model.compare"].as_dict()["catalog_input_schema"]["properties"]["model_ref"]["type"] == "string"
    finally:
        daemon.close()
