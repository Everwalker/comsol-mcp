"""T21 component-scoped definition ``list`` targeted tests.

Originally developed and run against an isolated package copy, these tests pin
the contract for
``definition.{coordinate,coupling,pair}_manage`` with ``action: list``:

* one real positive control per type on the public route (real ControlDaemon +
  real ProjectAuthority + fake engine endpoint), scoped by a single
  ``component:<ctag>`` NodePath;
* internal direct callers keep the ``component`` parameter, and the two
  spellings must agree;
* every statically invalid or conflicting scope is refused without a domain
  worker call and with a clean failure (never EXECUTION_STATE_UNKNOWN, never dirty), while a
  component that the engine does not have still goes through the engine read and
  comes back as NODE_NOT_FOUND - the validation stage is claimed only where the
  witness can corroborate it;
* the permission boundary is unchanged: an inspection-only project is still
  refused for the same new scope form.

No COMSOL, Worker, socket or historical model is contacted.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from comsol_mcp import _g3_ops, _g3_w14 as w14
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from tests.test_dynamic_project_auth_qwen_20261109 import FULL, WRITE_ONLY, make_harness
from tests.test_g3_w14 import world

LIST_OPS = ("definition.coordinate_manage", "definition.coupling_manage", "definition.pair_manage")
LIST_FIELDS = {"definition.coordinate_manage": "coordinate_systems",
               "definition.coupling_manage": "couplings",
               "definition.pair_manage": "pairs"}
LIST_TAGS = {"definition.coordinate_manage": "sys1",
             "definition.coupling_manage": "cpl1",
             "definition.pair_manage": "pairA"}
COLLECTIONS = {"definition.coordinate_manage": "coordSystem",
               "definition.coupling_manage": "cpl",
               "definition.pair_manage": "pair"}


def seg(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"segments": [{"collection": c, "tag": t} for c, t in pairs]}


def component_path(tag: str = "comp1") -> dict[str, Any]:
    return seg(("component", tag))


# ---------------------------------------------------------------------------
# positive controls: the public route, one per definition type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", LIST_OPS)
def test_public_list_scoped_by_one_component_node(tmp_path, monkeypatch, operation):
    harness = make_harness(tmp_path, f"cand-ok-{operation.replace('.', '-')}", monkeypatch, FULL)
    try:
        result = harness.call(operation, {"action": "list", "path": component_path()}, "cand-ok")
        assert result["success"] is True, json.dumps(result, sort_keys=True)[:1500]
        assert result["data"]["component"] == "comp1", result["data"]
        assert LIST_TAGS[operation] in [row["tag"] for row in result["data"][LIST_FIELDS[operation]]]
        # a success must actually have consulted the fake engine
        assert harness.witness.calls, "successful list issued no engine read"
        assert result["execution"]["dirty"] is False
    finally:
        harness.daemon.close()


@pytest.mark.parametrize("operation", LIST_OPS)
def test_internal_direct_call_keeps_the_component_parameter(operation):
    """Internal callers that pass ``component`` (a form the public schema refuses)."""
    worker, _, comp = world()
    comp.collections[COLLECTIONS[operation]].items[LIST_TAGS[operation]] = _fake_node(operation)
    data = w14.OPERATIONS[operation](worker, "Model", {"action": "list", "component": "comp1"})
    assert data["component"] == "comp1"
    assert LIST_TAGS[operation] in [row["tag"] for row in data[LIST_FIELDS[operation]]]
    assert comp.collections[COLLECTIONS[operation]].calls, "list must read the fake container"


def test_public_schema_still_rejects_a_top_level_component_argument(tmp_path, monkeypatch):
    """The candidate does not widen the published request surface."""
    harness = make_harness(tmp_path, "cand-schema", monkeypatch, FULL)
    try:
        for operation in LIST_OPS:
            result = harness.call(operation, {"action": "list", "component": "comp1",
                                              "path": component_path()}, f"cand-schema-{operation}")
            assert result["success"] is False, result
            assert harness.error_code(result) == "INVALID_REQUEST", result
            assert "unsupported arguments: component" in result["error"]["message"], result
        assert harness.witness.calls == []
    finally:
        harness.daemon.close()


# ---------------------------------------------------------------------------
# negative controls: an invalid scope never reaches the domain worker
# ---------------------------------------------------------------------------

BAD_SCOPES = {
    "repeated_component": seg(("component", "comp1"), ("component", "comp1")),
    "fabricated_second_component": seg(("component", "comp1"), ("component", "not-a-child")),
    "definition_node_as_scope": seg(("component", "comp1"), ("coordSystem", "sys1")),
    "accessor_only": {"segments": [{"accessor": "geom"}]},
    "wrong_first_collection": seg(("param", "p1")),
    "no_segments": {"segments": []},
}


@pytest.mark.parametrize("operation", LIST_OPS)
@pytest.mark.parametrize("label", sorted(BAD_SCOPES))
def test_invalid_scope_refused_with_zero_engine_calls(tmp_path, monkeypatch, operation, label):
    harness = make_harness(tmp_path, f"cand-bad-{label}-{operation.replace('.', '-')}", monkeypatch, FULL)
    try:
        worker = harness.daemon.backend.worker
        client = worker.client
        client_calls = []
        def tracked_client():
            client_calls.append(True)
            return client()
        monkeypatch.setattr(worker, "client", tracked_client)
        result = harness.call(operation, {"action": "list", "path": BAD_SCOPES[label]}, f"cand-{label}")
        assert result["success"] is False, json.dumps(result, sort_keys=True)[:1200]
        code = harness.error_code(result)
        assert code == "INVALID_NODE_PATH", result["error"]
        # No domain worker call. The service's snapshot uses a separate fake
        # adapter here, so this does not prove zero pre-callback native RPCs.
        assert result["error"]["stage"] == "validation", result["error"]
        assert harness.witness.calls == [], harness.witness
        assert client_calls == []
        assert result["execution"]["dirty"] is False, result["execution"]
        data = result["data"]
        assert data.get("refused") is True, data
        assert data.get("dispatch_stage") == "validation", data
        assert data.get("execution_state_unknown") is False, data
        assert data.get("witness", {}).get("mutation_issued") is False, data
        assert data.get("witness", {}).get("engine_calls") == 0, data
    finally:
        harness.daemon.close()


@pytest.mark.parametrize("operation", LIST_OPS)
def test_missing_scope_is_a_clean_invalid_request(tmp_path, monkeypatch, operation):
    harness = make_harness(tmp_path, f"cand-noscope-{operation.replace('.', '-')}", monkeypatch, FULL)
    try:
        result = harness.call(operation, {"action": "list"}, "cand-noscope")
        assert result["success"] is False
        assert harness.error_code(result) == "INVALID_REQUEST", result["error"]
        assert result["error"]["stage"] == "validation"
        assert harness.witness.calls == []
        assert result["execution"]["dirty"] is False
    finally:
        harness.daemon.close()


@pytest.mark.parametrize("operation", LIST_OPS)
def test_tag_that_the_engine_does_not_have_still_uses_the_engine(tmp_path, monkeypatch, operation):
    """An existence question must not be answered by the scope-shape check."""
    harness = make_harness(tmp_path, f"cand-absent-{operation.replace('.', '-')}", monkeypatch, FULL)
    try:
        result = harness.call(operation, {"action": "list", "path": component_path("ghost")}, "cand-absent")
        assert result["success"] is False
        assert harness.error_code(result) == "NODE_NOT_FOUND", result["error"]
        assert harness.witness.calls, "a component existence question must consult the engine"
        assert result["execution"]["dirty"] is False
    finally:
        harness.daemon.close()


# ---------------------------------------------------------------------------
# internal spellings: component parameter vs path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", LIST_OPS)
def test_internal_conflicting_identity_is_refused_before_any_call(operation):
    worker, _, comp = world()
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS[operation](worker, "Model", {
            "action": "list", "component": "comp1", "path": component_path("comp2")})
    assert info.value.code == "INVALID_REQUEST", info.value
    assert info.value.stage == "validation"
    for collection in ("coordSystem", "cpl", "pair"):
        assert comp.collections[collection].calls == []


@pytest.mark.parametrize("operation", LIST_OPS)
def test_internal_agreeing_identity_is_accepted(operation):
    worker, _, comp = world()
    comp.collections[COLLECTIONS[operation]].items[LIST_TAGS[operation]] = _fake_node(operation)
    data = w14.OPERATIONS[operation](worker, "Model", {
        "action": "list", "component": "comp1", "path": component_path("comp1")})
    assert data["component"] == "comp1"
    assert LIST_TAGS[operation] in [row["tag"] for row in data[LIST_FIELDS[operation]]]


@pytest.mark.parametrize("operation", LIST_OPS)
def test_internal_unusable_component_value_is_a_validation_refusal(operation):
    worker, _, comp = world()
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS[operation](worker, "Model", {"action": "list", "component": "not a tag"})
    assert info.value.code == "INVALID_REQUEST", info.value
    assert comp.collections[COLLECTIONS[operation]].calls == []


def test_path_component_tag_must_be_a_usable_tag():
    worker, _, comp = world()
    with pytest.raises(PreWriteRefusal) as info:
        w14.OPERATIONS["definition.coordinate_manage"](
            worker, "Model", {"action": "list", "path": component_path("not a tag")})
    assert info.value.code == "INVALID_REQUEST", info.value
    assert comp.collections["coordSystem"].calls == []


def test_list_refusals_do_not_touch_the_definition_node_paths():
    """Pair/coordinate/coupling inspect and remove keep their own two-segment contract."""
    worker, _, comp = world()
    for path, collection in ((seg(("component", "comp1"), ("coordSystem", "sys1")), "coordSystem"),
                             (seg(("component", "comp1"), ("cpl", "cpl1")), "cpl"),
                             (seg(("component", "comp1"), ("pair", "pairA")), "pair")):
        with pytest.raises(ExecutionContractError) as info:
            w14.OPERATIONS[{"coordSystem": "definition.coordinate_manage",
                            "cpl": "definition.coupling_manage",
                            "pair": "definition.pair_manage"}[collection]](
                worker, "Model", {"action": "inspect", "path": path})
        assert info.value.code == "NODE_NOT_FOUND", (collection, info.value.code)


# ---------------------------------------------------------------------------
# the permission boundary is untouched by the new scope form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", LIST_OPS)
def test_inspection_only_project_is_still_refused(tmp_path, monkeypatch, operation):
    harness = make_harness(tmp_path, f"cand-deny-{operation.replace('.', '-')}", monkeypatch, WRITE_ONLY)
    try:
        before = harness.operation_rows()
        result = harness.call(operation, {"action": "list", "path": component_path()}, "cand-deny")
        assert result["success"] is False, result
        assert harness.error_code(result) == "PERMISSION_DENIED", result["error"]
        assert "project_write" in result["error"]["message"], result["error"]
        assert harness.witness.calls == []
        assert "execution" not in result, result
        assert harness.operation_rows() == before
    finally:
        harness.daemon.close()


@pytest.mark.parametrize("operation", LIST_OPS)
def test_effect_class_of_the_three_list_paths_is_unchanged(operation):
    assert _g3_ops.EFFECTS[operation] == "DYNAMIC"


def _fake_node(operation: str) -> Any:
    from tests.test_g3_w14 import FNode
    return FNode(tag=LIST_TAGS[operation], type_id="Fake")
