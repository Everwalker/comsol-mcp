"""T21 generic project authorization for every server-side ``DYNAMIC`` effect.

Two independently reproduced defects are pinned here, both on the public
``registry_call`` entrypoint of the real :class:`ControlDaemon`:

* RED (before the conservative repair): every statically ``DYNAMIC`` operation
  — the nine implemented G3 ``*_manage`` adapters plus the ``api.invoke`` /
  ``registry.call`` catalogue entries — resolved to *no* enforced project
  effect mapping, so an already ``project_write``-authorized project was
  refused with ``PROJECT_SCOPE_UNSUPPORTED`` before any Worker dispatch
  (``docs/universal_mcp_execution/evidence/full/T21-public-read-unknown-parent-20261109.txt``).
* The repair must not weaken the boundary: the precheck has to demand the same
  conservative grant the ManagedBackend itself uses for its write ticket
  (``_managed_backend._G3_EFFECT_MAP`` / ``_execution_contract._G3_CATALOG_EFFECTS``
  map catalogue ``DYNAMIC`` to the write class), so an inspection-only project
  must still be refused for every dynamic action, including read-shaped ones
  such as ``list``/``inspect``.

Everything below drives the real ControlDaemon → ProjectAuthority →
ManagedBackend → ExecutionService chain.  The only test double is a fresh
in-process fake engine that records **every** method the adapter issues, so a
refusal is evidence of zero engine dispatch, and a pass is evidence of the real
mutation.  ``COMSOL_MCP_HOST_CONTROL`` plus the ledger grant mirror the
production host-gate used by ``project.policy_set`` so a real project can be
revoked to inspection-only after ``model.adopt`` (``model.adopt`` itself needs
``project_write``).  No COMSOL, Worker, socket or historical model is touched;
this is not native evidence.
"""
from __future__ import annotations

import json
import types
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pytest

from comsol_mcp import _g2_registry, _g3_ops, _managed_backend
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import (
    EFFECT_PERMISSIONS,
    ExecutionContractError,
    SessionLedger,
    _G3_CATALOG_EFFECTS,
)
from comsol_mcp._execution_service import ExecutionService

# Every implemented G3 operation the action catalogue classifies DYNAMIC.
DYNAMIC_G3_OPERATIONS = tuple(sorted(
    operation for operation, effect in _g3_ops.EFFECTS.items() if effect == "DYNAMIC"
))

# Schema-valid request bodies (the published input_schema is what the public
# route validates against, so a body here is rejected only by the ACL gate).
DYNAMIC_BODIES: dict[str, dict[str, Any]] = {
    "definition.component_manage": {"action": "list"},
    "definition.coordinate_manage": {"action": "inspect", "path": {
        "segments": [{"collection": "component", "tag": "comp1"},
                     {"collection": "coordSystem", "tag": "sys1"}]}},
    "definition.coupling_manage": {"action": "inspect", "path": {
        "segments": [{"collection": "component", "tag": "comp1"},
                     {"collection": "cpl", "tag": "cpl1"}]}},
    "definition.pair_manage": {"action": "inspect", "path": {
        "segments": [{"collection": "component", "tag": "comp1"},
                     {"collection": "pair", "tag": "pairA"}]}},
    "parameter.case_manage": {"action": "list", "group": "g1", "case_tag": "case01"},
    "physics.multiphysics_manage": {"action": "inspect", "path": {
        "segments": [{"collection": "multiphysics", "tag": "mp1"}]}},
    "plot.view_manage": {"action": "list"},
    "result.numerical_manage": {"action": "list"},
    "result.table_manage": {"action": "list"},
}

# ``parameter.case_manage`` keeps its case registry in the control-plane
# metadata store, so a successful list legitimately issues no engine call.
CONTROL_STORE_ONLY = frozenset({"parameter.case_manage"})


# ---------------------------------------------------------------------------
# fresh fake engine with an exhaustive call witness
# ---------------------------------------------------------------------------


class Witness:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, name: str) -> None:
        self.calls.append(name)

    def __repr__(self) -> str:  # pragma: no cover - assertion output only
        return f"Witness({self.calls!r})"


class FList:
    def __init__(self, name: str, witness: Witness) -> None:
        self.name = name
        self.witness = witness
        self.items: dict[str, Any] = {}

    def tags(self) -> list[str]:
        self.witness.record(f"{self.name}.tags")
        return list(self.items)

    def get(self, tag: str) -> Any:
        self.witness.record(f"{self.name}.get:{tag}")
        return self.items[tag]

    def hasTag(self, tag: str) -> bool:
        self.witness.record(f"{self.name}.hasTag:{tag}")
        return tag in self.items

    def create(self, tag: str, *type_id: Any) -> "FNode":
        self.witness.record(f"{self.name}.create:{tag}:{type_id}")
        node = FNode(tag=tag, type_id=str(type_id[0]) if type_id else "Fake",
                     container=self.name, witness=self.witness)
        self.items[tag] = node
        return node

    def remove(self, tag: str) -> None:
        self.witness.record(f"{self.name}.remove:{tag}")
        del self.items[tag]


class FNode:
    def __init__(self, *, tag: str, type_id: str = "Fake",
                 collections: Mapping[str, Any] | None = None,
                 container: str = "model", witness: Witness) -> None:
        self.tag_ = tag
        self.type_id = type_id
        self.collections = dict(collections or {})
        self.container = container
        self.witness = witness
        self.values: dict[str, Any] = {}

    def tag(self) -> str:
        return self.tag_

    def getType(self) -> str:
        return self.type_id

    def label(self, *args: Any) -> Any:
        self.witness.record(f"{self.container}:{self.tag_}.label")
        return self.tag_

    def properties(self) -> list[str]:
        self.witness.record(f"{self.container}:{self.tag_}.properties")
        return sorted(self.values)

    def hasProperty(self, name: str) -> bool:
        return name in self.values

    def getValueType(self, name: str) -> Any:
        return self.values.get(name)

    def getString(self, name: str) -> Any:
        self.witness.record(f"{self.container}:{self.tag_}.getString:{name}")
        return self.values.get(name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        collections = self.__dict__.get("collections") or {}
        if name in collections:
            target = collections[name]

            def accessor(tag: Any = None, *rest: Any) -> Any:
                if tag is None:
                    return target
                return target.get(tag)
            return accessor
        raise AttributeError(name)


class FTableNode(FNode):
    """Table node with the readback getters the table adapter probes."""

    def getColumnHeaders(self) -> list[str]:
        self.witness.record(f"{self.container}:{self.tag_}.getColumnHeaders")
        return ["x", "f"]

    def getReal(self) -> list[list[float]]:
        self.witness.record(f"{self.container}:{self.tag_}.getReal")
        return [[1.0, 2.0]]

    def getImag(self) -> Any:
        self.witness.record(f"{self.container}:{self.tag_}.getImag")
        return None

    def isComplex(self) -> bool:
        self.witness.record(f"{self.container}:{self.tag_}.isComplex")
        return False


class FakeWorker:
    generation = 1

    def __init__(self, model: FNode) -> None:
        self.model_node = model

    def client(self) -> Any:
        outer = self

        class _C:
            def model(self, tag: str) -> FNode:
                return outer.model_node
        return _C()

    def operation_context(self, *a: Any, **k: Any) -> Any:
        return nullcontext()


class SnapshotAdapter:
    def __init__(self, server_id: str) -> None:
        self.server_id = server_id

    def model_snapshot(self, tag: str) -> dict[str, Any]:
        return {"model_tag": tag, "server_instance_id": self.server_id,
                "external_event_counter": 0, "fingerprint": "fp-dynamic-auth"}


@dataclass
class Harness:
    daemon: ControlDaemon
    project: dict[str, Any]
    model: FNode
    env: dict[str, Any]
    witness: Witness
    grants: tuple[str, ...]
    pending: list[str] = field(default_factory=list)

    def call(self, operation: str, body: Mapping[str, Any], key: str,
             *, execution: Mapping[str, Any] | None = None) -> dict[str, Any]:
        envelope = dict(execution if execution is not None else self.env)
        envelope["idempotency_key"] = key
        envelope["request_id"] = key
        result = self.daemon.dispatch({"operation": "registry_call",
                                       "arguments": {"operation_id": operation,
                                                     "arguments": dict(body)},
                                       "execution": envelope})
        revision = (result.get("execution") or {}).get("revision")
        if isinstance(revision, int):
            self.env["expected_revision"] = revision
        return result

    def error_code(self, result: Mapping[str, Any]) -> Any:
        return (result.get("error") or {}).get("code")

    def operation_rows(self) -> int:
        with self.daemon.store.lock:
            return self.daemon.store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0]


def make_harness(tmp_path: Path, name: str, monkeypatch, final_grants: list[str]) -> Harness:
    """Real daemon + real ProjectAuthority; only the engine endpoint is fake."""
    monkeypatch.setattr(_g3_ops, "REQUIRES_ISOLATION", frozenset())
    # Production host gate, same mechanism tests/test_control_daemon_projects.py
    # uses, so project.policy_set can really revoke the project grant later.
    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    container = tmp_path / name / "projects"
    container.mkdir(parents=True, exist_ok=True)
    ledger = SessionLedger(f"session-{name}", f"server-{name}",
                           permissions={"inspect", "project_write", "compute", "host_control"})
    service = ExecutionService(ledger, SnapshotAdapter(f"server-{name}"), project_root=container)
    witness = Witness()
    model = FNode(tag="Model", type_id="Model", witness=witness, collections={
        "func": FList("func", witness), "param": FList("param", witness),
        "variable": FList("variable", witness), "component": FList("component", witness),
        "selection": FList("selection", witness), "multiphysics": FList("multiphysics", witness),
        "view": FList("view", witness),
        "result": FNode(tag="result", type_id="ResultContainer", container="result",
                        witness=witness, collections={
                            "table": FList("table", witness),
                            "numerical": FList("numerical", witness),
                            "plot": FNode(tag="plot", type_id="PlotGroup", container="plot",
                                          witness=witness,
                                          collections={"view": FList("view", witness)})}),
    })
    model.__dict__["java"] = types.SimpleNamespace(tag=lambda: "m1", getFilePath=lambda: "")
    component = FNode(tag="comp1", type_id="Component", container="component", witness=witness,
                      collections={"pair": FList("pair", witness),
                                   "coordSystem": FList("coordSystem", witness),
                                   "cpl": FList("cpl", witness), "geom": FList("geom", witness),
                                   "selection": FList("selection", witness),
                                   "physics": FList("physics", witness)})
    model.collections["component"].items["comp1"] = component
    for collection, tag, type_id in (("pair", "pairA", "Contact"),
                                     ("coordSystem", "sys1", "Cylindrical"),
                                     ("cpl", "cpl1", "IdentityMapping")):
        node = FNode(tag=tag, type_id=type_id, container=collection, witness=witness)
        component.collections[collection].items[tag] = node
    model.collections["result"].collections["numerical"].items["num1"] = FNode(
        tag="num1", type_id="EvalGlobal", container="numerical", witness=witness)
    model.collections["result"].collections["table"].items["tbl1"] = FTableNode(
        tag="tbl1", type_id="Table", container="table", witness=witness)
    model.collections["view"].items["view1"] = FNode(tag="view1", type_id="View",
                                                     container="view", witness=witness)
    model.collections["multiphysics"].items["mp1"] = FNode(
        tag="mp1", type_id="ThermalStress", container="multiphysics", witness=witness)

    daemon = ControlDaemon(tmp_path / name / "control", service=service, registry={},
                           worker=FakeWorker(model), project_root=container)
    daemon.backend.endpoint_key = "127.0.0.1:2036"
    daemon.backend.worker_identity = {
        "runtime_id": f"rt-{name}", "worker_instance_id": f"w-{name}", "connection_epoch": 1,
        "server_instance_id": f"server-{name}", "endpoint": "127.0.0.1:2036"}
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": f"proj-{name}", "workspace": f"proj-{name}",
                      "policy": {"permissions": ["inspect", "project_write", "compute"]}},
        "execution": {"request_id": f"pc-{name}", "idempotency_key": f"pc-{name}"}})
    assert created["success"], created
    project = created["data"]["project"]
    adopted = daemon.dispatch({
        "operation": "model.adopt",
        "arguments": {"server_model_tag": "m1"},
        "execution": {"project_id": project["project_id"], "session_id": f"session-{name}",
                      "request_id": f"adopt-{name}", "idempotency_key": f"adopt-{name}"}})
    assert adopted["success"], adopted
    harness = Harness(daemon=daemon, project=project, model=model, witness=witness,
                      grants=tuple(final_grants), env={
                          "project_id": project["project_id"], "session_id": f"session-{name}",
                          "model_ref": adopted["execution"]["model_ref"],
                          "expected_revision": adopted["execution"]["revision"]})
    if set(final_grants) != {"inspect", "project_write", "compute"}:
        revoked = daemon.dispatch({
            "operation": "project.policy_set",
            "arguments": {"project_id": project["project_id"],
                          "policy": {"permissions": list(final_grants)},
                          "authorization_ref": f"audit-{name}"},
            "execution": {"request_id": f"ps-{name}", "idempotency_key": f"ps-{name}"}})
        assert revoked["success"], revoked
        harness.project["policy"]["permissions"] = list(final_grants)
    return harness


WRITE_ONLY = ["inspect"]
FULL = ["inspect", "project_write", "compute"]


# ---------------------------------------------------------------------------
# RED-1: an authorized project must not be refused for lacking an effect map
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", DYNAMIC_G3_OPERATIONS)
def test_authorized_project_executes_dynamic_operation(tmp_path, monkeypatch, operation):
    """A ``project_write`` project reaches the engine for every DYNAMIC action."""
    harness = make_harness(tmp_path, f"auth-{operation.replace('.', '_')}", monkeypatch, FULL)
    try:
        before = harness.operation_rows()
        result = harness.call(operation, DYNAMIC_BODIES[operation], f"auth-{operation}")
        assert result["success"] is True, result
        assert harness.error_code(result) is None, result
        if operation not in CONTROL_STORE_ONLY:
            assert harness.witness.calls, f"{operation} claimed success without an engine read"
        # the request was a real admitted operation, not a precheck refusal
        assert harness.operation_rows() > before
    finally:
        harness.daemon.close()


def test_dynamic_write_mutates_the_engine_and_advances_the_revision(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "dynamic-write", monkeypatch, FULL)
    try:
        created = harness.call("definition.component_manage",
                               {"action": "create", "tag": "compNew"}, "dyn-create")
        assert created["success"] is True, created
        assert "component.create:compNew" in json.dumps(harness.witness.calls)
        assert "compNew" in harness.model.collections["component"].items
        assert created["execution"]["dirty"] is False
        assert created["execution"]["revision"] == harness.env["expected_revision"]

        harness.witness.calls.clear()
        removed = harness.call("definition.component_manage",
                               {"action": "remove", "tag": "compNew"}, "dyn-remove")
        assert removed["success"] is True, removed
        assert "component.remove:compNew" in json.dumps(harness.witness.calls)
        assert "compNew" not in harness.model.collections["component"].items
        assert removed["execution"]["revision"] > created["execution"]["revision"]
    finally:
        harness.daemon.close()


# ---------------------------------------------------------------------------
# RED-2: the conservative grant must be enforced, never weakened
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", DYNAMIC_G3_OPERATIONS)
def test_inspection_only_project_is_refused_with_zero_engine_calls(tmp_path, monkeypatch, operation):
    """Every DYNAMIC action, read-shaped ones included, needs project_write."""
    harness = make_harness(tmp_path, f"deny-{operation.replace('.', '_')}", monkeypatch, WRITE_ONLY)
    try:
        before = harness.operation_rows()
        result = harness.call(operation, DYNAMIC_BODIES[operation], f"deny-{operation}")
        assert result["success"] is False, result
        assert harness.error_code(result) == "PERMISSION_DENIED", result
        assert "project_write" in result["error"]["message"], result
        # no adapter ran, so the engine cannot have been touched
        assert harness.witness.calls == [], harness.witness
        # and nothing was durably admitted for later dispatch
        assert "execution" not in result, result
        assert harness.operation_rows() == before
    finally:
        harness.daemon.close()


def test_compute_grant_does_not_authorize_dynamic_operations(tmp_path, monkeypatch):
    """``compute`` must not be flattened into a dynamic write grant."""
    harness = make_harness(tmp_path, "dynamic-compute-only", monkeypatch, ["inspect", "compute"])
    try:
        for operation in DYNAMIC_G3_OPERATIONS:
            result = harness.call(operation, DYNAMIC_BODIES[operation], f"compute-{operation}")
            assert harness.error_code(result) == "PERMISSION_DENIED", (operation, result)
        assert harness.witness.calls == [], harness.witness
    finally:
        harness.daemon.close()


def test_nested_registry_call_is_authorized_by_the_inner_dynamic_operation(tmp_path, monkeypatch):
    """The wrapper's own ``inspect`` legacy effect must never cover the inner write."""
    harness = make_harness(tmp_path, "dynamic-nested", monkeypatch, WRITE_ONLY)
    try:
        assert _g2_registry.LEGACY_TOOL_EFFECTS["registry_call"] == "inspect"
        before = harness.operation_rows()
        result = harness.call("definition.component_manage",
                              {"action": "create", "tag": "compNested"}, "nested-deny")
        assert harness.error_code(result) == "PERMISSION_DENIED", result
        assert harness.witness.calls == [], harness.witness
        assert harness.operation_rows() == before

        # a doubly wrapped fallback is not an authorization bypass either
        harness.witness.calls.clear()
        doubled = harness.daemon.dispatch({
            "operation": "registry_call",
            "arguments": {"operation_id": "registry_call", "arguments": {
                "operation_id": "definition.component_manage",
                "arguments": {"action": "create", "tag": "compDoubled"}}},
            "execution": {**harness.env, "idempotency_key": "nested-double",
                          "request_id": "nested-double"}})
        assert doubled["success"] is False, doubled
        assert harness.error_code(doubled) == "UNSUPPORTED_OPERATION", doubled
        assert "component.create:compDoubled" not in json.dumps(harness.witness.calls)
        assert "compDoubled" not in harness.model.collections["component"].items
    finally:
        harness.daemon.close()


def test_stale_revision_refuses_a_dynamic_write_before_the_engine_runs(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "dynamic-revision", monkeypatch, FULL)
    try:
        stale = dict(harness.env)
        stale["expected_revision"] = harness.env["expected_revision"] + 7
        result = harness.call("definition.component_manage",
                              {"action": "create", "tag": "compStale"}, "stale-write",
                              execution=stale)
        assert harness.error_code(result) == "REVISION_CONFLICT", result
        assert harness.witness.calls == [], harness.witness
        assert "compStale" not in harness.model.collections["component"].items
    finally:
        harness.daemon.close()


# ---------------------------------------------------------------------------
# identity: no project or model escape
# ---------------------------------------------------------------------------


def test_dynamic_operation_cannot_escape_its_project(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "dynamic-escape", monkeypatch, FULL)
    other = make_harness(tmp_path, "dynamic-other", monkeypatch, FULL)
    try:
        # 1. foreign ModelRef under this project id
        foreign_ref = {"model_tag": "not-mine", "session_id": harness.env["session_id"],
                       "server_instance_id": "server-dynamic-other", "connection_epoch": 1}
        result = harness.call("definition.component_manage", {"action": "create", "tag": "compX"},
                              "escape-ref", execution={**harness.env, "model_ref": foreign_ref})
        assert harness.error_code(result) == "MODEL_IDENTITY_MISMATCH", result

        # 2. this project's ModelRef under an unrelated project id
        result = harness.call("definition.component_manage", {"action": "create", "tag": "compX"},
                              "escape-project",
                              execution={**harness.env, "project_id": other.project["project_id"]})
        assert harness.error_code(result) == "PROJECT_IDENTITY_MISMATCH", result

        # 3. bound ModelRef with the project identity removed entirely
        unscoped = {key: value for key, value in harness.env.items() if key != "project_id"}
        result = harness.call("definition.component_manage", {"action": "create", "tag": "compX"},
                              "escape-unscoped", execution=unscoped)
        assert harness.error_code(result) in {"PROJECT_IDENTITY_REQUIRED",
                                              "PROJECT_IDENTITY_MISMATCH"}, result

        # 4. an unregistered project id
        result = harness.call("definition.component_manage", {"action": "create", "tag": "compX"},
                              "escape-ghost",
                              execution={**harness.env, "project_id": "project-does-not-exist"})
        assert harness.error_code(result) in {"PROJECT_IDENTITY_MISMATCH",
                                              "PROJECT_STATE_UNKNOWN"}, result
        assert harness.witness.calls == [], harness.witness
        assert "compX" not in harness.model.collections["component"].items
        assert other.model.collections["component"].items == {"comp1":
                                                              other.model.collections["component"].items["comp1"]}
    finally:
        harness.daemon.close()
        other.daemon.close()


def test_nested_identity_fields_cannot_redirect_a_dynamic_operation(tmp_path, monkeypatch):
    """An inner ``project_id`` must never move the operation to another project.

    Observed residual (reported, not hidden): unlike the Unit-A and model
    delivery routes, the public G3 route does not compare inner identity fields
    with the outer envelope — it ignores them and executes the envelope's
    project/ModelRef.  This test pins that the ignored field is genuinely inert;
    the missing strict reconciliation is recorded as an open finding for the
    backend owner instead of being asserted away here.
    """
    harness = make_harness(tmp_path, "dynamic-identity", monkeypatch, FULL)
    try:
        smuggled = dict(DYNAMIC_BODIES["definition.component_manage"])
        smuggled["project_id"] = "another-project"
        result = harness.call("definition.component_manage", smuggled, "smuggle")
        # the envelope identity governed the call; the smuggled id changed nothing
        assert result["execution"]["model_ref"] == harness.env["model_ref"], result
        assert harness.error_code(result) is None, result
        assert harness.model.collections["component"].items == {"comp1":
                                                               harness.model.collections["component"].items["comp1"]}
        # a conflicting inner session identity is the same inert case
        smuggled_session = dict(DYNAMIC_BODIES["definition.component_manage"])
        smuggled_session["session_id"] = "someone-elses-session"
        result = harness.call("definition.component_manage", smuggled_session, "smuggle-session")
        assert result["execution"]["session_id"] == harness.env["session_id"], result
    finally:
        harness.daemon.close()


# ---------------------------------------------------------------------------
# api.invoke / registry.call entries must not inherit the dynamic write grant
# ---------------------------------------------------------------------------


def test_api_invoke_effect_comes_from_the_reviewed_capability_not_the_catalog(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "api-invoke-deny", monkeypatch, WRITE_ONLY)
    try:
        body = {"path": {"segments": [{"collection": "component", "tag": "comp1"}]},
                "method": "label", "arguments": [{"kind": "string", "shape": [], "data": "x"}],
                "declared_effect": "WRITE"}
        result = harness.call("api.invoke", body, "api-write")
        assert harness.error_code(result) == "PERMISSION_DENIED", result
        assert "project_write" in result["error"]["message"], result
        assert harness.witness.calls == [], harness.witness

        # an unreviewed method never becomes a project_write pass-through either
        harness.grants  # (same inspection-only project)
        bad = {"path": {"segments": [{"collection": "component", "tag": "comp1"}]},
               "method": "notReviewed", "arguments": [], "declared_effect": "READ"}
        result = harness.call("api.invoke", bad, "api-unreviewed")
        assert result["success"] is False, result
        assert harness.error_code(result) in {"API_UNSUPPORTED", "INVALID_REQUEST",
                                              "PERMISSION_DENIED"}, result
        assert harness.witness.calls == [], harness.witness
    finally:
        harness.daemon.close()


def test_unmapped_effect_still_fails_closed(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, "dynamic-failclosed", monkeypatch, FULL)
    try:
        with pytest.raises(ExecutionContractError) as raised:
            harness.daemon._authorize_project_execution("definition.quantum_manage", {},
                                                        dict(harness.env))
        assert raised.value.code == "PROJECT_SCOPE_UNSUPPORTED"
        assert ControlDaemon._permission_for_effect(None) is None
        assert ControlDaemon._permission_for_effect("MUTATE_THE_WORLD") is None
        assert ControlDaemon._permission_for_effect("dynamic") is None
    finally:
        harness.daemon.close()


# ---------------------------------------------------------------------------
# drift guard: the daemon must stay on the backend's conservative classification
# ---------------------------------------------------------------------------


def test_daemon_effect_mapping_equals_the_trusted_backend_tables():
    for catalog_effect, legacy_effect in _G3_CATALOG_EFFECTS.items():
        assert ControlDaemon._permission_for_effect(catalog_effect) == EFFECT_PERMISSIONS[legacy_effect]
    for legacy_effect, permission in EFFECT_PERMISSIONS.items():
        assert ControlDaemon._permission_for_effect(legacy_effect) == permission
    backend_map = _managed_backend._G3_EFFECT_MAP
    assert backend_map["DYNAMIC"] == "project_write"
    assert backend_map["DYNAMIC"] == _G3_CATALOG_EFFECTS["DYNAMIC"]


def test_every_dynamic_operation_requires_the_write_grant_and_never_inspect():
    assert len(DYNAMIC_G3_OPERATIONS) == 9
    for operation in DYNAMIC_G3_OPERATIONS:
        assert _g3_ops.is_implemented(operation)
        assert _g2_registry.BY_ID[operation].effect == "DYNAMIC"
        permission = ControlDaemon._permission_for_effect(_g3_ops.EFFECTS[operation])
        assert permission == "project_write", (operation, permission)
    # the catalogue's two envelope-shaped DYNAMIC entries are not authorized by
    # their catalogue effect: api.invoke resolves through the reviewed
    # capability table and registry.call through its inner operation.
    assert _g2_registry.BY_ID["api.invoke"].effect == "DYNAMIC"
    assert _g2_registry.BY_ID["registry.call"].effect == "DYNAMIC"
    assert _g2_registry.LEGACY_TOOL_EFFECTS["api_invoke"] == "dynamic"
    assert ControlDaemon._permission_for_effect(_g2_registry.LEGACY_TOOL_EFFECTS["api_invoke"]) is None


def test_model_inspect_effect_mapping_is_unchanged():
    assert ControlDaemon._permission_for_effect("inspect") == "inspect"
    assert ControlDaemon._permission_for_effect("project_write") == "project_write"
    assert ControlDaemon._permission_for_effect("INSPECT") == "inspect"
    assert ControlDaemon._permission_for_effect("PROJECT_WRITE") == "project_write"
    assert ControlDaemon._permission_for_effect("TRUSTED_CODE") == "trusted_code"
    assert ControlDaemon._permission_for_effect("HOST_CONTROL") == "host_control"
    assert ControlDaemon._permission_for_effect("EVALUATE") == "project_write"
    assert ControlDaemon._permission_for_effect("STATE_WRITE") == "project_write"
    assert ControlDaemon._permission_for_effect("FILE_WRITE") == "project_write"
