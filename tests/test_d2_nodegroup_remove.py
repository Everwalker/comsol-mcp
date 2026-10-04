"""D2 typed NodeGroup removal software evidence; no native COMSOL claim."""
from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from comsol_mcp import _domain_outcome
from comsol_mcp import _g2_engine
from comsol_mcp import _java_worker
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger, model_ref_from_mapping
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._java_worker import JavaWorkerError, PersistentJavaWorker, RemoteJava


VERSION = "6.4.0.293"
MODEL = "com.comsol.model.Model"
MODEL_ENTITY = "com.comsol.model.ModelEntity"
PRIMITIVE = "com.comsol.model.PrimitiveModelEntity"
NODE_GROUP = "com.comsol.model.NodeGroup"
NODE_GROUP_LIST = "com.comsol.model.NodeGroupList"
JAVA_STRING = "java.lang.String"
ROOT_PATH = {"segments": [{"collection": "nodeGroup", "tag": "g1"}]}


def _signature(interface: str, method: str, parameters: list[str], returns: str) -> dict:
    return {"interface": interface, "method": method, "parameters": parameters, "returns": returns}


def _descriptor(interface: str, method: str, parameters: list[str], returns: str,
                *, version: object = VERSION, receiver: str | None = None) -> dict:
    owner = receiver or interface
    return {"runtime_version": version, "interfaces": [interface],
            "methods": [_signature(owner, method, parameters, returns)]}


def _worker_reply(command: str, result: dict) -> dict:
    return {"ok": True, "request_id": "synthetic-request", "type": command,
            "status": "SUCCEEDED", "queued_at_ms": "1", "started_at_ms": "2",
            "completed_at_ms": "3", "result": result}


def _transport(monkeypatch, *, generation: object = 7, reply: dict | None = None,
               descriptor: dict | None = None):
    worker = PersistentJavaWorker.__new__(PersistentJavaWorker)
    worker._generation = generation
    worker.synthetic_calls = []
    worker.synthetic_reply = reply
    worker.synthetic_descriptor = descriptor

    def submit(command, payload, **kwargs):
        worker.synthetic_calls.append((command, dict(payload), dict(kwargs)))
        return worker.synthetic_reply

    def describe(node):
        return worker.synthetic_descriptor or _descriptor(
            NODE_GROUP_LIST, "tags", [], "[Ljava.lang.String;", receiver=NODE_GROUP_LIST
        )

    monkeypatch.setattr(worker, "submit", submit, raising=False)
    monkeypatch.setattr(worker, "describe_public", describe, raising=False)
    return worker


class OpaqueWorker:
    """Protocol-shaped synthetic Worker. Handles are opaque tokens, not objects."""

    def __init__(self, *, members: tuple[str, ...] = ("physics", "variable"), after: str | None = None):
        self.generation: object = 7
        self.model_tag = "m"
        self.root_tags = ["g1", "g2"]
        self.members = {"g1": list(members), "g2": []}
        self.member_kinds = {"physics": "PhysicsFeature", "variable": "VariableEntity",
                             "mesh": "MeshFeature", "study": "StudyFeature"}
        self.member_paths = {"physics": "/component/c1/physics/p1",
                             "variable": "/component/c1/variable/v1",
                             "mesh": "/mesh/m1", "study": "/study/s1"}
        self.after = after
        self.nested_tags: list[str] = []
        self.fault: str | None = None
        self.mutated = False
        self.counter = 0
        self.model_calls: list[str] = []
        self.engine_calls: list[tuple[str, str, tuple[object, ...]]] = []
        self.ungroup_calls: list[tuple[str, str]] = []
        self._referents = {"model": "model", "rootlist": "rootlist", "group:g1": "group:g1",
                           "group:g2": "group:g2", "nested:g1": "nested:g1", "anchor": "anchor",
                           "foreignmodel": "foreign-model"}
        self._containers: dict[str, str | None] = {"rootlist": "model", "group:g1": "model",
                                                    "group:g2": "model", "nested:g1": "group:g1",
                                                    "anchor": "model"}
        self._handle_type = {"model": MODEL, "rootlist": NODE_GROUP_LIST, "group:g1": NODE_GROUP,
                             "group:g2": NODE_GROUP, "nested:g1": NODE_GROUP_LIST,
                             "anchor": MODEL_ENTITY, "foreignmodel": MODEL}
        self._member_handle: dict[str, str] = {}
        self._member_counter = 0
        self._install_members()

    def _install_members(self) -> None:
        self._member_handle.clear()
        for index, tag in enumerate(self.members.get("g1", [])):
            # The Java handle is a string token. A second token can deliberately
            # refer to the same referent for duplicate-reference tests.
            handle = f"member:{index}:{tag}"
            self._member_handle[tag] = handle
            self._referents.setdefault(handle, f"member:{tag}")
            self._handle_type[handle] = MODEL_ENTITY
            self._containers[handle] = "model"
            self.member_paths.setdefault(tag, f"/synthetic/{tag}")

    def client(self):
        return self

    def model(self, tag: str):
        self.model_calls.append(tag)
        return RemoteJava(self, "model", self.generation, MODEL)

    def operation_context(self, *args, **kwargs):
        return nullcontext()

    def model_snapshot(self, tag: str):
        return {"model_tag": tag, "server_instance_id": "synthetic-server",
                "fingerprint": f"synthetic:{self.counter}", "external_event_counter": 0}

    backend_snapshot = model_snapshot

    def describe_public(self, node: RemoteJava):
        handle = node._handle
        if self.fault == "wrong_version":
            version = "6.3.0.290"
        else:
            version = VERSION
        interfaces = {
            MODEL: [MODEL, PRIMITIVE, MODEL_ENTITY],
            NODE_GROUP_LIST: [NODE_GROUP_LIST, PRIMITIVE],
            NODE_GROUP: [NODE_GROUP, MODEL_ENTITY, PRIMITIVE],
            MODEL_ENTITY: [MODEL_ENTITY, PRIMITIVE],
        }.get(self._handle_type.get(handle), [MODEL_ENTITY, PRIMITIVE])
        if self.fault == "wrong_member_interface" and handle.startswith("member:"):
            interfaces = [PRIMITIVE]
        return {"runtime_version": version, "interfaces": list(interfaces), "methods": []}

    def _remote(self, handle: str) -> RemoteJava:
        return RemoteJava(self, handle, self.generation, self._handle_type.get(handle, MODEL_ENTITY))

    def _member_tag(self, handle: str) -> str:
        for tag, token in self._member_handle.items():
            if token == handle:
                return tag
        return handle.split(":", 2)[-1]

    def read_nodegroup(self, node: RemoteJava, method: object, *args: object):
        self.engine_calls.append(("g2_nodegroup_read", str(method), tuple(args)))
        _domain_outcome.record_engine_method(method, *args, command="g2_nodegroup_read", receiver=node._handle)
        if (node._worker is not self or type(self.generation) is not int or type(node._generation) is not int
                or node._generation != self.generation):
            raise JavaWorkerError("STALE_WORKER_HANDLE")
        handle = node._handle
        if method == "nodeGroup":
            if not args:
                return self._remote("rootlist")
            tag = args[0]
            if tag not in self.root_tags:
                raise JavaWorkerError("NODE_NOT_FOUND")
            return self._remote(f"group:{tag}")
        if method == "tags":
            if handle == "rootlist":
                if self.fault == "truncated_tags":
                    return tuple(self.root_tags)
                return list(self.root_tags)
            if handle == "nested:g1":
                return list(self.nested_tags)
        if method == "size" and handle.startswith("group:"):
            tag = handle.split(":", 1)[1]
            value = len(self.members.get(tag, []))
            return True if self.fault == "wrong_return" else value
        if method == "feature" and handle.startswith("group:"):
            if self.fault == "wrong_signature":
                raise JavaWorkerError("exact NodeGroup.feature signature unavailable")
            return self._remote(f"nested:{handle.split(':', 1)[1]}")
        if method == "getAfter" and handle == "group:g1":
            return self._remote(self.after) if self.after else None
        if method == "get" and handle == "group:g1":
            return self._remote(self._member_handle[self.members["g1"][int(args[0])]])
        if method == "getContainer":
            if self.fault == "foreign_container" and handle.startswith("member:"):
                return self._remote("foreignmodel")
            if self.fault == "foreign_same_text" and handle.startswith("member:"):
                return self._remote("foreignmodel")
            if self.fault == "cycle" and handle.startswith("member:"):
                return self._remote("cycle:a")
            if self.fault == "cycle" and handle == "cycle:a":
                return self._remote("cycle:b")
            if self.fault == "cycle" and handle == "cycle:b":
                return self._remote("cycle:a")
            if self.fault == "null_container" and handle.startswith("member:"):
                return None
            if self.fault == "changed_survivor" and self.mutated and handle.startswith("member:"):
                return None
            if self.fault == "foreign_container" and handle == "foreignmodel":
                return None
            if self.fault == "foreign_same_text" and handle == "foreignmodel":
                return None
            if handle == "cycle:a":
                return self._remote("cycle:b")
            if handle == "cycle:b":
                return self._remote("cycle:a")
            parent = self._containers.get(handle)
            return self._remote(parent) if parent else None
        if method == "resolveModelPath":
            if self.fault == "member_getter_failure" and handle.startswith("member:"):
                raise JavaWorkerError("synthetic member path read failed")
            if handle == "group:g1":
                return "/nodeGroup/g1"
            if handle == "anchor":
                return "/nodeGroup/anchor"
            if handle.startswith("member:"):
                return self.member_paths[self._member_tag(handle)]
        raise JavaWorkerError(f"unmodeled synthetic D2 read: {method} {handle}")

    def entity_identity(self, left: RemoteJava, right: RemoteJava):
        self.engine_calls.append(("g2_entity_identity", "entity_identity", (left._handle, right._handle)))
        _domain_outcome.record_engine_method("entity_identity", left._handle, right._handle,
                                             self.generation, command="g2_entity_identity", receiver="private-worker")
        if (left._worker is not self or right._worker is not self or type(self.generation) is not int
                or type(left._generation) is not int or type(right._generation) is not int
                or left._generation != self.generation or right._generation != self.generation):
            raise JavaWorkerError("STALE_WORKER_HANDLE")
        if self.fault == "malformed_boolean":
            return "true"
        if self.fault == "false":
            return False
        if self.fault == "identity_extra_malformed":
            return 1
        return self._referents.get(left._handle, left._handle) == self._referents.get(right._handle, right._handle)

    def ungroup_nodegroup(self, node: RemoteJava, tag: str):
        if (node._worker is not self or type(self.generation) is not int or type(node._generation) is not int
                or node._generation != self.generation):
            raise JavaWorkerError("STALE_WORKER_HANDLE")
        self.ungroup_calls.append((node._handle, tag))
        _domain_outcome.record_engine_method("ungroup", tag, command="g2_nodegroup_ungroup", receiver=node._handle)
        self.mutated = True
        if self.fault != "target_remains":
            self.root_tags = [item for item in self.root_tags if item != tag]
        self.counter += 1
        if self.fault == "lost_reply_after_effect":
            raise JavaWorkerError("synthetic lost private Worker reply after effect")
        if self.fault == "lost_survivor":
            self.generation = 8
        return {"ungroup_dispatched": True, "generation": node._generation, "tag": tag,
                "semantic_operation": "NodeGroupList.ungroup(String)", "runtime_version": VERSION}


def _args(*, after: str | None = None):
    return {"path": ROOT_PATH, "cascade": False}


def _execution(ref: dict, *, revision: int = 0, key: str = "d2-request") -> dict:
    return {"project_id": "d2-project", "session_id": "d2-session", "model_ref": ref,
            "expected_revision": revision, "idempotency_key": key, "request_id": key}


@pytest.fixture
def env(tmp_path, monkeypatch):
    worker = OpaqueWorker()
    store = OperationStore(tmp_path / "operations.sqlite3")
    service = ExecutionService(SessionLedger("d2-session", "synthetic-server"), worker,
                               project_root=tmp_path)
    ref = service.bind_model("m")["execution"]["model_ref"]
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    backend.project_root = tmp_path
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"scope": "D2_SYNTHETIC_ONLY"})
    backend._bind_model_project(ref, "d2-project")
    tickets = []
    original_begin = SessionLedger.begin_write

    def counted_begin(self, *args, **kwargs):
        tickets.append((args, kwargs))
        return original_begin(self, *args, **kwargs)

    monkeypatch.setattr(SessionLedger, "begin_write", counted_begin)
    yield backend, worker, ref, tickets
    backend.docs_index.close()
    store.close()


def _invoke(env, *, arguments: dict | None = None, key: str = "d2-request", revision: int = 0):
    backend, _worker, ref, _tickets = env
    return backend.invoke("node.remove", arguments or _args(), _execution(ref, revision=revision, key=key),
                          "d2-operation", None)


def _state(env):
    backend, _worker, ref, _tickets = env
    return backend.service.ledger._state_for(model_ref_from_mapping(ref))


def test_flat_mixed_group_uses_real_service_store_claim_and_is_reported_incomplete(env):
    backend, worker, ref, tickets = env
    result = _invoke(env)
    assert result["success"] is True
    assert result["data"]["status"] == "INCOMPLETE" and result["data"]["complete"] is False
    assert result["data"]["dependency_check"]["incoming_complete"] is False
    assert result["data"]["readback"]["post_dispatch"]["target_group_absent"] is True
    assert result["data"]["retained_member_count"] == 2
    assert result["execution"]["revision"] == 1 and result["execution"]["model_ref"] == ref
    assert len(tickets) == 1 and worker.ungroup_calls == [("rootlist", "g1")]
    assert _state(env).dirty is False
    assert "NodeGroupList.ungroup(String)" == result["data"]["semantic_operation"]


@pytest.mark.parametrize("after", [None, "anchor"])
def test_empty_group_and_getafter_null_or_nonnull_are_observed(env, after):
    _backend, worker, _ref, _tickets = env
    worker.members["g1"] = []
    worker.after = after
    result = _invoke(env)
    assert result["success"] is True, result
    assert result["data"]["readback"]["preflight"]["group_size"] == 0
    assert result["data"]["readback"]["preflight"]["getAfter_path"] == (
        "/nodeGroup/anchor" if after else None
    )
    assert worker.ungroup_calls == [("rootlist", "g1")]


def test_nested_group_refuses_before_write(env):
    _backend, worker, _ref, tickets = env
    worker.nested_tags = ["nested1"]
    with pytest.raises(ExecutionContractError):
        _invoke(env)
    assert not worker.ungroup_calls and not tickets
    assert _state(env).dirty is False


@pytest.mark.parametrize("fault", ["wrong_version", "wrong_member_interface", "wrong_signature",
                                   "wrong_return", "foreign_container", "duplicate_identity",
                                   "member_getter_failure", "truncated_tags", "stale_projection"])
def test_prewrite_identity_signature_and_completeness_refusals(env, monkeypatch, fault):
    backend, worker, _ref, tickets = env
    worker.fault = fault
    if fault == "duplicate_identity":
        worker.members["g1"] = ["physics", "variable"]
        worker._install_members()
        worker._referents[worker._member_handle["variable"]] = worker._referents[worker._member_handle["physics"]]
    if fault == "stale_projection":
        original = backend.service.execute_legacy

        def alter_after_prepare(*args, **kwargs):
            worker.root_tags.append("late")
            return original(*args, **kwargs)

        monkeypatch.setattr(backend.service, "execute_legacy", alter_after_prepare)
    if fault == "stale_projection":
        result = _invoke(env)
        assert result["success"] is False
    else:
        with pytest.raises(ExecutionContractError):
            _invoke(env)
    assert not worker.ungroup_calls
    assert _state(env).dirty is False
    if fault == "stale_projection":
        assert len(tickets) == 1
    else:
        assert not tickets


def test_cascade_true_and_non_root_paths_refuse_without_receiver_access(env):
    _backend, worker, _ref, tickets = env
    with pytest.raises(ExecutionContractError):
        _invoke(env, arguments={"path": ROOT_PATH, "cascade": True})
    assert not worker.model_calls and not worker.ungroup_calls and not tickets
    nested_path = {"segments": ROOT_PATH["segments"] + [{"collection": "feature", "tag": "f1"}]}
    with pytest.raises(ExecutionContractError):
        _invoke(env, arguments={"path": nested_path, "cascade": False})
    assert not worker.model_calls and not worker.ungroup_calls and not tickets


@pytest.mark.parametrize(("operation", "body"), [
    ("api.describe", {"path": ROOT_PATH, "method": "comments"}),
    ("api.invoke", {"path": ROOT_PATH, "method": "comments", "arguments": [], "declared_effect": "READ"}),
    ("api.invoke", {"path": ROOT_PATH, "method": "comments", "arguments": [{"kind": "string", "shape": [], "data": "x"}], "declared_effect": "WRITE"}),
    ("node.label_set", {"path": ROOT_PATH, "label": "x"}),
    ("node.active_set", {"path": ROOT_PATH, "active": False}),
    ("node.create", {"parent": {"segments": []}, "collection": "nodeGroup", "tag": "x", "type_id": "NodeGroup"}),
    ("node.copy", {"source": ROOT_PATH, "target_parent": {"segments": []}, "tag": "copy"}),
    ("node.move", {"path": ROOT_PATH, "before": {"segments": [{"collection": "feature", "tag": "a"}]}}),
    ("node.selection_get", {"path": ROOT_PATH}),
    ("node.selection_set", {"path": ROOT_PATH, "selection": {"kind": "all", "entity_dimension": 2}}),
])
def test_operation_local_route_does_not_enable_generic_nodegroup_paths(env, operation, body):
    _backend, worker, _ref, _tickets = env
    with pytest.raises(ExecutionContractError):
        if operation == "api.describe":
            from comsol_mcp._g2_public_api import describe_api
            describe_api(worker, "m", body)
        elif operation == "api.invoke":
            from comsol_mcp._g2_public_api import prepare_invoke
            prepare_invoke(worker, "m", body)
        else:
            _g2_engine.validate_node_action(operation, body)
    assert not worker.ungroup_calls and not worker.engine_calls


def test_revision_and_permission_refuse_before_prepare_or_ticket(env):
    backend, worker, ref, tickets = env
    with pytest.raises(ExecutionContractError):
        _invoke(env, revision=99)
    assert not worker.model_calls and not tickets
    backend.service.ledger.permissions.discard("project_write")
    with pytest.raises(ExecutionContractError):
        _invoke(env)
    assert not worker.model_calls and not tickets and not worker.ungroup_calls


@pytest.mark.parametrize("fault", ["generation", "projection"])
def test_generation_or_prepared_projection_change_is_prewrite_in_claim_lane(env, monkeypatch, fault):
    backend, worker, _ref, tickets = env
    original = backend.service.execute_legacy

    def change_during_claim(*args, **kwargs):
        if fault == "generation":
            worker.generation = 8
        else:
            worker.member_paths["physics"] = "/component/c1/physics/changed"
        return original(*args, **kwargs)

    monkeypatch.setattr(backend.service, "execute_legacy", change_during_claim)
    result = _invoke(env)
    assert result["success"] is False, result
    assert not worker.ungroup_calls and len(tickets) == 1
    assert _state(env).dirty is False
    assert result["execution"]["revision"] == 0


@pytest.mark.parametrize("fault", ["lost_reply_after_effect", "target_remains", "changed_survivor", "lost_survivor"])
def test_post_dispatch_failures_retain_unknown_dirty_and_no_retry(env, fault):
    _backend, worker, _ref, tickets = env
    worker.fault = fault
    result = _invoke(env)
    assert result["success"] is False, result
    assert result["data"]["execution_state_unknown"] is True
    assert result["execution"]["dirty"] is True and result["error"]["safe_retry"] is False
    assert len(tickets) == 1 and len(worker.ungroup_calls) == 1
    assert _state(env).dirty is True


def test_same_key_replay_returns_saved_result_without_second_ungroup(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    worker = OpaqueWorker()
    service = ExecutionService(SessionLedger("d2-session", "synthetic-server"), worker,
                               project_root=root)
    ref = service.bind_model("m")["execution"]["model_ref"]
    daemon = ControlDaemon(tmp_path / "control", service=service, worker=worker,
                           project_root=root, registry={})
    tickets = []
    original_begin = SessionLedger.begin_write

    def counted_begin(self, *args, **kwargs):
        tickets.append((args, kwargs))
        return original_begin(self, *args, **kwargs)

    monkeypatch.setattr(SessionLedger, "begin_write", counted_begin)
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                        lambda: {"scope": "D2_SYNTHETIC_ONLY"})
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "D2 replay", "workspace": "d2-replay",
                          "policy": {"permissions": ["inspect", "project_write"]}},
            "execution": {"request_id": "create-d2-replay", "idempotency_key": "create-d2-replay"},
        })
        assert created["success"] is True, created
        project_id = created["data"]["project"]["project_id"]
        daemon.backend._bind_model_project(ref, project_id)
        daemon.store.put_metadata(
            "revisions", daemon.backend._model_project_key(ref),
            {"model_ref": ref, "project_id": project_id, "revision": 0},
        )
        request = {
            "operation": "node.remove",
            "arguments": _args(),
            "execution": {**_execution(ref, key="same-key"), "project_id": project_id,
                          "rpc_timeout_s": 2},
        }
        first = daemon.dispatch(request)
        assert first["success"] is True, first
        post_members = first["data"]["readback"]["post_dispatch"]["members"]
        assert len(post_members) == 2
        for member in post_members:
            chain = member["container_chain"]
            assert chain
            assert all(set(hop) == {"hop", "interfaces", "is_bound_model"} for hop in chain)
            assert chain[-1]["is_bound_model"] is True
            assert all(type(interface) is str for hop in chain for interface in hop["interfaces"])
        calls_after_first = list(worker.engine_calls)
        ungroup_after_first = list(worker.ungroup_calls)
        second = daemon.dispatch(json.loads(json.dumps(request)))
        assert second == first
        assert worker.engine_calls == calls_after_first
        assert worker.ungroup_calls == ungroup_after_first == [("rootlist", "g1")]
        assert len(tickets) == 1

        changed = json.loads(json.dumps(request))
        changed["arguments"]["path"]["segments"][0]["tag"] = "g2"
        conflict = daemon.dispatch(changed)
        assert conflict["success"] is False
        assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert worker.engine_calls == calls_after_first
        assert worker.ungroup_calls == ungroup_after_first
        assert len(tickets) == 1
    finally:
        daemon.close()


def test_remotejava_private_transport_fences_owner_generation_and_exact_table(monkeypatch):
    worker = _transport(monkeypatch, reply=_worker_reply("g2_nodegroup_read", {
        "generation": 7, "receiver_interface": NODE_GROUP_LIST, "method": "tags",
        "parameters": [], "return_type": "[Ljava.lang.String;", "runtime_version": VERSION,
        "value": ["g1"],
    }))
    node = RemoteJava(worker, "list-handle", 7, NODE_GROUP_LIST)
    assert worker.read_nodegroup(node, "tags") == ["g1"]
    assert len(worker.synthetic_calls) == 1
    foreign = _transport(monkeypatch, generation=7, reply=worker.synthetic_reply)
    foreign_node = RemoteJava(foreign, "foreign-list", 7, NODE_GROUP_LIST)
    before = len(worker.synthetic_calls)
    with pytest.raises(JavaWorkerError):
        worker.read_nodegroup(foreign_node, "tags")
    stale = RemoteJava(worker, "stale-list", 6, NODE_GROUP_LIST)
    with pytest.raises(JavaWorkerError):
        worker.read_nodegroup(stale, "tags")
    assert len(worker.synthetic_calls) == before
    future = RemoteJava(worker, "future-list", 8, NODE_GROUP_LIST)
    with pytest.raises(JavaWorkerError):
        worker.read_nodegroup(future, "tags")
    assert len(worker.synthetic_calls) == before
    for invalid_worker_generation in (None, True, 7.0, "7", 8):
        invalid_worker = _transport(monkeypatch, generation=invalid_worker_generation,
                                    reply=worker.synthetic_reply)
        invalid_node = RemoteJava(invalid_worker, "invalid-list", 7, NODE_GROUP_LIST)
        with pytest.raises(JavaWorkerError):
            invalid_worker.read_nodegroup(invalid_node, "tags")
        assert not invalid_worker.synthetic_calls


def test_private_dispatch_witness_is_narrow_and_generic_unknowns_stay_mutating():
    assert _domain_outcome.is_mutation_call("tags", (), command="g2_nodegroup_read") is False
    assert _domain_outcome.is_mutation_call("nodeGroup", ("m",), command="g2_nodegroup_read") is False
    assert _domain_outcome.is_mutation_call("get", (True,), command="g2_nodegroup_read") is True
    assert _domain_outcome.is_mutation_call(None, (), command="g2_nodegroup_read") is True
    assert _domain_outcome.is_mutation_call("entity_identity", ("left", "right", 7),
                                           command="g2_entity_identity") is False
    assert _domain_outcome.is_mutation_call("entity_identity", ("left", 7),
                                           command="g2_entity_identity") is True
    assert _domain_outcome.is_mutation_call(None, (), command="g2_nodegroup_ungroup") is True
    assert _domain_outcome.is_mutation_call("unapprovedMethod", ()) is True


@pytest.mark.parametrize("fault", ["foreign_same_text", "cycle", "null_container"])
def test_foreign_same_text_cycle_and_null_container_chains_refuse_before_write(env, fault):
    _backend, worker, _ref, tickets = env
    worker.fault = fault
    with pytest.raises(ExecutionContractError):
        _invoke(env)
    assert not worker.ungroup_calls and not tickets
    assert _state(env).dirty is False


@pytest.mark.parametrize("fault", ["false", "malformed_boolean", "boolean_generation"])
def test_false_or_malformed_java_identity_reply_refuses_before_write(env, fault):
    _backend, worker, _ref, tickets = env
    worker.fault = fault
    if fault == "boolean_generation":
        worker.generation = True
    with pytest.raises(ExecutionContractError):
        _invoke(env)
    assert not worker.ungroup_calls and not tickets
    assert _state(env).dirty is False


@pytest.mark.parametrize("fault", ["boolean_generation", "wrong_receiver", "wrong_version_type",
                                   "tuple_parameters", "extra_field"])
def test_typed_read_reply_rejects_wrong_version_receiver_and_field_types(monkeypatch, fault):
    result = {"generation": 7, "receiver_interface": NODE_GROUP_LIST, "method": "tags",
              "parameters": [], "return_type": "[Ljava.lang.String;", "runtime_version": VERSION,
              "value": ["g1"]}
    if fault == "boolean_generation":
        result["generation"] = True
    elif fault == "wrong_receiver":
        result["receiver_interface"] = NODE_GROUP
    elif fault == "wrong_version_type":
        result["runtime_version"] = 6.4
    elif fault == "tuple_parameters":
        result["parameters"] = ()
    else:
        result["extra"] = "unreviewed"
    reply = _worker_reply("g2_nodegroup_read", result)
    descriptor = _descriptor(NODE_GROUP_LIST, "tags", [], "[Ljava.lang.String;", receiver=NODE_GROUP_LIST)
    worker = _transport(monkeypatch, reply=reply, descriptor=descriptor)
    with pytest.raises(JavaWorkerError):
        worker.read_nodegroup(RemoteJava(worker, "list", 7, NODE_GROUP_LIST), "tags")
    assert len(worker.synthetic_calls) == 1


def test_java_d2_dispatch_source_contract_static_only():
    source = Path(__file__).parents[1] / "comsol_mcp" / "worker_java" / "PersistentComsolWorker.java"
    text = source.read_text(encoding="utf-8")
    start = text.index("private Object g2EntityIdentity")
    stop = text.index("private Object g2NodeGroupRead", start)
    identity = text[start:stop]
    assert "left == right" in identity
    assert "getComsolVersion" not in identity and "requireD2RuntimeVersion" not in identity
    assert "private Object g2NodeGroupUngroup" in text
    assert "((NodeGroupList) target).ungroup(tag)" in text
    methods = text[text.index("private static final Set<String> METHODS"):text.index("private static final Set<String> METHODS") + 2400]
    assert '"ungroup"' not in methods
    assert "requireD2Generation" in text and "requireD2RequestFields" in text


def test_private_transport_uses_opaque_handles_as_protocol_operands_not_identity(monkeypatch):
    result = {"same_reference": True, "generation": 7, "identity_scope": "java_reference_identity"}
    worker = _transport(monkeypatch, reply=_worker_reply("g2_entity_identity", result))
    left = RemoteJava(worker, "opaque-left", 7, MODEL_ENTITY)
    right = RemoteJava(worker, "opaque-right", 7, MODEL_ENTITY)
    assert worker.entity_identity(left, right) is True
    command, payload, _kwargs = worker.synthetic_calls[0]
    assert command == "g2_entity_identity"
    assert payload == {"left_handle": "opaque-left", "right_handle": "opaque-right", "generation": 7}


@pytest.mark.parametrize(("worker_generation", "node_generation"), [(True, 1), (1, True), (1.0, 1), ("1", 1)])
def test_private_remote_generation_operands_require_exact_python_integers(monkeypatch, worker_generation, node_generation):
    worker = _transport(monkeypatch, generation=worker_generation,
                        reply=_worker_reply("g2_entity_identity", {
                            "same_reference": True, "generation": worker_generation,
                            "identity_scope": "java_reference_identity"}))
    node = RemoteJava(worker, "h", node_generation, MODEL_ENTITY)
    with pytest.raises(JavaWorkerError):
        worker.entity_identity(node, node)
    assert not worker.synthetic_calls


@pytest.mark.parametrize("result", [
    {"same_reference": 1, "generation": 7, "identity_scope": "java_reference_equality"},
    {"same_reference": True, "generation": True, "identity_scope": "java_reference_equality"},
    {"same_reference": True, "generation": 7},
    {"same_reference": True, "generation": 7, "identity_scope": None},
])
def test_private_identity_reply_requires_exact_boolean_generation_and_fields(monkeypatch, result):
    worker = _transport(monkeypatch, reply=_worker_reply("g2_entity_identity", result))
    node = RemoteJava(worker, "h", 7, MODEL_ENTITY)
    with pytest.raises(JavaWorkerError):
        worker.entity_identity(node, node)
    assert len(worker.synthetic_calls) == 1


@pytest.mark.parametrize(("method", "args"), [
    (None, ()), ("", ()), ("nodeGroup", ("m", "extra")), ("nodeGroup", ("",)),
    ("get", (True,)), ("get", (-1,)), ("get", ()), ("getAfter", (0,)),
    ("getContainer", ("extra",)), ("size", (0,)), ("ungroup", ("g1",)),
])
def test_private_read_helper_refuses_wrong_overload_and_mutation_before_submit(monkeypatch, method, args):
    descriptor = _descriptor(NODE_GROUP_LIST, "tags", [], "[Ljava.lang.String;", receiver=NODE_GROUP_LIST)
    worker = _transport(monkeypatch, reply=_worker_reply("g2_nodegroup_read", {}), descriptor=descriptor)
    node = RemoteJava(worker, "list", 7, NODE_GROUP_LIST)
    with pytest.raises(JavaWorkerError):
        worker.read_nodegroup(node, method, *args)
    assert not worker.synthetic_calls


@pytest.mark.parametrize("fault", ["generation_bool", "wrong_receiver", "wrong_version_type",
                                   "tuple_parameters", "extra_field"])
def test_private_read_transport_rejects_malformed_signature_reply(monkeypatch, fault):
    result = {"generation": 7, "receiver_interface": NODE_GROUP_LIST, "method": "tags",
              "parameters": [], "return_type": "[Ljava.lang.String;", "runtime_version": VERSION,
              "value": ["g1"]}
    if fault == "generation_bool":
        result["generation"] = True
    elif fault == "wrong_receiver":
        result["receiver_interface"] = NODE_GROUP
    elif fault == "wrong_version_type":
        result["runtime_version"] = 6.4
    elif fault == "tuple_parameters":
        result["parameters"] = ()
    else:
        result["extra_field"] = "not allowed"
    worker = _transport(monkeypatch, reply=_worker_reply("g2_nodegroup_read", result))
    with pytest.raises(JavaWorkerError):
        worker.read_nodegroup(RemoteJava(worker, "list", 7, NODE_GROUP_LIST), "tags")
    assert len(worker.synthetic_calls) == 1


@pytest.mark.parametrize("generation", [True, 7.0, "7", None])
def test_private_ungroup_signature_rejects_coercible_generation(monkeypatch, generation):
    result = {"ungroup_dispatched": True, "generation": generation, "tag": "g1",
              "semantic_operation": "NodeGroupList.ungroup(String)", "runtime_version": VERSION}
    descriptor = _descriptor(NODE_GROUP_LIST, "ungroup", [JAVA_STRING], "void", receiver=NODE_GROUP_LIST)
    worker = _transport(monkeypatch, reply=_worker_reply("g2_nodegroup_ungroup", result), descriptor=descriptor)
    with pytest.raises(JavaWorkerError):
        worker.ungroup_nodegroup(RemoteJava(worker, "list", 7, NODE_GROUP_LIST), "g1")
    assert len(worker.synthetic_calls) == 1


@pytest.mark.parametrize(("method", "args", "command"), [
    (None, (), "g2_nodegroup_read"), ("", (), "g2_nodegroup_read"), (1, (), "g2_nodegroup_read"),
    ("get", (-1,), "g2_nodegroup_read"), ("get", (True,), "g2_nodegroup_read"),
    ("nodeGroup", ("m", "extra"), "g2_nodegroup_read"),
    ("ungroup", ("g1",), "g2_nodegroup_read"),
    ("entity_identity", ("h",), "g2_entity_identity"),
    ("entity_identity", ("h", 2), "g2_entity_identity"),
    (None, (), "g2_nodegroup_ungroup"),
])
def test_private_dispatch_malformed_forms_remain_potential_mutations(method, args, command):
    assert _domain_outcome.is_mutation_call(method, args, command=command) is True
    assert _domain_outcome.is_mutation_call(["tags"], (), command="g2_nodegroup_read") is True
    assert _domain_outcome.is_mutation_call({"method": "tags"}, (), command="g2_nodegroup_read") is True


@pytest.mark.parametrize("value", [
    {"$worker_handle": "h", "generation": True, "java_type": MODEL},
    {"$worker_handle": 1, "generation": 7, "java_type": MODEL},
    {"$worker_handle": "h", "generation": 7, "java_type": None},
    {"$worker_handle": "h", "generation": 7, "java_type": MODEL, "extra": "x"},
])
def test_private_typed_handle_decoder_rejects_coercible_fields(monkeypatch, value):
    worker = _transport(monkeypatch)
    with pytest.raises(JavaWorkerError):
        _java_worker._decode_d2_typed_value(value, worker, 7)
